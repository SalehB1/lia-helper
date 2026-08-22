"""Operator-editable overrides for the agent knobs that ``.env`` only sets defaults for."""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.base import Base, TimestampMixin, UUIDMixin


class AppSetting(Base, UUIDMixin, TimestampMixin):
    """One stored override, keyed by name and holding a JSON-encoded value.

    A key/value table rather than a column per knob: ``init_db`` runs ``create_all`` and
    has no migration tool, so adding a knob must never mean altering a table. An absent row
    means "use the ``.env`` default" — deleting a row is how an override is undone.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    #: ``json.dumps`` of the value. Typed on the way out by ``AgentSettings``, so a row
    #: written by an older build with a since-changed shape degrades to the default.
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
