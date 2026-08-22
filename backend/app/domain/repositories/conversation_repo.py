"""Conversation access — always scoped to the owning session.

There is deliberately no ``get_by_uuid``: a conversation uuid is identity, not
authorization. Every accessor filters on ``session_id`` inside the same query.
"""

from __future__ import annotations

import re

from sqlalchemy import func, select

from app.domain.models.conversation import Conversation
from app.domain.repositories.base import BaseRepository
from app.shared.constants import MAX_TITLE_CHARS

#: Shown when the caller has no usable title yet.
DEFAULT_TITLE = "گفت‌وگوی جدید"


#: Codepoints that reorder or hide the text around them. A title is rendered in the sidebar
#: beside other people's — well, beside the user's other conversations — and an unpaired
#: override leaks out of its own element and scrambles the rest of the list. `U+200C` (ZWNJ)
#: is deliberately NOT here: it is a letter-joining character Persian genuinely needs, and
#: stripping it turns «گفت‌وگو» into «گفتوگو».
_BIDI_AND_CONTROL = re.compile(
    r"[\x00-\x1f\x7f\u200e\u200f\u202a-\u202e\u2066-\u2069]"
)

#: Quotes a model wraps a title in when asked for one. Stripped as a pair of edges, not
#: globally, so «راه‌اندازی "دیسک" در لیارا» keeps its inner quotes.
_WRAPPING = "\"'«»`\u200f\u200e "


def _clean_title(title: str) -> str:
    """Make any proposed title safe to store and to render, or fall back to the default.

    The one sanitiser for all three writers — the truncated placeholder, the generated
    title and a user's rename — so there is no per-caller guard to forget. SQLite does not
    enforce ``String(120)``, which makes the clamp here the only thing keeping the column
    honest.

    Deliberately NOT ``persian.normalize``: that maps آ to ا and strips diacritics, which is
    right for matching a search query and wrong for showing someone their own words back.

    Args:
        title: Proposed title from any source, trusted or not.

    Returns:
        A single line, free of control and bidi-override characters, unwrapped, whitespace
        collapsed and clamped — or the Persian default when nothing usable is left.
    """
    # First line BEFORE the control-character scrub, or the scrub turns the newline into a
    # space and the explanation a model sometimes appends becomes part of the title.
    lines = (title or "").splitlines()
    first = lines[0] if lines else ""
    first = _BIDI_AND_CONTROL.sub(" ", first)
    first = re.sub(r"\s+", " ", first).strip().strip(_WRAPPING).strip()
    return first[:MAX_TITLE_CHARS] or DEFAULT_TITLE


class ConversationRepository(BaseRepository[Conversation]):
    """Session-scoped CRUD for conversations."""

    model = Conversation

    async def create(self, session_id: int, title: str) -> Conversation:
        """Create a conversation owned by a session.

        Args:
            session_id: Internal id of the owning session.
            title: Proposed title; trimmed and clamped, falling back to a Persian default.

        Returns:
            The persisted conversation.
        """
        return await self.add(Conversation(session_id=session_id, title=_clean_title(title)))

    async def get_scoped(self, conversation_uuid: str, session_id: int) -> Conversation | None:
        """Fetch a conversation only if this session owns it.

        Args:
            conversation_uuid: Public conversation identifier.
            session_id: Internal id of the requesting session.

        Returns:
            The conversation, or None when it does not exist or belongs to someone else.
        """
        result = await self.db.execute(
            select(Conversation).where(
                Conversation.uuid == conversation_uuid,
                Conversation.session_id == session_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_session(self, session_id: int, limit: int = 50) -> list[Conversation]:
        """List a session's conversations, most recently updated first.

        Args:
            session_id: Internal id of the requesting session.
            limit: Maximum rows to return.

        Returns:
            The conversations owned by this session.
        """
        result = await self.db.execute(
            select(Conversation)
            .where(Conversation.session_id == session_id)
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_for_session(self, session_id: int) -> int:
        """Count a session's conversations.

        Args:
            session_id: Internal id of the requesting session.

        Returns:
            The number of conversations owned by this session.
        """
        result = await self.db.execute(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.session_id == session_id)
        )
        return int(result.scalar_one() or 0)

    async def delete_scoped(self, conversation_uuid: str, session_id: int) -> bool:
        """Delete a conversation only if this session owns it.

        Args:
            conversation_uuid: Public conversation identifier.
            session_id: Internal id of the requesting session.

        Returns:
            True when a row was deleted, False when nothing matched.
        """
        conversation = await self.get_scoped(conversation_uuid, session_id)
        if conversation is None:
            return False
        await self.delete(conversation)
        return True

    async def set_active(self, conversation: Conversation, message_id: int | None) -> None:
        """Point a conversation at the leaf of the branch it should display.

        Args:
            conversation: A conversation already fetched through a scoped accessor.
            message_id: Internal id of the new active leaf, already resolved inside this
                same conversation.
        """
        conversation.active_message_id = message_id
        await self.db.flush()

    async def touch_title(self, conversation: Conversation, title: str) -> None:
        """Rename a conversation that was already fetched through a scoped accessor.

        Args:
            conversation: The conversation row to rename.
            title: Proposed title; trimmed and clamped.
        """
        conversation.title = _clean_title(title)
        await self.db.flush()
