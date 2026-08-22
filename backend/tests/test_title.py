"""Self-check for conversation names — the generated one and the sanitiser under it.

A title is the one piece of model output this app renders *outside* the answer surface, in
the sidebar, with no citation machinery around it. Two things therefore matter more than the
wording: it cannot carry text that reorders the list it sits in, and it can never overwrite a
name its owner chose by hand.

The third thing asserted is the degradation contract. A conversation is created with the
first sixty characters of its question already on it, so every failure here — no key, a dead
provider, a timeout, junk output, the setting switched off — has the same visible outcome:
the conversation keeps the name it has had since the moment it existed.

No network: `llm_service` is replaced by a fake.

Usage (from ``backend/``)::

    .venv-uv/bin/python -m tests.test_title
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

from app.core.security import hash_password  # noqa: E402
from app.domain.models.base import Base  # noqa: E402
from app.domain.repositories.conversation_repo import (  # noqa: E402
    DEFAULT_TITLE,
    ConversationRepository,
    _clean_title,
)
from app.domain.repositories.session_repo import SessionRepository  # noqa: E402
from app.domain.repositories.user_repo import UserRepository  # noqa: E402
from app.domain.services import chat_service as chat_module  # noqa: E402
from app.domain.services.agent_settings import _defaults  # noqa: E402
from app.shared.constants import MAX_TITLE_CHARS  # noqa: E402

STEP_TIMEOUT = 20.0
ZWNJ = "‌"


class _FakeLLM:
    """Returns one canned title, or raises, and records that it was asked."""

    available = True

    def __init__(self, text: str = "اتصال دیسک به برنامه", error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls = 0
        self.reasoning: list[str | None] = []

    async def complete(self, messages: list[dict], **kwargs: Any) -> Any:
        """Stand in for `LLMService.complete`."""
        self.calls += 1
        self.reasoning.append(kwargs.get("reasoning"))
        if self.error is not None:
            raise self.error
        return type("R", (), {"text": self.text, "usage": {}, "model": "fake"})()


@asynccontextmanager
async def _world(llm: _FakeLLM, **overrides: Any) -> AsyncIterator[tuple[Any, int, str]]:
    """Yield ``(factory, user_id, conversation_uuid)`` with one conversation already made."""
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
            conversation = await ConversationRepository(db).create(
                session_row.id, "چطور یک دیسک بسازم و به برنامه وصلش کنم؟"
            )
            await db.commit()
            user_id, conversation_uuid = user.id, conversation.uuid

        settings = dataclasses.replace(_defaults(), **overrides)
        real = (chat_module.llm_service, chat_module.AsyncSessionLocal, chat_module.effective)
        chat_module.llm_service = llm
        chat_module.AsyncSessionLocal = factory
        chat_module.effective = lambda: settings
        try:
            yield factory, user_id, conversation_uuid
        finally:
            (chat_module.llm_service, chat_module.AsyncSessionLocal, chat_module.effective) = real
            await engine.dispose()


async def _title(factory, conversation_uuid: str) -> str:
    async with factory() as db:
        rows = await ConversationRepository(db).list_for_session(1, limit=10)
        for row in rows:
            if row.uuid == conversation_uuid:
                return row.title
    raise AssertionError("conversation vanished")


# --------------------------------------------------------------------------- checks


def test_the_sanitiser_keeps_persian_and_drops_everything_dangerous() -> None:
    """One function guards all three writers: the placeholder, the model, and a rename."""
    # ZWNJ is a letter-joining character Persian needs; stripping it breaks real words.
    assert ZWNJ in _clean_title(f"گفت{ZWNJ}وگو دربارهٔ دیسک")
    # A bidi override leaks out of its own element and scrambles the list around it.
    assert "‮" not in _clean_title("قبل‮بعد")
    # A model asked for a title sometimes writes one and then explains it.
    assert _clean_title("عنوان کوتاه\nو بعد یک توضیح اضافه") == "عنوان کوتاه"
    # Models wrap titles in quotes; the wrapping goes, inner punctuation stays.
    assert _clean_title('"استقرار Django"') == "استقرار Django"
    assert _clean_title("«اتصال دیسک»") == "اتصال دیسک"
    # SQLite does not enforce String(120), so this clamp is the only thing that does.
    assert len(_clean_title("ا" * 500)) == MAX_TITLE_CHARS
    assert _clean_title("   ") == DEFAULT_TITLE
    assert _clean_title("چند     فاصله") == "چند فاصله"


async def test_a_generated_name_replaces_the_truncated_question() -> None:
    llm = _FakeLLM("اتصال دیسک به برنامه")
    async with _world(llm) as (factory, user_id, conversation_uuid):
        await chat_module.write_title(conversation_uuid, user_id, "چطور یک دیسک بسازم؟")
        assert await _title(factory, conversation_uuid) == "اتصال دیسک به برنامه"
        # Explicitly minimal: on a GPT-5 model the operator's effort would spend more
        # reasoning tokens deliberating over six words than the words themselves cost.
        assert llm.reasoning == ["minimal"], llm.reasoning


async def test_a_name_the_user_chose_is_never_overwritten() -> None:
    """The compare-and-set, which is why no `title_edited` column exists."""
    llm = _FakeLLM("چیزی که مدل ساخته")
    async with _world(llm) as (factory, user_id, conversation_uuid):
        original = _FakeLLM.complete

        async def rename_mid_flight(self, messages, **kwargs):
            # The user renames it by hand while the model is thinking.
            async with factory() as db:
                session_row = await SessionRepository(db).get_or_create_for_user(user_id)
                row = await ConversationRepository(db).get_scoped(conversation_uuid, session_row.id)
                await ConversationRepository(db).touch_title(row, "اسمی که خودم گذاشتم")
                await db.commit()
            return await original(self, messages, **kwargs)

        _FakeLLM.complete = rename_mid_flight
        try:
            await chat_module.write_title(conversation_uuid, user_id, "چطور یک دیسک بسازم؟")
        finally:
            _FakeLLM.complete = original
        assert await _title(factory, conversation_uuid) == "اسمی که خودم گذاشتم"


async def test_every_failure_keeps_the_name_the_conversation_already_has() -> None:
    """The degradation contract: no key, dead provider, junk, or the setting off."""
    placeholder = "چطور یک دیسک بسازم و به برنامه وصلش کنم؟"

    # The provider is down.
    llm = _FakeLLM(error=RuntimeError("provider down"))
    async with _world(llm) as (factory, user_id, conversation_uuid):
        await chat_module.write_title(conversation_uuid, user_id, "پرسش")
        assert await _title(factory, conversation_uuid) == placeholder

    # It answered with nothing usable.
    llm = _FakeLLM("   \n  ")
    async with _world(llm) as (factory, user_id, conversation_uuid):
        await chat_module.write_title(conversation_uuid, user_id, "پرسش")
        assert await _title(factory, conversation_uuid) == placeholder

    # The operator switched it off — and it must not even be asked.
    llm = _FakeLLM("نامی که نباید ساخته شود")
    async with _world(llm, auto_title=False) as (factory, user_id, conversation_uuid):
        await chat_module.write_title(conversation_uuid, user_id, "پرسش")
        assert await _title(factory, conversation_uuid) == placeholder
        assert llm.calls == 0, "a switched-off setting must not spend a call"


async def test_a_conversation_deleted_mid_flight_is_not_resurrected() -> None:
    llm = _FakeLLM("نامی برای چیزی که دیگر نیست")
    async with _world(llm) as (factory, user_id, conversation_uuid):
        async with factory() as db:
            session_row = await SessionRepository(db).get_or_create_for_user(user_id)
            await ConversationRepository(db).delete_scoped(conversation_uuid, session_row.id)
            await db.commit()
        # Must not raise, and must not write anything back.
        await chat_module.write_title(conversation_uuid, user_id, "پرسش")


async def _run(check) -> None:
    if asyncio.iscoroutinefunction(check):
        await asyncio.wait_for(check(), STEP_TIMEOUT)
    else:
        check()


def main() -> None:
    """Run every check in this module."""
    checks = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for check in checks:
        asyncio.run(_run(check))
        print("ok ", check.__name__)
    print(f"\n{len(checks)} checks passed")


if __name__ == "__main__":
    main()
