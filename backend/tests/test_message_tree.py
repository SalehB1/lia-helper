"""Self-check for conversation branching — the message tree and its active leaf.

Retry must not append a second copy of the question, an edit must fork the question rather
than overwrite it, and whichever version is selected must define the history the model
sees from that point on. All of that is invisible in the UI until it is wrong, so it is
asserted here: siblings instead of duplicates, an active branch that excludes the
abandoned version, switching versions changing what history returns, a foreign parent uuid
refused exactly like a nonexistent one, and a corrupt parent link that terminates the walk
instead of hanging the request.

No network: ``stream_answer`` is driven only as far as its ``meta`` event, which is emitted
before the model is ever called, and the assistant row is written through the service's own
persistence path.

Usage (from ``backend/``)::

    .venv-uv/bin/python -m tests.test_message_tree
"""

from __future__ import annotations

import asyncio
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

from sqlalchemy import event, text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.exceptions import NotFoundException, ValidationException  # noqa: E402
from app.domain.models.base import Base  # noqa: E402
from app.domain.models.conversation import Conversation  # noqa: E402
from app.domain.models.message import Message  # noqa: E402
from app.domain.models.session import Session  # noqa: E402
from app.domain.repositories.conversation_repo import ConversationRepository  # noqa: E402
from app.domain.repositories.message_repo import MAX_TREE_ROWS, MessageRepository  # noqa: E402
from app.domain.services.chat_service import ChatService, _Turn  # noqa: E402
from app.shared.enums import MessageRole  # noqa: E402

#: Any walk that does not terminate must fail the run instead of hanging it.
STEP_TIMEOUT = 5.0


@asynccontextmanager
async def _database() -> AsyncIterator[AsyncSession]:
    """Yield a session on a throwaway sqlite file with foreign keys enforced."""
    with tempfile.TemporaryDirectory() as folder:
        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(folder) / 'test.db'}")

        @event.listens_for(engine.sync_engine, "connect")
        def _fk(dbapi_connection: Any, record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            yield db
        await engine.dispose()


async def _session(db: AsyncSession) -> Session:
    """Create one anonymous session row — the whole identity this app has."""
    row = Session(uuid=str(uuid4()))
    db.add(row)
    await db.commit()
    return row


async def _ask(
    service: ChatService,
    session: Session,
    content: str = "",
    conversation_uuid: str | None = None,
    parent_uuid: str | None = None,
    parent_given: bool = False,
    supersedes: str | None = None,
    answer: str = "پاسخ",
) -> tuple[dict, Conversation, Message, Message]:
    """Run one turn up to its ``meta`` event, then persist an answer for it.

    Returns:
        The meta payload, the conversation, the anchor (question) row and the answer row.
    """
    events = service.stream_answer(
        session, content, conversation_uuid, parent_uuid, parent_given, supersedes
    )
    meta = await asyncio.wait_for(events.__anext__(), STEP_TIMEOUT)
    await events.aclose()

    conversation = await service.conversations.get_scoped(
        meta.data["conversationUuid"], session.id
    )
    assert conversation is not None
    anchor = await service.messages.get_scoped(meta.data["userMessageUuid"], conversation.id)
    assert anchor is not None
    turn = _Turn(anchor_id=anchor.id, anchor_uuid=anchor.uuid)
    row = await service._persist(conversation, turn, answer)
    return meta.data, conversation, anchor, row


async def _questions(db: AsyncSession, conversation: Conversation) -> list[str]:
    """Every user message stored in a conversation, across all branches."""
    rows = await MessageRepository(db).list_for_conversation(conversation.id)
    return [row.content for row in rows if row.role is MessageRole.USER]


async def _history(db: AsyncSession, conversation: Conversation) -> list[str]:
    """The message contents the model would be given for the active branch."""
    messages = MessageRepository(db)
    leaf = await messages.resolve_leaf(conversation.id, conversation.active_message_id)
    rows = await asyncio.wait_for(
        messages.recent_history(conversation.id, leaf, limit=100), STEP_TIMEOUT
    )
    return [row.content for row in rows]


async def test_regenerate_answers_again_without_repeating_the_question() -> None:
    """Retry must add a sibling answer, not a second copy of the question."""
    async with _database() as db:
        service = ChatService(db)
        session = await _session(db)
        _, conversation, question, first = await _ask(service, session, "سوال", answer="اول")

        meta, _, anchor, second = await _ask(
            service,
            session,
            content="",
            conversation_uuid=conversation.uuid,
            parent_uuid=question.uuid,
            answer="دوم",
        )

        assert anchor.id == question.id, "regenerate reused the question row"
        assert await _questions(db, conversation) == ["سوال"], "the question was stored twice"
        assert second.parent_id == question.id, second.parent_id
        assert first.parent_id == question.id, first.parent_id
        assert await MessageRepository(db).version_of(second) == (2, 2)
        assert (meta["versionIndex"], meta["versionCount"]) == (1, 1), meta


async def test_active_branch_drops_the_abandoned_version() -> None:
    """The regenerated answer takes over the branch; the old one leaves the context."""
    async with _database() as db:
        service = ChatService(db)
        session = await _session(db)
        _, conversation, question, first = await _ask(service, session, "سوال", answer="اول")
        await _ask(
            service,
            session,
            conversation_uuid=conversation.uuid,
            parent_uuid=question.uuid,
            answer="دوم",
        )

        assert await _history(db, conversation) == ["سوال", "دوم"], await _history(db, conversation)
        branch = await MessageRepository(db).active_branch(
            conversation.id, conversation.active_message_id
        )
        assert [node.message.content for node in branch] == ["سوال", "دوم"]
        assert first.uuid not in {node.message.uuid for node in branch}
        assert [node.version_count for node in branch] == [1, 2], branch
        # The listing must count the branch, not every abandoned version with it.
        lengths = await MessageRepository(db).branch_lengths(
            {conversation.id: conversation.active_message_id}
        )
        assert lengths[conversation.id] == 2, lengths


async def test_switching_versions_changes_what_history_returns() -> None:
    """Selecting a version rewinds the branch to it, including for later turns."""
    async with _database() as db:
        service = ChatService(db)
        session = await _session(db)
        _, conversation, question, first = await _ask(service, session, "سوال", answer="اول")
        await _ask(
            service,
            session,
            conversation_uuid=conversation.uuid,
            parent_uuid=question.uuid,
            answer="دوم",
        )
        assert await _history(db, conversation) == ["سوال", "دوم"]

        messages = MessageRepository(db)
        leaf = await messages.deepest_leaf(conversation.id, first.id)
        await ConversationRepository(db).set_active(conversation, leaf)
        await db.commit()
        assert await _history(db, conversation) == ["سوال", "اول"]

        # A new turn continues from the selected version, not from the newest row.
        await _ask(service, session, "بعدی", conversation_uuid=conversation.uuid, answer="سوم")
        assert await _history(db, conversation) == ["سوال", "اول", "بعدی", "سوم"]


async def test_edit_forks_the_question_into_a_sibling() -> None:
    """An edited question is a new version of it, and its branch is what continues."""
    async with _database() as db:
        service = ChatService(db)
        session = await _session(db)
        _, conversation, first_q, first_a = await _ask(service, session, "سوال یک", answer="یک")
        _, _, second_q, _ = await _ask(
            service, session, "سوال دو", conversation_uuid=conversation.uuid, answer="دو"
        )
        assert second_q.parent_id == first_a.id, "a plain turn must extend the active branch"

        meta, _, edited, _ = await _ask(
            service,
            session,
            "سوال دوی ویرایش‌شده",
            conversation_uuid=conversation.uuid,
            parent_uuid=first_a.uuid,
            answer="دوی تازه",
        )
        assert edited.id != second_q.id, "the edit overwrote the original question"
        assert edited.parent_id == first_a.id, edited.parent_id
        assert (meta["versionIndex"], meta["versionCount"]) == (2, 2), meta
        assert meta["parentUuid"] == first_a.uuid, meta
        assert await _history(db, conversation) == [
            "سوال یک",
            "یک",
            "سوال دوی ویرایش‌شده",
            "دوی تازه",
        ], await _history(db, conversation)

        # Editing the very first question has no parent to name: an explicit null forks it.
        _, _, rewritten, _ = await _ask(
            service,
            session,
            "سوال یک، دوباره",
            conversation_uuid=conversation.uuid,
            parent_uuid=None,
            parent_given=True,
            answer="یک تازه",
        )
        assert rewritten.parent_id is None, rewritten.parent_id
        assert await _history(db, conversation) == ["سوال یک، دوباره", "یک تازه"]


async def test_a_foreign_parent_is_refused_like_a_nonexistent_one() -> None:
    """A parent uuid from someone else's conversation must not resolve — same 404 body."""
    async with _database() as db:
        service = ChatService(db)
        owner = await _session(db)
        intruder = await _session(db)
        _, conversation, question, _ = await _ask(service, owner, "سوال")
        _, other, _, _ = await _ask(service, intruder, "سوال دیگر")

        async def refusal(**kwargs: Any) -> NotFoundException:
            try:
                await _ask(service, intruder, **kwargs)
            except NotFoundException as exc:
                return exc
            raise AssertionError(f"accepted a parent it must not resolve: {kwargs}")

        stolen = await refusal(conversation_uuid=other.uuid, parent_uuid=question.uuid)
        unknown = await refusal(conversation_uuid=other.uuid, parent_uuid=str(uuid4()))
        assert (stolen.code, stolen.message) == (unknown.code, unknown.message), stolen.message
        assert stolen.status_code == unknown.status_code == 404
        # And the message must stay unreachable through the repository itself.
        assert await MessageRepository(db).get_scoped(question.uuid, other.id) is None
        # A parent without a conversation cannot be resolved either.
        await refusal(parent_uuid=question.uuid)


async def test_a_turn_without_content_or_a_question_parent_is_refused() -> None:
    """Empty content is only ever a regenerate, and only under a user message."""
    async with _database() as db:
        service = ChatService(db)
        session = await _session(db)
        _, conversation, _, answer = await _ask(service, session, "سوال")

        for kwargs in (
            {},
            {"conversation_uuid": conversation.uuid},
            {"conversation_uuid": conversation.uuid, "parent_uuid": answer.uuid},
        ):
            try:
                await _ask(service, session, content="   ", **kwargs)
            except ValidationException as exc:
                assert exc.status_code == 422, exc.status_code
            else:
                raise AssertionError(f"accepted an empty question: {kwargs}")
        assert await _questions(db, conversation) == ["سوال"], "an empty turn was stored"


async def test_the_walk_terminates_on_a_self_referencing_row() -> None:
    """A corrupt parent link must end the walk, never spin the event loop."""
    async with _database() as db:
        service = ChatService(db)
        session = await _session(db)
        _, conversation, question, answer = await _ask(service, session, "سوال")

        await db.execute(
            text("UPDATE messages SET parent_id = :id WHERE id = :id"), {"id": answer.id}
        )
        await db.commit()
        # Refresh only this row: expiring the whole identity map would make the ORM reload
        # the conversation lazily on first read, which under async is a MissingGreenlet.
        await db.refresh(answer)

        assert await _history(db, conversation) == ["پاسخ"], "the cycle leaked into history"
        branch = await asyncio.wait_for(
            MessageRepository(db).active_branch(conversation.id, answer.id), STEP_TIMEOUT
        )
        assert [node.message.uuid for node in branch] == [answer.uuid], branch
        # The row is now its own child, so descending from it must stop on it rather than
        # follow the link forever. The question lost its only child with that edit.
        for start, expected in ((answer.id, answer.id), (question.id, question.id)):
            assert (
                await asyncio.wait_for(
                    MessageRepository(db).deepest_leaf(conversation.id, start), STEP_TIMEOUT
                )
                == expected
            )


async def test_deleting_a_conversation_still_removes_its_tree() -> None:
    """The self-referencing FK must not block the cascade that deletes a conversation."""
    async with _database() as db:
        service = ChatService(db)
        session = await _session(db)
        _, conversation, question, _ = await _ask(service, session, "سوال")
        await _ask(
            service,
            session,
            conversation_uuid=conversation.uuid,
            parent_uuid=question.uuid,
            answer="دوم",
        )

        assert await ConversationRepository(db).delete_scoped(conversation.uuid, session.id)
        await db.commit()
        remaining = await db.execute(
            Message.__table__.select().where(Message.conversation_id == conversation.id)
        )
        assert remaining.first() is None, "messages outlived their conversation"


async def test_a_question_never_parents_a_question() -> None:
    """An edit forks beside the original question; it never chains under it.

    A user-under-user chain hands the provider two consecutive user turns and puts a
    question and an answer in the same sibling group, so the panel offers an answer as a
    "version" of a question.
    """
    async with _database() as db:
        session = await _session(db)
        service = ChatService(db)
        _, conversation, first, _ = await _ask(service, session, "پرسش یک")
        # The intuitive client mistake: "edit this" pointing at the question's own uuid.
        _, _, edited, _ = await _ask(
            service,
            session,
            "پرسش یک، ویرایش‌شده",
            conversation_uuid=conversation.uuid,
            parent_uuid=first.uuid,
            parent_given=True,
        )
        assert edited.parent_id == first.parent_id, (edited.parent_id, first.parent_id)
        assert edited.parent_id != first.id
        roles = [row for row in await _history(db, conversation)]
        assert "پرسش یک، ویرایش‌شده" in roles, roles
        assert "پرسش یک" not in roles, roles


async def test_a_failed_turn_does_not_orphan_its_question() -> None:
    """A question whose answer never arrived still owns the branch, and the next one
    lands beside it rather than under it."""
    async with _database() as db:
        session = await _session(db)
        service = ChatService(db)
        _, conversation, first, _ = await _ask(service, session, "پرسش یک")

        # A turn that dies after the question is committed: stream_answer writes the row
        # and points the branch at it, then the model never produces anything.
        events = service.stream_answer(session, "پرسشِ ناکام", conversation.uuid, None, False)
        meta = await asyncio.wait_for(events.__anext__(), STEP_TIMEOUT)
        await events.aclose()
        orphan_uuid = meta.data["userMessageUuid"]
        await db.refresh(conversation)
        orphan = await service.messages.get_scoped(orphan_uuid, conversation.id)
        assert orphan is not None
        assert conversation.active_message_id == orphan.id, conversation.active_message_id

        _, _, retried, _ = await _ask(
            service, session, "پرسش دوباره", conversation_uuid=conversation.uuid
        )
        assert retried.parent_id == orphan.parent_id, (retried.parent_id, orphan.parent_id)
        assert retried.parent_id != orphan.id
        history = await _history(db, conversation)
        assert "پرسشِ ناکام" not in history, history
        assert history[-2:] == ["پرسش دوباره", "پاسخ"], history
        assert first.content in history


async def test_the_newest_rows_survive_the_tree_cap() -> None:
    """Past MAX_TREE_ROWS the window must keep the newest turns, not the oldest.

    Taking the oldest rows drops the active leaf out of the window, and every walk then
    silently reattaches to the last row of that window: the newest turns vanish from the
    transcript and the model stops seeing what was just said.
    """
    async with _database() as db:
        session = await _session(db)
        service = ChatService(db)
        messages = MessageRepository(db)
        _, conversation, _, last = await _ask(service, session, "پرسش ۰")
        for index in range(1, MAX_TREE_ROWS // 2 + 4):
            _, _, _, last = await _ask(
                service, session, f"پرسش {index}", conversation_uuid=conversation.uuid
            )
        await db.refresh(conversation)
        rows = await messages.list_for_conversation(conversation.id)
        assert len(rows) == MAX_TREE_ROWS, len(rows)
        assert rows[-1].id == last.id, (rows[-1].id, last.id)
        leaf = await messages.resolve_leaf(conversation.id, conversation.active_message_id)
        assert leaf == last.id, (leaf, last.id)
        history = await _history(db, conversation)
        assert history[-1] == "پاسخ"
        assert f"پرسش {MAX_TREE_ROWS // 2 + 3}" in history, history[-4:]
        # The sidebar count and the transcript must agree under the same cap.
        lengths = await messages.branch_lengths({conversation.id: leaf})
        branch = await messages.active_branch(conversation.id, leaf)
        assert lengths[conversation.id] == len(branch), (lengths, len(branch))


async def test_a_textless_failure_leaves_no_phantom_version() -> None:
    """A turn that produced nothing withdraws its question instead of minting a version.

    Otherwise a provider timeout leaves a question nobody answered, the retry lands beside
    it, and the panel draws «۲ / ۲» offering a dead attempt as a version to switch to.
    """
    async with _database() as db:
        session = await _session(db)
        service = ChatService(db)
        _, conversation, first, answer = await _ask(service, session, "پرسش یک")

        events = service.stream_answer(session, "پرسشِ ناکام", conversation.uuid, None, False)
        meta = await asyncio.wait_for(events.__anext__(), STEP_TIMEOUT)
        await events.aclose()
        orphan_uuid = meta.data["userMessageUuid"]
        orphan = await service.messages.get_scoped(orphan_uuid, conversation.id)
        assert orphan is not None
        # No token ever arrived, so the failure paths withdraw it.
        turn = _Turn(anchor_id=orphan.id, anchor_uuid=orphan.uuid, anchor_created=True)
        await service._persist_partial(conversation, turn)

        assert await service.messages.get_scoped(orphan_uuid, conversation.id) is None
        await db.refresh(conversation)
        assert conversation.active_message_id == answer.id, conversation.active_message_id
        assert await _questions(db, conversation) == ["پرسش یک"], await _questions(db, conversation)

        # The retry is then an ordinary first attempt, not version 2 of a dead one.
        _, _, retried, _ = await _ask(
            service, session, "پرسش دوباره", conversation_uuid=conversation.uuid
        )
        assert await MessageRepository(db).version_of(retried) == (1, 1)
        assert retried.parent_id == answer.id, (retried.parent_id, answer.id)
        assert first.content in await _history(db, conversation)


async def test_regenerate_keeps_its_question_when_the_turn_fails() -> None:
    """Withdrawal must never touch a question the turn did not write."""
    async with _database() as db:
        session = await _session(db)
        service = ChatService(db)
        _, conversation, question, _ = await _ask(service, session, "پرسش یک")

        turn = _Turn(anchor_id=question.id, anchor_uuid=question.uuid, anchor_created=False)
        await service._persist_partial(conversation, turn)

        assert await service.messages.get_scoped(question.uuid, conversation.id) is not None


async def test_editing_an_unanswered_question_replaces_it() -> None:
    """A question nobody answered is a dead attempt: the rewrite takes its place.

    Without this every failed turn leaves a «۲ / ۲» under the retyped question, offering a
    dead attempt as a version to switch to — the branch the user is on IS the only one.
    """
    async with _database() as db:
        session = await _session(db)
        service = ChatService(db)
        _, conversation, first_q, first_a = await _ask(service, session, "پرسش یک", answer="یک")

        # A turn that dies right after its question is committed.
        events = service.stream_answer(session, "پرسشِ ناکام", conversation.uuid, None, False)
        meta = await asyncio.wait_for(events.__anext__(), STEP_TIMEOUT)
        await events.aclose()
        orphan = await service.messages.get_scoped(meta.data["userMessageUuid"], conversation.id)
        assert orphan is not None

        edited_meta, _, edited, _ = await _ask(
            service,
            session,
            "پرسشِ بازنویسی‌شده",
            conversation_uuid=conversation.uuid,
            parent_uuid=first_a.uuid,
            supersedes=orphan.uuid,
            answer="پاسخ تازه",
        )

        assert await service.messages.get_scoped(orphan.uuid, conversation.id) is None
        assert (edited_meta["versionIndex"], edited_meta["versionCount"]) == (1, 1), edited_meta
        assert await MessageRepository(db).version_of(edited) == (1, 1)
        assert await _questions(db, conversation) == ["پرسش یک", "پرسشِ بازنویسی‌شده"], (
            await _questions(db, conversation)
        )
        assert await _history(db, conversation) == [
            "پرسش یک",
            "یک",
            "پرسشِ بازنویسی‌شده",
            "پاسخ تازه",
        ], await _history(db, conversation)
        assert first_q.content in await _history(db, conversation)


async def test_editing_an_answered_question_still_forks() -> None:
    """`supersedes` is advisory: a question with an answer under it keeps its history."""
    async with _database() as db:
        session = await _session(db)
        service = ChatService(db)
        _, conversation, first_q, first_a = await _ask(service, session, "پرسش یک", answer="یک")
        _, _, second_q, _ = await _ask(
            service, session, "پرسش دو", conversation_uuid=conversation.uuid, answer="دو"
        )

        meta, _, edited, _ = await _ask(
            service,
            session,
            "پرسش دوی ویرایش‌شده",
            conversation_uuid=conversation.uuid,
            parent_uuid=first_a.uuid,
            supersedes=second_q.uuid,
            answer="دوی تازه",
        )

        assert await service.messages.get_scoped(second_q.uuid, conversation.id) is not None
        assert edited.id != second_q.id
        assert (meta["versionIndex"], meta["versionCount"]) == (2, 2), meta
        assert first_q.content in await _history(db, conversation)


async def test_superseding_an_assistant_message_is_ignored() -> None:
    """An answer is never a dead question, so it must survive an edit that names it."""
    async with _database() as db:
        session = await _session(db)
        service = ChatService(db)
        _, conversation, _, answer = await _ask(service, session, "پرسش یک", answer="یک")

        _, _, edited, _ = await _ask(
            service,
            session,
            "پرسش یک، ویرایش‌شده",
            conversation_uuid=conversation.uuid,
            parent_uuid=None,
            parent_given=True,
            supersedes=answer.uuid,
        )

        assert await service.messages.get_scoped(answer.uuid, conversation.id) is not None
        assert edited.parent_id is None, edited.parent_id


async def test_a_foreign_supersedes_is_refused_like_a_nonexistent_one() -> None:
    """`supersedes` is scoped exactly like `parentUuid` — same 404, no row touched."""
    async with _database() as db:
        service = ChatService(db)
        owner = await _session(db)
        intruder = await _session(db)
        _, victim, question, _ = await _ask(service, owner, "پرسش قربانی")
        _, mine, _, _ = await _ask(service, intruder, "پرسش خودم")

        async def refusal(target: str) -> NotFoundException:
            try:
                await _ask(
                    service,
                    intruder,
                    "تلاش",
                    conversation_uuid=mine.uuid,
                    parent_given=True,
                    supersedes=target,
                )
            except NotFoundException as exc:
                return exc
            raise AssertionError(f"accepted a supersedes it must not resolve: {target}")

        stolen = await refusal(question.uuid)
        unknown = await refusal(str(uuid4()))
        assert (stolen.code, stolen.message) == (unknown.code, unknown.message), stolen.message
        assert stolen.status_code == unknown.status_code == 404
        assert await service.messages.get_scoped(question.uuid, victim.id) is not None


async def _run() -> int:
    """Run every check in this module and report."""
    checks: list[tuple[str, Callable[[], Awaitable[None]]]] = [
        (name, value)
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for name, check in checks:
        await check()
        print(f"ok  {name}")
    print(f"\n{len(checks)} checks passed")
    return 0


def main() -> int:
    """Entry point: run the async checks on a fresh event loop."""
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
