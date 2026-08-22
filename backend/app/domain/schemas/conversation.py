"""Conversation and message response payloads."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import Field, ValidationError

from app.domain.models.message import Message
from app.domain.schemas.common import CamelModel
from app.shared.constants import MAX_TITLE_CHARS, MAX_VERSIONS
from app.shared.enums import MessageRole


def _json_list(raw: str | None) -> list[Any]:
    """Decode a JSON side-car column into a list, never raising.

    Args:
        raw: Raw column value; may be NULL or corrupt.

    Returns:
        The decoded list, or an empty list for anything unusable.
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return data if isinstance(data, list) else []


class SourceRef(CamelModel):
    """One numbered citation as rendered next to an answer."""

    n: int
    title: str
    url: str
    heading: str | None = None


class MessageResponse(CamelModel):
    """A conversation turn as served to the panel.

    ``versionIndex``/``versionCount`` are this message's place among its sibling versions —
    what the panel renders as «۲ / ۳» under a regenerated answer or an edited question.
    """

    uuid: str
    role: MessageRole
    content: str
    created_at: datetime
    parent_uuid: str | None = None
    version_index: int = 1
    version_count: int = 1
    sources: list[SourceRef] = Field(default_factory=list)
    tool_trace: list[dict] = Field(default_factory=list)

    @classmethod
    def from_model(
        cls,
        message: Message,
        parent_uuid: str | None = None,
        version_index: int = 1,
        version_count: int = 1,
    ) -> MessageResponse:
        """Build a response from an ORM row, parsing its JSON columns defensively.

        Malformed or NULL ``sources_json``/``tool_trace_json`` degrade to empty lists;
        individual entries that do not fit the schema are dropped rather than raising.

        Args:
            message: The ORM row to convert.
            parent_uuid: Public uuid of the message this one follows, if any.
            version_index: 1-based position among its sibling versions.
            version_count: How many versions that group holds.

        Returns:
            The serializable message.
        """
        sources: list[SourceRef] = []
        for entry in _json_list(message.sources_json):
            if not isinstance(entry, dict):
                continue
            try:
                sources.append(SourceRef.model_validate(entry))
            except ValidationError:
                continue

        return cls(
            uuid=message.uuid,
            role=message.role,
            content=message.content,
            created_at=message.created_at,
            parent_uuid=parent_uuid,
            version_index=version_index,
            version_count=version_count,
            sources=sources,
            tool_trace=[e for e in _json_list(message.tool_trace_json) if isinstance(e, dict)],
        )


class ConversationResponse(CamelModel):
    """A conversation in list views."""

    uuid: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class ConversationDetailResponse(ConversationResponse):
    """A conversation together with the message branch currently selected.

    Not every row: abandoned versions and everything written under them stay out of
    ``messages``. They are reachable by switching to them with ``PATCH .../active``.
    """

    messages: list[MessageResponse]
    #: Whether a turn of this caller's is generating in this conversation right now, so
    #: the panel knows to re-attach to it instead of drawing an unanswered question.
    #: Unfinished runs only — reporting a finished one would make an answer already on
    #: screen re-type itself every time the conversation is opened.
    active_run: bool = False


class ConversationRenameRequest(CamelModel):
    """PATCH body for a rename. One field, and the same name in both cases.

    The bound mirrors ``MAX_TITLE_CHARS`` and the column, but it is the first gate and not
    the only one: ``_clean_title`` runs again in the repository, so a title written by any
    other path is sanitised identically.
    """

    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)


class ActiveMessageRequest(CamelModel):
    """Which version to switch a conversation's displayed branch to.

    ``versionIndex`` selects a sibling of ``messageUuid`` by its 1-based slot. The branch
    response only ever carries the version currently on screen, so a client stepping
    through versions knows the slot it wants but not that sibling's uuid; without this it
    would need a second round trip just to learn it.
    """

    message_uuid: str = Field(min_length=1, max_length=36)
    version_index: int | None = Field(default=None, ge=1, le=MAX_VERSIONS)
