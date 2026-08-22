"""Application settings, read once from the environment via python-decouple.

Every value has a working default, so the app boots with no ``.env`` file and no
environment variables set. Key values are never logged or serialized.
"""

from __future__ import annotations

import logging
from pathlib import Path

from decouple import config as env

from app.shared.constants import (
    AGENT_MAX_LLM_CALLS,
    AGENT_MAX_MODEL_ATTEMPTS,
    AGENT_RETRY_TOOL_ROUNDS,
    AGENT_TOOL_ROUNDS,
    AGENT_TURN_BUDGET_SECONDS,
    LLM_ATTEMPT_TIMEOUT_SECONDS,
)
from app.shared.enums import ChatModel

#: The escalation ladder tried within one turn when a model cannot produce an answer.
#: Deliberately cross-family: an error pattern that traps one model is usually outside the
#: failure distribution of a model from another vendor, which is the entire reason a second
#: rung helps at all. Every id is re-checked against the ChatModel allowlist.
DEFAULT_LADDER = "gpt-5-mini,claude-haiku-4-5,gpt-4.1-mini"

#: Model used on the fallback providers, which have their own id namespace.
DEFAULT_FALLBACK_MODEL = "google/gemini-2.5-flash"

#: Used when MODEL_PRIMARY names a model the provider does not serve.
#: GPT-5 Mini is the default because the provider's own prices make it both newer and
#: cheaper than GPT-4.1 Mini for this input-heavy RAG workload (~$1.18 vs $1.48 per 1k
#: turns) — see app/shared/model_catalog.py.
DEFAULT_MODEL = ChatModel.GPT_5_MINI.value


def _csv(raw: str) -> list[str]:
    """Split a comma-separated environment value into a clean list."""
    return [item.strip() for item in raw.split(",") if item.strip()]


def _origins(raw: str) -> list[str]:
    """Split and normalize an origin allowlist.

    An origin is compared byte-for-byte against the browser's ``Origin`` header, which is
    always lowercase and never carries a trailing slash. Normalizing here means a value
    written as ``https://Panel.example.com/`` still matches instead of silently refusing
    every write from the real panel — a failure that reads like a broken session, not like
    a typo in an environment variable.
    """
    return [item.rstrip("/").lower() for item in _csv(raw)]


def _chat_model(raw: str) -> str:
    """Return ``raw`` when it is an allowlisted chat model, else the safe default.

    Booting with an id the provider will reject turns every chat into a 404 at the first
    token, so a typo in ``MODEL_PRIMARY`` degrades to a working model instead.
    """
    try:
        return ChatModel(raw).value
    except ValueError:
        # structlog is not configured yet at import time; stdlib logging is, and
        # app.core.logging imports this module, so it cannot be imported here.
        logging.getLogger("app.config").warning(
            "model_primary_invalid: %r is not an allowed chat model; using %r",
            raw,
            DEFAULT_MODEL,
        )
        return DEFAULT_MODEL


class Settings:
    """Runtime configuration for the backend."""

    def __init__(self) -> None:
        # AvalAI is the primary provider: one OpenAI-compatible endpoint serving chat AND
        # embeddings, reachable from Iranian infrastructure without a proxy.
        self.avalai_api_key: str = env("AVALAI_API_KEY", default="")
        self.avalai_base_url: str = env("AVALAI_BASE_URL", default="https://api.avalai.ir/v1")
        self.model_primary: str = _chat_model(env("MODEL_PRIMARY", default=DEFAULT_MODEL))
        # Optional extra providers, tried only after AvalAI fails.
        self.openrouter_api_key: str = env("OPENROUTER_API_KEY", default="")
        self.gemini_api_key: str = env("GEMINI_API_KEY", default="")
        self.model_fallback: str = env("MODEL_FALLBACK", default=DEFAULT_FALLBACK_MODEL)
        # The turn-level model ladder and its budgets. Read these through
        # `app.domain.services.agent_settings.agent_settings()`, never off this object:
        # the admin layer overlays stored values on top and only that accessor merges them.
        self.agent_model_ladder: list[str] = _csv(env("AGENT_MODEL_LADDER", default=DEFAULT_LADDER))
        self.agent_tool_rounds: int = env("AGENT_TOOL_ROUNDS", default=AGENT_TOOL_ROUNDS, cast=int)
        self.agent_retry_tool_rounds: int = env(
            "AGENT_RETRY_TOOL_ROUNDS", default=AGENT_RETRY_TOOL_ROUNDS, cast=int
        )
        self.agent_max_model_attempts: int = env(
            "AGENT_MAX_MODEL_ATTEMPTS", default=AGENT_MAX_MODEL_ATTEMPTS, cast=int
        )
        self.agent_turn_budget_seconds: float = env(
            "AGENT_TURN_BUDGET_SECONDS", default=AGENT_TURN_BUDGET_SECONDS, cast=float
        )
        self.agent_max_llm_calls: int = env(
            "AGENT_MAX_LLM_CALLS", default=AGENT_MAX_LLM_CALLS, cast=int
        )
        self.agent_escalation: bool = env("AGENT_ESCALATION", default=True, cast=bool)
        self.llm_attempt_timeout_seconds: float = env(
            "LLM_ATTEMPT_TIMEOUT_SECONDS", default=LLM_ATTEMPT_TIMEOUT_SECONDS, cast=float
        )
        # HS256 signing key for the panel's session cookie. There is no working default on
        # purpose: an unset secret means login refuses and every admin route 401s — a locked
        # door, never an open one.
        self.auth_secret: str = env("AUTH_SECRET", default="")
        # Cookie shape. Deliberately NOT database-backed: a wrong value here locks every
        # operator out of the only screen that could fix it, and the way back would be
        # sqlite3 on the mounted volume.
        self.cookie_secure: bool = env("COOKIE_SECURE", default=False, cast=bool)
        self.cookie_samesite: str = env("COOKIE_SAMESITE", default="lax").strip().lower()
        # First-boot superuser. Used ONLY while the users table is empty; changing the
        # password here does not rotate an existing account's password.
        self.bootstrap_admin_user: str = env("BOOTSTRAP_ADMIN_USER", default="admin")
        self.bootstrap_admin_password: str = env("BOOTSTRAP_ADMIN_PASSWORD", default="")
        # How hard a GPT-5-family model thinks before it writes. Measured on this corpus
        # against AvalAI: minimal 4.5s / 0 reasoning tokens, low 6.2s / 192, medium 10.7s /
        # 640 — and a turn spends this three times over. The documents are already in front
        # of the model by then, so deep reasoning buys little. Ignored by every other model
        # family, which would reject the parameter. Empty disables it.
        self.reasoning_effort: str = env("REASONING_EFFORT", default="low").strip().lower()
        # Embeddings always go through the OpenAI-compatible /embeddings endpoint. The model
        # here MUST match the one that built data/embeddings.npz — see the dimension guard in
        # RetrievalService._load_matrix.
        self.embed_model: str = env("EMBED_MODEL", default="text-embedding-3-small")
        self.embed_dim: int = env("EMBED_DIM", default=1536, cast=int)
        self.allowed_origins: list[str] = _origins(
            env("ALLOWED_ORIGINS", default="http://localhost:3000")
        )
        self.database_path: str = env("DATABASE_PATH", default="./storage/app.db")
        self.data_dir: str = env("DATA_DIR", default="./data")
        self.agent_mode: bool = env("AGENT_MODE", default=True, cast=bool)
        self.stream_enabled: bool = env("STREAM_ENABLED", default=True, cast=bool)
        self.log_level: str = env("LOG_LEVEL", default="INFO")
        # Hosts that must never be sent through a local proxy. Measured here: the same
        # payload-free GET is ~10x slower through the shell's proxy than direct, and hangs
        # outright about one time in five — which is what two 60s LLM attempts were really
        # spending their time on. AvalAI is reachable directly from Iranian networks, so it
        # is excluded; the fallback providers are exactly the ones a local proxy exists for
        # and stay proxied. Exported to the process environment at startup (see main.py),
        # because httpx reads NO_PROXY from there rather than from this object.
        self.no_proxy_hosts: list[str] = _csv(env("NO_PROXY_HOSTS", default="api.avalai.ir"))

    @property
    def database_file(self) -> Path:
        """Absolute path of the sqlite file, resolved from a possibly relative setting."""
        return Path(self.database_path).expanduser().resolve()

    @property
    def database_url(self) -> str:
        """SQLAlchemy async URL for the sqlite database."""
        return f"sqlite+aiosqlite:///{self.database_file}"

    @property
    def has_llm(self) -> bool:
        """Whether at least one chat provider key is configured."""
        return bool(self.avalai_api_key or self.openrouter_api_key or self.gemini_api_key)

    @property
    def has_embeddings(self) -> bool:
        """Whether query embedding is possible.

        Only AvalAI serves the OpenAI-compatible ``/embeddings`` endpoint this app uses, so
        without its key retrieval runs BM25-only regardless of the other provider keys.
        """
        return bool(self.avalai_api_key)

    def __repr__(self) -> str:
        """Render settings without ever exposing key material."""
        return (
            f"Settings(model_primary={self.model_primary!r}, "
            f"embed_model={self.embed_model!r}, data_dir={self.data_dir!r}, "
            f"agent_mode={self.agent_mode!r}, has_llm={self.has_llm!r}, "
            f"has_embeddings={self.has_embeddings!r})"
        )


settings = Settings()

# `allow_credentials=True` (required for the session cookie) plus a wildcard origin makes
# Starlette answer `*`, which every browser refuses for a credentialed request — the panel
# would fail every call in a way that reads like a cookie bug. Fail at import instead.
if "*" in settings.allowed_origins:
    raise RuntimeError("ALLOWED_ORIGINS must be an explicit origin list, never '*'")

# Each of the three below produces a deployment that looks healthy and is completely
# unusable, in a way whose symptom points somewhere else entirely. Refusing to start is the
# only honest failure mode: the whole app is now behind the login these values control.

# Starlette only asserts this deep inside `set_cookie`, so a typo surfaces as a generic
# Persian 500 on sign-in rather than as a configuration error.
if settings.cookie_samesite not in ("lax", "strict", "none"):
    raise RuntimeError("COOKIE_SAMESITE must be one of: lax, strict, none")

# Browsers silently drop a SameSite=None cookie that is not Secure. Sign-in returns 200, the
# cookie never lands, and every following request is a 401 — which reads as a broken session
# store, not as a missing flag.
if settings.cookie_samesite == "none" and not settings.cookie_secure:
    raise RuntimeError("COOKIE_SAMESITE=none requires COOKIE_SECURE=true")

# Without a signing secret `read_token` returns None for every cookie, so nobody can sign in
# at all. That used to close only the admin screens; now it closes the entire product.
# `cookie_secure` is the marker for a real deployment — localhost keeps working unsecured.
if settings.cookie_secure and not settings.auth_secret:
    raise RuntimeError("AUTH_SECRET must be set when COOKIE_SECURE is true")
