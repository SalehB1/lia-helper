"""ASGI entrypoint for the Liara Docs Assistant API.

Boot is deliberately forgiving: neither a broken database nor a missing documentation
corpus stops the process from starting. Both failures are logged and then reported by
`/healthz`, which is the only place an operator should have to look.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response

from app.api import limiter
from app.api.router import api_router
from app.api.v1.endpoints import health
from app.core.config import settings
from app.domain.services import chat_runs
from app.core.database import AsyncSessionLocal, init_db
from app.core.exception_handlers import error_response, register_exception_handlers
from app.core.logging import RequestLoggingMiddleware, configure_logging, get_logger
from app.domain.services.agent_settings import refresh, seed_from_env
from app.domain.services.auth_service import ensure_bootstrap_user
from app.domain.services.retrieval_service import retrieval_service
from app.shared.constants import MAX_BODY_BYTES

configure_logging()
logger = get_logger("app.main")

TITLE = "Liara Docs Assistant API"
DESCRIPTION = "دستیار فارسی مستندات لیارا — جست‌وجوی ترکیبی، پاسخ استنادشده و دو جادوگر ابزار."
VERSION = "1.0.0"

ALLOWED_METHODS = ["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"]
# "X-Session-Id" is dead — nothing reads it any more — but it stays here deliberately.
# Removing it from the allowlist makes the CORS preflight fail for any panel build still
# sending it, which is every browser tab open during a deploy. It costs nothing to keep.
ALLOWED_HEADERS = ["Content-Type", "X-Session-Id"]


def _bypass_proxy_for(hosts: list[str]) -> None:
    """Merge ``hosts`` into the process-wide NO_PROXY list.

    python-decouple only fills :data:`settings`; httpx reads the *process* environment, so
    this export is the whole bridge between the setting and every client built later. Both
    spellings are written because the two stdlib helpers httpx leans on read different ones.
    Inert where no proxy is configured, which is the case on Liara.

    Args:
        hosts: Hostnames that must always be reached directly.
    """
    for name in ("NO_PROXY", "no_proxy"):
        current = [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]
        os.environ[name] = ",".join(current + [h for h in hosts if h not in current])


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Prepare the database and the retrieval index, degrading instead of failing.

    Args:
        app: The application being started.

    Yields:
        Control back to the server for the lifetime of the process.
    """
    # Before anything can build an HTTP client — the provider clients are lazy, so this is
    # early enough, and it must not run after the first one is cached.
    if settings.no_proxy_hosts:
        _bypass_proxy_for(settings.no_proxy_hosts)
        logger.info("no_proxy_applied", hosts=settings.no_proxy_hosts)
    try:
        await init_db()
    except Exception as exc:  # noqa: BLE001 - boot must survive a bad disk mount.
        logger.error("init_db_failed", error=type(exc).__name__, exc_info=exc)
    try:
        # After init_db, because it writes a row. A failure here leaves the admin surface
        # unreachable but must never stop the chat path from serving.
        await ensure_bootstrap_user()
    except Exception as exc:  # noqa: BLE001 - a failed bootstrap must not block boot.
        logger.error("bootstrap_user_failed", error=type(exc).__name__, exc_info=exc)
    try:
        # Copy .env into the database the first time only, then publish the snapshot the
        # synchronous readers use. From here on the database is the source of truth and this
        # file is not consulted for these values again.
        async with AsyncSessionLocal() as db:
            await seed_from_env(db)
            await db.commit()
            current = await refresh(db)
        # Apply the STORED level, not the one `configure_logging()` read from .env at import:
        # without this the panel shows a level that has never been in force since the last
        # restart, which is worse than not offering the setting at all.
        configure_logging(current.log_level)
        logger.info("settings_ready", model=current.model_primary, agent_mode=current.agent_mode)
    except Exception as exc:  # noqa: BLE001 - fall back to the compiled defaults.
        logger.error("settings_load_failed", error=type(exc).__name__, exc_info=exc)
    try:
        retrieval_service.load()
        logger.info(
            "retrieval_ready",
            chunks=retrieval_service.chunk_count,
            embeddings=retrieval_service.has_embeddings,
        )
    except Exception as exc:  # noqa: BLE001 - boot must survive a missing corpus.
        logger.error("retrieval_load_failed", error=type(exc).__name__, exc_info=exc)
    logger.info("startup_complete", has_llm=settings.has_llm)
    yield
    # Shutdown. Turns run on their own tasks now, so a redeploy that simply exits would
    # abandon every answer in flight — the exact failure this whole change removes, just
    # moved from the client to the server. Bounded, and deliberately BEFORE anything
    # disposes the engine: the drain's whole job is to let those writes land.
    try:
        await chat_runs.shutdown()
    except Exception as exc:  # noqa: BLE001 - a failed drain must not mask the shutdown.
        logger.error("chat_runs_drain_failed", error=type(exc).__name__, exc_info=exc)


#: On a real deployment the interactive docs are free reconnaissance — they publish the
#: whole admin, users, registration and usage surface, and they are not rate limited.
#: Two markers of "this is a real deployment", because either one alone has a gap: TLS
#: terminated at the edge with `COOKIE_SECURE` forgotten would publish everything, and a
#: local run with a secret set is still local. A deployment that has neither cannot sign
#: anybody in, so there is nothing behind the docs to reach.
_DOCS_URLS: dict[str, str | None] = (
    {"docs_url": None, "redoc_url": None, "openapi_url": None}
    if settings.cookie_secure or settings.auth_secret
    else {}
)

app = FastAPI(
    title=TITLE,
    description=DESCRIPTION,
    version=VERSION,
    lifespan=lifespan,
    **_DOCS_URLS,
)

# slowapi's per-route decorators read the limiter from their closure; app.state is what
# its own helpers and any future middleware look at.
app.state.limiter = limiter

OVERSIZE_MESSAGE = "حجم درخواست بیش از حد مجاز است."


# Registered before the middlewares below so it runs INSIDE them: an oversize request is
# still logged and still carries CORS headers, but is rejected before FastAPI buffers and
# parses the body. Rate limiting cannot cover this — the body is resolved before the
# endpoint (and therefore before the limiter decorator) ever runs.
@app.middleware("http")
async def limit_body_size(request: Request, call_next) -> Response:  # noqa: ANN001
    """Reject a request whose declared body exceeds ``MAX_BODY_BYTES`` with 413.

    Args:
        request: The incoming request.
        call_next: The next handler in the chain.

    Returns:
        A 413 envelope, or whatever the downstream handler produced.
    """
    # ponytail: Content-Length only. A chunked body without it would slip past; add a
    # counting stream wrapper if a client ever legitimately streams uploads here.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return error_response(413, "PAYLOAD_TOO_LARGE", OVERSIZE_MESSAGE)
    return await call_next(request)


#: Requests that cannot change anything, so an Origin they did not choose is harmless.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

BAD_ORIGIN_MESSAGE = "منبع این درخواست مجاز نیست."


# Registered here so it runs inside the logger and CORS but outside the routing: a rejected
# cross-site write is still logged and still carries CORS headers.
@app.middleware("http")
async def enforce_origin(request: Request, call_next) -> Response:  # noqa: ANN001
    """Refuse a state-changing request whose ``Origin`` is not an allowed one.

    This is the authoritative CSRF defence, and it is not optional. ``liara.run`` is on the
    Public Suffix List, so two apps deployed there are cross-**site**, not merely
    cross-origin — the session cookie therefore needs ``SameSite=None``, which removes the
    browser's own protection entirely. What remained was the JSON-preflight argument, and
    that leans on FastAPI refusing other content types; ``CORSMiddleware`` cannot help here,
    because a *simple* cross-site request is delivered and executed, and only the response
    is withheld from the attacker.

    An absent ``Origin`` is allowed: curl, the platform's health probe and server-to-server
    callers send none, and browsers always do on the requests this is guarding.
    ``Origin`` is a forbidden header name, so page JavaScript cannot forge it.

    Args:
        request: The incoming request.
        call_next: The next handler in the chain.

    Returns:
        A 403 envelope, or whatever the downstream handler produced.
    """
    # Normalized the same way `config._origins` normalized the allowlist, so a header that
    # differs only in case or a trailing slash still matches.
    raw_origin = request.headers.get("origin")
    origin = raw_origin.rstrip("/").lower() if raw_origin else ""
    if request.method not in _SAFE_METHODS and origin and origin not in settings.allowed_origins:
        logger.warning("origin_refused", method=request.method, path=request.url.path)
        return error_response(403, "BAD_ORIGIN", BAD_ORIGIN_MESSAGE)
    return await call_next(request)


app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    # True since the panel session moved into an httpOnly cookie: without it the browser
    # neither sends the cookie nor hands any cross-origin response body to JS. This is why
    # ALLOWED_ORIGINS must stay an explicit list — config.py refuses "*" at import.
    allow_credentials=True,
    allow_methods=ALLOWED_METHODS,
    allow_headers=ALLOWED_HEADERS,
    # The middleware sets X-Request-Id but browsers hide it from JS unless it is exposed.
    expose_headers=["X-Request-Id"],
)

# Registers the RateLimitExceeded handler too, in the shared {"success": false, …} shape —
# do not additionally register slowapi's own handler, it would win and change the body.
register_exception_handlers(app)

app.include_router(api_router, prefix="/api")
# Same endpoint, second mount: the platform health check polls the root path.
app.include_router(health.router, prefix="/healthz", tags=["health"])
