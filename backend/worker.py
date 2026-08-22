"""The gunicorn worker class, with one setting changed and one reason for it.

`chat_runs.shutdown()` runs in the app's lifespan and is what lets an in-flight answer
finish its write during a redeploy. Uvicorn only reaches the lifespan shutdown *after* it
has waited for every open connection to close, and `UvicornWorker` never sets
`timeout_graceful_shutdown`, so that wait is unbounded. A chat panel holds SSE connections
open by design, so the wait never ends, gunicorn SIGKILLs at `--graceful-timeout`, and the
drain is dead code exactly when it matters.

Ten seconds for connections plus the drain's own eight is inside gunicorn's thirty.
"""

from __future__ import annotations

from uvicorn.workers import UvicornWorker


class GracefulUvicornWorker(UvicornWorker):
    """UvicornWorker that stops waiting on open streams so the lifespan drain can run."""

    CONFIG_KWARGS = {**UvicornWorker.CONFIG_KWARGS, "timeout_graceful_shutdown": 10}
