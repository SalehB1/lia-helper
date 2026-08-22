"""Chat request payloads."""

from __future__ import annotations

from pydantic import Field

from app.domain.schemas.common import CamelModel
from app.shared.constants import MAX_MESSAGE_CHARS


class ChatRequest(CamelModel):
    """One user turn. Omitting ``conversationUuid`` starts a new conversation.

    ``parentUuid`` places the turn in the message tree, and its three states are three
    different intents — *omitted* and *explicitly null* do not mean the same thing:

    * omitted, ``content`` set — a normal turn, appended under the active leaf.
    * a message uuid, ``content`` empty — regenerate: another answer under that same
      question, with no second copy of the question.
    * a message uuid or an explicit ``null``, ``content`` set — an edited question, stored
      as a sibling version of the original. ``null`` edits the first turn, which has no
      parent to name.

    ``content`` is therefore optional here and required by the service in every case but
    regenerate; the length clamp still applies at this boundary.

    ``supersedes`` names the message an edit is meant to *replace* rather than fork. The
    service honours it only for a question of the caller's own conversation that nothing
    hangs under — a dead attempt is not a version worth keeping — and ignores it otherwise,
    so an answered question always keeps its history.
    """

    conversation_uuid: str | None = None
    parent_uuid: str | None = None
    supersedes: str | None = None
    content: str = Field(default="", max_length=MAX_MESSAGE_CHARS)
