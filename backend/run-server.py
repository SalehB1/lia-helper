"""Run the Liara Docs Assistant API.

Usage:
    Development:  ../.venv/bin/python run-server.py --reload
    Production:   ../.venv/bin/python run-server.py

Environment (all optional, read from ``backend/.env`` by python-decouple):
    PORT        listen port, default 8000 (Liara sets 80 in the container)
    HOST        bind address, default 0.0.0.0
    WORKERS     see the warning below — this app is single-worker by design
    LOG_LEVEL   uvicorn/gunicorn log level, default info
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import uvicorn  # noqa: E402

from app.core.config import settings  # noqa: E402

#: main.py lives at the backend root and exposes `app`.
APP = "main:app"

# Local addresses must bypass any system HTTP proxy. A browser proxy on 127.0.0.1:2080 is
# common on Iranian dev machines, and httpx/openai honour http_proxy — without this, calls
# to our own health endpoint (and to a local DB) would be routed through it and hang.
# Only localhost is bypassed: outbound calls to AvalAI SHOULD still use the proxy if one is
# configured, which is often how the provider is reachable in the first place.
_NO_PROXY = "localhost,127.0.0.1,::1"
os.environ.setdefault("no_proxy", _NO_PROXY)
os.environ.setdefault("NO_PROXY", _NO_PROXY)


def _int_env(name: str, default: int) -> int:
    """Read an int from the environment, falling back on anything unparseable."""
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _retrieval_mode(chunks_path: Path) -> str:
    """Describe the retrieval mode the app will actually end up in.

    The presence of ``embeddings.npz`` is not enough: the service rejects a matrix whose row
    count or width disagrees with the corpus, which is exactly what a half-finished ingest
    produces. Reporting "hybrid" for a file the app is about to refuse would hide the very
    problem this line exists to surface, so the counts are checked here too.
    """
    vectors_path = Path(settings.data_dir) / "embeddings.npz"
    if not vectors_path.is_file():
        return "BM25-only (no embeddings.npz — run ingest to enable dense search)"
    try:
        import numpy as np

        with np.load(vectors_path) as data:
            rows, width = len(data["ids"]), int(data["vectors"].shape[1])
        total = sum(1 for _ in chunks_path.open(encoding="utf-8")) if chunks_path.is_file() else 0
    except Exception as exc:  # noqa: BLE001 - a banner must never block startup
        return f"unknown ({type(exc).__name__} reading embeddings.npz)"
    if rows != total:
        return f"BM25-only — embeddings.npz is partial ({rows}/{total}); resume the ingest"
    if width != settings.embed_dim:
        return f"BM25-only — embeddings.npz is {width}-dim but EMBED_DIM={settings.embed_dim}"
    return f"hybrid — BM25 + {settings.embed_model} ({rows} vectors)"


def _banner(mode: str, host: str, port: int) -> None:
    """Print what is about to run and the state of everything it depends on.

    The corpus and key checks are the two things that silently degrade this app, so they
    are surfaced at startup instead of being discovered later through a bad answer.
    """
    chunks = Path(settings.data_dir) / "chunks.jsonl"
    line = "=" * 62
    print(line)
    print(f"  Liara Docs Assistant API — {mode}")
    print(line)
    print(f"  REST      → http://{host}:{port}")
    print(f"  Swagger   → http://localhost:{port}/docs")
    print(f"  Health    → http://localhost:{port}/healthz")
    print("-" * 62)
    print(f"  corpus    : {'OK' if chunks.is_file() else 'MISSING — run ingest/ingest.py'}")
    print(f"  retrieval : {_retrieval_mode(chunks)}")
    print(f"  chat model: {settings.model_primary}" if settings.has_llm else "  chat model: NO KEY — chat returns a Persian 'not configured' error")
    print(f"  CORS      : {', '.join(settings.allowed_origins)}")
    print(f"  database  : {settings.database_file}")
    print("-" * 62)


def _resolve_workers() -> int:
    """Return the worker count, refusing to silently run a broken multi-worker setup.

    This app keeps two things in per-process memory: the retrieval index (chunks + BM25 +
    the embedding matrix) and the slowapi rate-limit counters. More than one worker would
    therefore multiply memory by N and divide the effective rate limit by N — 20/min per
    worker is 80/min for the user. Overriding is allowed but must be deliberate.
    """
    workers = _int_env("WORKERS", 1)
    if workers > 1:
        print(
            f"  WARNING: WORKERS={workers}. The retrieval index is loaded per process and "
            f"the rate limiter counts per process, so this multiplies memory by {workers} "
            f"and weakens rate limiting {workers}x. Use 1 unless you have moved both to "
            "shared storage."
        )
    return max(1, workers)


def main() -> int:
    """Start the server in development or production mode."""
    host = os.environ.get("HOST", "0.0.0.0")
    port = _int_env("PORT", 8000)
    log_level = os.environ.get("LOG_LEVEL", "info").lower()
    reload_mode = "--reload" in sys.argv

    if reload_mode:
        _banner("DEVELOPMENT (auto-reload)", host, port)
        # Bounded here too: without it a save can hang the reloader on an open SSE stream
        # while the new process is already re-running init_db against the same file.
        uvicorn.run(
            APP, host=host, port=port, reload=True, log_level=log_level,
            timeout_graceful_shutdown=5,
        )
        return 0

    _banner("PRODUCTION", host, port)
    workers = _resolve_workers()

    try:
        import gunicorn  # noqa: F401
    except ImportError:
        print("  gunicorn not installed — falling back to uvicorn.")
        uvicorn.run(APP, host=host, port=port, workers=workers, log_level=log_level)
        return 0

    # Next to the interpreter, not on PATH: the documented way to start this is
    # `../.venv/bin/python run-server.py`, which never activates the venv, so a bare
    # "gunicorn" is a FileNotFoundError on every machine that has one installed globally.
    gunicorn_bin = Path(sys.executable).with_name("gunicorn")

    # --timeout 120: SSE responses stay open while the model streams; the default 30s
    # would cut long answers off mid-stream.
    cmd = [
        str(gunicorn_bin) if gunicorn_bin.is_file() else "gunicorn",
        APP,
        "--bind", f"{host}:{port}",
        "--workers", str(workers),
        "--worker-class", "worker.GracefulUvicornWorker",
        "--timeout", "120",
        "--graceful-timeout", "30",
        "--forwarded-allow-ips", os.environ.get("FORWARDED_ALLOW_IPS", "*"),
        "--access-logfile", "-",
        "--error-logfile", "-",
        "--log-level", log_level,
    ]
    print(f"  gunicorn, {workers} worker(s)")
    print("-" * 62)
    return subprocess.run(cmd, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
