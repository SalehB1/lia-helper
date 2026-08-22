"""Self-check for the turn that outlives its client.

The regression, measured before the fix: a browser refreshed 270 characters into an answer,
Starlette closed the request's anyio cancel scope, the emergency write died at its first
await with `PendingRollbackError`, and the conversation was left holding the user's question
and **nothing else**. Not a truncated answer — no answer at all.

What is asserted here is that the turn no longer belongs to the connection:

* dropping the follower mid-answer still commits the whole answer;
* a second follower replays everything it missed and then follows live;
* an explicit stop keeps what passed the commit point and withdraws what did not;
* a turn abandoned mid-stream with NO cleanup at all still has its text on disk;
* two turns cannot race in one conversation, and the caps refuse rather than queue;
* a pre-first-event failure still reaches the caller, and a later one never truncates a body.

No network: `llm_service` is replaced by the scripted fake, and `AsyncSessionLocal` by a
throwaway file database.

Usage (from ``backend/``)::

    .venv-uv/bin/python -m tests.test_detached_turn
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.exceptions import RateLimitException, ValidationException  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.domain.models.base import Base  # noqa: E402
from app.domain.repositories.conversation_repo import ConversationRepository  # noqa: E402
from app.domain.repositories.message_repo import MessageRepository  # noqa: E402
from app.domain.repositories.session_repo import SessionRepository  # noqa: E402
from app.domain.repositories.user_repo import UserRepository  # noqa: E402
from app.domain.services import chat_runs  # noqa: E402
from app.domain.services import chat_service as chat_module  # noqa: E402
from app.domain.services.agent_settings import AgentSettings, _defaults  # noqa: E402
from app.domain.services.llm_service import LLMChunk  # noqa: E402
from app.shared.enums import MessageRole  # noqa: E402

STEP_TIMEOUT = 30.0

#: Long enough that a follower can drop out mid-answer and there is still text left to
#: produce afterwards — which is the entire point of the first check.
ANSWER = "پاسخ کامل به پرسش کاربر است. " * 12

SETTINGS = dataclasses.replace(
    _defaults(),
    model_primary="model-a",
    ladder=("model-a",),
    max_model_attempts=1,
    escalation=False,
    agent_mode=False,
    speculative_retrieval=False,
    auto_title=False,          # the title path has its own self-check
    handoff_enabled=False,
)


class _FakeLLM:
    """Replays one scripted answer, slowly enough that a follower can leave mid-stream."""

    available = True

    def __init__(self, text: str = ANSWER, delay: float = 0.01) -> None:
        self.text = text
        self.delay = delay
        self.rounds = 0

    async def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        model: str | None = None,
        timeout: float = 0.0,
        reasoning: str | None = None,
    ) -> AsyncIterator[LLMChunk]:
        """Yield the answer in small deltas, pausing between them."""
        self.rounds += 1
        size = 24
        for start in range(0, len(self.text), size):
            await asyncio.sleep(self.delay)
            yield LLMChunk(kind="token", text=self.text[start : start + size], model="model-a")


@asynccontextmanager
async def _world(llm: _FakeLLM | None = None) -> AsyncIterator[tuple[Any, str, int]]:
    """Yield ``(factory, session_uuid, user_id)`` wired to a throwaway database."""
    with tempfile.TemporaryDirectory() as folder:
        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(folder) / 'test.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

        async with factory() as db:
            user = await UserRepository(db).create(
                username="owner", password_hash=hash_password("owner-password-long"), is_superuser=False
            )
            session_row = await SessionRepository(db).get_or_create_for_user(user.id)
            await db.commit()
            session_uuid, user_id = session_row.uuid, user.id

        async def _settings(_db: AsyncSession) -> AgentSettings:
            return SETTINGS

        real = (
            chat_module.llm_service,
            chat_module.agent_settings,
            chat_runs.AsyncSessionLocal,
            chat_module.AsyncSessionLocal,
            chat_runs.effective,
            chat_module.effective,
        )
        chat_module.llm_service = llm or _FakeLLM()
        chat_module.agent_settings = _settings
        chat_runs.AsyncSessionLocal = factory
        chat_module.AsyncSessionLocal = factory
        chat_runs.effective = lambda: SETTINGS
        chat_module.effective = lambda: SETTINGS
        chat_module._answer_cache.clear()
        chat_runs.reset_for_tests()
        try:
            yield factory, session_uuid, user_id
        finally:
            (
                chat_module.llm_service,
                chat_module.agent_settings,
                chat_runs.AsyncSessionLocal,
                chat_module.AsyncSessionLocal,
                chat_runs.effective,
                chat_module.effective,
            ) = real
            chat_runs.reset_for_tests()
            await engine.dispose()


def _begin(session_uuid: str, user_id: int, content: str, conversation: str | None = None):
    """Start a turn with the arguments the endpoint would pass."""
    return chat_runs.start(
        user_id=user_id,
        session_uuid=session_uuid,
        content=content,
        conversation_uuid=conversation,
        parent_uuid=None,
        parent_given=False,
        supersedes=None,
    )


async def _stored(factory, conversation_uuid: str, session_id: int) -> list[tuple[str, str]]:
    """Read back the persisted branch as ``(role, content)`` pairs."""
    async with factory() as db:
        conversation = await ConversationRepository(db).get_scoped(conversation_uuid, session_id)
        assert conversation is not None
        nodes = await MessageRepository(db).active_branch(
            conversation.id, conversation.active_message_id
        )
        return [
            (
                node.message.role.value
                if isinstance(node.message.role, MessageRole)
                else str(node.message.role),
                node.message.content,
            )
            for node in nodes
        ]


async def _session_id(factory, user_id: int) -> int:
    async with factory() as db:
        return (await SessionRepository(db).get_or_create_for_user(user_id)).id


# --------------------------------------------------------------------------- checks


async def test_walking_away_mid_answer_still_stores_the_whole_answer() -> None:
    """THE regression. Before this, the same sequence stored no assistant row at all."""
    async with _world() as (factory, session_uuid, user_id):
        events = _begin(session_uuid, user_id, "پرسش کاربر")
        conversation_uuid = ""
        seen = 0
        # Read a few events, then walk away exactly as a refreshing browser does.
        async for event in events:
            if event.event == "meta":
                conversation_uuid = event.data["conversationUuid"]
            if event.event == "token":
                seen += 1
                if seen >= 2:
                    break
        await events.aclose()

        run = chat_runs.live(conversation_uuid, session_uuid)
        assert run is not None and run.task is not None
        await asyncio.wait_for(asyncio.shield(run.task), STEP_TIMEOUT)

        branch = await _stored(factory, conversation_uuid, await _session_id(factory, user_id))
        assert [role for role, _ in branch] == ["user", "assistant"], branch
        assert branch[1][1] == ANSWER.strip(), f"stored {len(branch[1][1])} of {len(ANSWER.strip())}"


async def test_coming_back_replays_everything_missed_and_then_follows_live() -> None:
    """Re-attach: a second follower sees the turn from its first event, not from now."""
    async with _world() as (factory, session_uuid, user_id):
        first = _begin(session_uuid, user_id, "پرسش کاربر")
        conversation_uuid = ""
        async for event in first:
            if event.event == "meta":
                conversation_uuid = event.data["conversationUuid"]
            if event.event == "token":
                break
        await first.aclose()

        run = chat_runs.live(conversation_uuid, session_uuid)
        assert run is not None
        replayed = [event.event async for event in chat_runs.follow(run)]
        assert replayed[0] == "meta", replayed[:3]
        assert replayed[-1] == "done", replayed[-3:]
        assert run.followers == 0, "the follower did not release its slot"


async def test_stopping_after_the_commit_point_keeps_what_was_written() -> None:
    """An explicit stop is not a disconnect: it keeps the text the user already saw."""
    # Long, for the reason spelled out in the crash check: the buffer holds TAIL_HOLD_CHARS
    # back, so a short answer arrives as one token at flush time and there is no mid-stream
    # left to stop — the check would pass on a turn that simply ran to completion.
    long_answer = "پاسخ کامل به پرسش کاربر است. " * 90
    async with _world(_FakeLLM(long_answer, delay=0.01)) as (factory, session_uuid, user_id):
        events = _begin(session_uuid, user_id, "پرسش کاربر")
        conversation_uuid = ""
        seen = 0
        async for event in events:
            if event.event == "meta":
                conversation_uuid = event.data["conversationUuid"]
            if event.event == "token":
                seen += 1
                if seen >= 2:
                    break
        await events.aclose()

        run = chat_runs.live(conversation_uuid, session_uuid)
        assert run is not None and run.task is not None
        chat_runs.cancel(run, "user_stop")
        await asyncio.wait_for(asyncio.gather(run.task, return_exceptions=True), STEP_TIMEOUT)

        branch = await _stored(factory, conversation_uuid, await _session_id(factory, user_id))
        assert [role for role, _ in branch] == ["user", "assistant"], branch
        kept = branch[1][1]
        # Truncated text is always marked as truncated, whichever path stored it.
        assert kept.endswith(chat_module._MSG_CUT_SHORT), kept[-80:]
        body = kept[: -len(chat_module._MSG_CUT_SHORT)].strip()
        assert body, "a stop after the commit point must keep the text already released"
        assert long_answer.strip().startswith(body), "stored text is not a prefix"
        assert len(body) < len(long_answer.strip()), "the stop did not actually interrupt"


async def test_a_stop_before_any_text_withdraws_the_question() -> None:
    """Nothing was released, so nothing is kept — and the unanswered question goes too.

    This is what stops a dead turn from minting a «۲ / ۲» version under a question.
    """
    async with _world(_FakeLLM(delay=5.0)) as (factory, session_uuid, user_id):
        events = _begin(session_uuid, user_id, "پرسش کاربر")
        conversation_uuid = ""
        async for event in events:
            if event.event == "meta":
                conversation_uuid = event.data["conversationUuid"]
                break
        await events.aclose()

        run = chat_runs.live(conversation_uuid, session_uuid)
        assert run is not None and run.task is not None
        chat_runs.cancel(run, "user_stop")
        await asyncio.wait_for(asyncio.gather(run.task, return_exceptions=True), STEP_TIMEOUT)

        branch = await _stored(factory, conversation_uuid, await _session_id(factory, user_id))
        assert branch == [], f"expected the question withdrawn, got {branch}"


async def test_a_process_that_dies_mid_answer_leaves_the_text_on_disk() -> None:
    """The crash case: no stop, no drain, no `_persist_partial` — the turn is abandoned.

    `aclose()` throws `GeneratorExit` at the suspended yield. That is a `BaseException`, so
    none of `stream_answer`'s three handlers catch it and **nothing cleans up** — which is
    precisely the state a SIGKILL leaves behind. Whether the committed pages then survive the
    kill is `PRAGMA synchronous`'s job, not this check's; what is asserted here is that the
    text was on disk BEFORE the turn ended, in exactly one row, on the branch, and marked
    honestly rather than passed off as a whole answer.
    """
    # Long enough that `_AnswerBuffer` releases progressively: it always holds back
    # TAIL_HOLD_CHARS (200) looking for the suggestion marker, so a short answer arrives as
    # ONE token at flush time and there is no mid-stream to be abandoned in.
    long_answer = "پاسخ کامل به پرسش کاربر است. " * 90
    async with _world(_FakeLLM(long_answer, delay=0.002)) as (factory, session_uuid, user_id):
        async with factory() as db:
            session_row = await SessionRepository(db).get_or_create_for_user(user_id)
            service = chat_module.ChatService(db)
            events = service.stream_answer(session_row, "پرسش کاربر", None, None, False)
            conversation_uuid, tokens = "", 0
            async for event in events:
                if event.event == "meta":
                    conversation_uuid = event.data["conversationUuid"]
                if event.event == "token":
                    tokens += 1
                    # Past the third release the `_open` checkpoint has certainly run: it is
                    # awaited when the consumer comes back for the next event, which is
                    # exactly what the real driver in `chat_runs` does without pausing.
                    if tokens >= 3:
                        break
            await events.aclose()  # the process dies here

        session_id = await _session_id(factory, user_id)
        branch = await _stored(factory, conversation_uuid, session_id)
        assert [role for role, _ in branch] == ["user", "assistant"], branch
        kept = branch[1][1]
        assert kept.endswith(chat_module._MSG_CUT_SHORT), kept[-80:]
        body = kept[: -len(chat_module._MSG_CUT_SHORT)].strip()
        assert body and long_answer.strip().startswith(body), body[-80:]
        assert len(body) < len(long_answer.strip()), "this should be a PARTIAL answer"

        async with factory() as db:
            conversation = await ConversationRepository(db).get_scoped(
                conversation_uuid, session_id
            )
            messages = MessageRepository(db)
            nodes = await messages.active_branch(
                conversation.id, conversation.active_message_id
            )
            answers = [n.message for n in nodes if n.message.role is MessageRole.ASSISTANT]
            # Exactly one row, and no phantom sibling: rows under one parent are versions.
            assert len(answers) == 1, answers
            assert (nodes[-1].version_index, nodes[-1].version_count) == (1, 1)
            assert conversation.active_message_id == answers[0].id


async def test_one_conversation_cannot_answer_two_questions_at_once() -> None:
    """Two live turns on one branch would both repoint it and mint a version nobody asked for."""
    async with _world(_FakeLLM(delay=0.05)) as (factory, session_uuid, user_id):
        events = _begin(session_uuid, user_id, "پرسش کاربر")
        conversation_uuid = ""
        async for event in events:
            if event.event == "meta":
                conversation_uuid = event.data["conversationUuid"]
                break

        raised = False
        try:
            _begin(session_uuid, user_id, "پرسش دوم", conversation_uuid)
        except ValidationException:
            raised = True
        assert raised, "a second turn on a live conversation must be refused"

        run = chat_runs.live(conversation_uuid, session_uuid)
        assert run is not None
        chat_runs.cancel(run, "user_stop")
        await events.aclose()
        await asyncio.wait_for(asyncio.gather(run.task, return_exceptions=True), STEP_TIMEOUT)


async def test_the_account_cap_refuses_rather_than_queueing() -> None:
    """Refusing is the safe direction; evicting a live run would drop its only reference."""
    async with _world(_FakeLLM(delay=5.0)) as (factory, session_uuid, user_id):
        started = []
        for index in range(chat_runs.CHAT_MAX_LIVE_RUNS_PER_ACCOUNT):
            events = _begin(session_uuid, user_id, f"پرسش {index}")
            async for event in events:
                if event.event == "meta":
                    break
            started.append(events)

        raised = False
        try:
            _begin(session_uuid, user_id, "یکی بیشتر")
        except RateLimitException:
            raised = True
        assert raised, "over the per-account cap the turn must be refused"
        assert chat_runs.count() == chat_runs.CHAT_MAX_LIVE_RUNS_PER_ACCOUNT

        for run in list(chat_runs._runs.values()):
            chat_runs.cancel(run, "user_stop")
        for events in started:
            await events.aclose()
        await asyncio.wait_for(
            asyncio.gather(*[r.task for r in chat_runs._runs.values() if r.task], return_exceptions=True),
            STEP_TIMEOUT,
        )


async def test_a_failure_before_the_first_event_still_reaches_the_caller() -> None:
    """An unknown conversation must be a JSON 404, not a stream that opens and dies."""
    async with _world() as (factory, session_uuid, user_id):
        events = _begin(session_uuid, user_id, "پرسش", "not-a-real-conversation")
        raised = ""
        try:
            async for _ in events:
                pass
        except Exception as exc:  # noqa: BLE001 - the type is the assertion
            raised = type(exc).__name__
        assert raised == "NotFoundException", raised


async def test_the_health_count_only_counts_live_turns() -> None:
    async with _world(_FakeLLM(delay=0.05)) as (factory, session_uuid, user_id):
        assert chat_runs.count() == 0
        events = _begin(session_uuid, user_id, "پرسش کاربر")
        async for event in events:
            if event.event == "meta":
                break
        assert chat_runs.count() == 1
        async for _ in events:
            pass
        assert chat_runs.count() == 0, "a finished run must not be reported as live"


async def _run(check) -> None:
    await asyncio.wait_for(check(), STEP_TIMEOUT * 2)


def main() -> None:
    """Run every check in this module."""
    checks = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for check in checks:
        asyncio.run(_run(check))
        print("ok ", check.__name__)
    print(f"\n{len(checks)} checks passed")


if __name__ == "__main__":
    main()
