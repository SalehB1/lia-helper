"""The turn runs on its own task, so the client can leave and come back.

**The bug this exists to remove.** ``stream_answer`` used to be driven by the HTTP response
itself. When a browser refreshed, Starlette closed the request's anyio cancel scope, the
model call was abandoned mid-answer, and the emergency write in
``ChatService._persist_partial`` died at its first ``await`` — the in-flight ``flush`` was
cancelled, the session went to ``PendingRollbackError``, and the handler swallowed it. The
user came back to their own question with **no answer at all**, not even the text they had
already watched arrive. Measured, not theorised: 270 streamed characters, zero assistant rows.

**The shape.** ``stream_answer`` is untouched — it is still a plain async generator, and
every invariant CLAUDE.md documents for it (``_Turn.opened`` as the commit point, the
240-character screening window, ``_Escalate`` never surfacing as an error) holds by
construction, which is also why every existing test still drives it directly. What moved is
the *driver*: a task owned here iterates that generator with its **own** database session
and appends each event to a per-run list. The HTTP response is now a *follower* over that
list. A follower going away is nothing; the turn finishes and commits on a normal,
uncancelled path.

Re-attaching then costs nothing extra: the list is a replay log, so a returning tab reads it
from index 0 and continues live. Two tabs can follow one turn.

**Process-local, and that is load-bearing on ``-w 1``.** Like the retrieval index, the answer
cache, the settings snapshot and the rate-limit counters, this registry lives in one process.
With more workers a turn started on worker A is invisible to worker B, so the re-attach route
would 404 and the panel would fall back to reading the finished row — degraded, not broken.
See the table in CLAUDE.md; this row belongs in it.

Nothing here is a cache. The persisted message row is the artifact; a run is retained for a
short grace window only so a reload that straddles the commit still collects the tail.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Coroutine
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.core.database import AsyncSessionLocal
from app.core.exceptions import (
    RateLimitException,
    ServiceUnavailableException,
    ValidationException,
)
from app.core.logging import get_logger
from app.domain.repositories.session_repo import SessionRepository
from app.domain.services.agent_settings import effective
from app.domain.services.chat_service import ChatService
from app.shared.constants import (
    CHAT_MAX_FOLLOWERS_PER_RUN,
    CHAT_MAX_LIVE_RUNS,
    CHAT_MAX_LIVE_RUNS_PER_ACCOUNT,
    CHAT_RUN_DRAIN_SECONDS,
    CHAT_RUN_RETAIN_SECONDS,
)
from app.shared.sse import SSEEvent

logger = get_logger("chat_runs")

_MSG_ALREADY_RUNNING = "پاسخ پرسش قبلی این گفت‌وگو هنوز در حال آماده شدن است؛ چند لحظه صبر کنید."
_MSG_BUSY = "دستیار همین حالا چند پاسخ دیگر در دست دارد؛ چند لحظهٔ دیگر دوباره بفرستید."
_MSG_TOO_MANY_FOLLOWERS = "این گفت‌وگو از چند جا باز است؛ یکی از آنها را ببندید."
_MSG_SHUTTING_DOWN = "سرویس در حال راه‌اندازی دوباره است؛ چند لحظهٔ دیگر دوباره بفرستید."


@dataclass(slots=True)
class _Run:
    """One turn in flight, and everything a follower needs to replay it."""

    #: Server-minted and never on the wire. A run is addressed by its conversation uuid,
    #: which the caller already had to prove they own.
    run_id: str
    #: Ownership token. The session UUID, never the reusable integer rowid.
    session_uuid: str
    #: Only to re-read the session row on the run's own database session, once.
    user_id: int
    #: Filled from the `meta` event; "" until then, so a run is unaddressable until the
    #: conversation it belongs to exists.
    conversation_uuid: str = ""
    #: The user message this turn answers, from the same `meta` event.
    anchor_uuid: str = ""
    #: Whether this turn created the conversation — the gate for naming it.
    new_conversation: bool = False
    #: The replay log. A list rather than a queue on purpose: a queue lets one consumer
    #: steal events from another, and re-attach needs the history anyway.
    events: list[SSEEvent] = field(default_factory=list)
    #: Replaced (not just set) on every append, so a follower that captured the previous
    #: one before reading the length can never miss a wakeup.
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    finished: bool = False
    #: Only ever set while `events` is empty — the pre-first-event window `_peeked` exists
    #: for. After that `stream_answer` reports failures as `error` events itself.
    error: BaseException | None = None
    followers: int = 0
    #: "" | "user_stop" | "shutdown" | "deadline" — for the log line, never for the user.
    stop_reason: str = ""
    #: Asks the turn to wrap up. Cooperative, and that is not a stylistic choice: cancelling
    #: the task interrupts the turn's own database write mid-flush, which is precisely how
    #: an abandoned answer used to be lost outright. The turn ends on a healthy session and
    #: keeps what the user already saw.
    stopping: asyncio.Event = field(default_factory=asyncio.Event)
    started: float = field(default_factory=time.perf_counter)
    attached_ever: bool = False
    task: asyncio.Task | None = None


#: Live and recently-finished runs, keyed by the server-minted run id.
_runs: dict[str, _Run] = {}

#: THE strong reference to every background task. asyncio keeps only a weak one, so a task
#: that nothing holds can be garbage-collected mid-await — the "Task was destroyed but it is
#: pending" class of bug, and here it would silently lose an answer.
_tasks: set[asyncio.Task] = set()

#: Set by `shutdown()`. New turns are refused from that moment: accepting one would start
#: work the drain is already trying to finish.
_draining = False


def _spawn(coro: Coroutine[Any, Any, Any], name: str) -> asyncio.Task:
    """Create a background task that is referenced, named, and never silently swallowed.

    The only place in this app that creates one. A bare ``create_task`` leaves the result
    unconsumed, so an exception surfaces — if at all — as a warning from the garbage
    collector at some unrelated moment.

    Args:
        coro: The coroutine to run.
        name: Task name, used in the crash log line.

    Returns:
        The scheduled task.
    """
    task = asyncio.create_task(coro, name=name)
    _tasks.add(task)
    task.add_done_callback(_finalize)
    return task


def _finalize(task: asyncio.Task) -> None:
    """Drop the strong reference and consume the task's result exactly once."""
    _tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        # Type name only: an exception's text can carry a provider URL, which carries a key.
        logger.error("background_task_crashed", task=task.get_name(), error=type(exc).__name__)


def _unfinished() -> list[_Run]:
    """Every run still generating."""
    return [run for run in _runs.values() if not run.finished]


def count() -> int:
    """How many turns are generating right now, for the health probe."""
    return len(_unfinished())


def live(conversation_uuid: str, session_uuid: str) -> _Run | None:
    """Find this caller's run for this conversation, finished or not.

    Both halves matter: the conversation uuid says which run, and the session uuid says the
    caller may have it. The caller has already been checked against the database by the
    time this runs; this is the second, independent check.

    Args:
        conversation_uuid: Public conversation identifier.
        session_uuid: The calling session's own uuid.

    Returns:
        The run, or None. Retained-but-finished runs still match, so a reload that lands
        just after the commit still collects the tail.
    """
    # ponytail: linear scan over at most CHAT_MAX_LIVE_RUNS entries; index it by
    # conversation uuid if that cap ever rises past a few dozen.
    for run in _runs.values():
        if run.conversation_uuid == conversation_uuid and run.session_uuid == session_uuid:
            return run
    return None


def live_unfinished(conversation_uuid: str, session_uuid: str) -> _Run | None:
    """The same lookup, restricted to runs still generating.

    This is what the conversation detail reports as ``activeRun``. Reporting a *finished*
    run there would make an answer that is already on screen re-type itself on open.
    """
    run = live(conversation_uuid, session_uuid)
    return run if run is not None and not run.finished else None


def _deadline() -> float:
    """The hard ceiling on one detached turn, in seconds.

    The turn budget is checked between model calls, never inside one, so a provider that
    accepts a connection and then says nothing is bounded by the attempt timeout rather
    than by the budget. Their sum is the first moment both are certainly spent.
    """
    current = effective()
    return current.turn_budget_seconds + current.attempt_timeout_seconds


def start(
    *,
    user_id: int | None,
    session_uuid: str,
    content: str,
    conversation_uuid: str | None,
    parent_uuid: str | None,
    parent_given: bool,
    supersedes: str | None,
) -> AsyncIterator[SSEEvent]:
    """Begin a turn on its own task and return a follower over it.

    Synchronous on purpose: everything it refuses is refused **before** a task exists, so a
    refusal is a normal exception out of the endpoint expression and becomes a JSON error
    envelope through the registered handlers, exactly as a pre-first-event raise did before.

    Args:
        user_id: Internal id of the signed-in user, from the verified cookie.
        session_uuid: The calling session's public uuid, used as the ownership token.
        content: The user's message.
        conversation_uuid: Existing conversation, or None to start one.
        parent_uuid: Message the turn hangs under.
        parent_given: Whether the client sent the field at all.
        supersedes: A question this edit replaces outright.

    Returns:
        An async iterator of this run's events, from the first one.

    Raises:
        ServiceUnavailableException: The process is shutting down.
        ValidationException: The session has no user, or this conversation is already
            answering. Two turns racing for one branch would both repoint
            ``active_message_id`` and mint a version the user never asked for.
        RateLimitException: The per-account or process-wide ceiling is reached. Refused,
            never queued — and never by evicting a live run, which would drop the only
            strong reference to its task and reintroduce the very bug this module removes.
    """
    if _draining:
        raise ServiceUnavailableException(_MSG_SHUTTING_DOWN)
    if user_id is None:
        # `Session.user_id` is nullable only for a pre-auth migration path. Fail closed.
        raise ServiceUnavailableException(_MSG_SHUTTING_DOWN)
    if conversation_uuid and live_unfinished(conversation_uuid, session_uuid) is not None:
        raise ValidationException(_MSG_ALREADY_RUNNING)

    unfinished = _unfinished()
    if len(unfinished) >= CHAT_MAX_LIVE_RUNS:
        logger.warning("chat_run_refused", reason="process_cap", live=len(unfinished))
        raise RateLimitException(_MSG_BUSY)
    mine = sum(1 for run in unfinished if run.session_uuid == session_uuid)
    if mine >= CHAT_MAX_LIVE_RUNS_PER_ACCOUNT:
        logger.warning("chat_run_refused", reason="account_cap", live=mine)
        raise RateLimitException(_MSG_BUSY)

    run = _Run(
        run_id=str(uuid4()),
        session_uuid=session_uuid,
        user_id=user_id,
        new_conversation=conversation_uuid is None,
    )
    _runs[run.run_id] = run
    run.task = _spawn(
        _supervise(
            run,
            content=content,
            conversation_uuid=conversation_uuid,
            parent_uuid=parent_uuid,
            parent_given=parent_given,
            supersedes=supersedes,
        ),
        "chat_run",
    )
    return follow(run)


async def _supervise(run: _Run, **turn: Any) -> None:
    """Run one turn to completion under a hard deadline, then release the followers."""
    try:
        await asyncio.wait_for(_drive(run, **turn), _deadline())
    except asyncio.TimeoutError:
        # `wait_for` cancels the inner coroutine and awaits its unwind, so `stream_answer`'s
        # own handler has already persisted whatever passed the commit point.
        run.stop_reason = run.stop_reason or "deadline"
        logger.warning("chat_run_deadline", conversation=run.conversation_uuid)
    except asyncio.CancelledError:
        run.stop_reason = run.stop_reason or "cancelled"
    except Exception as exc:  # noqa: BLE001 - a background turn must never crash the loop
        logger.exception("chat_run_crashed", conversation=run.conversation_uuid)
        if not run.events:
            run.error = exc
    finally:
        run.finished = True
        run.wake.set()
        logger.info(
            "chat_run_finished",
            conversation=run.conversation_uuid,
            reason=run.stop_reason,
            attached=run.attached_ever,
            events=len(run.events),
            ms=int((time.perf_counter() - run.started) * 1000),
        )
        # A timer handle rather than a sleeping task: nothing extra for the drain to reason
        # about, and it cannot itself be the thing that keeps the loop alive.
        asyncio.get_running_loop().call_later(
            CHAT_RUN_RETAIN_SECONDS, _runs.pop, run.run_id, None
        )


async def _drive(
    run: _Run,
    *,
    content: str,
    conversation_uuid: str | None,
    parent_uuid: str | None,
    parent_given: bool,
    supersedes: str | None,
) -> None:
    """Own a database session for the whole turn and append every event to the log.

    The session ownership is the entire point. The request's session is committed and
    returned to the pool while this runs; using it here is what made a disconnect lose the
    answer. Only primitives cross into this function — an ORM row loaded on another session
    would be a lazy read waiting to happen inside a task nobody is watching.
    """
    async with AsyncSessionLocal() as db:
        # Re-read rather than pass the row in: today only loaded scalars are touched, but
        # one future lazy attribute here is a MissingGreenlet in a background task, and one
        # extra SELECT buys the whole class of bug.
        session_row = await SessionRepository(db).get_or_create_for_user(run.user_id)
        await db.commit()
        service = ChatService(db)
        gen = service.stream_answer(
            session_row,
            content,
            conversation_uuid,
            parent_uuid,
            parent_given=parent_given,
            supersedes=supersedes,
            stop=run.stopping,
        )
        try:
            async with aclosing(gen):
                async for event in gen:
                    if event.event == "meta" and not run.conversation_uuid:
                        run.conversation_uuid = str(event.data.get("conversationUuid") or "")
                        run.anchor_uuid = str(event.data.get("userMessageUuid") or "")
                        _on_conversation_named(run, content)
                    run.events.append(event)
                    run.wake.set()
                    run.wake = asyncio.Event()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reported to the follower, not the loop
            if not run.events:
                # The pre-first-event window: a 404 for an unknown conversation must still
                # reach the caller as a JSON error, which is what `_peeked` is for.
                run.error = exc
            else:
                logger.exception("chat_run_failed", conversation=run.conversation_uuid)


def _on_conversation_named(run: _Run, question: str) -> None:
    """Hook for work that needs the conversation uuid but must not delay the answer."""
    if not (run.new_conversation and run.conversation_uuid):
        return
    # Imported here, not at module scope: chat_service must not import this module back.
    from app.domain.services.chat_service import write_title

    _spawn(write_title(run.conversation_uuid, run.user_id, question), "chat_title")


async def follow(run: _Run) -> AsyncIterator[SSEEvent]:
    """Replay this run from its first event, then follow it live.

    Never applies backpressure: a slow reader, or one that walks away mid-answer, must not
    be able to stall the model loop. That is the whole feature.

    Args:
        run: The run to follow.

    Yields:
        Every event so far, in order, then each new one as it arrives.

    Raises:
        RateLimitException: This run already has as many followers as it will serve.
        BaseException: Whatever the turn failed with, but **only** when it failed before
            producing a single event. Once a body has started, truncating it with a raise
            is exactly what the endpoint's `_peeked` exists to prevent.
    """
    if run.followers >= CHAT_MAX_FOLLOWERS_PER_RUN:
        raise RateLimitException(_MSG_TOO_MANY_FOLLOWERS)
    run.followers += 1
    run.attached_ever = True
    try:
        index = 0
        while True:
            # Captured BEFORE the length is read: the driver replaces this object on every
            # append, so an event landing between the read and the wait cannot be missed.
            wake = run.wake
            while index < len(run.events):
                yield run.events[index]
                index += 1
            if run.finished:
                if not run.events and run.error is not None:
                    raise run.error
                return
            await wake.wait()
    finally:
        run.followers -= 1


def cancel(run: _Run, reason: str) -> None:
    """Ask a turn to wrap up. Whatever passed the commit point is still persisted.

    Sets a flag rather than cancelling the task. The turn notices between streamed chunks
    and ends exactly the way it ends when a provider dies mid-answer — ``cut_short``, then
    the normal persist path on a session that is still healthy. A ``task.cancel()`` here
    would interrupt that very write, which is the failure this whole module removes.

    Args:
        run: The run to stop.
        reason: Recorded on the run for the finish log line, never shown to a user.
    """
    run.stop_reason = reason
    run.stopping.set()


async def shutdown() -> None:
    """Refuse new turns, stop the running ones, and wait for their writes to land.

    Bounded: a drain that outlives the worker's graceful timeout is killed mid-write, which
    is worse than not draining at all. `CHAT_RUN_DRAIN_SECONDS` is sized under it.
    """
    global _draining
    _draining = True
    running = _unfinished()
    for run in running:
        cancel(run, "shutdown")
    # Cooperative, so give the turns a moment to notice and commit before the wait below
    # decides they are stuck.
    if not _tasks:
        return
    logger.info("chat_runs_draining", runs=len(running), tasks=len(_tasks))
    try:
        await asyncio.wait_for(
            asyncio.gather(*list(_tasks), return_exceptions=True), CHAT_RUN_DRAIN_SECONDS
        )
    except asyncio.TimeoutError:
        logger.warning("chat_runs_drain_timeout", pending=len(_tasks))


def reset_for_tests() -> None:
    """Clear the registry between self-check cases. Never called by the app."""
    global _draining
    _draining = False
    _runs.clear()
    _tasks.clear()
