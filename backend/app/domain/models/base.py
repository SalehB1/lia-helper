"""Declarative base plus the mixins every table in the app shares."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Base class for all ORM models.

    Carries the integer surrogate primary key. The integer ``id`` is internal only:
    it is never serialized and never leaves the process. Public references use ``uuid``.
    """

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)


class UUIDMixin:
    """Adds the public, externally visible identifier."""

    uuid: Mapped[str] = mapped_column(
        String(36),
        unique=True,
        index=True,
        nullable=False,
        default=lambda: str(uuid4()),
    )


class TimestampMixin:
    """Adds creation/update timestamps.

    Both columns also carry a Python-side default so the value is populated on ``flush``
    without an extra ``SELECT``; a server-side refresh would need IO and blow up under
    async (``MissingGreenlet``). ``onupdate`` is Python-side for the same reason.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
    )
