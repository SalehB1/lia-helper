"""Panel operators — the admin identity. The chat surface stays anonymous."""

from __future__ import annotations

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.base import Base, TimestampMixin, UUIDMixin


class User(Base, UUIDMixin, TimestampMixin):
    """One person who may sign in to the panel.

    ``is_superuser`` is the entire authorization model: true means they can also manage the
    accounts themselves, false means they can only operate the assistant. A boolean rather
    than a role string because there are exactly two kinds of operator and a flag cannot hold
    a value nobody anticipated — an unrecognised role string would need a fail-closed
    coercion, whereas ``NULL``/absent here simply reads as false, which is the safe answer.
    """

    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    #: ``scrypt$n$r$p$salt$key``. Never a plaintext password, never logged, never serialized —
    #: no schema in `app/domain/schemas/` exposes this column.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(80), nullable=True, default=None)
    #: Whether this operator may also manage other operators.
    is_superuser: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Deactivation rather than deletion is the reversible way to take access away; both the
    #: login path and every per-request check refuse an inactive account.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
