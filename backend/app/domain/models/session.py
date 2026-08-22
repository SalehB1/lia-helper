"""The chat workspace belonging to one signed-in user."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.base import Base, TimestampMixin, UUIDMixin


class Session(Base, UUIDMixin, TimestampMixin):
    """One row per user, holding their conversations and their stack profile.

    This used to be keyed on a client-minted ``X-Session-Id`` header, which made it a device
    token and not an identity — anyone who guessed a uuid read someone else's history. It is
    now reached only through the signed-in user, so every accessor that already filtered on
    ``session_id`` became an ownership filter with no query rewritten.

    ``user_id`` is nullable only because SQLite cannot add a NOT NULL column to an existing
    table; every row created from here on has an owner. UNIQUE is what keeps it one row per
    user when the panel fires several requests at once on mount.
    """

    __tablename__ = "sessions"

    profile_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=True,
        default=None,
    )
