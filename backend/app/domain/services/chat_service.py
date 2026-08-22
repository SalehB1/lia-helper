"""One chat turn, end to end: agent loop, SSE streaming, persistence.

The generator returned by :meth:`ChatService.stream_answer` outlives the request handler,
so every database write happens inside it and is committed explicitly at the points where
the data must survive a disconnect: after the user message, and after the answer.

Retrieval is the model's own decision: it calls ``search_docs`` (rephrasing and retrying
the query itself) for anything about Liara, and answers small talk directly without a tool
call. Abstention follows from that — it is a prompt rule enforced textually by rule ۵ of
the system prompt, not a pre-LLM gate that can only ever emit one canned sentence.

The citation backstop is unchanged and still holds even when the model ignores the prompt:
the ``sources`` event is built from the registry (which falls back to every provided
source), never from whatever ``[n]`` markers the model happened to write.

The suggestions line (``@@@ a | b | c``) is stripped by holding back the tail of the
stream, so no fragment of the marker can ever reach the client as a visible token.

**A turn is a ladder of models, not one model behind a wall.** When a model errors out,
stalls, writes nothing, or tries to tell the user it has hit a limit, the turn silently
starts over on the next model with every document the previous attempt fetched already in
context. The whole thing rests on one invariant:

    The first answer byte released to the client commits the turn to that model.

Escalation is legal only while nothing has been released, so text is never retracted. The
pre-release window is made wide enough to cover every failure mode: tool-round preambles
are dropped from the wire entirely, and the first :data:`HEAD_HOLD_CHARS` of the answer are
held back and screened. A ladder that runs out ends in a composed answer naming what was
searched and what was nearest — never an ``error``, never a word about limits.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    AppException,
    NotFoundException,
    ServiceUnavailableException,
    ValidationException,
)
from app.core.logging import get_logger
from app.domain.models.conversation import Conversation
from app.domain.models.message import Message
from app.domain.models.session import Session
from app.domain.repositories.conversation_repo import (
    DEFAULT_TITLE,
    ConversationRepository,
    _clean_title,
)
from app.domain.repositories.message_repo import MessageRepository
from app.domain.repositories.session_repo import SessionRepository
from app.domain.services import handoff
from app.domain.services.agent_settings import AgentSettings, agent_settings, effective
from app.domain.services.citations import CitationRegistry
from app.domain.services.llm_service import llm_service
from app.domain.services.prompt_settings import digest as prompt_digest
from app.domain.services.prompt_settings import prompts_for
from app.domain.services.prompts import (
    FINAL_ROUND_INSTRUCTION,
    HANDOFF_INSTRUCTION,
    TITLE_PROMPT,
    build_system_prompt,
)
from app.domain.services.retrieval_service import Hit, retrieval_service
from app.domain.services.tools_service import TOOL_LABELS, TOOL_SCHEMAS, ToolResult, execute_tool
from app.core.database import AsyncSessionLocal
from app.shared.constants import (
    ANSWER_CACHE_SIZE,
    ANSWER_CACHE_TTL_SECONDS,
    HEAD_HOLD_CHARS,
    MAX_CONVERSATIONS_PER_USER,
    MAX_MESSAGE_CHARS,
    CHAT_CHECKPOINT_CHARS,
    CHAT_CHECKPOINT_SECONDS,
    SUGGESTION_MARKER,
    TITLE_QUESTION_CHARS,
    TITLE_TIMEOUT_SECONDS,
)
from app.shared.enums import ChatModel, MessageRole, ToolName
from app.shared.model_catalog import MODEL_CATALOG
from app.shared.persian import ZWNJ, normalize
from app.shared.sse import SSEEvent

logger = get_logger("chat")

#: Characters of the first user message used as the conversation title.
TITLE_CHARS = 60
#: Upper bound on the stream tail held back so a split ``@@@`` is never emitted.
TAIL_HOLD_CHARS = 200
#: Upper bound on parsed follow-up suggestions.
MAX_SUGGESTIONS = 5
#: Upper bound on one suggestion's length.
SUGGESTION_CHARS = 80

#: Appended when a turn is cut short AFTER text has already reached the client. Nothing
#: can be retracted at that point, so the answer ends honestly about being incomplete —
#: without naming a limit, a provider or a model.
_MSG_CUT_SHORT = "\n\nتا همین‌جا توانستم ادامه بدهم. بنویس «ادامه بده» تا بقیه‌اش را بیاورم."

_MSG_NO_CONVERSATION = "این گفت‌وگو پیدا نشد."
_MSG_NO_MESSAGE = "این پیام پیدا نشد."
_MSG_EMPTY_QUESTION = "متن پرسش خالی است."
_MSG_TOO_MANY = "تعداد گفت‌وگوهای این نشست به سقف رسیده است؛ چند گفت‌وگوی قدیمی را حذف کنید."
_MSG_FAILED = "پردازش پاسخ با خطا مواجه شد؛ لطفاً دوباره تلاش کنید."
_LABEL_UNKNOWN = "ابزار ناشناخته"

#: Sentences in which the assistant blames its own tooling, budget or quota instead of
#: answering. Every alternative carries a first-person or self-referential cue on purpose:
#: the corpus is full of legitimate sentences about Liara's own limits («محدودیت حجم
#: دیسک…»), and eating those would be a worse bug than the one this guards against.
_LIMIT_RE = re.compile(
    r"دسترسی\s*(به[^.\n]{0,40})?\s*ندار(م|یم)"
    r"|نمی\s*(توانم|تونم)[^.\n]{0,40}(جست|جستجو|ابزار|بگردم|سرچ)"
    r"|(ابزار|جست\s*و?\s*جو|جستجو)[^.\n]{0,30}(در دسترس نیست|غیرفعال|از دسترس خارج)"
    r"|(محدودیت|سقف|سهمیه|اعتبار)[^.\n]{0,40}(رسید|خورد|تمام شد|پر شد)"
    r"|(don't|do not|no longer|cannot|can't)\s+have\s+access"
    r"|tools?\s+(are\s+)?(not\s+available|unavailable|disabled)"
    r"|(rate|usage|context)\s+limit|quota|out of credits"
)
#: Reused from the citation layer: a sentence carrying a source marker is a grounded claim
#: about Liara, not the assistant talking about itself.
_CITED_RE = re.compile(r"\[\s*\d{1,3}")


async def write_title(conversation_uuid: str, user_id: int, question: str) -> None:
    """Name a brand-new conversation from its first question. Never raises, never blocks.

    Runs on its own task alongside the answer, so it costs the turn no time at all — a
    six-word completion finishes long before a grounded answer does, and time-to-first-token
    never sees it. The conversation already carries the first sixty characters of the
    question from the moment it was created, so **every** failure path here is simply "keep
    the name it already has": no key, provider down, timeout, junk output, or the setting
    switched off.

    Two short-lived sessions rather than one held across the network call, and a
    compare-and-set between them: if the user renamed the conversation by hand while the
    model was thinking, the stored title no longer matches what was read and the generated
    one is dropped. That is the whole reason there is no ``title_edited`` column — nothing
    ever regenerates a title, so the only race is this one, and existing data closes it.

    Args:
        conversation_uuid: The conversation just created by this turn.
        user_id: The owning user, used to resolve their session for the scoped read.
        question: The first question, which is what the name describes.
    """
    if not question.strip() or not llm_service.available or not effective().auto_title:
        return
    try:
        async with AsyncSessionLocal() as db:
            session_row = await SessionRepository(db).get_or_create_for_user(user_id)
            conversations = ConversationRepository(db)
            # Scoped even here: a background task is not an excuse to look a row up by uuid
            # alone, and the filter belongs in the query whatever calls it.
            row = await conversations.get_scoped(conversation_uuid, session_row.id)
            if row is None:
                return
            before = row.title

        result = await llm_service.complete(
            [
                {"role": "system", "content": TITLE_PROMPT},
                {"role": "user", "content": question[:TITLE_QUESTION_CHARS]},
            ],
            model=effective().model_primary,
            timeout=TITLE_TIMEOUT_SECONDS,
            # Explicitly minimal: the operator's effort is tuned for answering a
            # documentation question, and on a GPT-5 model it would spend more reasoning
            # tokens deliberating over six words than the words themselves cost.
            reasoning="minimal",
        )
        title = _clean_title(result.text)
        if title == DEFAULT_TITLE or title == before:
            return

        async with AsyncSessionLocal() as db:
            session_row = await SessionRepository(db).get_or_create_for_user(user_id)
            conversations = ConversationRepository(db)
            row = await conversations.get_scoped(conversation_uuid, session_row.id)
            # Deleted under us, or renamed by hand while the model was thinking. Either way
            # the user's intent is newer than ours.
            if row is None or row.title != before:
                return
            await conversations.touch_title(row, title)
            await db.commit()
        logger.info(
            "chat_title_generated",
            conversation=conversation_uuid,
            chars=len(title),
            model=result.model,
            # Counted in the log only. These tokens are deliberately NOT written onto a
            # message row, because `/admin/usage` sums per-model cost off answers and a
            # title is not one — it would inflate the per-turn averages an operator reads.
            prompt_tokens=int((result.usage or {}).get("prompt_tokens") or 0),
            completion_tokens=int((result.usage or {}).get("completion_tokens") or 0),
        )
    except Exception as exc:  # noqa: BLE001 - a name is never worth failing anything for
        # Type name only: an httpx error's text embeds the request URL, which carries the key.
        logger.warning("chat_title_failed", conversation=conversation_uuid, error=type(exc).__name__)


class _Cached(NamedTuple):
    """One finished, history-free turn, replayable while its key still holds."""

    expires_at: float
    answer: str
    #: Citation records exactly as the answering turn's `CitationRegistry` produced them —
    #: the `[n]` markers in `answer` refer to these numbers and to no others.
    sources: list[dict]
    suggestions: list[str]


#: Finished turns, least recently used first. Hand-rolled rather than `functools.lru_cache`
#: because an entry carries a TTL, mirroring `llm_service._embed_cache`.
#:
#: ponytail: a process-local dict, correct only because the server is pinned to a single
#: worker (`-w 1` in both `Dockerfile` and `run-server.py`, already load-bearing for the
#: retrieval index, the rate limiter and the settings snapshot). Raising `-w` corrupts
#: nothing here — each worker would just keep its own copy — but the shared version of this
#: is a small SQLite table keyed exactly the same way, not a cache server.
_answer_cache: OrderedDict[str, _Cached] = OrderedDict()


class _Escalate(Exception):
    """Internal signal: this model cannot finish the turn, try the next one.

    Deliberately not an :class:`AppException` — ``stream_answer``'s handler turns those
    into a user-visible ``error`` event, and an escalation is the opposite of that: the
    user must never learn that an attempt happened at all.
    """

    def __init__(self, reason: str) -> None:
        """Record why the attempt was abandoned, for the log line only."""
        super().__init__(reason)
        self.reason = reason


def _claims_limit(text: str) -> bool:
    """Whether an answer blames a limit or a lost tool instead of answering.

    Args:
        text: Candidate answer text, normally only its held-back head.

    Returns:
        True when the text must not be shipped. A false positive costs one escalation and
        is invisible to the user; a false negative ships the exact sentence this whole
        design exists to prevent — so the asymmetry is deliberate.
    """
    head = normalize(text or "").replace(ZWNJ, " ")
    return bool(_LIMIT_RE.search(head)) and not _CITED_RE.search(head)


def _plain(text: str) -> str:
    """Flatten a corpus-derived title for inline use in a composed sentence."""
    return " ".join((text or "").replace("[", "").replace("]", "").split())[:60]


def _model_label(model: str) -> str:
    """Human name of a model id, falling back to the id when it is not catalogued."""
    try:
        return MODEL_CATALOG[ChatModel(model)].label
    except (ValueError, KeyError):
        return model


def _ms(started: float) -> int:
    """Milliseconds elapsed since a ``time.perf_counter()`` mark."""
    return int((time.perf_counter() - started) * 1000)


def _label_for(name: str) -> str:
    """Return the Persian label of a tool, tolerating a name the model invented."""
    try:
        return TOOL_LABELS[ToolName(name)]
    except (ValueError, KeyError):
        return _LABEL_UNKNOWN


def _usage_payload(raw: dict | None) -> dict:
    """Normalize a provider usage object to the camelCase shape the API speaks.

    Args:
        raw: Provider-reported usage; key spellings differ per provider.

    Returns:
        ``{"promptTokens", "completionTokens", "totalTokens"}`` with integer values.
    """
    source = raw or {}

    def pick(*keys: str) -> int:
        for key in keys:
            value = source.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                return int(value)
        return 0

    prompt = pick("prompt_tokens", "promptTokens", "input_tokens", "inputTokens")
    completion = pick("completion_tokens", "completionTokens", "output_tokens", "outputTokens")
    total = pick("total_tokens", "totalTokens") or prompt + completion
    return {"promptTokens": prompt, "completionTokens": completion, "totalTokens": total}


def _add_usage(left: dict, right: dict) -> dict:
    """Sum two normalized usage payloads so a multi-round turn reports its total."""
    keys = ("promptTokens", "completionTokens", "totalTokens")
    return {key: int(left.get(key, 0)) + int(right.get(key, 0)) for key in keys}


def _usage_event(turn: _Turn) -> dict:
    """Build the ``done`` event's usage payload, tagged with the model that answered.

    Args:
        turn: The finished turn.

    Returns:
        The accumulated token counts plus the model that ACTUALLY produced the answer.
        These differ whenever the turn escalated to another rung of the ladder or the
        provider layer failed over, and reporting the requested one would be a quiet lie.
        Normalized rather than splatted, so a turn that billed nothing — a replay from the
        answer cache, a composed fallback — still reports the three counts as zeros instead
        of omitting them and leaving the client to render `undefined`.
    """
    return {
        **_usage_payload(turn.usage),
        "model": turn.answered_by or turn.model,
        "requestedModel": turn.requested_model,
        "attempts": turn.attempt + 1,
    }


def _usage_record(turn: _Turn) -> dict | None:
    """Build the usage blob stored alongside the answer, or None when nothing was billed.

    Separate from :func:`_usage_event` on purpose. That one describes the turn to the client
    and carries fields — the requested model, the attempt count — that mean nothing to a cost
    report months later. This one records what was *spent*: the totals, the model that
    answered, and the per-model split that lets an escalated turn be priced correctly.

    Args:
        turn: The finished turn.

    Returns:
        The blob to store, or None for a turn with no token usage at all — a fallback answer
        is composed locally and bills nothing, and an empty row would drag a fake zero-token
        answer into every average.
    """
    if not turn.usage:
        return None
    # Never mutate `turn.usage` itself: it is splatted into a `logger.info(**turn.usage)`
    # call further down, and an extra key there raises TypeError *after* this row has been
    # committed and *before* the `done` event — killing the stream on a persisted answer.
    return {
        **turn.usage,
        "model": turn.answered_by or turn.model,
        "byModel": turn.usage_by_model,
    }


def _final_note(text: str) -> dict:
    """The system message that turns the closing, toolless call into a real answer."""
    return {"role": "system", "content": text or FINAL_ROUND_INSTRUCTION}


def _dump_args(args: Any) -> str:
    """Serialize tool-call arguments back to the JSON string the wire format expects."""
    return json.dumps(args if isinstance(args, dict) else {}, ensure_ascii=False)


def _hits_payload(hits: list[Hit], registry: CitationRegistry) -> list[dict]:
    """Render a tool's hits as citation-numbered source records for the UI.

    ``assign`` is idempotent, so numbers already handed out while formatting the tool
    output are reused rather than re-allocated.
    """
    return [
        {
            "n": registry.assign(hit.chunk),
            "title": hit.chunk.title,
            "url": hit.chunk.url,
            "heading": hit.chunk.heading or None,
        }
        for hit in hits
    ]


def _cache_key(turn: _Turn, history: list[dict], prompt: str) -> str:
    """Build this turn's answer-cache key, or ``""`` when it must not be cached at all.

    Args:
        turn: The turn in flight; its question, model and origin decide eligibility.
        history: The branch history already loaded for this turn — no extra query.
        prompt: The system prompt built for this turn.

    Returns:
        ``"{corpus}:{model}:{prompt}:{question}"``, or ``""`` when the turn may not be
        cached. These four are the entire input of the answer, so the key is the function's
        own domain and nothing else: the corpus hash makes a re-ingest orphan every entry
        for free, the model is there because answers are model-flavoured, and the prompt
        digest carries everything user-shaped that reaches the model — the profile memo
        above all — so two people whose answers could differ can never share an entry.

        The digest covers the closing instruction as well as the system prompt. Both are
        operator-editable and the process never restarts, so leaving it out would replay up
        to ``ANSWER_CACHE_SIZE`` answers written under a closing instruction that has since
        been edited away.

        Deliberately NOT keyed by the asker. The docs are public and the answer is a pure
        function of the four parts above, so an owner in the key would only key the cache
        on an input the answer does not have, which is exactly why it would never hit. The
        cost is one narrow side channel: a fast reply reveals that somebody previously asked
        this same *documentation* question. That is not conversation, profile or ownership
        data, and it is the price of the cache existing at all.

        Eligibility is just as narrow: only a turn whose entire branch is the question just
        written, and only one that wrote that question itself, so a regenerate — an explicit
        request for a different answer — never gets the old one handed back.
    """
    text = normalize(turn.question)
    if ANSWER_CACHE_SIZE <= 0 or not text or not turn.anchor_created:
        return ""
    if len(history) != 1 or history[0].get("role") != MessageRole.USER.value:
        return ""
    digest = prompt_digest(f"{prompt}\x00{turn.final_note}")
    fingerprint = retrieval_service.corpus_fingerprint
    return f"{fingerprint}:{turn.requested_model}:{digest}:{text}"


def _tool_errored(turn: _Turn) -> bool:
    """Whether any tool this turn ran reported a failure.

    ``search_docs`` finding nothing counts as one: an answer written without the documents
    it asked for is not an answer to replay to the next person who asks.
    """
    return any((entry.get("meta") or {}).get("error") for entry in turn.trace)


class _AnswerBuffer:
    """Accumulates the answer while hiding the trailing ``@@@ …`` suggestions line.

    Text is released only once it can no longer turn out to be part of the marker: the
    last :data:`TAIL_HOLD_CHARS` characters are always held back, and everything from the
    marker onwards is diverted into the suggestions tail instead of being emitted.
    """

    def __init__(self) -> None:
        self._emitted: list[str] = []
        self._pending = ""
        self._tail = ""
        self._found = False

    def feed(self, text: str) -> str:
        """Absorb one model delta.

        Args:
            text: Raw delta from the provider.

        Returns:
            The portion that is safe to send to the client; often empty.
        """
        if not text:
            return ""
        if self._found:
            self._tail += text
            return ""
        self._pending += text
        index = self._pending.find(SUGGESTION_MARKER)
        if index >= 0:
            safe = self._pending[:index]
            self._tail = self._pending[index + len(SUGGESTION_MARKER) :]
            self._pending = ""
            self._found = True
        else:
            # Correctness needs only ``len(marker) - 1`` characters held back, since the
            # search above scans the whole pending buffer. The unfinished last line is
            # held as well (capped) so the suggestions line never flickers into view,
            # while every completed line streams immediately.
            line_start = self._pending.rfind("\n") + 1
            hold = min(
                max(len(SUGGESTION_MARKER) - 1, len(self._pending) - line_start),
                TAIL_HOLD_CHARS,
            )
            keep = max(len(self._pending) - hold, 0)
            safe = self._pending[:keep]
            self._pending = self._pending[keep:]
        if safe:
            self._emitted.append(safe)
        return safe

    def flush(self) -> str:
        """Release the held-back tail once the stream is over.

        A trailing fragment that is a prefix of the marker (``"@"``, ``"@@"``) is dropped:
        the model started the suggestions line and was cut off.

        Returns:
            The last piece of visible answer text, possibly empty.
        """
        text, self._pending = self._pending, ""
        if self._found or not text:
            return ""
        for size in range(len(SUGGESTION_MARKER) - 1, 0, -1):
            if text.endswith(SUGGESTION_MARKER[:size]):
                text = text[:-size]
                break
        if text:
            self._emitted.append(text)
        return text

    def begin_round(self) -> int:
        """Mark where the current round's text starts, so a preamble can be undone."""
        return len(self._emitted)

    def round_text(self, mark: int) -> str:
        """Everything this round produced, held-back tail included."""
        return "".join(self._emitted[mark:]) + self._pending

    def end_round(self, mark: int, drop: bool) -> str:
        """Close a round that ended in tool calls, and forget its marker state.

        Text written before a tool call is a preamble ("let me look that up"), not the
        answer. It still goes into the assistant message that records the tool call, but it
        is normally dropped from the answer entirely — that is what keeps the promise the
        escalation design rests on: **the first byte released to the client is the first
        byte of the final answer**, so no tool round can ever commit the turn to a model.

        Any :data:`SUGGESTION_MARKER` the preamble contained belonged to it: without this
        reset ``_found`` would stay set for the rest of the turn and divert the entire real
        answer into the suggestions tail.

        Args:
            mark: The value ``begin_round`` returned for this round.
            drop: False when this round's text already reached the client — a preamble
                longer than the hold window. It cannot be unsent, so it is kept and
                separated from the next round instead, exactly as before.

        Returns:
            The text that still has to be sent to the client; empty on the dropping path.
        """
        text = ""
        if drop:
            del self._emitted[mark:]
        else:
            if self._pending:
                self._emitted.append(self._pending)
                text = self._pending
            if self._emitted and not "".join(self._emitted).endswith("\n\n"):
                self._emitted.append("\n\n")
                text += "\n\n"
        self._pending = ""
        self._tail = ""
        self._found = False
        return text

    @property
    def text(self) -> str:
        """Everything released to the client so far."""
        return "".join(self._emitted)

    @property
    def partial(self) -> str:
        """Answer text including what is still held back — never the suggestions tail."""
        return "".join(self._emitted) + self._pending

    @property
    def suggestions(self) -> list[str]:
        """Follow-up suggestions parsed from the line after the marker."""
        if not self._found:
            return []
        lines = [line for line in self._tail.splitlines() if line.strip()]
        if not lines:
            return []
        items = [part.strip()[:SUGGESTION_CHARS] for part in lines[0].split("|")]
        return [item for item in items if item][:MAX_SUGGESTIONS]


@dataclass(slots=True)
class _Turn:
    """Mutable state of the turn in flight, shared with the error/cancel paths."""

    registry: CitationRegistry = field(default_factory=CitationRegistry)
    buffer: _AnswerBuffer = field(default_factory=_AnswerBuffer)
    trace: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    #: The same tokens, split by the model that actually billed them. A turn that escalates
    #: up the ladder pays two models, and tagging the row with one of them would charge a
    #: discarded ten-thousand-token attempt to whichever model happened to answer last.
    #: Like `usage`, deliberately NOT reset by `discard_attempt` — the tokens were spent.
    usage_by_model: dict[str, dict] = field(default_factory=dict)
    #: Citation records of a replayed answer. Its `[n]` were minted by the registry of the
    #: turn that first produced it, so `_persist_partial` must write these rather than fall
    #: back to this turn's registry — which a speculative search has already filled with
    #: unrelated pages, and whose `[n]` would resolve the stored answer to the wrong source.
    #: None on every path that actually generated its own text.
    sources: list[dict] | None = None
    #: The allowlisted chat model of the attempt in flight; see `ChatService._ladder`.
    model: str = ""
    #: The model the user asked for — rung zero of the ladder.
    requested_model: str = ""
    #: The model the provider layer says actually produced the text, which is not
    #: necessarily `model`: a provider failover swaps it underneath us.
    answered_by: str = ""
    #: The question being answered, kept for the raw-query search fusion and for the
    #: composed fallback answer.
    question: str = ""
    #: The closing instruction this turn runs under, read once with the system prompt so a
    #: save landing mid-turn cannot give one attempt the old text and the next the new one.
    #: Empty means the compiled default. Part of the cache key, because it is injected into
    #: the payload after the system prompt the key already digests.
    final_note: str = ""
    #: Zero-based rung of the ladder in flight.
    attempt: int = 0
    #: LLM calls issued so far across every attempt of this turn.
    llm_calls: int = 0
    #: Answer text produced but not yet released, held so a limit-blaming attempt can still
    #: be discarded. Empty once `opened` is true.
    head: str = ""
    #: Whether any answer byte has reached the client. **This is the commit point**: while
    #: it is false the turn may still switch models, and once it is true it never can.
    opened: bool = False
    #: Whether the user's own wording has already been fused into a search this turn.
    raw_fused: bool = False
    #: Whether the answer stopped early after it had already started streaming.
    cut_short: bool = False
    #: Set from outside to ask this turn to wrap up — the stop button, and the shutdown
    #: drain. Checked between streamed chunks, and it ends the turn the same way a provider
    #: dying mid-answer does: `cut_short`, then the normal persist path.
    #:
    #: Cooperative rather than `task.cancel()` on purpose, and the reason is the whole bug
    #: this feature exists to fix: a database write issued from inside a cancelled task is
    #: itself interrupted mid-flush, the session lands in `PendingRollbackError`, and the
    #: answer is lost. Asking the turn to stop lets it finish on a healthy session.
    stop: asyncio.Event | None = None
    #: The invitation copy this turn owes the user, or "" when the handoff policy did not
    #: fire. Read once with the prompts and carried, like `final_note`: an operator saving a
    #: new text mid-turn must not give the model one wording and the fallback another.
    handoff_note: str = ""

    #: The user message being answered; the assistant row hangs under it, on both the
    #: normal path and the partial-persist one.
    anchor_id: int | None = None
    #: Public uuid of that same message, for the ``done`` event's tree placement.
    anchor_uuid: str = ""
    #: Whether this turn wrote the anchor itself. A regenerate answers a question that was
    #: already there, and must never remove it when the turn fails.
    anchor_created: bool = False
    persisted: bool = False
    #: The conversation this turn answers. Held here rather than threaded through
    #: `_ladder_loop` / `_attempt` / `_release` / `_open` so the checkpoint can reach it. The
    #: same object `_persist` is handed, on the same session; `expire_on_commit=False` keeps
    #: it usable across the checkpoint commits.
    conversation: Conversation | None = None
    #: The assistant row this turn is writing, once it exists. **Set once, at the commit
    #: point, and never cleared.** `_persist` UPDATEs it rather than inserting; clearing it
    #: would insert a SECOND answer under one question, and rows sharing a parent ARE the
    #: versions of each other — the panel would draw «۲ / ۲» for a question asked once.
    row_id: int | None = None
    #: Public uuid of that row, so `_persist` can re-read it instead of holding the instance.
    row_uuid: str = ""
    #: Released characters at the last checkpoint, and the clock reading then. The throttle.
    written_chars: int = 0
    written_at: float = 0.0
    #: A checkpoint that failed switches the rest of them off: a backup that can kill the
    #: answer it protects is worse than no backup. `row_id` is deliberately NOT cleared — a
    #: forgotten handle would insert a second row.
    checkpoints_off: bool = False
    #: Checkpoint writes and what they cost, reported on `chat_answered`. The WAL grows
    #: several times faster per answer now, so its autocheckpoint fires more often and runs
    #: on whichever connection committed — i.e. inside a live turn. This is the number that
    #: makes that stall visible instead of inferred.
    writes: int = 0
    write_ms: int = 0
    #: When the turn started, so `chat_answered` can report end-to-end latency.
    started: float = field(default_factory=time.perf_counter)
    #: Milliseconds from the start of the turn to the first byte the user could see, set at
    #: the two places one actually leaves: the commit point and the composed fallback. Zero
    #: means nothing was ever released. This is the number the whole TTFT work is judged on,
    #: so it is measured rather than inferred from `ms`, which includes persistence.
    ttft_ms: int = 0

    def begin_attempt(self, model: str, position: int) -> None:
        """Reset the per-attempt state so a rejected attempt leaves no trace.

        The buffer is replaced rather than cleared, which is what guarantees a discarded
        attempt's text can never be persisted by ``_persist_partial``. Everything that
        represents work already paid for — the citation registry, the tool trace, the token
        usage — is deliberately NOT reset: it carries forward to the next model.
        """
        self.model = model
        self.attempt = position
        self.discard_attempt()

    def discard_attempt(self) -> None:
        """Throw away the text of an attempt that will not be shipped.

        Called the moment an attempt is abandoned, not merely when the next one starts:
        ``buffer.text`` is what ``_answer`` persists and what ``_persist_partial`` writes on
        a disconnect, so a rejected attempt that lingered here would be saved as the answer
        even though not one byte of it was ever streamed.
        """
        if self.row_id is not None:
            # Unreachable by construction: `_Escalate` is raised only while `opened` is
            # false, and a row exists only once it is true. `row_id` is deliberately NOT
            # cleared — a stale handle makes the next attempt overwrite that one row, which
            # is wrong but recoverable; clearing it would insert a second answer under one
            # question and mint a version nobody asked for.
            logger.error("chat_attempt_discarded_after_write", row=self.row_uuid)
        self.buffer = _AnswerBuffer()
        self.head = ""
        self.opened = False


class ChatService:
    """Answers one user message: retrieves, reasons with tools, streams and persists."""

    def __init__(self, db: AsyncSession) -> None:
        """Bind the service to a database session.

        Args:
            db: Async session that stays open for the whole stream. This service commits
                it itself; it does not rely on the request dependency's commit.
        """
        self.db = db
        self.sessions = SessionRepository(db)
        self.conversations = ConversationRepository(db)
        self.messages = MessageRepository(db)

    async def stream_answer(
        self,
        session: Session,
        content: str,
        conversation_uuid: str | None,
        parent_uuid: str | None = None,
        parent_given: bool = False,
        supersedes: str | None = None,
        stop: asyncio.Event | None = None,
    ) -> AsyncIterator[SSEEvent]:
        """Run one chat turn and stream it as server-sent events.

        Three shapes of turn share this path — see :class:`~app.domain.schemas.chat.
        ChatRequest` for the wire contract:

        1. **new turn** — no parent: the question is appended under the active leaf.
        2. **regenerate** — a user message's uuid and no content: no second copy of the
           question is written; the answer becomes another child of that same question.
        3. **edit** — a parent (or an explicit None for the first turn) plus content: the
           question is written as a sibling version of the original.
        4. **edit of a dead turn** — the same, plus ``supersedes`` naming a question with
           nothing under it: the rewrite takes its place and no version is minted.

        Args:
            session: The owning session row.
            content: The user's message; may be empty only when regenerating.
            conversation_uuid: Existing conversation to continue, or None for a new one.
            parent_uuid: Message the turn hangs under, resolved inside this conversation.
            parent_given: Whether the client sent the field at all. An omitted parent means
                "continue the branch on screen"; an explicit null means "start a new root
                version", and the two are different placements in the tree.
            supersedes: A question this edit replaces outright instead of forking. Resolved
                inside the same conversation, so a foreign uuid is refused exactly like a
                nonexistent one; honoured only when nothing hangs under it.
            stop: Set by the caller to ask the turn to wrap up — the stop button, or the
                shutdown drain. Cooperative on purpose: a write issued from inside a
                cancelled task is interrupted mid-flush and the answer is lost.

        Yields:
            ``meta``, ``tool``, ``token``, ``sources``, ``suggestions``, ``done`` and — on
            failure — ``error`` events.

        Raises:
            NotFoundException: The conversation, parent or superseded uuid is unknown to
                this session.
            ValidationException: The message is empty when it is required, or the
                conversation cap is reached. Both are raised before the first event; once
                streaming has begun every failure is reported as an ``error`` event.
        """
        question = content.strip()[:MAX_MESSAGE_CHARS]
        # A parent lives inside a conversation, so without one it cannot resolve — and an
        # unresolvable parent is a 404, never a silently ignored field.
        if parent_uuid and not conversation_uuid:
            raise NotFoundException(_MSG_NO_MESSAGE)
        if not question and not parent_uuid:
            raise ValidationException(_MSG_EMPTY_QUESTION)

        conversation = await self._resolve(session, question, conversation_uuid)
        parent: Message | None = None
        if parent_uuid:
            parent = await self.messages.get_scoped(parent_uuid, conversation.id)
            if parent is None:
                raise NotFoundException(_MSG_NO_MESSAGE)

        superseded: Message | None = None
        if supersedes:
            superseded = await self.messages.get_scoped(supersedes, conversation.id)
            if superseded is None:
                raise NotFoundException(_MSG_NO_MESSAGE)

        anchor = await self._anchor(conversation, parent, parent_given, question, superseded)
        question = anchor.content
        # `_anchor` hands back the very object it was given only on the regenerate path,
        # where the question already existed and must survive a failed turn.
        turn = _Turn(
            anchor_id=anchor.id,
            anchor_uuid=anchor.uuid,
            anchor_created=anchor is not parent,
            conversation=conversation,
            stop=stop,
        )
        index, count = await self.messages.version_of(anchor)
        yield SSEEvent(
            "meta",
            {
                "conversationUuid": conversation.uuid,
                "userMessageUuid": anchor.uuid,
                "parentUuid": await self.messages.parent_uuid_of(anchor),
                "versionIndex": index,
                "versionCount": count,
            },
        )

        try:
            async for event in self._answer(session, conversation, question, turn):
                yield event
        except asyncio.CancelledError:
            # NOT the client hanging up any more — the turn runs on its own task, so a
            # closed socket never reaches here. What does: the stop button, the hard turn
            # deadline, and the shutdown drain. All three are explicit `task.cancel()`
            # calls, which is why the write below can now complete at all: under the
            # request's anyio cancel scope it died at its first await and the answer was
            # lost outright, PendingRollbackError and all.
            await self._persist_partial(conversation, turn)
            raise
        except AppException as exc:
            logger.warning(
                "chat_turn_failed",
                code=exc.code,
                conversation=conversation.uuid,
                model=turn.model,
            )
            await self._persist_partial(conversation, turn)
            yield SSEEvent("error", {"code": exc.code, "message": exc.message})
        except Exception:  # noqa: BLE001 - the stream must degrade, never propagate
            logger.exception("chat_turn_crashed", conversation=conversation.uuid)
            await self._persist_partial(conversation, turn)
            yield SSEEvent("error", {"code": "CHAT_FAILED", "message": _MSG_FAILED})

    # ------------------------------------------------------------------ turn body

    async def _answer(
        self,
        session: Session,
        conversation: Conversation,
        question: str,
        turn: _Turn,
    ) -> AsyncIterator[SSEEvent]:
        """Produce every event after ``meta``, persisting the answer before ``done``.

        This method never emits an ``error``. A model that fails, stalls, produces nothing
        or tries to blame a limit is replaced by the next rung of the ladder; a ladder that
        runs out produces a composed answer built from what the turn did find. The user is
        never told that any of that happened.

        A turn whose whole branch is the question just asked may also be answered from the
        answer cache, which skips the ladder entirely and still persists a normal message.
        """
        profile = await self.sessions.get_profile(session)
        cfg = await agent_settings(self.db)
        turn.question = question
        # The model is the operator's setting, never the caller's: `cfg` is the always-fresh
        # read, so a change in the panel lands on this very turn. Rung zero of the ladder.
        turn.requested_model = cfg.model_primary

        # Both texts read once, here, and carried on the turn: read again per attempt, a save
        # landing mid-turn would give one rung the old prompt and the next rung the new one.
        active = await prompts_for(self.db)
        turn.final_note = active.final_round
        prompt = build_system_prompt(profile, retrieval_service.docs_map(), active.system)
        messages: list[dict] = [{"role": "system", "content": prompt}]
        # Speculative retrieval. In agent mode the model's first move is almost always a
        # `search_docs` call on the user's own wording, and that entire round-trip is
        # invisible: its preamble is dropped from the wire, so the user waits two full LLM
        # calls plus a tool for the first byte. Running the same search here spends the same
        # embedding and the same index scan one call earlier and the answer can come out of
        # round zero. Started BEFORE the history read so its network hop overlaps the
        # database round-trips; tools stay in the payload, so the model can still search
        # again when these results do not cover the question. With tools off this is not an
        # optimisation at all — it is the only retrieval the turn gets.
        pre_task = (
            asyncio.create_task(self._pre_retrieve(question, turn))
            if cfg.speculative_retrieval or not cfg.agent_mode
            else None
        )
        # The user's message is already persisted, so the history window carries this turn.
        history = await self._history(conversation, turn.anchor_id)
        messages.extend(history)
        # Has this user said more than once that they are still stuck? If so the turn owes
        # them a way to reach a person, and the model is told so in its own system message —
        # appended after the history, the same slot the speculative-retrieval note uses, so
        # it rides along to every rung of the ladder with the rest of `messages`.
        #
        # No effect on the answer cache, and none is needed: `_cache_key` already refuses any
        # turn whose branch is longer than the question just written, and `handoff_after` has
        # a floor of 2, so a turn that can carry an invitation is never a turn that can be
        # cached or replayed. That pairing is asserted in `tests/test_handoff`.
        if handoff.triggered(cfg, active, history, turn.question):
            turn.handoff_note = active.handoff
            messages.append(
                {"role": "system", "content": f"{HANDOFF_INSTRUCTION}\n{active.handoff}"}
            )
        # The cache is consulted only now, because eligibility is decided by the history
        # that was just read — and the lookup runs alongside the speculative search, so a
        # miss costs the turn nothing.
        key = _cache_key(turn, history, prompt)
        cached = self._cache_lookup(key) if key else None

        fallback = False
        if cached is not None:
            # The answer is already known, so the search in flight can only add latency.
            if pre_task is not None:
                pre_task.cancel()
            turn.model = turn.requested_model
            turn.ttft_ms = _ms(turn.started)
            answer = cached.answer
            # Copied, so a replay can never hand the next one a mutated record. Onto the
            # turn as well, so a disconnect before the commit writes these numbers and not
            # this turn's speculative registry.
            sources = turn.sources = [dict(source) for source in cached.sources]
            suggestions = list(cached.suggestions)
            # Into the buffer as well as onto the wire: a stop between here and the commit
            # below must persist what the user actually saw, not withdraw the question as
            # if the turn had produced nothing. The stored answer is the text the buffer
            # already stripped its marker from, so feeding it back is lossless.
            #
            # `opened` by hand because this path releases real bytes without going through
            # `_open`: it is the commit point in fact, and `_persist_partial` now refuses
            # to save text from a turn that never reached one.
            turn.opened = True
            turn.buffer.feed(answer)
            yield SSEEvent("token", {"delta": answer})
        else:
            if pre_task is not None:
                pre = await pre_task
                messages.append({"role": "system", "content": pre.content})
                # The raw question has now been searched, so `_run_call` must not fuse it
                # into the model's own first search as well and re-inject the same chunks.
                turn.raw_fused = True

            async for event in self._ladder_loop(messages, turn, cfg):
                yield event

            answer = turn.buffer.text.strip()
            if not answer and turn.stop is not None and turn.stop.is_set():
                # Stopped before a single byte was released. There is nothing to keep, and
                # nothing to apologise for either: composing the "I could not find it"
                # fallback here would blame retrieval for something the user did. The
                # unanswered question goes with it, so a retry does not land beside a dead
                # attempt and draw «۲ / ۲» under a question that was only ever asked once.
                logger.info("chat_stopped_before_answer", conversation=conversation.uuid)
                await self._persist_partial(conversation, turn)
                return
            if answer and turn.cut_short:
                yield SSEEvent("token", {"delta": _MSG_CUT_SHORT})
                answer += _MSG_CUT_SHORT
            if answer:
                suggestions = turn.buffer.suggestions
            else:
                # Every rung failed. Nothing has been streamed (the commit point guarantees
                # it), so a composed answer can still take the whole turn's place.
                fallback = True
                answer, suggestions = await self._fallback_answer(turn)
                logger.warning(
                    "chat_fallback_answer",
                    conversation=conversation.uuid,
                    sources=turn.registry.count,
                    tools=len(turn.trace),
                )
                turn.ttft_ms = turn.ttft_ms or _ms(turn.started)
                yield SSEEvent("token", {"delta": answer})
            # Taken from the registry, not from the model's markers: when docs were used
            # the UI always gets sources, even if the model forgot to cite them.
            sources = turn.registry.used_sources(answer)

        yield SSEEvent("sources", {"sources": sources})
        # Deduped here rather than per producer: repeated titles (several sources from
        # one page) and a model that repeats itself both land in the same event, and
        # identical chips are unclickable twins the UI keys by their text.
        suggestions = list(dict.fromkeys(suggestions))
        if suggestions:
            yield SSEEvent("suggestions", {"items": suggestions})

        row = await self._persist(conversation, turn, answer, sources)
        index, count = await self.messages.version_of(row)
        # Cached only once the answer is whole and clean. A cut-short turn, the composed
        # fallback and anything a tool failed inside would each be replayed as if they were
        # a real answer to this question, and the fallback would freeze one bad retrieval
        # into every later ask of it.
        if (
            key
            and cached is None
            and answer
            and not fallback
            and not turn.cut_short
            and not _tool_errored(turn)
        ):
            self._cache_store(key, answer, sources, suggestions)
        logger.info(
            "chat_answered",
            conversation=conversation.uuid,
            model=turn.model,
            chars=len(answer),
            sources=turn.registry.count,
            # Retrieved-vs-cited: `sources` is what the turn fetched, this is what the answer
            # actually leaned on. A wide gap between them is retrieval noise, not grounding.
            sources_cited=len(sources),
            tools=len(turn.trace),
            ms=_ms(turn.started),
            ttft_ms=turn.ttft_ms,
            # Checkpoint cost. The WAL grows several times faster per answer now, and
            # its autocheckpoint runs on whichever connection committed — inside a live
            # turn. This is what makes that stall measurable rather than inferred.
            writes=turn.writes,
            write_ms=turn.write_ms,
            # Pairs with the digest in `admin_prompts_changed`: one line says a new text went
            # live at T, and every answer after T carries it. That is the whole audit trail
            # for "which prompt produced this answer", without logging the prompt.
            prompt=prompt_digest(prompt),
            **turn.usage,
        )
        yield SSEEvent(
            "done",
            {
                "messageUuid": row.uuid,
                "parentUuid": turn.anchor_uuid or None,
                "versionIndex": index,
                "versionCount": count,
                "usage": _usage_event(turn),
            },
        )

    def _ladder(self, requested: str, cfg: AgentSettings) -> list[str]:
        """Order the models this turn may use, the user's choice first.

        Args:
            requested: The session's chosen (or default) model.
            cfg: Effective agent settings.

        Returns:
            Deduped model ids, capped at ``max_model_attempts``. One entry when escalation
            is switched off, which is the old single-model behaviour minus the wall.
        """
        if not cfg.escalation:
            return [requested]
        rungs = [requested]
        for model in cfg.ladder:
            if model not in rungs:
                rungs.append(model)
        return rungs[: max(1, cfg.max_model_attempts)]

    async def _ladder_loop(
        self, messages: list[dict], turn: _Turn, cfg: AgentSettings
    ) -> AsyncIterator[SSEEvent]:
        """Give each model in turn one shot, stopping at the first that answers.

        ``messages`` is shared across attempts on purpose: the assistant/tool pairs a
        failed model produced stay in it, so the next model reads the documentation the
        previous one already fetched instead of spending its own rounds re-finding it.
        """
        if not llm_service.available:
            # No key at all is the one genuinely hopeless case, and it costs nothing to
            # notice. Fall straight through to the composed answer.
            return
        provider_failures = 0
        for position, model in enumerate(self._ladder(turn.requested_model, cfg)):
            turn.begin_attempt(model, position)
            rounds = cfg.tool_rounds if position == 0 else cfg.retry_tool_rounds
            try:
                async for event in self._attempt(messages, turn, cfg, rounds):
                    yield event
            except _Escalate as stop:
                logger.warning(
                    "model_escalated", model=model, reason=stop.reason, attempt=position
                )
                turn.discard_attempt()
                # Two providers-down in a row is an outage, not a model that is stuck on
                # this question; a third rung would only pay the same timeout again.
                provider_failures = provider_failures + 1 if stop.reason == "provider" else 0
                if provider_failures >= 2:
                    return
                continue
            return

    async def _attempt(
        self, messages: list[dict], turn: _Turn, cfg: AgentSettings, rounds: int
    ) -> AsyncIterator[SSEEvent]:
        """One model's shot: up to ``rounds`` tool rounds, then one toolless closing call.

        Raises:
            _Escalate: The model cannot finish. Only ever raised while ``turn.opened`` is
                false, so escalation can never retract text the client has already seen.
        """
        # From the turn's settings, not read again here: the two reads used to be
        # independent, so a save landing between them produced a turn that pre-retrieved AND
        # got tools, or neither.
        tools = TOOL_SCHEMAS if cfg.agent_mode else None
        rounds = max(0, rounds) if tools else 0

        for index in range(rounds + 1):
            spent = self._exhausted(turn, cfg)
            if spent:
                if not turn.opened:
                    raise _Escalate(spent)
                turn.cut_short = True
                break
            # The closing call carries NO tools at all. Sending `tool_choice="none"`
            # alongside a tools array is what produced «دسترسی به جست‌وجو ندارم»: the model
            # sees capabilities it is forbidden to use and narrates that to the user.
            final = index == rounds
            call_tools = None if final else tools
            tool_choice = "auto" if call_tools else None
            payload = messages if not final else [*messages, _final_note(turn.final_note)]

            calls: list[dict] = []
            mark = turn.buffer.begin_round()
            started = time.perf_counter()
            first_chunk: float | None = None
            stopped = False
            turn.llm_calls += 1

            try:
                # `aclosing`, because `_release` raising `_Escalate` mid-stream is a normal
                # path here, not an error: without it the abandoned provider connection
                # would stay open until the async-generator finalizer got round to it.
                async with aclosing(
                    llm_service.stream_chat(
                        payload,
                        call_tools,
                        tool_choice,
                        model=turn.model,
                        timeout=cfg.attempt_timeout_seconds,
                    )
                ) as stream:
                    async for chunk in stream:
                        if turn.stop is not None and turn.stop.is_set():
                            # Same exit as a provider dying past the commit point: keep the
                            # text the user already has and persist it normally. Before the
                            # commit point there is nothing to keep, and `_answer` withdraws
                            # the question rather than leaving it unanswered.
                            logger.info("attempt_stopped", model=turn.model, round=index)
                            turn.cut_short = turn.opened
                            stopped = True
                            break
                        if first_chunk is None:
                            first_chunk = time.perf_counter() - started
                        if chunk.model:
                            turn.answered_by = chunk.model
                        if chunk.kind == "token":
                            delta = turn.buffer.feed(chunk.text)
                            if delta:
                                async for event in self._release(turn, delta):
                                    yield event
                        elif chunk.kind == "tool_calls" and chunk.tool_calls:
                            calls.extend(chunk.tool_calls)
                        elif chunk.kind == "usage" and chunk.usage:
                            # Every round bills separately; the row stores the total.
                            round_usage = _usage_payload(chunk.usage)
                            turn.usage = _add_usage(turn.usage, round_usage)
                            # `chunk.model` is what the provider says served this round, which
                            # a failover can change underneath us; `turn.model` is the rung we
                            # asked for. Bill whichever one is actually known.
                            billed = chunk.model or turn.model
                            turn.usage_by_model[billed] = _add_usage(
                                turn.usage_by_model.get(billed, {}), round_usage
                            )
            except ServiceUnavailableException:
                if not turn.opened:
                    raise _Escalate("provider") from None
                # Past the commit point: the text already sent is the user's, so the turn
                # ends with what it has rather than being replayed on another model.
                logger.warning("attempt_cut_short", model=turn.model, round=index)
                turn.cut_short = True
                break

            # Asked to wrap up mid-stream. Checked after the try/except so the stream is
            # already closed by `aclosing` and nothing is left half-read on the provider.
            if stopped:
                break

            # A turn is several of these; without a per-round line a slow answer is just
            # "the chat is slow" and there is no way to tell a slow search from a model
            # that spent the time reasoning.
            logger.info(
                "llm_round",
                round=index,
                model=turn.model,
                attempt=turn.attempt,
                tools=bool(call_tools),
                ms=_ms(started),
                first_chunk_ms=int((first_chunk or 0.0) * 1000),
                calls=len(calls),
                prompt_chars=sum(len(str(message.get("content") or "")) for message in payload),
            )
            if not calls:
                break
            messages.append(self._assistant_call_message(turn.buffer.round_text(mark), calls))
            preamble = turn.buffer.end_round(mark, drop=not turn.opened)
            if not turn.opened:
                # The preamble is gone from the answer, so it must go from the head too.
                turn.head = ""
            elif preamble:
                yield SSEEvent("token", {"delta": preamble})
            for call in calls:
                async for event in self._run_call(call, messages, turn):
                    yield event

        tail = turn.buffer.flush()
        if tail:
            async for event in self._release(turn, tail):
                yield event
        if not turn.opened:
            # The whole answer fitted inside the hold window, so it is screened in full.
            if not turn.head.strip():
                raise _Escalate("empty")
            if _claims_limit(turn.head):
                raise _Escalate("guard")
            async for event in self._open(turn):
                yield event

    @staticmethod
    def _exhausted(turn: _Turn, cfg: AgentSettings) -> str:
        """Which per-turn budget, if any, is already spent. Checked between calls only."""
        if turn.llm_calls >= cfg.max_llm_calls:
            return "calls"
        if time.perf_counter() - turn.started > cfg.turn_budget_seconds:
            return "budget"
        return ""

    async def _release(self, turn: _Turn, delta: str) -> AsyncIterator[SSEEvent]:
        """Send answer text, or hold it back while the attempt is still revocable.

        Raises:
            _Escalate: The held head blames a limit. Nothing has been sent, so the attempt
                is thrown away silently and the next model answers instead.
        """
        if turn.opened:
            yield SSEEvent("token", {"delta": delta})
            # Throttled inside; on most deltas this returns without touching the database.
            await self._checkpoint(turn)
            return
        turn.head += delta
        if len(turn.head) >= HEAD_HOLD_CHARS:
            if _claims_limit(turn.head):
                raise _Escalate("guard")
            async for event in self._open(turn):
                yield event

    async def _open(self, turn: _Turn) -> AsyncIterator[SSEEvent]:
        """Commit the turn to the model in flight and release everything held back."""
        turn.opened = True
        turn.ttft_ms = turn.ttft_ms or _ms(turn.started)
        head, turn.head = turn.head, ""
        if turn.attempt > 0:
            # Announced here rather than at the switch itself, so a rung that also fails is
            # never announced. Says which model, never why — the reason is ours, not the
            # user's, and «سرویس قبلی جواب نداد» is exactly the sentence we are avoiding.
            yield SSEEvent(
                "notice",
                {
                    "kind": "model_switched",
                    "to": turn.model,
                    "text": f"روی مدل {_model_label(turn.model)} ادامه دادیم.",
                },
            )
        if head:
            yield SSEEvent("token", {"delta": head})
        # AFTER the yields, always. `ttft_ms` is stamped at the top of this method and the
        # token is already in the run's replay log by the time this line resumes, so the
        # disk write can never get between the user and the first byte.
        await self._checkpoint(turn)

    async def _fallback_answer(self, turn: _Turn) -> tuple[str, list[str]]:
        """Compose the answer of last resort from what this turn actually found.

        No LLM call, so it cannot fail, time out, or produce the very sentence the guard
        exists to catch. The nearest pages are rendered with their own ``[n]`` markers,
        which is what makes ``used_sources`` populate the ``sources`` event for free.

        Returns:
            The answer text and its follow-up suggestions.
        """
        if turn.registry.count == 0 and turn.question:
            try:
                await self._pre_retrieve(turn.question, turn)
            except Exception:  # noqa: BLE001 - this is already the last resort
                logger.warning("fallback_retrieval_failed")

        queries: list[str] = []
        for entry in turn.trace:
            query = str((entry.get("meta") or {}).get("query") or "").strip()
            if query and query not in queries:
                queries.append(query)
        # Nearest PAGES, not nearest chunks: a question is routinely answered by two
        # snippets of one page, which named that page twice in the sentence below and left
        # the user two suggestion chips instead of the three this answer promises.
        sources: list[dict] = []
        seen_urls: set[str] = set()
        for source in turn.registry.all_sources():
            if source["url"] in seen_urls:
                continue
            seen_urls.add(source["url"])
            sources.append(source)
            if len(sources) == 3:
                break

        lines = ["هنوز نتوانستم پاسخ روشنی برای این پرسش از مستندات لیارا بیرون بکشم."]
        if turn.handoff_note:
            # Verbatim, and first: on this path there is no model left to say it in its own
            # words, and a user who has already told us twice that they are stuck should not
            # have to read three more paragraphs of "try rephrasing" before being told a
            # person exists. This is the whole reason the operator's text is copy rather than
            # an instruction — an instruction printed here would be nonsense to a reader.
            lines.append(turn.handoff_note)
        if queries:
            searched = "، ".join(f"«{_plain(query)}»" for query in queries[:3])
            lines.append(f"این عبارت‌ها را گشتم: {searched}.")
        if sources:
            nearest = "، ".join(
                f"«{_plain(source['title'])}» [{source['n']}]" for source in sources
            )
            lines.append(f"نزدیک‌ترین صفحه‌هایی که پیدا شد: {nearest}.")
            lines.append(
                "بگو کدام‌یک را باز کنم و مرحله‌به‌مرحله توضیح بدهم، یا پرسشت را با "
                "جزئیات بیشتری (نام سرویس و پلتفرم) بنویس تا دوباره بگردم."
            )
            suggestions = [f"توضیح «{_plain(source['title'])[:40]}»" for source in sources]
        else:
            lines.append(
                "اگر نام سرویس و پلتفرمی که رویش کار می‌کنی را بنویسی، دقیق‌تر می‌گردم."
            )
            suggestions = [
                "پرسشم را دقیق‌تر می‌نویسم",
                "نام سرویس را می‌گویم",
                "یک موضوع دیگر",
            ]
        return "\n\n".join(lines), suggestions[:3]

    async def _run_call(
        self, call: dict, messages: list[dict], turn: _Turn
    ) -> AsyncIterator[SSEEvent]:
        """Execute one model-requested tool, feeding its output back into ``messages``."""
        name = str(call.get("name") or "")
        raw_args = call.get("arguments")
        args = raw_args if isinstance(raw_args, dict) else {}
        label = _label_for(name)

        yield SSEEvent("tool", {"name": name, "status": "start", "label": label})
        started = time.perf_counter()
        # The user's own sentence is fused into the FIRST search of the turn only: it is a
        # second, independently-failing shot at the right page, but re-injecting the same
        # handful of chunks on every retry would just crowd the context.
        raw = "" if turn.raw_fused else turn.question
        if name == ToolName.SEARCH_DOCS.value:
            turn.raw_fused = True
        result = await execute_tool(name, args, turn.registry, raw)
        messages.append(
            {
                "role": "tool",
                "tool_call_id": str(call.get("id") or ""),
                "name": name,
                "content": result.content,
            }
        )
        hits = _hits_payload(result.hits, turn.registry)
        turn.trace.append({"name": name, "label": result.label or label, "meta": result.meta})
        logger.info(
            "tool_executed",
            tool=name,
            hits=len(hits),
            ok=not result.meta.get("error"),
            ms=_ms(started),
            query=str(result.meta.get("query") or result.meta.get("url") or "")[:120],
        )
        yield SSEEvent("tool", {"name": name, "status": "end", "hits": hits})

    @staticmethod
    def _assistant_call_message(preamble: str, calls: list[dict]) -> dict:
        """Rebuild the assistant turn that requested tools, in OpenAI wire shape."""
        return {
            "role": "assistant",
            "content": preamble or None,
            "tool_calls": [
                {
                    "id": str(call.get("id") or f"call_{position}"),
                    "type": "function",
                    "function": {
                        "name": str(call.get("name") or ""),
                        "arguments": _dump_args(call.get("arguments")),
                    },
                }
                for position, call in enumerate(calls)
            ],
        }

    # ------------------------------------------------------------------ retrieval

    async def _pre_retrieve(self, question: str, turn: _Turn) -> ToolResult:
        """Search once on the raw question and return it ready to inject as context.

        Three callers, one path: the speculative pre-fetch that saves a round-trip, the
        only retrieval a turn gets when ``agent_mode`` is off, and the last-resort search
        behind the composed fallback answer. Everything it returns has already been through
        ``execute_tool`` — citation numbers from the registry, snippets neutralized and
        wrapped in the ``<docs source="untrusted">`` envelope — so injecting it opens no
        trust boundary the model-driven tool call does not already open.
        """
        result = await execute_tool(ToolName.SEARCH_DOCS.value, {"query": question}, turn.registry)
        turn.trace.append(
            {
                "name": ToolName.SEARCH_DOCS.value,
                "label": result.label or TOOL_LABELS[ToolName.SEARCH_DOCS],
                "meta": result.meta,
            }
        )
        return result

    # --------------------------------------------------------------- answer cache

    @staticmethod
    def _cache_lookup(key: str) -> _Cached | None:
        """Replay the answer already written for this exact question, if it still holds.

        Exact match only. A semantic near-match layer was built here and removed: matching
        a *different* question by cosine is the one part of a cache that can be wrong, and
        0.95 does not separate a sentence from its negation — «می‌توانم X را حذف کنم؟» and
        «نمی‌توانم X را حذف کنم؟» score above it, so the wrong answer would be replayed
        with citations that look right.

        Args:
            key: The key from :func:`_cache_key`; never empty.

        Returns:
            The entry to replay, or None.
        """
        entry = _answer_cache.get(key)
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            del _answer_cache[key]
            return None
        _answer_cache.move_to_end(key)
        # The question is never logged: it is the user's text, and here it is also the key.
        logger.info("chat_cache_hit", layer=1)
        return entry

    @staticmethod
    def _cache_store(key: str, answer: str, sources: list[dict], suggestions: list[str]) -> None:
        """Remember a finished answer, evicting the least recently used entry.

        Args:
            key: The key from :func:`_cache_key`.
            answer: The answer text exactly as it was streamed.
            sources: The citation records the answer's ``[n]`` markers point at, straight
                from the turn's :class:`CitationRegistry` — the only place a source is ever
                allowed to come from.
            suggestions: The follow-up chips, already deduped.
        """
        _answer_cache[key] = _Cached(
            time.monotonic() + ANSWER_CACHE_TTL_SECONDS, answer, sources, suggestions
        )
        # An assignment to an existing key keeps its old position, so freshness is restored
        # explicitly; then the oldest entries go, exactly as `llm_service._embed_cache` does.
        _answer_cache.move_to_end(key)
        while len(_answer_cache) > ANSWER_CACHE_SIZE:
            _answer_cache.popitem(last=False)

    # ---------------------------------------------------------------- persistence

    async def _resolve(
        self, session: Session, question: str, conversation_uuid: str | None
    ) -> Conversation:
        """Fetch the scoped conversation or create a new one titled after the question."""
        if conversation_uuid:
            conversation = await self.conversations.get_scoped(conversation_uuid, session.id)
            if conversation is None:
                raise NotFoundException(_MSG_NO_CONVERSATION)
            return conversation
        if await self.conversations.count_for_session(session.id) >= MAX_CONVERSATIONS_PER_USER:
            raise ValidationException(_MSG_TOO_MANY)
        return await self.conversations.create(session.id, question[:TITLE_CHARS])

    async def _anchor(
        self,
        conversation: Conversation,
        parent: Message | None,
        parent_given: bool,
        question: str,
        superseded: Message | None = None,
    ) -> Message:
        """Return the user message this turn answers, creating it unless regenerating.

        Args:
            conversation: The scoped conversation.
            parent: The resolved parent message, if the client named one.
            parent_given: Whether the ``parentUuid`` field was present in the request.
            question: The clamped question text; empty means regenerate.
            superseded: A resolved message this edit asks to replace; deleted after the
                replacement is written, but only when it is a question nothing hangs under.

        Returns:
            The user message the answer will hang under — the existing one when
            regenerating, a freshly written one otherwise. Committed before the stream
            starts, so it survives a disconnect.

        Raises:
            ValidationException: No content was sent and the named parent is not a question
                that could be re-answered.
        """
        if not question:
            if parent is None or parent.role is not MessageRole.USER:
                raise ValidationException(_MSG_EMPTY_QUESTION)
            return parent

        if parent is not None:
            # An edit forks the question into a SIBLING, so its parent must be the answer
            # the original hung under — never another question. Without this, a client that
            # sends the original question's own uuid (the intuitive reading of "edit this")
            # chains user-under-user: the model then receives two consecutive user turns,
            # and the sibling group mixes a question with an assistant answer, so the panel
            # offers an answer as a "version" of a question.
            if parent.role is MessageRole.USER:
                parent_id: int | None = parent.parent_id
            else:
                parent_id = parent.id
        elif parent_given:
            # Explicit null: an edit of the first question, so a new root version.
            parent_id = None
        else:
            leaf_id = await self.messages.resolve_leaf(
                conversation.id, conversation.active_message_id
            )
            parent_id = await self.messages.question_parent(conversation.id, leaf_id)
        # An edit normally forks the question into a sibling version. When the question it
        # replaces was never answered there is no branch to preserve — the turn died before
        # the assistant row was written — so the rewrite takes its place outright and the
        # panel stays on «۱ / ۱». A question that DOES have something under it keeps its
        # history: `supersedes` is ignored and the edit forks as usual. Checked before the
        # write so the new row, a sibling, can never be mistaken for a child of its own.
        dead = (
            superseded
            if superseded is not None
            and superseded.role is MessageRole.USER
            and not await self.messages.has_children(conversation.id, superseded.id)
            else None
        )
        row = await self.messages.create(
            conversation.id, MessageRole.USER, question, parent_id=parent_id
        )
        # Point the branch at the question immediately, not only once an answer lands. A
        # turn that dies after this commit — no LLM key, provider down, client hangs up —
        # would otherwise leave the leaf on the previous answer, and the user's next
        # attempt would be written as a sibling of the orphan: the panel would then draw a
        # version switcher whose other version is a question nobody ever answered.
        conversation.active_message_id = row.id
        # After the leaf has been repointed, so the delete can never strand it. `dead` is
        # childless by construction, so the ON DELETE SET NULL on parent_id has nothing to
        # unwind, and active_message_id is deliberately not a foreign key.
        if dead is not None:
            await self.messages.delete(dead)
        await self.db.commit()
        return row

    async def _history(self, conversation: Conversation, leaf_id: int | None) -> list[dict]:
        """Return the recent turns of the branch being answered, as chat messages.

        Args:
            conversation: The scoped conversation.
            leaf_id: The branch to walk up from — the question being answered, so the
                abandoned versions of it and their answers stay out of the model's context.

        Returns:
            The trailing window of that branch in provider wire shape.
        """
        rows = await self.messages.recent_history(conversation.id, leaf_id)
        history: list[dict] = []
        for row in rows:
            role: Any = row.role
            body = (row.content or "").strip()[:MAX_MESSAGE_CHARS]
            if body:
                history.append(
                    {
                        "role": role.value if isinstance(role, MessageRole) else str(role),
                        "content": body,
                    }
                )
        return history

    async def _checkpoint(self, turn: _Turn) -> None:
        """Write the answer so far to the row that will BECOME the answer. Never raises.

        The first call INSERTs that row and repoints the branch at it in one commit; every
        later call rewrites the same row; :meth:`_persist` finishes it. There is never a
        second row, and that is the whole tree-safety argument — rows sharing a ``parent_id``
        are the versions of one another, so a turn that inserted twice would draw «۲ / ۲»
        under a question asked once.

        Repointing is not optional and cannot be deferred: ``active_branch`` walks UP from
        the leaf, so a committed row nothing points at is invisible, and the next question
        would then be written as a sibling of the one that was actually answered.

        Called only from :meth:`_open` and from :meth:`_release`'s opened branch, and that is
        the safety gate. ``turn.opened`` is the commit point; below it an attempt is still
        revocable and its head is still unscreened by ``_claims_limit``, so writing there
        could ship the very limit-blaming sentence that guard exists to suppress.

        Writes ``buffer.text`` — released text only — never ``buffer.partial``, whose
        held-back tail can end in a half-arrived suggestion marker. Writing the whole of it
        each time is safe because past the commit point the released text never shrinks; an
        append-only disk cursor would drift the moment a tool round closed.

        Every row it writes carries :data:`_MSG_CUT_SHORT`. That is what removes the need for
        a column, a flag and a boot sweep: text on disk is never truncated text presented as
        a whole answer — not for one commit, and not to an older build after a rollback.
        :meth:`_persist` overwrites the column whole.
        """
        conversation = turn.conversation
        if conversation is None or turn.anchor_id is None or turn.checkpoints_off:
            return
        text = turn.buffer.text.strip()
        if not text:
            return
        now = time.perf_counter()
        if turn.row_id is not None and (
            len(text) - turn.written_chars < CHAT_CHECKPOINT_CHARS
            or now - turn.written_at < CHAT_CHECKPOINT_SECONDS
        ):
            return
        started = time.perf_counter()
        # `turn.sources` first, for the reason `_persist_partial` does the same: a replayed
        # answer's `[n]` belong to the registry of the turn that produced it. Nothing on this
        # path sets it today — the cache replay never checkpoints — and it stays here so that
        # can never quietly become a wrong-citation bug.
        sources = turn.sources if turn.sources is not None else turn.registry.used_sources(text)
        try:
            if turn.row_id is None:
                row = await self.messages.create(
                    conversation.id,
                    MessageRole.ASSISTANT,
                    text + _MSG_CUT_SHORT,
                    sources=sources,
                    tool_trace=turn.trace,
                    usage=_usage_record(turn),
                    parent_id=turn.anchor_id,
                )
                await self.conversations.set_active(conversation, row.id)
                turn.row_id, turn.row_uuid = row.id, row.uuid
            else:
                await self.messages.rewrite(
                    turn.row_id,
                    text + _MSG_CUT_SHORT,
                    sources=sources,
                    tool_trace=turn.trace,
                    usage=_usage_record(turn),
                )
            await self.db.commit()
        except Exception:  # noqa: BLE001 - a backup that can kill the answer is worse
            logger.warning("chat_checkpoint_failed", conversation=conversation.uuid)
            turn.checkpoints_off = True
            try:
                await self.db.rollback()
            except Exception:  # noqa: BLE001 - nothing left to do about it
                pass
            return
        turn.written_chars, turn.written_at = len(text), now
        turn.writes += 1
        turn.write_ms += _ms(started)

    async def _persist(
        self,
        conversation: Conversation,
        turn: _Turn,
        answer: str,
        sources: list[dict] | None = None,
    ) -> Message:
        """Write the assistant row and commit it, so it survives a later disconnect.

        Insert-once, update-thereafter: a turn that passed the commit point already owns a
        row, written there by ``_checkpoint``, and this rewrites it — so ``done``'s
        ``messageUuid`` is the uuid that row has held since the first byte was released.

        The row hangs under the question it answers — a regenerate makes it a sibling of
        the previous answer rather than replacing it — and then becomes the conversation's
        active leaf, which is what makes this branch the one displayed and continued.

        Args:
            conversation: The scoped conversation.
            turn: The finished turn.
            answer: The text to store.
            sources: The citation records to store with it. Given explicitly for a replayed
                answer, whose ``[n]`` markers belong to the registry of the turn that first
                produced it, not to this turn's; the registry is the default for every
                other path.
        """
        side_cars = {
            "sources": turn.registry.used_sources(answer) if sources is None else sources,
            "tool_trace": turn.trace,
            "usage": _usage_record(turn),
        }
        row: Message | None = None
        if turn.row_id is not None:
            # The row the checkpoints have been writing. UPDATE, never a second INSERT:
            # siblings under one parent ARE versions, so inserting here would mint «۲ / ۲»
            # under every answered question in the app.
            await self.messages.rewrite(turn.row_id, answer, **side_cars)
            row = await self.messages.get_scoped(turn.row_uuid, conversation.id)
        if row is None:
            row = await self.messages.create(
                conversation.id,
                MessageRole.ASSISTANT,
                answer,
                parent_id=turn.anchor_id,
                **side_cars,
            )
        await self.conversations.set_active(conversation, row.id)
        await self.db.commit()
        turn.persisted = True
        return row

    async def _persist_partial(self, conversation: Conversation, turn: _Turn) -> None:
        """Save whatever text exists after a failure or a disconnect; never raises.

        With nothing to save, the question this turn wrote is withdrawn instead. It is
        committed before streaming starts so a disconnect keeps a half-written answer, but
        a turn that produced no text at all — a provider timeout, a missing key — would
        otherwise leave a question nobody answered sitting in the tree. The next attempt
        then lands beside it and the panel draws «۲ / ۲» under the question, offering a
        dead attempt as a version to switch to. A network hiccup must not mint a version.
        """
        if turn.persisted:
            return
        # Only text that passed the commit point may be saved. `_attempt` feeds the buffer
        # BEFORE `_release` decides whether the text may leave, so `buffer.partial` can hold
        # up to HEAD_HOLD_CHARS of unscreened head — including the limit-blaming sentence
        # `_claims_limit` exists to suppress. That was unreachable while this whole path
        # died to a re-delivered cancel; now that a stop or a shutdown drain runs it to
        # completion, a redeploy could otherwise persist exactly that sentence.
        partial = turn.buffer.partial.strip() if turn.opened else ""
        if not partial:
            if turn.row_id is not None:
                # Past the commit point with nothing left in the buffer: the checkpointed row
                # already holds the released text, honestly marked. Falling through to
                # `_withdraw_anchor` would delete the question above it, and `parent_id` is
                # ON DELETE SET NULL — the half answer would float up as a ROOT and be
                # offered as a version of the conversation's first question.
                return
            await self._withdraw_anchor(conversation, turn)
            return
        if turn.row_id is not None and not partial.endswith(_MSG_CUT_SHORT):
            # This path IS truncation — a stop, a deadline, the drain. `_answer` marks its
            # own cut-short answers before calling `_persist`; nothing marks this one. Gated
            # on `row_id` so a REPLAYED cached answer, which is whole and never checkpoints,
            # is never labelled incomplete.
            partial += _MSG_CUT_SHORT
        try:
            await self._persist(conversation, turn, partial, turn.sources)
        except Exception:  # noqa: BLE001 - this path already runs under a failure
            logger.warning("partial_persist_failed", conversation=conversation.uuid)
            try:
                await self.db.rollback()
            except Exception:  # noqa: BLE001 - nothing left to do about it
                pass

    async def _withdraw_anchor(self, conversation: Conversation, turn: _Turn) -> None:
        """Remove the question this turn wrote, once it is certain no answer followed."""
        if not turn.anchor_created or turn.anchor_id is None:
            return
        if turn.row_id is not None:
            # Unreachable: a row exists only past the commit point, and every caller
            # withdraws only when nothing was released. Belted because the failure would be
            # silent and permanent — SET NULL would turn the orphaned answer into a root.
            logger.error("anchor_withdraw_refused", conversation=conversation.uuid)
            return
        try:
            row = await self.messages.get_scoped(turn.anchor_uuid, conversation.id)
            if row is None:
                return
            conversation.active_message_id = row.parent_id
            await self.messages.delete(row)
            await self.db.commit()
            logger.info("chat_anchor_withdrawn", conversation=conversation.uuid)
        except Exception:  # noqa: BLE001 - this path already runs under a failure
            logger.warning("anchor_withdraw_failed", conversation=conversation.uuid)
            try:
                await self.db.rollback()
            except Exception:  # noqa: BLE001 - nothing left to do about it
                pass
