"""Wire shapes for the operator-facing agent configuration and cost report."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from app.domain.schemas.common import CamelModel
from app.domain.services.prompt_settings import MAX_PROMPT_CHARS


class ModelUsageRecord(CamelModel):
    """What one model has been asked to do, and what it cost."""

    model: str
    #: Display name, or the Persian "unknown" label for a model the catalog no longer knows.
    label: str
    turns: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    #: ``None`` — not zero — when the model carries no published price here. Zero would be a
    #: claim that the tokens were free, and the panel renders the difference.
    cost_usd: float | None = None


class UsageResponse(CamelModel):
    """Everything the assistant has spent since the database was created."""

    total_cost_usd: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    turns: int = 0
    by_model: list[ModelUsageRecord] = Field(default_factory=list)


class SettingRecord(CamelModel):
    """One setting as the panel sees it."""

    key: str
    #: Number, boolean, string, or the ordered list of models. ``bool`` leads and ``int``
    #: precedes ``float`` so a round count serializes as ``3`` rather than ``3.0`` — the form
    #: puts this straight into a number input and sends it back unchanged.
    value: bool | int | float | str | list[str]
    #: ``"db"`` when a stored value is in force, ``"default"`` when nothing is stored and the
    #: compiled-in value applies. Note this is no longer ``"env"``: after the first boot,
    #: ``.env`` is not consulted for these at all.
    source: str
    min: float | None = None
    max: float | None = None
    #: The allowed values, for the settings that are a fixed list rather than a range. Lets
    #: the panel render a real picker without hardcoding the options on its own side.
    options: list[str] | None = None
    #: True when part of the change only lands after the process restarts. Exactly one
    #: setting is honest about this today, and the panel says so next to it.
    restart_required: bool = False


class AdminSettingsResponse(CamelModel):
    """Every setting, with the value currently in force and where it came from."""

    settings: list[SettingRecord]
    #: Keys the server refused on the way in — out of range, not on the allowlist, wrong
    #: shape. Present because silently dropping half a save and answering 200 tells the
    #: operator their change landed when it did not.
    rejected: list[str] = []


class AdminSettingsUpdate(CamelModel):
    """PUT body. Every field is optional; ``null`` reverts a knob to its ``.env`` default.

    Unknown fields are refused outright rather than ignored — this is the one write path
    into the agent's own budgets, so a typo must be loud, not silently discarded. Ranges are
    re-checked server-side in ``agent_settings.clean_overrides`` regardless of what these
    annotations allow: the schema is the first gate, never the only one.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    tool_rounds: int | None = Field(default=None, ge=1, le=6)
    model_primary: str | None = Field(default=None, max_length=64)
    model_fallback: str | None = Field(default=None, max_length=64)
    reasoning_effort: str | None = Field(default=None, max_length=16)
    agent_mode: bool | None = None
    speculative_retrieval: bool | None = None
    stream_enabled: bool | None = None
    log_level: str | None = Field(default=None, max_length=16)
    retry_tool_rounds: int | None = Field(default=None, ge=0, le=4)
    ladder: list[str] | None = Field(default=None, max_length=8)
    max_model_attempts: int | None = Field(default=None, ge=1, le=5)
    turn_budget_seconds: float | None = Field(default=None, ge=15, le=300)
    max_llm_calls: int | None = Field(default=None, ge=1, le=30)
    escalation: bool | None = None
    attempt_timeout_seconds: float | None = Field(default=None, ge=5, le=120)
    auto_title: bool | None = None
    handoff_enabled: bool | None = None
    handoff_after: int | None = Field(default=None, ge=2, le=5)


class PromptRecord(CamelModel):
    """One editable prompt as the panel sees it."""

    key: str
    #: The text in force — the stored one only when the loader accepted it.
    text: str
    #: The compiled-in text, so the screen can offer "revert" without a second request and
    #: can show what "changed" is measured against.
    default: str
    #: ``"db"`` when the text differs from the compiled default, ``"default"`` otherwise.
    #: Never "a row exists": a row holding a rejected text is not in force.
    source: str
    max_chars: int = MAX_PROMPT_CHARS
    #: The sentences an edit may not remove, published so the operator reads the contract
    #: before breaking it and so the panel hardcodes none of it.
    required_fragments: list[str] = Field(default_factory=list)
    forbidden_fragments: list[str] = Field(default_factory=list)


class PromptRejection(CamelModel):
    """One prompt the server refused, and why.

    A flat list of key names is what the settings screen gets away with; for a prompt the
    reason *is* the message, because "which rule did I delete" is the only useful answer.
    """

    key: str
    #: Reason codes — ``too_long``, ``tool_not_named:<name>``, ``missing_fragment:<text>``,
    #: ``forbidden_fragment:<text>``. The fragment is the compiled-in one, never an echo of
    #: what was submitted.
    reasons: list[str] = Field(default_factory=list)


class AdminPromptsResponse(CamelModel):
    """Every editable prompt, with the text currently in force."""

    prompts: list[PromptRecord]
    rejected: list[PromptRejection] = []


class AdminPromptsUpdate(CamelModel):
    """PUT body. Every field is optional; ``null`` or blank reverts to the compiled default.

    Unknown fields are refused outright rather than ignored — this is the write path into the
    text that carries every rule the assistant answers under, so a typo must be loud. The
    length bound here is the first gate, never the only one: ``prompt_settings.check`` runs
    again on read, so a row written by hand cannot ship a prompt with a rule missing.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    system: str | None = Field(default=None, max_length=MAX_PROMPT_CHARS)
    final_round: str | None = Field(default=None, max_length=MAX_PROMPT_CHARS)
    wizard: str | None = Field(default=None, max_length=MAX_PROMPT_CHARS)
    handoff: str | None = Field(default=None, max_length=MAX_PROMPT_CHARS)
    handoff_phrases: str | None = Field(default=None, max_length=MAX_PROMPT_CHARS)
