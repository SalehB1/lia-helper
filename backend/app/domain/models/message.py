"""A single turn inside a conversation."""

from __future__ import annotations

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.base import Base, TimestampMixin, UUIDMixin
from app.shared.enums import MessageRole


class Message(Base, UUIDMixin, TimestampMixin):
    """A user or assistant message plus the JSON side-cars produced while answering.

    Messages form a tree, not a list: ``parent_id`` is the message this one replies to (or
    follows). Rows sharing a ``parent_id`` are *versions* of each other — a regenerated
    answer or an edited question — ordered by ``id``. The branch actually on screen is the
    chain of parents above :attr:`Conversation.active_message_id`.
    """

    __tablename__ = "messages"

    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
        default=None,
    )
    role: Mapped[MessageRole] = mapped_column(
        SAEnum(
            MessageRole,
            native_enum=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    tool_trace_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    usage_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
