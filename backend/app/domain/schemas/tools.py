"""Payloads for the two standalone wizards: config generation and log diagnosis."""

from __future__ import annotations

from pydantic import Field, field_validator

from app.domain.schemas.common import CamelModel
from app.domain.schemas.conversation import SourceRef
from app.shared.constants import MAX_LOG_CHARS
from app.shared.enums import Platform

#: Free-text requirement lines are clamped, not rejected — the wizard stays forgiving.
MAX_NEED_CHARS = 80
MAX_NEEDS = 10


class ConfigRequest(CamelModel):
    """Ask for a `liara.json` tailored to a platform and a few stated requirements."""

    platform: Platform
    needs: list[str] | None = None

    @field_validator("needs")
    @classmethod
    def _clamp_needs(cls, value: list[str] | None) -> list[str] | None:
        """Drop blank entries, clamp each need to 80 chars and the list to 10 items."""
        if value is None:
            return None
        cleaned = [item.strip()[:MAX_NEED_CHARS] for item in value if item and item.strip()]
        return cleaned[:MAX_NEEDS]


class ConfigResponse(CamelModel):
    """The generated configuration plus the docs it was built from."""

    content: str
    sources: list[SourceRef]
    platform: Platform


class DiagnoseRequest(CamelModel):
    """A pasted build/runtime log to explain."""

    log: str = Field(min_length=5, max_length=MAX_LOG_CHARS)


class DiagnoseResponse(CamelModel):
    """The extracted error signature, the explanation, and its citations."""

    signature: str
    content: str
    sources: list[SourceRef]
