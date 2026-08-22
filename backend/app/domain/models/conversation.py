"""A chat thread owned by exactly one session."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.base import Base, TimestampMixin, UUIDMixin


class Conversation(Base, UUIDMixin, TimestampMixin):
    """A conversation. Every read path must filter on ``session_id`` as well as ``uuid``."""

    __tablename__ = "conversations"

    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    #: Leaf of the branch currently displayed — see :class:`~app.domain.models.message.Message`.
    #: Deliberately NOT a ForeignKey: conversations and messages would then reference each
    #: other and deleting a conversation would have to unwind that cycle under
    #: ``PRAGMA foreign_keys=ON``. A stale or dangling id is harmless; the branch walk falls
    #: back to the newest message of the conversation.
    active_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
