#!/bin/sh
# Refresh the corpus from upstream docs, then prove retrieval still works.
#
# Drops the clone cache so the next ingest sees today's docs, re-embeds only the chunks
# that actually changed (--resume reuses the rest, so this is nearly free), and fails
# loudly if the eval harness drops below its floors — a corpus refresh that quietly makes
# retrieval worse is the failure mode worth catching.
#
# Weekly, via crontab -e:
#   17 4 * * 1 /path/to/liara/backend/ingest/reingest.sh >>/tmp/liara-reingest.log 2>&1
set -eu

BACKEND=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON="$BACKEND/.venv-uv/bin/python"

cd "$BACKEND"
"$PYTHON" ingest/ingest.py --refresh --resume
"$PYTHON" -m tests.eval_retrieval
