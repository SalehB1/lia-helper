# Escape hatch for the Liara "python" platform: full control over the run command.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Requirements first so the dependency layer is cached across code changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code, including data/chunks.jsonl and data/embeddings.npz.
COPY . .

# SQLite lives on the mounted disk (see liara.json).
RUN mkdir -p /app/storage

EXPOSE 80

# -w 1: the retrieval index and the slowapi rate limiter are per-process in-memory state.
# --timeout 120: SSE answers stream far longer than the 30s default.
CMD ["gunicorn", "main:app", "-k", "worker.GracefulUvicornWorker", "-w", "1", "-b", "0.0.0.0:80", "--timeout", "120", "--access-logfile", "-"]
