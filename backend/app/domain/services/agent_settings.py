"""The one gate every tunable setting is read through.

**The database is the source of truth.** ``.env`` seeds these values the first time the
database is created and is never consulted for them again; after that an operator changes
them from the panel and the change applies to the next answer, with no restart. Nothing else
in the app may read one of these off the ``settings`` object — going around this module is
exactly how a stored value silently stops applying to half the code.

What deliberately did NOT move, and why, is documented in `.env.example`: API keys (secrets),
the cookie flags and `ALLOWED_ORIGINS` (a wrong value locks every operator out of the only
screen that could fix it), `DATABASE_PATH` (it is the path to the database that would hold
the setting), and `EMBED_MODEL`/`EMBED_DIM` (properties of a file on disk, not preferences —
a mismatched pair returns silently nonsensical rankings rather than an error).

Validation lives here rather than in the endpoint on purpose: the schema and the loader must
agree about what a legal value is, and a row written by an older build — or by hand with
sqlite3 — must degrade to the default rather than take a chat turn down with it.

Two readers, one meaning: :func:`agent_settings` is the async, always-fresh read used by the
chat path, and :func:`effective` is a synchronous snapshot for the handful of call sites that
have no database session in scope. Both return the same object; the snapshot is refreshed at
startup, on every admin write, and on every turn.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass

from pydantic.alias_generators import to_camel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import DEFAULT_FALLBACK_MODEL, DEFAULT_LADDER, DEFAULT_MODEL, settings
from app.core.logging import get_logger
from app.domain.repositories.settings_repo import SettingsRepository
from app.shared.constants import (
    AGENT_MAX_LLM_CALLS,
    AGENT_MAX_MODEL_ATTEMPTS,
    AGENT_RETRY_TOOL_ROUNDS,
    AGENT_TOOL_ROUNDS,
    AGENT_TURN_BUDGET_SECONDS,
    LLM_ATTEMPT_TIMEOUT_SECONDS,
)
from app.shared.model_catalog import list_models, valid_model

logger = get_logger("agent_settings")

#: Bounds on every operator-settable number. A knob outside its range is not a preference,
#: it is a way to turn one chat turn into an unbounded spend, so the range is enforced on
#: read as well as on write.
BOUNDS: dict[str, tuple[float, float]] = {
    "tool_rounds": (1, 6),
    "retry_tool_rounds": (0, 4),
    "max_model_attempts": (1, 5),
    "turn_budget_seconds": (15.0, 300.0),
    "max_llm_calls": (1, 30),
    "attempt_timeout_seconds": (5.0, 120.0),
    # The floor of 2 is load-bearing, not a preference. `_cache_key` refuses any turn whose
    # branch is longer than the question just written, so a policy that cannot fire before the
    # user's second message can never interact with the answer cache — no cached reply can
    # arrive carrying a handoff, or arrive missing one it should have had. Lowering this to 1
    # would reopen that quietly, on the replay path where it is hardest to notice.
    "handoff_after": (2, 5),
}


@dataclass(frozen=True, slots=True)
class AgentSettings:
    """The effective agent knobs for one turn."""

    #: Tool rounds granted to the first model of the ladder.
    tool_rounds: int
    #: Tool rounds granted to each escalated attempt; the earlier attempt's documents are
    #: already in ``messages``, so a retry rarely needs to search again.
    retry_tool_rounds: int
    #: Ordered model ids tried within one turn.
    ladder: tuple[str, ...]
    #: Ceiling on ladder rungs actually attempted, including the user's chosen model.
    max_model_attempts: int
    #: Wall-clock ceiling for the whole turn, checked before each call, never mid-stream.
    turn_budget_seconds: float
    #: Ceiling on LLM calls across every attempt of one turn.
    max_llm_calls: int
    #: When false the ladder is one rung long — today's behaviour, minus the wall.
    escalation: bool
    #: Per-call timeout handed to the provider client.
    attempt_timeout_seconds: float
    #: The model a session with no preference of its own gets, and rung zero of the ladder.
    model_primary: str
    #: The model used on the fallback providers. A different namespace from ``model_primary``
    #: — OpenRouter ids look like ``google/gemini-2.5-flash`` — so the ChatModel allowlist
    #: cannot validate it and a character whitelist does the job instead.
    model_fallback: str
    #: How hard a GPT-5-family model thinks before writing. Ignored by other families.
    reasoning_effort: str
    #: Whether the model chooses its own searches. Off means one fixed pre-retrieval instead.
    agent_mode: bool
    #: Whether the turn searches the user's own wording before the first model call, so the
    #: answer can come out of round zero instead of after a tool round nobody sees. Ignored
    #: when ``agent_mode`` is off — there the same pre-retrieval is the only retrieval there
    #: is. This is the documented off-switch for that behaviour.
    speculative_retrieval: bool
    #: Whether answers stream token by token or arrive as one response.
    stream_enabled: bool
    #: Verbosity of the application log. The web server's own access log is fixed at start.
    log_level: str
    #: Whether a brand-new conversation gets a short generated name after its first
    #: question. Off means it keeps the first few words of that question, which is what it
    #: had before this existed — the user can rename it by hand either way.
    auto_title: bool
    #: Whether a user who keeps saying their problem is unsolved gets pointed at a human.
    #: Off by default: it changes what the assistant says to users, so it is the operator's
    #: to switch on, not something a deploy turns on underneath them.
    handoff_enabled: bool
    #: How many unhappy messages from the user, in this conversation, before that happens.
    handoff_after: int


def _defaults() -> AgentSettings:
    """The compiled-in defaults — what a knob reverts to when its stored row is removed.

    Deliberately NOT read from ``settings``: once the database has been seeded, ``.env`` is
    no longer the source of truth for these, and reading it here would make "revert to
    default" mean two different things depending on how the file happened to be edited.
    """
    return AgentSettings(
        tool_rounds=AGENT_TOOL_ROUNDS,
        retry_tool_rounds=AGENT_RETRY_TOOL_ROUNDS,
        ladder=_clean_ladder(DEFAULT_LADDER) or (DEFAULT_MODEL,),
        max_model_attempts=AGENT_MAX_MODEL_ATTEMPTS,
        turn_budget_seconds=AGENT_TURN_BUDGET_SECONDS,
        max_llm_calls=AGENT_MAX_LLM_CALLS,
        escalation=True,
        attempt_timeout_seconds=LLM_ATTEMPT_TIMEOUT_SECONDS,
        model_primary=DEFAULT_MODEL,
        model_fallback=DEFAULT_FALLBACK_MODEL,
        reasoning_effort="low",
        agent_mode=True,
        speculative_retrieval=True,
        stream_enabled=True,
        log_level="INFO",
        auto_title=True,
        handoff_enabled=False,
        handoff_after=2,
    )


def _env_seed() -> dict:
    """What ``.env`` asks for, as overrides — used once, when the database is first created."""
    return clean_overrides(
        {
            "tool_rounds": settings.agent_tool_rounds,
            "retry_tool_rounds": settings.agent_retry_tool_rounds,
            "ladder": settings.agent_model_ladder,
            "max_model_attempts": settings.agent_max_model_attempts,
            "turn_budget_seconds": settings.agent_turn_budget_seconds,
            "max_llm_calls": settings.agent_max_llm_calls,
            "escalation": settings.agent_escalation,
            "attempt_timeout_seconds": settings.llm_attempt_timeout_seconds,
            "model_primary": settings.model_primary,
            "model_fallback": settings.model_fallback,
            "reasoning_effort": settings.reasoning_effort,
            "agent_mode": settings.agent_mode,
            "stream_enabled": settings.stream_enabled,
            "log_level": settings.log_level,
        }
    )


def _clean_ladder(raw: object) -> tuple[str, ...]:
    """Filter a ladder down to allowlisted model ids, order preserved and deduped.

    Args:
        raw: A list of ids, or a comma-separated string.

    Returns:
        The surviving ids. Empty when nothing survives, which the caller must treat as
        "no override" rather than "an empty ladder" — a turn with no models cannot run.
    """
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, (list, tuple)):
        return ()
    seen: list[str] = []
    for item in raw:
        model = valid_model(str(item).strip()) if item is not None else None
        if model and model not in seen:
            seen.append(model)
    return tuple(seen)


#: The knobs whose legal values are a fixed list rather than a range.
CHOICES: dict[str, tuple[str, ...]] = {
    "reasoning_effort": ("", "minimal", "low", "medium", "high"),
    "log_level": ("DEBUG", "INFO", "WARNING", "ERROR"),
}

#: OpenRouter and Gemini use their own model namespace (``google/gemini-2.5-flash``), so the
#: ChatModel allowlist would reject every legal value and a shape whitelist is the boundary
#: instead. Written as "segments joined by /" rather than as a bag of allowed characters:
#: the loose version accepts ``../../etc/passwd``, which is harmless where this value is used
#: today — a JSON field, never a path — but a whitelist that admits a traversal string is one
#: refactor away from being a real bug.
_FALLBACK_MODEL_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*$"
)
MAX_FALLBACK_MODEL_CHARS = 64


def _clean_choice(key: str, value: object) -> str | None:
    """Match a value against its allowed list, case- and space-insensitively."""
    text = str(value).strip()
    allowed = CHOICES[key]
    for option in allowed:
        if text.lower() == option.lower():
            return option
    return None


def clean_overrides(raw: dict) -> dict:
    """Keep only the overrides that are both known and in range.

    Unknown keys are dropped rather than rejected so a stored row from a newer build cannot
    break an older one, and so an operator's typo is visibly ignored instead of 500-ing.

    Args:
        raw: Candidate ``{key: value}`` pairs from a stored row or a request body.

    Returns:
        The subset safe to apply. ``None`` values survive: they mean "revert to default".
    """
    clean: dict = {}
    for key, value in raw.items():
        if value is None:
            if key in {field.name for field in dataclasses.fields(AgentSettings)}:
                clean[key] = None
            continue
        if key == "ladder":
            ladder = _clean_ladder(value)
            if ladder:
                clean[key] = ladder
            else:
                logger.warning("agent_setting_rejected", key=key, reason="no_allowlisted_model")
            continue
        if key in (
            "escalation",
            "agent_mode",
            "speculative_retrieval",
            "stream_enabled",
            "auto_title",
            "handoff_enabled",
        ):
            if isinstance(value, bool):
                clean[key] = value
            continue
        if key in CHOICES:
            choice = _clean_choice(key, value)
            if choice is None:
                logger.warning("agent_setting_rejected", key=key, reason="not_an_option")
            else:
                clean[key] = choice
            continue
        if key == "model_primary":
            model = valid_model(str(value).strip())
            if model is None:
                logger.warning("agent_setting_rejected", key=key, reason="not_allowlisted")
            else:
                clean[key] = model
            continue
        if key == "model_fallback":
            text = str(value).strip()
            if len(text) <= MAX_FALLBACK_MODEL_CHARS and _FALLBACK_MODEL_RE.match(text):
                clean[key] = text
            else:
                logger.warning("agent_setting_rejected", key=key, reason="bad_model_id")
            continue
        low, high = BOUNDS.get(key, (None, None))
        if low is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            logger.warning("agent_setting_rejected", key=key, reason="not_a_number")
            continue
        if not low <= value <= high:
            logger.warning("agent_setting_rejected", key=key, reason="out_of_range")
            continue
        clean[key] = float(value) if isinstance(low, float) else int(value)
    return clean


#: Process-local cache of the stored overrides. One row set is read per process rather than
#: per turn; `invalidate()` is called by the admin write path, which is the only writer.
_cache: dict | None = None

#: The merged settings as of the last read. Exists because four call sites — the provider
#: list, the model catalogue, the streaming gate and the startup banner — are synchronous and
#: have no database session in scope, and making them async would thread a session through
#: half the codebase. Refreshed at startup, after every admin write, and on every turn, so it
#: is never stale for longer than one answer.
#:
#: Process-local, which is safe only because the app runs a single worker — `Dockerfile` and
#: `run-server.py` both pin that, and this makes the pin load-bearing for configuration too.
_effective: AgentSettings = _defaults()


def invalidate() -> None:
    """Drop the cached overrides so the next read goes back to the database."""
    global _cache
    _cache = None


def effective() -> AgentSettings:
    """The settings in force, with no IO.

    For synchronous callers only. Never returns None and never blocks; the trade is that it
    can be up to one turn behind a change made in another request.
    """
    return _effective


async def agent_settings(db: AsyncSession) -> AgentSettings:
    """Return the effective knobs: ``.env`` defaults with stored overrides applied.

    Args:
        db: The turn's session. Used only on a cache miss.

    Returns:
        The merged settings. A database that cannot be read yields the defaults — this runs
        on the chat path, so it degrades rather than raising.
    """
    global _cache, _effective
    if _cache is None:
        try:
            _cache = clean_overrides(await SettingsRepository(db).get_all())
        except Exception:  # noqa: BLE001 - the chat path must never die on configuration
            logger.exception("agent_settings_unreadable")
            return _effective
    overrides = {key: value for key, value in _cache.items() if value is not None}
    _effective = dataclasses.replace(_defaults(), **overrides)
    return _effective


def describe(current: AgentSettings, overrides: dict) -> list[dict]:
    """Render the knobs for the admin screen, each tagged with where its value came from.

    Args:
        current: The merged settings.
        overrides: The stored overrides that produced them.

    Returns:
        One record per setting, ``key`` camelCase — the same spelling the update schema
        accepts, so the screen can send back exactly what it read.
    """
    described: list[dict] = []
    defaults = _defaults()
    for field in dataclasses.fields(AgentSettings):
        low, high = BOUNDS.get(field.name, (None, None))
        value = getattr(current, field.name)
        # "Changed" means "differs from the compiled default", NOT "has a row": every setting
        # has a row once the database is seeded, so row-existence says nothing.
        changed = value != getattr(defaults, field.name)
        described.append(
            {
                "key": to_camel(field.name),
                # The EFFECTIVE value, never the stored one: a row the loader rejected must
                # not be shown as if it were in force. The badge would otherwise claim a
                # setting had been changed while the assistant ignored it.
                "value": list(value) if isinstance(value, tuple) else value,
                "source": "db" if changed else "default",
                "min": low,
                "max": high,
                "options": _options(field.name),
                # structlog picks a new level up at once; the web server's access log does
                # not, and saying so is more useful than pretending the change is complete.
                "restartRequired": field.name == "log_level",
            }
        )
    return described


def _options(name: str) -> list[str] | None:
    """The allowed values for a setting whose domain is a list, else None.

    ``model_primary`` is the whole allowlist, cheapest first, so the panel renders a real
    picker instead of a free-text box an operator can typo a model id into. The ids are
    shown raw and untranslated on purpose: an operator setting a model is choosing a
    provider id, and `ladder` beside it is spelled the same way.

    Args:
        name: An :class:`AgentSettings` field name.

    Returns:
        The options, or None when the setting is a range or free text.
    """
    if name == "model_primary":
        # Called at request time, never at import: `list_models` reaches back into this
        # module for `effective()`, and at module scope that pair is a circular import.
        return [record["id"] for record in list_models()]
    return list(CHOICES[name]) if name in CHOICES else None


async def stored_overrides(db: AsyncSession) -> dict:
    """Return the validated stored overrides, for the admin read path."""
    return clean_overrides(await SettingsRepository(db).get_all())


async def seed_from_env(db: AsyncSession) -> None:
    """Write the ``.env`` values into the database the first time it is created.

    **Every** setting gets a row, not only the ones that differ from the compiled default:
    the database is meant to be the whole picture, so that opening ``app_settings`` shows
    what the assistant is actually configured to do rather than a sparse diff. Whether a value
    counts as "changed" is therefore decided by comparing it to the default, not by whether a
    row happens to exist.

    Runs once per key, ever: ``AppSetting.key`` is unique, so "does a row exist?" is the whole
    idempotency check and no seeded-marker table is needed. After this, ``.env`` is never read
    for these values again — the panel is the way to change them, and editing the file has no
    effect.

    Args:
        db: A session to write with. The caller commits.
    """
    stored = await SettingsRepository(db).get_all()
    seed = {**dataclasses.asdict(_defaults()), **_env_seed()}
    pending = {
        key: (list(value) if isinstance(value, tuple) else value)
        for key, value in seed.items()
        if key not in stored and value is not None
    }
    if not pending:
        return
    await SettingsRepository(db).upsert(pending)
    invalidate()
    logger.info("settings_seeded_from_env", keys=sorted(pending))


async def refresh(db: AsyncSession) -> AgentSettings:
    """Re-read the stored settings and republish the synchronous snapshot."""
    invalidate()
    return await agent_settings(db)
