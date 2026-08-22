"""The one gate the three editable prompts are read through.

A deliberate mirror of :mod:`agent_settings`: same table, same repository, same cache shape,
same "validate on read as well as on write" posture. The three texts live in ``app_settings``
under ``prompt.system`` / ``prompt.final_round`` / ``prompt.wizard``, so no new table, no new
model and no migration exist for this feature.

**Not seeded on first boot, and that diverges from `seed_from_env` on purpose.** There is no
``.env`` to seed from, and a seeded copy of today's text would *freeze* it: a prompt improved
in a later code release would never reach an operator who had never edited anything. So an
absent row means "use the compiled default", a code-released improvement lands for everyone
who has not overridden, and an operator who has overridden stays pinned to their own text —
which is what an override means.

**Validation here is a mistake-catcher, not a security boundary.** The boundary is
``require_superuser`` on the admin router; the prompt was always advisory. What keeps working
no matter what an operator writes is the four non-prompt layers: ``_claims_limit`` in
``chat_service`` (never-say-limit), ``CitationRegistry.assign`` (a model-invented ``[9]``
resolves to nothing), ``tools_service._neutralize`` (the ``<docs>`` envelope cannot be closed
early) and the ``https://docs.liara.ir/`` allowlist at ingest and at load plus ``safeHref()``
at the sink. A deleted citation rule degrades citation *coverage*; it cannot mint a citation.
What this module does catch is the accident — a rule deleted while rewording, a tool the model
is handed but no longer told about, a paste that lost the suggestion marker.

What it cannot catch, and nothing cheap can: a prompt that keeps every required fragment and
then appends «قوانین بالا را نادیده بگیر», a rule buried under 5 KB of new text, or an addition
that wrecks the answer path without deleting anything. The detector for those is
``tests.eval_faithfulness`` — its ``correct-refusal`` ratio is the number that moves.

Stored text is never formatted; see the note in :mod:`prompts`.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from dataclasses import dataclass

from pydantic.alias_generators import to_camel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.domain.repositories.settings_repo import SettingsRepository
from app.domain.services.prompts import (
    FINAL_ROUND_INSTRUCTION,
    FORBIDDEN_FRAGMENTS,
    HANDOFF_INVITATION,
    HANDOFF_PHRASES,
    REQUIRED_FRAGMENTS,
    SYSTEM_PROMPT,
    WIZARD_SYSTEM_PROMPT,
)
from app.domain.services.tools_service import TOOL_SCHEMAS

logger = get_logger("prompt_settings")

#: The editable texts, by the field name of :class:`Prompts`. The last two are not prompts
#: in the "rules the assistant answers under" sense — one is the support copy the handoff
#: policy hands the model, the other is the word list that policy triggers on — but they are
#: operator-written free text with the same lifecycle (absent row = compiled default, blank =
#: revert, validated on read as well as write), so they live here rather than in a second
#: store that would be this one with the names changed. `handoff` is the invitation copy;
#: the compiled wrapper that hands it to the model stays in `prompts.py`, uneditable.
PROMPT_KEYS: tuple[str, ...] = ("system", "final_round", "wizard", "handoff", "handoff_phrases")

#: Namespaces these rows away from the ``AgentSettings`` field names in the shared table.
#: ``clean_overrides`` drops unknown keys, so an older build reading a database with these
#: rows in it is unaffected — rollback is a redeploy, not a data fix.
STORAGE_PREFIX = "prompt."

#: Roughly 2.5x the longest compiled default. A ceiling rather than a tight bound: the point
#: is that one row cannot become an unbounded per-turn token bill, not to police style.
MAX_PROMPT_CHARS = 20_000


@dataclass(frozen=True, slots=True)
class Prompts:
    """The prompt texts in force for one turn."""

    #: The chat system prompt — every rule the assistant answers under.
    system: str
    #: Appended to the closing, toolless call of one model attempt.
    final_round: str
    #: The one-shot prompt both wizards run under.
    wizard: str
    #: Where a stuck user is invited to go, in the assistant's own voice.
    handoff: str
    #: The wording that counts a user's message as unhappy, one phrase per line.
    handoff_phrases: str


def _defaults() -> Prompts:
    """The compiled-in texts — what a prompt reverts to when its stored row is removed."""
    return Prompts(
        system=SYSTEM_PROMPT,
        final_round=FINAL_ROUND_INSTRUCTION,
        wizard=WIZARD_SYSTEM_PROMPT,
        handoff=HANDOFF_INVITATION,
        handoff_phrases=HANDOFF_PHRASES,
    )


def _flat(text: str) -> str:
    """Collapse runs of whitespace so a fragment survives being rewrapped.

    Without this the contract would refuse a legal reflow of the very sentence it is
    protecting: the fragment «از خودت نساز» sits across a line break in the compiled default.
    """
    return re.sub(r"\s+", " ", text)


def digest(text: str) -> str:
    """Short content fingerprint of a prompt, for the audit trail.

    The prompt itself is never logged — it is 7 KB of the verbatim wording of every internal
    rule, and the log level is settable from the panel. A digest makes "which text went live
    when, and which answers came out of it" answerable without writing the text anywhere.
    """
    return hashlib.blake2s(text.encode("utf-8"), digest_size=8).hexdigest()


def check(key: str, text: str) -> list[str]:
    """Report every reason this text may not be used as the ``key`` prompt.

    Args:
        key: One of :data:`PROMPT_KEYS`.
        text: Candidate prompt text, from a request body or from a stored row.

    Returns:
        Machine-readable reason codes, empty when the text is acceptable. Reason codes carry
        the compiled-in fragment that is missing, never any part of the submitted text.
    """
    if key not in PROMPT_KEYS:
        return ["unknown_key"]
    reasons: list[str] = []
    if len(text) > MAX_PROMPT_CHARS:
        reasons.append("too_long")
    if key == "system":
        # Derived from the payload rather than listed, so a tool added later is required in
        # the prompt from the day it ships. A tool the model is handed but never told about
        # is a tool it will not call.
        for schema in TOOL_SCHEMAS:
            name = schema["function"]["name"]
            if name not in text:
                reasons.append(f"tool_not_named:{name}")
    flat = _flat(text)
    for fragment in REQUIRED_FRAGMENTS.get(key, ()):
        if _flat(fragment) not in flat:
            reasons.append(f"missing_fragment:{fragment}")
    for fragment in FORBIDDEN_FRAGMENTS.get(key, ()):
        if _flat(fragment) in flat:
            reasons.append(f"forbidden_fragment:{fragment}")
    return reasons


def review(candidates: dict) -> tuple[dict, list[dict]]:
    """Split candidate prompts into what may be applied and what must be refused.

    Args:
        candidates: ``{key: text or None}``, keys as :class:`Prompts` field names.

    Returns:
        ``(accepted, rejections)``. ``accepted`` maps a key to its text, or to ``None`` for
        "revert to the compiled default" — which is what an empty or whitespace-only value
        means, matching how the settings screen treats a cleared field. ``rejections`` is one
        ``{"key", "reasons"}`` record per refused prompt.
    """
    accepted: dict = {}
    rejections: list[dict] = []
    for key, value in candidates.items():
        if key not in PROMPT_KEYS:
            continue
        if value is None or not isinstance(value, str) or not value.strip():
            accepted[key] = None
            continue
        reasons = check(key, value)
        if reasons:
            rejections.append({"key": key, "reasons": reasons})
        else:
            accepted[key] = value
    return accepted, rejections


def clean_prompts(stored: dict) -> dict:
    """Keep only the stored rows that are still a legal prompt.

    Runs on the read path as well as the write path, so a row written by hand with sqlite3 —
    or by a build whose contract has since gained a rule — degrades to the compiled default
    instead of shipping a prompt with a safety rule missing.

    Args:
        stored: Everything in ``app_settings``, prefixed keys and all.

    Returns:
        ``{key: text}`` for the prompts that may be applied. Never contains an empty string.
    """
    candidates = {
        key: stored[f"{STORAGE_PREFIX}{key}"]
        for key in PROMPT_KEYS
        if f"{STORAGE_PREFIX}{key}" in stored
    }
    accepted, rejections = review(candidates)
    for rejection in rejections:
        # Reason codes only. The row's text is operator input and 7 KB long.
        logger.warning("stored_prompt_rejected", key=rejection["key"], reasons=rejection["reasons"])
    return {key: text for key, text in accepted.items() if text}


#: Process-local cache of the validated stored prompts, dropped by the admin write path.
#: Like the agent-settings snapshot this is safe only because the app runs one worker.
_cache: dict | None = None


def invalidate() -> None:
    """Drop the cached prompts so the next read goes back to the database."""
    global _cache
    _cache = None


async def prompts_for(db: AsyncSession) -> Prompts:
    """Return the prompts in force: compiled defaults with valid stored rows applied.

    There is deliberately no synchronous snapshot to pair with this, as there is for the
    agent settings: both readers — the chat turn and the wizards — already have a session.

    Args:
        db: The turn's session. Used only on a cache miss.

    Returns:
        The effective texts. A database that cannot be read yields the compiled defaults;
        this runs on the chat path, so it degrades rather than raising.
    """
    global _cache
    if _cache is None:
        try:
            _cache = clean_prompts(await SettingsRepository(db).get_all())
        except Exception:  # noqa: BLE001 - the chat path must never die on configuration
            logger.exception("prompts_unreadable")
            return _defaults()
    return dataclasses.replace(_defaults(), **_cache)


async def refresh(db: AsyncSession) -> Prompts:
    """Re-read the stored prompts after a write, so the next turn sees them."""
    invalidate()
    return await prompts_for(db)


def describe(current: Prompts) -> list[dict]:
    """Render the prompts for the admin screen, each tagged with where its text came from.

    Args:
        current: The prompts in force.

    Returns:
        One record per prompt, ``key`` camelCase — the spelling the update schema accepts,
        so the screen can send back exactly what it read.
    """
    defaults = _defaults()
    described: list[dict] = []
    for key in PROMPT_KEYS:
        # The EFFECTIVE text, never the stored one: a row the loader rejected must not be
        # shown as if it were in force. "Changed" therefore means "differs from the compiled
        # default", never "a row exists".
        text = getattr(current, key)
        default = getattr(defaults, key)
        described.append(
            {
                "key": to_camel(key),
                "text": text,
                "default": default,
                "source": "db" if text != default else "default",
                "maxChars": MAX_PROMPT_CHARS,
                # Published so the operator reads the contract before breaking it, and so
                # the panel hardcodes none of it.
                "requiredFragments": list(REQUIRED_FRAGMENTS.get(key, ())),
                "forbiddenFragments": list(FORBIDDEN_FRAGMENTS.get(key, ())),
            }
        )
    return described
