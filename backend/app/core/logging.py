"""Structured JSON logging on stdout plus per-request access logging."""

from __future__ import annotations

import logging
import sys
import time
from typing import Any
from uuid import uuid4

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings

REQUEST_ID_HEADER = "X-Request-Id"


def configure_logging(log_level: str | None = None) -> None:
    """Configure structlog to emit one JSON object per line on stdout.

    Safe to call more than once; the last call wins — which is what lets an operator change
    the level from the panel and have it apply immediately.

    Args:
        log_level: The level to apply. Defaults to the ``.env`` value, which is what boot
            uses; the admin write path passes the stored one. The web server's own access
            log is fixed when the process starts and is not affected either way.
    """
    level = getattr(logging, (log_level or settings.log_level).upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level, force=True)
    # slowapi logs the FULL rate-limit key — here the session id, which is this app's only
    # credential — as plain text on every 429. The app's own request line already records
    # the 429 with a truncated session prefix, so nothing is lost by silencing it.
    logging.getLogger("slowapi").setLevel(logging.ERROR)
    # Third-party statement logging is a disclosure, not verbosity, and it is pinned here
    # rather than left to inherit the root level:
    #   sqlalchemy.engine / aiosqlite  log every statement WITH ITS BOUND PARAMETERS — which
    #                                  includes `users.password_hash` on insert and on a
    #                                  password reset, and every chat message body. Note
    #                                  SQLAlchemy emits these at **INFO**, not DEBUG, so
    #                                  inheriting the default level already leaked them.
    #   openai / httpx / httpcore      log the full request payload at DEBUG — system prompt,
    #                                  the user's question, the retrieved documents.
    # WARNING keeps genuine failures (a broken pool, a dropped connection) while dropping the
    # payloads. Nobody raising the app's log level is asking for a copy of the users table,
    # and that level is now settable by the least privileged operator role.
    # "httpx2"/"httpcore2" are not decorative: openai 3.x depends on httpx2, so the loggers
    # actually used by the LLM path are NOT descendants of the "httpx"/"httpcore" names
    # pinned beside them. Without these two, raising log_level to DEBUG from the admin panel
    # — which the least privileged operator role can do — turns on httpcore2's wire trace,
    # including full upstream request and response headers.
    for noisy in (
        "aiosqlite",
        "sqlalchemy.engine",
        "openai",
        "httpx",
        "httpcore",
        "httpx2",
        "httpcore2",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.WriteLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str = "app") -> Any:
    """Return a bound structlog logger for the given name."""
    return structlog.get_logger(name)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs one JSON line per request and stamps a short request id on the response."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Time the request, log its outcome, and add the request-id header.

        Args:
            request: The incoming request.
            call_next: The next handler in the middleware chain.

        Returns:
            The downstream response, with ``X-Request-Id`` attached.
        """
        request_id = uuid4().hex[:8]
        request.state.request_id = request_id
        # Imported here, not at module scope: `app.api` is imported by every endpoint module
        # and this module is imported by `app.core.config`'s consumers well before them.
        from app.api import rate_limit_key

        # What the rate limiter actually keyed on. Logging it is the only way to find out,
        # on the real deployment, which X-Forwarded-For entry the ingress leaves us — and
        # therefore whether the limits are per-caller or all sharing one bucket.
        client = rate_limit_key(request)
        logger = get_logger("app.request")
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.error(
                "request_failed",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                client=client,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                exc_info=True,
            )
            raise
        logger.info(
            "request",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            client=client,
            status_code=response.status_code,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
