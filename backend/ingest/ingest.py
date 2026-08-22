"""Ingest the Liara documentation corpus into `data/chunks.jsonl` (+ `embeddings.npz`).

Standalone script, run locally — never at deploy time. It clones
https://github.com/liara-cloud/docs (shallow), parses `public/llms/**/*.md`, chunks each
page on its `##`/`###` headings without ever cutting a fenced code block, and optionally
embeds every chunk through AvalAI's OpenAI-compatible /embeddings endpoint.

Usage:
    python ingest/ingest.py --skip-embeddings
    AVALAI_API_KEY=... python ingest/ingest.py --resume
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import httpx
import numpy as np

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

REPO_URL = "https://github.com/liara-cloud/docs"
# Where the shallow clone is kept between runs. Defined here, and dropped here via
# --refresh, so no caller has to restate the path in another language and drift from it.
CLONE_CACHE = Path(tempfile.gettempdir()) / "liara-docs-cache"
DOCS_GLOB = "public/llms/**/*.md"
DOCS_BASE_URL = "https://docs.liara.ir"

MIN_CHUNK_CHARS = 200
MAX_CHUNK_CHARS = 1500
# Joins a `###` heading to the `##` above it. Also the separator tools_service already uses
# between title and heading, so a snippet header reads as one path: «عنوان › پدر › فرزند».
HEADING_SEP = "›"
EMBED_BATCH = int(os.environ.get("EMBED_BATCH", "50"))
# Must equal the app's EMBED_DIM (app/core/config.py) — both read the same variable so a
# stored matrix and a query vector can never describe different spaces.
EMBED_DIM = int(os.environ.get("EMBED_DIM", "1536"))
EMBED_TEXT_MAX = 6000
EMBED_SLEEP_SECONDS = 1.0
EMBED_RETRIES = 5
EMBED_TIMEOUT_SECONDS = 120.0
EMBED_FLUSH_EVERY = 10
DEFAULT_EMBED_MODEL = "text-embedding-3-small"
AVALAI_BASE = os.environ.get("AVALAI_BASE_URL", "https://api.avalai.ir/v1").rstrip("/")

_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_ORIGINAL_LINK_RE = re.compile(r"^\s*Original link:\s*(\S+)\s*$", re.IGNORECASE)
_PERSIAN_RE = re.compile(r"[؀-ۿ]")
# One page carries a 48 KB inline base64 image; it is noise for retrieval and would blow
# the embedding payload, so the payload is collapsed while the marker stays readable.
_BASE64_RE = re.compile(r"(data:[\w.+-]+/[\w.+-]+;base64,)[A-Za-z0-9+/=]{100,}")
_NON_SPACE_RE = re.compile(r"\S")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
#: Instruction-shaped text in the corpus. Report-only — see :func:`injection_scan`.
_INJECTION_RE = re.compile(
    r"^\s*system\s*:"
    r"|<\s*/?\s*docs\b"
    r"|ignore\s+(?:all\s+|the\s+)?(?:previous|above)"
    r"|disregard\s.{0,30}instructions"
    r"|you\s+are\s+now\b"
    r"|act\s+as\b"
    # The known content brief in ai/ai-sdk-errors tells its reader to rewrite links, which
    # is this app's own URL trust boundary talked at directly. One page, no false positives.
    r"|rewrite\s+(?:them|the\s+links|links)"
    r"|دستورات?\s+(?:قبلی|بالا)\s+را\s+نادیده"
    r"|از\s+دستورالعمل"
    r"|نقش\s+خود\s+را\s+فراموش",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(slots=True)
class Doc:
    """One parsed markdown page."""

    rel_path: str
    url: str
    title: str
    lang: str
    sections: list[tuple[str, str]]


# --------------------------------------------------------------------------- parsing


def iter_lines(lines: list[str]) -> Iterator[tuple[str, bool]]:
    """Yield each line paired with whether it belongs to a fenced code block.

    Both the opening and the closing fence lines report ``True`` so that nothing inside a
    code block — including lines that look like markdown headings, such as ``# pip install``
    in a bash sample — is ever treated as structure.

    Args:
        lines: Raw markdown lines.

    Yields:
        Tuples of ``(line, in_fence)``.
    """
    open_marker = ""
    for line in lines:
        match = _FENCE_RE.match(line)
        marker = match.group(1) if match else ""
        if open_marker:
            in_fence = True
            if marker and marker[0] == open_marker[0] and len(marker) >= len(open_marker):
                open_marker = ""
        elif marker:
            open_marker = marker
            in_fence = True
        else:
            in_fence = False
        yield line, in_fence


def detect_lang(text: str) -> str:
    """Return ``"fa"`` when more than 20% of the visible characters are Persian."""
    visible = len(_NON_SPACE_RE.findall(text))
    if not visible:
        return "en"
    return "fa" if len(_PERSIAN_RE.findall(text)) / visible > 0.2 else "en"


def path_to_url(rel_path: str) -> str:
    """Derive the canonical docs.liara.ir URL from a corpus-relative markdown path."""
    stem = rel_path[:-3] if rel_path.endswith(".md") else rel_path
    return f"{DOCS_BASE_URL}/{stem}/"


def slugify(rel_path: str) -> str:
    """Turn a corpus-relative path into a stable ASCII slug used as the chunk id prefix."""
    stem = rel_path[:-3] if rel_path.endswith(".md") else rel_path
    return _SLUG_RE.sub("-", stem.lower()).strip("-") or "doc"


def parse_doc(path: Path, root: Path) -> Doc:
    """Parse one markdown file into a title, a URL, a language and heading sections.

    Args:
        path: Absolute path to the markdown file.
        root: The `public/llms` root the path is relative to.

    Returns:
        The parsed :class:`Doc`. Sections are ``(heading, body)`` pairs; the text before the
        first `##` heading gets an empty heading, and a `###` heading carries the `##` above
        it as ``"parent › child"``.
    """
    rel_path = path.relative_to(root).as_posix()
    raw = _BASE64_RE.sub(r"\1…", path.read_text(encoding="utf-8-sig", errors="replace"))
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    url = ""
    title = ""
    sections: list[tuple[str, list[str]]] = [("", [])]
    parent = ""

    for line, in_fence in iter_lines(lines):
        if not in_fence:
            if not url:
                link = _ORIGINAL_LINK_RE.match(line)
                if link:
                    # The corpus is third-party markdown: only accept a link that really
                    # points at the docs site. Anything else (javascript:, data:, another
                    # host) falls through to the path-derived URL below.
                    candidate = link.group(1)
                    if candidate.startswith(f"{DOCS_BASE_URL}/"):
                        url = candidate
                    continue
            heading = _HEADING_RE.match(line)
            if heading:
                level, text = len(heading.group(1)), heading.group(2).strip()
                if level == 1 and not title:
                    title = text
                    continue
                if level == 2:
                    parent = text
                    sections.append((text, []))
                    continue
                if level == 3:
                    # A `###` alone is unmoored — «در زمان بیلد» means nothing without the
                    # `##` above it. Carry the path so the snippet header the model reads
                    # says which section of the page it is standing in.
                    sections.append((f"{parent} {HEADING_SEP} {text}" if parent else text, []))
                    continue
        sections[-1][1].append(line)

    body = "\n".join(lines)
    if not title:
        title = slugify(rel_path).replace("-", " ")
    if not url:
        url = path_to_url(rel_path)

    return Doc(
        rel_path=rel_path,
        url=url,
        title=title,
        lang=detect_lang(body),
        sections=[(head, "\n".join(text).strip()) for head, text in sections],
    )


# -------------------------------------------------------------------------- chunking


def paragraphs(text: str) -> list[str]:
    """Split text into paragraphs on blank lines, keeping each fenced block atomic."""
    blocks: list[str] = []
    buf: list[str] = []
    for line, in_fence in iter_lines(text.split("\n")):
        if not in_fence and not line.strip():
            if buf:
                blocks.append("\n".join(buf).strip())
                buf = []
        else:
            buf.append(line)
    if buf:
        blocks.append("\n".join(buf).strip())
    return [b for b in blocks if b]


def hard_split(text: str, limit: int) -> list[str]:
    """Split an oversized paragraph on line boundaries, never inside a fenced block.

    A single fenced block larger than ``limit`` is kept whole — an intact code sample is
    worth more than a tidy chunk size.

    Args:
        text: The paragraph to split.
        limit: Soft character budget per piece.

    Returns:
        A list of one or more pieces.
    """
    pieces: list[str] = []
    buf: list[str] = []
    size = 0
    for line, in_fence in iter_lines(text.split("\n")):
        if buf and not in_fence and size + len(line) > limit:
            pieces.append("\n".join(buf).strip())
            buf, size = [], 0
        if not in_fence and len(line) > limit:
            # A single unbreakable line: slice it so chunk size stays bounded.
            pieces.extend(line[i : i + limit] for i in range(0, len(line), limit))
            continue
        buf.append(line)
        size += len(line) + 1
    if buf:
        pieces.append("\n".join(buf).strip())
    return [p for p in pieces if p] or [text]


def split_long(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Pack a long section into ``limit``-sized pieces at paragraph boundaries."""
    if len(text) <= limit:
        return [text]

    pieces: list[str] = []
    buf: list[str] = []
    size = 0
    for para in paragraphs(text):
        if len(para) > limit:
            parts = hard_split(para, limit)
            if buf:
                # Keep the sentence that introduces a big code block attached to it.
                parts[0] = "\n\n".join(buf) + "\n\n" + parts[0]
                buf, size = [], 0
            pieces.extend(parts)
            continue
        if buf and size + len(para) > limit:
            pieces.append("\n\n".join(buf))
            buf, size = [], 0
        buf.append(para)
        size += len(para) + 2
    if buf:
        pieces.append("\n\n".join(buf))

    if len(pieces) > 1 and len(pieces[-1]) < MIN_CHUNK_CHARS:
        tail = pieces.pop()
        pieces[-1] = f"{pieces[-1]}\n\n{tail}"
    return pieces


def chunk_doc(doc: Doc) -> list[dict[str, str]]:
    """Turn a parsed page into contract-shaped chunk dicts.

    Sections shorter than ``MIN_CHUNK_CHARS`` are merged into the previous one (or the next
    one when they lead the page), then sections longer than ``MAX_CHUNK_CHARS`` are split on
    paragraph breaks.

    Args:
        doc: The parsed page.

    Returns:
        A list of ``{"id", "url", "title", "heading", "text", "lang"}`` dicts.
    """
    merged: list[list[str]] = []
    for heading, text in doc.sections:
        # Only the leaf goes back into the body — the full path lives in the `heading`
        # field, and `get_page` stitches these bodies back into a readable page where a
        # repeated parent on every subsection would be noise.
        leaf = heading.rsplit(f" {HEADING_SEP} ", 1)[-1]
        body = f"## {leaf}\n{text}".strip() if heading else text.strip()
        if not body:
            continue
        if merged and len(body) < MIN_CHUNK_CHARS:
            merged[-1][1] = f"{merged[-1][1]}\n\n{body}"
            continue
        merged.append([heading, body])

    if len(merged) > 1 and len(merged[0][1]) < MIN_CHUNK_CHARS:
        head = merged.pop(0)
        merged[0][1] = f"{head[1]}\n\n{merged[0][1]}"

    slug = slugify(doc.rel_path)
    chunks: list[dict[str, str]] = []
    for heading, body in merged:
        for piece in split_long(body):
            chunks.append(
                {
                    "id": f"{slug}#{len(chunks):04d}",
                    "url": doc.url,
                    "title": doc.title,
                    "heading": heading,
                    "text": piece,
                    "lang": doc.lang,
                }
            )
    return chunks


def embed_text_of(chunk: dict[str, str]) -> str:
    """Build the embedding input for a chunk: `«title › heading»` prepended to its text."""
    heading = chunk.get("heading") or ""
    prefix = f"«{chunk['title']} › {heading}»" if heading else f"«{chunk['title']}»"
    return f"{prefix}\n{chunk['text']}"[:EMBED_TEXT_MAX]


# --------------------------------------------------------------------------- corpus


def ensure_repo(repo_dir: str | None, refresh: bool = False) -> Path:
    """Return the `public/llms` root, shallow-cloning the docs repo into a cache if needed.

    Args:
        repo_dir: An existing checkout to use instead of cloning.
        refresh: Drop the cached clone first, so the next ingest sees today's docs. Owned
            here rather than by the caller: a scheduled refresh that deletes the wrong
            directory silently re-ingests a stale clone and reports success.

    Returns:
        Path to the `public/llms` directory.

    Raises:
        SystemExit: If the clone fails or the corpus directory is absent.
    """
    if repo_dir:
        root = Path(repo_dir).expanduser().resolve()
    else:
        if refresh:
            print(f"[ingest] حذف کلون کش‌شده: {CLONE_CACHE}")
            shutil.rmtree(CLONE_CACHE, ignore_errors=True)
        root = CLONE_CACHE / "docs"
        if (root / ".git").is_dir():
            print(f"[ingest] استفاده از کلون موجود: {root}")
        else:
            root.parent.mkdir(parents=True, exist_ok=True)
            print(f"[ingest] git clone --depth 1 {REPO_URL} → {root}")
            result = subprocess.run(
                ["git", "clone", "--depth", "1", REPO_URL, str(root)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                sys.exit(f"[ingest] clone ناموفق بود: {result.stderr.strip()[:400]}")

    llms = root / "public" / "llms"
    if not llms.is_dir():
        sys.exit(f"[ingest] مسیر public/llms در {root} پیدا نشد.")
    return llms


def build_chunks(llms_root: Path, limit: int | None) -> tuple[list[dict[str, str]], int]:
    """Parse and chunk every markdown page under the corpus root.

    Args:
        llms_root: The `public/llms` directory.
        limit: Optional cap on the number of files, for quick runs.

    Returns:
        Tuple of (chunks, number of files processed).
    """
    paths = sorted(llms_root.rglob("*.md"))
    if limit:
        paths = paths[:limit]

    chunks: list[dict[str, str]] = []
    for path in paths:
        chunks.extend(chunk_doc(parse_doc(path, llms_root)))
    return chunks, len(paths)


def write_chunks(chunks: list[dict[str, str]], out_dir: Path) -> Path:
    """Write `chunks.jsonl` (one JSON object per line, UTF-8, unescaped Persian)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "chunks.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    return path


def corpus_meta(repo_root: Path, chunks_path: Path, model: str) -> dict[str, object]:
    """Describe the corpus that was just written, for the app to verify at load time.

    The hash is taken over the **bytes** of `chunks.jsonl`, which is exactly what
    ``RetrievalService`` re-computes at startup: stamping it into `embeddings.npz` is what
    lets the app tell a matrix built for this corpus from one left over from an older run.

    Args:
        repo_root: Any path inside the docs checkout; ``git -C`` walks up to its root.
        chunks_path: The `chunks.jsonl` just written.
        model: Embedding model name the vectors are (or will be) built with.

    Returns:
        A JSON-serializable dict; ``git_commit`` is None when git or the checkout is
        unavailable. ``files`` counts distinct pages present in `chunks.jsonl`.
    """
    commit: str | None = None
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
        )
        commit = result.stdout.strip() or None if result.returncode == 0 else None
    except OSError:  # git not installed
        commit = None

    raw = chunks_path.read_bytes()
    urls = {
        json.loads(line).get("url", "")
        for line in raw.decode("utf-8", errors="replace").splitlines()
        if line.strip()
    }
    return {
        "git_commit": commit,
        "ingested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "corpus_sha256": hashlib.sha256(raw).hexdigest(),
        "embed_model": model,
        "embed_dim": EMBED_DIM,
        "files": len(urls),
        "chunks": sum(1 for line in raw.splitlines() if line.strip()),
    }


def write_meta(meta: dict[str, object], out_dir: Path) -> Path:
    """Write the provenance sidecar `corpus_meta.json`.

    A sidecar rather than a header line inside `chunks.jsonl`: the app's reader would treat
    such a line as a URL-less chunk and reject it.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "corpus_meta.json"
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


# ------------------------------------------------------------------------ embedding


def read_api_key() -> str:
    """Read `AVALAI_API_KEY` from the environment, falling back to `backend/.env`."""
    key = os.environ.get("AVALAI_API_KEY", "").strip()
    if key:
        return key
    env_file = BACKEND_ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "AVALAI_API_KEY":
                return value.strip().strip("'\"")
    return ""


def l2_normalize(matrix: np.ndarray) -> np.ndarray:
    """L2-normalize each row; all-zero rows are left untouched."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def embed_batch(
    client: httpx.Client, texts: list[str], api_key: str, model: str
) -> list[list[float]]:
    """Embed up to ``EMBED_BATCH`` texts, retrying 429/5xx with exponential backoff.

    Uses AvalAI's OpenAI-compatible ``/embeddings`` route — the same endpoint and the same
    ``EMBED_MODEL`` the running app uses for queries. If those two ever diverge, the stored
    matrix and the query vector describe different spaces and every ranking is nonsense, so
    the app refuses a matrix whose width disagrees with ``EMBED_DIM``.

    Args:
        client: An open httpx client.
        texts: The batch of texts (<= 100 items).
        api_key: AvalAI API key — never logged.
        model: Embedding model name.

    Returns:
        One vector per input text, in order.

    Raises:
        RuntimeError: If every retry failed or the response was incomplete.
    """
    payload: dict[str, object] = {"model": model, "input": texts, "encoding_format": "float"}
    # OpenAI's v3 models accept an explicit width; others reject the field, so only send it
    # when it can apply.
    if model.startswith("text-embedding-3"):
        payload["dimensions"] = EMBED_DIM
    url = f"{AVALAI_BASE}/embeddings"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    last_error = ""
    for attempt in range(EMBED_RETRIES):
        try:
            response = client.post(url, headers=headers, json=payload)
            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"HTTP {response.status_code}"
            else:
                response.raise_for_status()
                data = response.json()
                rows = sorted(data.get("data", []), key=lambda item: item.get("index", 0))
                vectors = [row.get("embedding", []) for row in rows]
                if len(vectors) != len(texts):
                    raise RuntimeError(
                        f"پاسخ ناقص: {len(vectors)} بردار برای {len(texts)} متن"
                    )
                return vectors
        except httpx.HTTPStatusError as exc:  # 4xx other than 429: not retryable
            raise RuntimeError(f"خطای API (HTTP {exc.response.status_code})") from exc
        except httpx.HTTPError as exc:
            last_error = type(exc).__name__
        if attempt < EMBED_RETRIES - 1:
            backoff = 2.0**attempt
            print(f"[ingest] تلاش مجدد پس از {backoff:.0f}s ({last_error})")
            time.sleep(backoff)
    raise RuntimeError(f"دسته پس از {EMBED_RETRIES} تلاش ناموفق ماند ({last_error})")


def text_sha(text: str) -> str:
    """Content address for one embedding input — the key `--resume` reuses a vector by."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def save_embeddings(
    out_dir: Path,
    ids: list[str],
    vectors: list[list[float]],
    meta: dict[str, object] | None = None,
    text_shas: list[str] | None = None,
) -> Path:
    """Write `embeddings.npz` with float16 L2-normalized vectors and a parallel id array.

    Args:
        out_dir: Directory to write into.
        ids: Chunk ids, parallel to ``vectors``.
        vectors: One vector per id.
        meta: Provenance from :func:`corpus_meta`, stored so the app can refuse a matrix
            built for a different corpus or embedding model.
        text_shas: :func:`text_sha` of each vector's embedding input, parallel to
            ``vectors``. Without it a later ``--resume`` cannot tell a reusable vector
            from a stale one and re-embeds everything.

    Returns:
        Path to the written npz.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "embeddings.npz"
    matrix = l2_normalize(np.asarray(vectors, dtype=np.float32)).astype(np.float16)
    # Unicode dtype (not object) so the npz loads without allow_pickle anywhere — the same
    # trick applies to `meta`, stored as a 0-d unicode array holding its JSON.
    arrays: dict[str, np.ndarray] = {
        "vectors": matrix,
        "ids": np.asarray(ids, dtype=np.str_),
    }
    if text_shas is not None:
        arrays["text_shas"] = np.asarray(text_shas, dtype=np.str_)
    if meta is not None:
        arrays["meta"] = np.asarray(json.dumps(meta, ensure_ascii=False), dtype=np.str_)
    np.savez_compressed(path, **arrays)
    return path


def load_existing(out_dir: Path) -> dict[str, list[float]]:
    """Load reusable vectors from `embeddings.npz`, keyed by their embedding input's hash.

    Keyed by content, never by chunk id: ids are page-local positions, so an upstream
    edit that leaves a page's chunk count alone would otherwise let ``--resume`` keep a
    vector built from text that no longer exists. Content addressing also *widens* reuse —
    text that merely moved to a new position keeps its vector.
    """
    path = out_dir / "embeddings.npz"
    if not path.is_file():
        return {}
    try:
        with np.load(path) as data:
            if "text_shas" not in data.files:
                print("[ingest] npz موجود هش متن ندارد؛ همهٔ بردارها از نو ساخته می‌شوند.")
                return {}
            shas = [str(s) for s in data["text_shas"]]
            vectors = np.asarray(data["vectors"], dtype=np.float32)
    except (OSError, KeyError, ValueError) as exc:
        print(f"[ingest] npz موجود قابل خواندن نبود ({exc}); از صفر شروع می‌شود.")
        return {}
    if len(shas) != len(vectors):
        return {}
    return {sha: vector.tolist() for sha, vector in zip(shas, vectors)}


def embed_with_split(
    client: httpx.Client, texts: list[str], api_key: str, model: str
) -> list[list[float]]:
    """Embed ``texts``, halving the batch when the transport keeps failing.

    ``RemoteProtocolError`` from this provider is intermittent rather than size-determined,
    but a smaller payload recovers far more often than another identical retry. Splitting
    turns one unlucky batch into a brief slowdown instead of aborting a 35-batch run and
    leaving a partial matrix the app will refuse to load.

    Args:
        client: An open httpx client.
        texts: The batch to embed.
        api_key: AvalAI API key — never logged.
        model: Embedding model name.

    Returns:
        One vector per input text, in order.

    Raises:
        RuntimeError: If even a single-text request cannot succeed.
    """
    try:
        return embed_batch(client, texts, api_key, model)
    except RuntimeError:
        if len(texts) <= 1:
            raise
        middle = len(texts) // 2
        print(f"[ingest] تقسیم دسته به {middle} + {len(texts) - middle} پس از خطای مکرر")
        left = embed_with_split(client, texts[:middle], api_key, model)
        time.sleep(EMBED_SLEEP_SECONDS)
        right = embed_with_split(client, texts[middle:], api_key, model)
        return left + right


def build_embeddings(
    chunks: list[dict[str, str]],
    out_dir: Path,
    api_key: str,
    model: str,
    resume: bool,
    meta: dict[str, object] | None = None,
) -> tuple[int, int]:
    """Embed every chunk in batches of 100, resumable and 429-tolerant.

    The npz is flushed every ``EMBED_FLUSH_EVERY`` batches so an interrupted run can be
    continued with ``--resume``.

    Args:
        chunks: All chunks, in file order.
        out_dir: Directory holding `embeddings.npz`.
        api_key: AvalAI API key — never logged.
        model: Embedding model name.
        resume: Reuse vectors already present in the npz.
        meta: Provenance stamped into every write of the npz, partial flushes included.

    Returns:
        Tuple of (newly embedded count, reused count).
    """
    cached = load_existing(out_dir) if resume else {}
    inputs = [embed_text_of(chunk) for chunk in chunks]
    shas = [text_sha(text) for text in inputs]
    reused = sum(1 for sha in shas if sha in cached)
    # Deduped by content: two chunks with identical embedding input are one API call.
    pending: dict[str, str] = {}
    for text, sha in zip(inputs, shas):
        if sha not in cached:
            pending.setdefault(sha, text)
    if reused:
        print(f"[ingest] {reused} بردار از npz موجود بازاستفاده شد.")
    if not pending:
        print("[ingest] همهٔ بردارها از قبل موجود بودند.")
        vectors = [cached[sha] for sha in shas]
        save_embeddings(out_dir, [c["id"] for c in chunks], vectors, meta, shas)
        return 0, reused

    done: dict[str, list[float]] = dict(cached)
    embedded = 0
    items = list(pending.items())
    total_batches = (len(items) + EMBED_BATCH - 1) // EMBED_BATCH

    def flush() -> None:
        ready = [(c["id"], sha) for c, sha in zip(chunks, shas) if sha in done]
        save_embeddings(
            out_dir,
            [chunk_id for chunk_id, _ in ready],
            [done[sha] for _, sha in ready],
            meta,
            [sha for _, sha in ready],
        )

    with httpx.Client(timeout=EMBED_TIMEOUT_SECONDS) as client:
        for index in range(total_batches):
            batch = items[index * EMBED_BATCH : (index + 1) * EMBED_BATCH]
            print(f"[ingest] دستهٔ {index + 1}/{total_batches} ({len(batch)} متن)")
            try:
                vectors = embed_with_split(client, [text for _, text in batch], api_key, model)
            except RuntimeError as exc:
                flush()
                print(f"[ingest] توقف embedding: {exc}")
                print("[ingest] بردارهای جزئی ذخیره شد؛ با --resume ادامه دهید.")
                return embedded, reused
            for (sha, _), vector in zip(batch, vectors):
                done[sha] = vector
            embedded += len(batch)
            if (index + 1) % EMBED_FLUSH_EVERY == 0:
                flush()
            if index < total_batches - 1:
                time.sleep(EMBED_SLEEP_SECONDS)

    flush()
    return embedded, reused


# ------------------------------------------------------------------------ self-check


def self_check(chunks: list[dict[str, str]], out_dir: Path) -> bool:
    """Verify the written artifacts and run a real BM25 query against the corpus.

    Args:
        chunks: The chunks just written.
        out_dir: Directory holding `chunks.jsonl` and `embeddings.npz`.

    Returns:
        True when every assertion passed.
    """
    ok = True
    chunk_ids = {chunk["id"] for chunk in chunks}
    assert len(chunk_ids) == len(chunks), "شناسهٔ تکراری در chunks.jsonl"

    npz_path = out_dir / "embeddings.npz"
    if npz_path.is_file():
        with np.load(npz_path) as data:
            ids = [str(i) for i in data["ids"]]
            vectors = np.asarray(data["vectors"])
        assert len(ids) == len(vectors), "طول ids و vectors برابر نیست"
        missing = [i for i in ids if i not in chunk_ids]
        assert not missing, f"{len(missing)} شناسه در chunks.jsonl نیست: {missing[:3]}"
        print(f"[check] embeddings.npz سالم است ({len(ids)} بردار، بعد {vectors.shape[-1]}).")
    else:
        print("[check] embeddings.npz وجود ندارد — بررسی بردارها رد شد.")

    try:
        from rank_bm25 import BM25Okapi

        from app.shared.persian import expand_query, tokenize
    except ImportError as exc:
        print(f"[check] BM25 بررسی نشد (import ناموفق: {exc}).")
        return False

    query = "دیپلوی nodejs"
    corpus = [
        tokenize(f"{c['text']} {c['title']} {c['heading']}") for c in chunks
    ]
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(expand_query(tokenize(query)))
    order = np.argsort(scores)[::-1][:3]
    print(f"[check] BM25 «{query}» → ")
    for rank, idx in enumerate(order, start=1):
        print(f"         {rank}. {scores[idx]:.2f}  {chunks[idx]['url']}")
    top_url = chunks[int(order[0])]["url"]
    if "nodejs" in top_url:
        print("[check] بررسی BM25 موفق بود.")
    else:
        ok = False
        print(f"[check] شکست: نتیجهٔ برتر شامل nodejs نیست ({top_url})")
    return ok


def injection_scan(chunks: list[dict[str, str]]) -> int:
    """Report chunk lines that read like instructions rather than documentation.

    The corpus is public third-party markdown and genuinely contains such text — the
    leftover content brief in `ai/ai-sdk-errors/ai-api-call-error.md` is a known true
    positive — so this is **report-only and never fails the build**. The real mitigations
    are at runtime (`tools_service._neutralize`, the untrusted envelope, the citation
    registry); this only makes the contamination visible before it ships.

    Args:
        chunks: The chunks just written.

    Returns:
        The number of matching lines. Callers ignore it; ingest still exits 0.
    """
    findings = 0
    for chunk in chunks:
        for line in chunk["text"].split("\n"):
            if not _INJECTION_RE.search(line):
                continue
            findings += 1
            # Corpus text is hostile input: drop anything unprintable before it reaches a
            # terminal, and clamp the length.
            excerpt = "".join(ch if ch.isprintable() else " " for ch in line.strip())[:120]
            print(f"[scan] {chunk['id']} {chunk['url']}: {excerpt}")
    print(f"[scan] {findings} خط دستورنما در {len(chunks)} chunk (گزارشی؛ ingest ادامه می‌یابد).")
    return findings


def describe_lengths(chunks: list[dict[str, str]]) -> str:
    """Return a one-line character-length distribution over the chunk texts."""
    lengths = np.asarray([len(chunk["text"]) for chunk in chunks])
    percentiles = np.percentile(lengths, [50, 90, 99]).astype(int)
    return (
        f"min={lengths.min()} p50={percentiles[0]} p90={percentiles[1]} "
        f"p99={percentiles[2]} max={lengths.max()} mean={int(lengths.mean())}"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command-line flags declared by the contract."""
    parser = argparse.ArgumentParser(description="Ingest Liara docs into chunks/embeddings.")
    parser.add_argument("--repo-dir", default=None, help="Existing docs checkout to use.")
    parser.add_argument(
        "--out-dir", default=str(BACKEND_ROOT / "data"), help="Where to write artifacts."
    )
    parser.add_argument("--limit", type=int, default=None, help="Only process N files.")
    parser.add_argument(
        "--skip-embeddings", action="store_true", help="Write chunks.jsonl only."
    )
    parser.add_argument(
        "--resume", action="store_true", help="Reuse vectors from an existing npz."
    )
    parser.add_argument(
        "--refresh", action="store_true", help="Drop the cached clone and re-fetch the docs."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the full ingest pipeline and return a process exit code."""
    args = parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()

    llms_root = ensure_repo(args.repo_dir, args.refresh)
    chunks, file_count = build_chunks(llms_root, args.limit)
    if not chunks:
        sys.exit("[ingest] هیچ chunkی تولید نشد.")
    chunks_path = write_chunks(chunks, out_dir)
    print(f"[ingest] {len(chunks)} chunk از {file_count} فایل → {chunks_path}")

    model = os.environ.get("EMBED_MODEL", DEFAULT_EMBED_MODEL).strip() or DEFAULT_EMBED_MODEL
    meta = corpus_meta(llms_root, chunks_path, model)
    print(f"[ingest] provenance → {write_meta(meta, out_dir)} (commit={meta['git_commit']})")

    api_key = "" if args.skip_embeddings else read_api_key()
    embedded = reused = 0
    if args.skip_embeddings:
        banner("embedding رد شد (--skip-embeddings). برنامه در حالت BM25-only اجرا می‌شود.")
    elif not api_key:
        banner(
            "AVALAI_API_KEY تنظیم نشده است — هیچ بردار معنایی ساخته نشد. "
            "chunks.jsonl کامل نوشته شد و برنامه در حالت BM25-only کار می‌کند."
        )
    else:
        embedded, reused = build_embeddings(chunks, out_dir, api_key, model, args.resume, meta)

    print("\n=== خلاصه ===")
    print(f"files    : {file_count}")
    print(f"chunks   : {len(chunks)}")
    print(f"embedded : {embedded} (بازاستفاده: {reused})")
    print(f"skipped  : {len(chunks) - embedded - reused}")
    print(f"lengths  : {describe_lengths(chunks)}")

    injection_scan(chunks)
    return 0 if self_check(chunks, out_dir) else 1


def banner(message: str) -> None:
    """Print a loud, unmissable box around an operational warning."""
    line = "!" * 78
    print(f"\n{line}\n!! {message}\n{line}\n")


if __name__ == "__main__":
    raise SystemExit(main())
