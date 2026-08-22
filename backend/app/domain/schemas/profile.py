"""The tiny per-session memory the assistant personalizes answers with."""

from __future__ import annotations

from pydantic import Field

from app.domain.schemas.common import CamelModel

#: Mirrors ``PROFILE_VALUE_CHARS`` in the session repository.
MAX_PROFILE_VALUE_CHARS = 80


class ProfileUpdate(CamelModel):
    """PATCH body. Omitted fields stay as they are; an empty string clears a field."""

    platform: str | None = Field(default=None, max_length=MAX_PROFILE_VALUE_CHARS)
    framework: str | None = Field(default=None, max_length=MAX_PROFILE_VALUE_CHARS)
    notes: str | None = Field(default=None, max_length=MAX_PROFILE_VALUE_CHARS)


class ProfileResponse(CamelModel):
    """The stored profile for the current session."""

    platform: str | None = None
    framework: str | None = None
    notes: str | None = None
