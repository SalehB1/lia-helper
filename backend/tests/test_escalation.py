"""Self-check for the model ladder: the user must never be told we hit a limit.

The incident this guards against: a model burned its three tool rounds, the closing round
was sent with ``tool_choice="none"`` while ``tools`` was still in the payload, and the model
narrated its own predicament — «حالا دسترسی به جست‌وجوی مستندات ندارم». Everything asserted
here exists so that sentence can never reach a user again:

* the closing call carries no tools at all, so the stimulus is gone;
* a model that produces that sentence anyway is discarded and another one answers, with
  nothing streamed, so the user cannot even tell it happened;
* a ladder that runs out ends in a composed answer, never an ``error`` event.

No network: ``llm_service`` is replaced by a scripted fake that records every call.

Usage (from ``backend/``)::

    .venv-uv/bin/python -m tests.test_escalation
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.exceptions import ServiceUnavailableException  # noqa: E402
from app.domain.models.base import Base  # noqa: E402
from app.domain.models.session import Session  # noqa: E402
from app.domain.services import chat_service as chat_module  # noqa: E402
from app.domain.services.agent_settings import AgentSettings, _defaults  # noqa: E402
from app.domain.services.chat_service import ChatService, _claims_limit  # noqa: E402
from app.domain.services.llm_service import LLMChunk  # noqa: E402
from app.shared.enums import MessageRole  # noqa: E402

STEP_TIMEOUT = 10.0

#: The exact sentence the incident produced.
INCIDENT = (
    "سلام! حالا دسترسی به جست‌وجوی مستندات ندارم، برای همین نمی‌تونم مراحل دقیق از "
    "docs.liara.ir بیارم. قبل از ادامه: کنسول یا CLI؟"
)

#: Two rungs, so a discarded attempt has somewhere to go. Built by replacing fields on the
#: real defaults rather than by listing every one, so adding a setting cannot break this file.
LADDER = dataclasses.replace(
    _defaults(),
    tool_rounds=2,
    retry_tool_rounds=1,
    # Rung zero is the operator's `model_primary`, so the pin that used to live in
    # `_service` (a stubbed `_resolve_model`) is now just a field on these settings.
    model_primary="model-a",
    ladder=("model-a", "model-b"),
    max_model_attempts=2,
    escalation=True,
)


class _Call:
    """One recorded request to the fake provider."""

    def __init__(self, messages: list[dict], tools: Any, tool_choice: Any, model: str) -> None:
        self.messages = [dict(message) for message in messages]
        self.tools = tools
        self.tool_choice = tool_choice
        self.model = model


class _FakeLLM:
    """Replays a per-model script and records what it was asked.

    A script entry is either a list of ``LLMChunk`` (one round's output) or an exception
    instance to raise. Entries are consumed in order, and the last one repeats — so a model
    scripted with a single answer keeps giving it however many rounds it is granted.
    """

    available = True

    def __init__(self, scripts: dict[str, list[Any]]) -> None:
        self._scripts = {model: list(rounds) for model, rounds in scripts.items()}
        self.calls: list[_Call] = []

    async def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        model: str | None = None,
        timeout: float = 0.0,
    ) -> AsyncIterator[LLMChunk]:
        """Record the request, then replay this model's next scripted round."""
        self.calls.append(_Call(messages, tools, tool_choice, str(model)))
        script = self._scripts.get(str(model)) or [[]]
        step = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(step, BaseException):
            raise step
        for chunk in step:
            yield chunk


def _tokens(text: str, model: str = "") -> list[LLMChunk]:
    """One round that writes ``text`` in small deltas, the way a provider streams."""
    size = 12
    return [
        LLMChunk(kind="token", text=text[start : start + size], model=model)
        for start in range(0, len(text), size)
    ] or [LLMChunk(kind="token", text="", model=model)]


def _tool_round(query: str = "smtp") -> list[LLMChunk]:
    """One round that asks for a documentation search."""
    return [
        LLMChunk(
            kind="tool_calls",
            tool_calls=[{"id": "c1", "name": "search_docs", "arguments": {"query": query}}],
        )
    ]


@asynccontextmanager
async def _service(scripts: dict[str, list[Any]]) -> AsyncIterator[tuple[ChatService, Session, _FakeLLM]]:
    """Yield a chat service wired to a throwaway database and the scripted provider."""
    with tempfile.TemporaryDirectory() as folder:
        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(folder) / 'test.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        fake = _FakeLLM(scripts)
        # The answer cache is keyed by question, not by asker, so every case here asks the
        # same question and would replay the previous case's script instead of running its
        # own ladder. A fresh process starts empty; so does each case.
        chat_module._answer_cache.clear()
        real_llm = chat_module.llm_service
        real_settings = chat_module.agent_settings

        async def _settings(_db: AsyncSession) -> AgentSettings:
            return LADDER

        chat_module.llm_service = fake
        chat_module.agent_settings = _settings
        try:
            async with factory() as db:
                row = Session(uuid=str(uuid4()))
                db.add(row)
                await db.commit()
                service = ChatService(db)
                yield service, row, fake
        finally:
            chat_module.llm_service = real_llm
            chat_module.agent_settings = real_settings
            await engine.dispose()


async def _turn(service: ChatService, session: Session, question: str = "چطور کاربر جدید smtp ایجاد کنم؟") -> list[Any]:
    """Drive one whole turn and collect every event it emitted."""
    events: list[Any] = []
    stream = service.stream_answer(session, question, None)
    async for event in stream:
        events.append(event)
        if len(events) > 400:  # a loop that will not terminate must fail, not hang
            await stream.aclose()
            raise AssertionError("the turn emitted an implausible number of events")
    return events


def _of(events: list[Any], name: str) -> list[Any]:
    """Every event of one type."""
    return [event for event in events if event.event == name]


def _streamed(events: list[Any]) -> str:
    """Everything the user would actually have seen."""
    return "".join(event.data.get("delta", "") for event in _of(events, "token"))


# --------------------------------------------------------------------------- checks


async def test_the_closing_call_drops_tools_instead_of_forbidding_them() -> None:
    """The incident's direct cause: `tool_choice="none"` sent alongside a tools array.

    A model that keeps searching until its rounds run out is exactly the situation that
    produced the incident, so that is what is scripted here. The round cap still bounds the
    loop — it just no longer ends by showing the model capabilities it may not use.
    """
    async with _service(
        {"model-a": [_tool_round()], "model-b": [_tokens("پاسخ [1]")]}
    ) as (service, session, fake):
        await _turn(service, session)

        assert fake.calls, "the model was never called"
        for call in fake.calls:
            assert not (call.tools and call.tool_choice == "none"), (
                "sent tools the model is forbidden to use — this is the incident"
            )
        for_a = [call for call in fake.calls if call.model == "model-a"]
        assert len(for_a) == LADDER.tool_rounds + 1, [call.tools is None for call in for_a]
        assert for_a[-1].tools is None, "the closing call still carried tools"
        assert for_a[-1].tool_choice is None, for_a[-1].tool_choice
        assert any(
            "پاسخ نهایی" in str(message.get("content", ""))
            or "دربارهٔ ابزار" in str(message.get("content", ""))
            for message in for_a[-1].messages
        ), "the closing call carried no final-answer instruction"


async def test_a_limit_claim_is_swallowed_and_another_model_answers() -> None:
    """The incident sentence must never reach the client; the next rung answers instead."""
    answer = "برای ساخت کاربر SMTP در کنسول لیارا روی «افزودن کاربر SMTP» بزن [1]."
    async with _service(
        {"model-a": [_tokens(INCIDENT, "model-a")], "model-b": [_tokens(answer, "model-b")]}
    ) as (service, session, fake):
        events = await _turn(service, session)

        streamed = _streamed(events)
        assert not _of(events, "error"), "an escalation leaked to the user as an error"
        assert "دسترسی" not in streamed, streamed
        assert streamed.strip() == answer, streamed
        notices = _of(events, "notice")
        assert len(notices) == 1 and notices[0].data["kind"] == "model_switched", notices
        assert notices[0].data["to"] == "model-b", notices[0].data
        done = _of(events, "done")[-1]
        assert done.data["usage"]["model"] == "model-b", done.data["usage"]
        assert done.data["usage"]["attempts"] == 2, done.data["usage"]

        # And nothing of the rejected rung reached disk. The answer is checkpointed to its
        # row as it streams now, so the guard that keeps the incident sentence off the wire
        # has to keep it out of the database too — which it does by construction, because a
        # row is only ever written past the commit point and this rung never reached one.
        conversations = await service.conversations.list_for_session(session.id)
        rows = await service.messages.list_for_conversation(conversations[0].id)
        stored = [row for row in rows if row.role is MessageRole.ASSISTANT]
        assert len(stored) == 1, stored
        assert stored[0].content.strip() == answer, stored[0].content
        assert "دسترسی" not in stored[0].content, "the discarded rung's text reached disk"


async def test_an_empty_answer_escalates_instead_of_erroring() -> None:
    """A model that writes nothing is replaced, not surfaced as EMPTY_ANSWER."""
    async with _service(
        {"model-a": [_tokens("   ")], "model-b": [_tokens("پاسخ درست [1]")]}
    ) as (service, session, _fake):
        events = await _turn(service, session)

        assert not _of(events, "error"), "empty answer surfaced as an error"
        assert _streamed(events).strip() == "پاسخ درست [1]"


async def test_a_dead_provider_escalates_before_anything_is_streamed() -> None:
    """A pre-first-chunk provider failure is a silent switch, not a service error."""
    async with _service(
        {
            "model-a": [ServiceUnavailableException("provider down")],
            "model-b": [_tokens("پاسخ جایگزین [1]")],
        }
    ) as (service, session, _fake):
        events = await _turn(service, session)

        assert not _of(events, "error"), "a provider failure reached the user"
        assert _streamed(events).strip() == "پاسخ جایگزین [1]"


async def test_an_exhausted_ladder_still_answers_the_user() -> None:
    """Nothing worked. The user gets a real message, sources and next steps — not an error."""
    async with _service(
        {"model-a": [_tokens(INCIDENT)], "model-b": [_tokens(INCIDENT)]}
    ) as (service, session, _fake):
        events = await _turn(service, session)

        assert not _of(events, "error"), "the dead end surfaced as an error"
        text = _streamed(events)
        assert text.strip(), "the user got nothing at all"
        assert not _claims_limit(text), text
        for word in ("محدودیت", "سقف", "مدل", "خطا"):
            assert word not in text, f"the composed answer mentions «{word}»: {text}"
        assert _of(events, "sources"), "no sources event"
        suggestions = _of(events, "suggestions")
        assert suggestions and len(suggestions[-1].data["items"]) == 3, suggestions
        assert _of(events, "done"), "the turn did not finish normally"

        rows = await service.messages.list_for_conversation(
            (await service.conversations.list_for_session(session.id))[0].id
        )
        stored = [row for row in rows if row.role is MessageRole.ASSISTANT]
        assert len(stored) == 1 and stored[0].content.strip() == text.strip(), stored


async def test_the_next_model_inherits_the_documents_already_fetched() -> None:
    """Escalation must not throw away the searches the previous attempt paid for."""
    async with _service(
        {
            "model-a": [_tool_round(), _tokens(INCIDENT)],
            "model-b": [_tokens("پاسخ با همان مستندات [1]")],
        }
    ) as (service, session, fake):
        await _turn(service, session)

        for_b = [call for call in fake.calls if call.model == "model-b"]
        assert for_b, "the second rung was never tried"
        roles = [message.get("role") for message in for_b[0].messages]
        assert "tool" in roles, roles
        assert any(
            message.get("role") == "tool" and message.get("name") == "search_docs"
            for message in for_b[0].messages
        ), "the search result did not carry forward"


async def test_text_already_streamed_is_never_retracted() -> None:
    """Past the commit point the turn finishes with what it has, on the same model."""
    long_answer = "برای ساخت کاربر SMTP " + ("مرحلهٔ بعدی را ببین. " * 30)
    async with _service(
        {
            "model-a": [_tokens(long_answer), ServiceUnavailableException("died mid-turn")],
            "model-b": [_tokens("این هرگز نباید دیده شود")],
        }
    ) as (service, session, fake):
        events = await _turn(service, session)

        streamed = _streamed(events)
        assert not _of(events, "error"), "a mid-answer failure reached the user"
        assert "هرگز نباید دیده شود" not in streamed, "the turn was replayed on another model"
        assert streamed.startswith("برای ساخت کاربر SMTP"), streamed[:60]
        assert {call.model for call in fake.calls} == {"model-a"}, "escalated after committing"


async def test_the_guard_reads_intent_not_keywords() -> None:
    """A false positive costs one escalation; eating a real answer would be worse."""
    for text in (
        INCIDENT,
        "I don't have access to my tools right now",
        "به سقف درخواست‌ها رسیدم",
        "tools are not available",
    ):
        assert _claims_limit(text), f"missed a limit claim: {text}"
    for text in (
        "محدودیت حجم دیسک در پلن رایگان ۱۰ گیگابایت است [3]",
        "در مستندات لیارا پاسخ این پرسش را پیدا نکردم",
        "برای افزودن کاربر SMTP روی «ساخت کاربر» کلیک کن [1].",
        "سقف حجم آپلود را در liara.json تنظیم کن [2].",
    ):
        assert not _claims_limit(text), f"ate a legitimate answer: {text}"


async def _run() -> int:
    """Run every check in this module and report."""
    checks: list[tuple[str, Callable[[], Awaitable[None]]]] = [
        (name, value)
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for name, check in checks:
        await asyncio.wait_for(check(), STEP_TIMEOUT * 4)
        print(f"ok  {name}")
    print(f"\n{len(checks)} checks passed")
    return 0


def main() -> int:
    """Entry point: run the async checks on a fresh event loop."""
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
