"""Liveness/readiness probe.

The same router is mounted twice: at ``/healthz`` (what the Liara platform polls) and at
``/api/v1/health`` (what the panel polls). **The only route in the API that needs no
account** — the platform polls it unauthenticated, and a probe that could fail closed would
take the whole app down with it.

Because it is public it is also the cheapest thing on the internet to hammer, so the disk
round trip behind it is memoized and the route is rate limited like any other.
"""

from __future__ import annotations

import time
from uuid import uuid4

from fastapi import Request, Response
from starlette.concurrency import run_in_threadpool

from app.api import limiter
from app.api.cbv import SlashInferringRouter, cbv
from app.core.config import settings
from app.core.database import db_healthy
from app.core.logging import get_logger
from app.domain.schemas.common import HealthResponse
from app.domain.services import chat_runs
from app.domain.services.retrieval_service import retrieval_service
from app.shared.constants import RATE_LIMIT_DEFAULT

logger = get_logger("app.health")

router = SlashInferringRouter()

#: Written and read back next to the sqlite file to prove the disk is really mounted.
MARKER_NAME = ".healthz"

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"


#: How long one disk round trip is trusted for. The platform polls every ~30s, so this
#: costs the same one write per poll while making a flood of probes free.
DISK_CACHE_SECONDS = 30.0

#: ``(checked_at, result)`` from the last real round trip.
_disk_cache: tuple[float, bool] = (0.0, False)


def _disk_writable_cached() -> bool:
    """Return the last disk-writability result, refreshing it at most every 30 seconds.

    Without this, an unauthenticated caller can drive one file write plus one read against
    a network-backed disk per request, for free and without limit.

    Returns:
        Whether the database directory accepted a write recently.
    """
    global _disk_cache
    checked_at, result = _disk_cache
    now = time.monotonic()
    if now - checked_at < DISK_CACHE_SECONDS:
        return result
    result = _disk_writable()
    _disk_cache = (now, result)
    return result


def _disk_writable() -> bool:
    """Write a marker file next to the database and read it back.

    A Liara disk mounted at the wrong path looks fine until data silently disappears at
    the next deploy, so the probe does a real round trip instead of a ``stat``.

    Returns:
        True when the directory holding the sqlite file accepts a write and returns it.
    """
    token = uuid4().hex
    marker = settings.database_file.parent / MARKER_NAME
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(token, encoding="utf-8")
        return marker.read_text(encoding="utf-8") == token
    except OSError as exc:
        logger.warning("disk_unwritable", error=type(exc).__name__)
        return False


def _provenance(value: object) -> str | None:
    """Coerce one corpus-meta field into a short string for the public probe.

    The sidecar is a file on disk rather than a trusted constant, and this route is the
    one thing in the API that answers without an account, so anything that is not a
    reasonable short string is reported as absent.

    Args:
        value: The raw value read from `data/corpus_meta.json`.

    Returns:
        The value clamped to 64 characters, or None.
    """
    return str(value)[:64] if isinstance(value, str) and value else None


@cbv(router)
class HealthEndpoints:
    """Readiness of the three things that can silently break in production."""

    @router.get(
        "",
        response_model=HealthResponse,
        summary="Health probe",
        responses={503: {"description": "Database unreachable"}},
    )
    @limiter.limit(RATE_LIMIT_DEFAULT)
    async def health(self, request: Request, response: Response) -> HealthResponse:
        """Report database, corpus and disk state.

        Args:
            request: Required by the rate limiter to identify the caller.
            response: Injected so the status code can be lowered to 503 in place.

        Returns:
            The health payload. The status code is 503 only when the database is
            unreachable. An empty corpus and a read-only disk are reported as ``degraded``
            with a 200: both are real problems, but neither is fixed by restarting the
            container, and answering 503 makes the platform restart-loop it — which now
            takes the login page down along with the assistant.
        """
        db_ok = await db_healthy()
        disk_ok = await run_in_threadpool(_disk_writable_cached)
        chunks = retrieval_service.chunk_count
        meta = retrieval_service.corpus_meta
        if not db_ok:
            response.status_code = 503
        return HealthResponse(
            status=STATUS_OK if db_ok and disk_ok and chunks else STATUS_DEGRADED,
            chunks=chunks,
            embeddings=retrieval_service.has_embeddings,
            db=db_ok,
            disk=disk_ok,
            ingested_at=_provenance(meta.get("ingested_at")),
            corpus_commit=_provenance(meta.get("git_commit")),
            live_runs=chat_runs.count(),
        )
