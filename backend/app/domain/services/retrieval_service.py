"""Hybrid retrieval over the Liara documentation corpus.

The index lives entirely in process memory: a list of chunks read from
``data/chunks.jsonl``, a BM25 index built over them, and — when
``data/embeddings.npz`` is present — a dense float32 matrix used for cosine search.
Missing or mismatched embeddings are a normal operating mode, not an error: the service
then answers from BM25 alone.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.logging import get_logger
from app.shared.constants import (
    DOCS_MAP_MAX_CHARS,
    PAGE_CHARS,
    RETRIEVAL_CANDIDATES,
    RETRIEVAL_TOP_K,
    RRF_K,
)
from app.shared.persian import char_ngrams, expand_query, tokenize

logger = get_logger("retrieval")

CHUNKS_FILE = "chunks.jsonl"
EMBEDDINGS_FILE = "embeddings.npz"
#: Provenance sidecar written by ``ingest`` next to the two data files.
META_FILE = "corpus_meta.json"
#: Chunk URLs become hrefs in the panel; only the real docs site is ever served.
ALLOWED_URL_PREFIX = "https://docs.liara.ir/"
#: How many documents the character-n-gram list votes on — deliberately shorter than
#: :data:`RETRIEVAL_CANDIDATES`, because sub-word overlap is the weakest of the three
#: signals and a long tail of it outvotes the word index on literal questions. Measured on
#: the 100-question set, hybrid: at 18–22 literal recall@5 stays at the 51/55 baseline while
#: paraphrase goes 14→18; at the full 50 paraphrase reaches 20 but literal drops to 49. This
#: buys the paraphrase gain at zero literal cost, which is the trade the floors ask for.
NGRAM_CANDIDATES = 20
#: The prefix check alone is not enough. ``ingest`` accepts any whitespace-free
#: ``Original link:`` a page declares, so everything after the prefix is attacker-chosen —
#: and a chunk URL is later interpolated into the ``<docs source="untrusted">`` envelope
#: shown to the model as well as rendered as an ``href``. A URL carrying ``</docs>`` would
#: close that envelope from inside. Real docs paths use only ``[a-z0-9-]`` and ``/`` with a
#: longest segment of 33 characters, so a slug whitelist costs nothing and closes both.
_URL_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9._-]{0,49}"
_URL_PATH_RE = re.compile(rf"(?:{_URL_SEGMENT}/){{0,9}}{_URL_SEGMENT}/?\Z")
#: Section keys additionally reach the system prompt, where prose is the threat rather than
#: markup; see ``docs_map``, which wraps them in the untrusted envelope for that reason.
_SECTION_KEY_RE = re.compile(r"[A-Za-z0-9._/-]{1,80}\Z")


@dataclass(slots=True)
class Chunk:
    """One retrievable slice of a documentation page."""

    id: str
    url: str
    title: str
    heading: str
    text: str
    lang: str


@dataclass(slots=True)
class Hit:
    """A chunk returned by a search, with its fused and raw scores."""

    chunk: Chunk
    score: float
    dense_score: float
    rank: int


def _top_indices(scores: np.ndarray, k: int) -> list[int]:
    """Return the indices of the ``k`` highest scores, best first.

    Uses ``argpartition`` so the whole array is never sorted.

    Args:
        scores: One score per document.
        k: How many indices to keep.

    Returns:
        At most ``k`` indices ordered by descending score.
    """
    total = int(scores.shape[0])
    k = min(k, total)
    if k <= 0:
        return []
    if k < total:
        candidates = np.argpartition(scores, total - k)[total - k :]
    else:
        candidates = np.arange(total)
    ordered = candidates[np.argsort(scores[candidates])[::-1]]
    return [int(i) for i in ordered]


class RetrievalService:
    """In-memory hybrid (dense + BM25) search over the documentation chunks."""

    def __init__(self) -> None:
        self._chunks: list[Chunk] = []
        self._by_url: dict[str, list[int]] = {}
        self._bm25: BM25Okapi | None = None
        self._bm25_ngram: BM25Okapi | None = None
        self._matrix: np.ndarray | None = None
        self._docs_map: str | None = None
        self._corpus_sha = ""
        self._corpus_meta: dict[str, object] = {}
        self._loaded = False

    # ------------------------------------------------------------------ loading

    def load(self) -> None:
        """Load chunks, build the BM25 index and attach embeddings when available.

        Idempotent and cheap to call again: subsequent calls return immediately. Any
        failure degrades the service (empty index, or BM25-only) instead of raising, so
        boot never depends on a data file or an API key.
        """
        if self._loaded:
            return
        data_dir = Path(settings.data_dir).expanduser()
        self._chunks = self._read_chunks(data_dir / CHUNKS_FILE)
        self._by_url = self._group_by_url(self._chunks)
        self._bm25 = self._build_bm25(self._chunks)
        self._bm25_ngram = self._build_bm25_ngram(self._chunks)
        # Hashed once here, not per query: it is both the staleness check below and the
        # corpus component of the answer-cache key.
        self._corpus_sha = self._file_sha256(data_dir / CHUNKS_FILE)
        self._corpus_meta = self._read_meta(data_dir / META_FILE)
        self._matrix = self._load_matrix(data_dir / EMBEDDINGS_FILE, self._chunks)
        self._loaded = True
        logger.info(
            "retrieval_loaded",
            chunks=len(self._chunks),
            embeddings=self._matrix is not None,
            urls=len(self._by_url),
            corpus_sha=self._corpus_sha[:12],
        )

    @staticmethod
    def _file_sha256(path: Path) -> str:
        """Hash a data file byte for byte, or return an empty string when unreadable."""
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            logger.warning("corpus_hash_failed", error=type(exc).__name__)
            return ""

    @staticmethod
    def _read_meta(path: Path) -> dict[str, object]:
        """Read the provenance sidecar; a missing or malformed file is not an error."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _read_chunks(self, path: Path) -> list[Chunk]:
        """Read ``chunks.jsonl``, skipping malformed lines."""
        if not path.is_file():
            logger.error("chunks_missing", path=str(path))
            return []
        chunks: list[Chunk] = []
        rejected = 0
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    # A chunk URL is rendered as a clickable href in the panel and is also
                    # interpolated into the <docs source="untrusted"> envelope the model
                    # reads, so it is a trust boundary on two sides: the corpus is
                    # third-party markdown and a poisoned page could otherwise smuggle a
                    # `javascript:` URI into an <a href>, or a `</docs>` into the envelope.
                    # This is the single seam every source flows through — the SSE
                    # `sources` event, the wizard responses and SourceRef alike.
                    url = str(raw.get("url", ""))
                    if not url.startswith(ALLOWED_URL_PREFIX) or not _URL_PATH_RE.match(
                        url[len(ALLOWED_URL_PREFIX) :]
                    ):
                        rejected += 1
                        continue
                    chunks.append(
                        Chunk(
                            id=str(raw.get("id", "")),
                            url=url,
                            title=str(raw.get("title", "")),
                            heading=str(raw.get("heading", "")),
                            text=str(raw.get("text", "")),
                            lang=str(raw.get("lang", "fa")),
                        )
                    )
        except OSError as exc:  # unreadable file, bad mount
            logger.error("chunks_unreadable", path=str(path), error=str(exc))
            return []
        if rejected:
            logger.warning("chunks_rejected_url", count=rejected, prefix=ALLOWED_URL_PREFIX)
        return chunks

    @staticmethod
    def _group_by_url(chunks: list[Chunk]) -> dict[str, list[int]]:
        """Map each page URL to its chunk indices, ordered by chunk id."""
        grouped: dict[str, list[int]] = {}
        for index, chunk in enumerate(chunks):
            if chunk.url:
                grouped.setdefault(chunk.url, []).append(index)
        for indices in grouped.values():
            indices.sort(key=lambda i: chunks[i].id)
        return grouped

    @staticmethod
    def _build_bm25(chunks: list[Chunk]) -> BM25Okapi | None:
        """Build the BM25 index over chunk text plus its title and heading."""
        if not chunks:
            return None
        corpus = [
            tokenize(f"{chunk.text} {chunk.title} {chunk.heading}") or [chunk.id]
            for chunk in chunks
        ]
        return BM25Okapi(corpus)

    @staticmethod
    def _build_bm25_ngram(chunks: list[Chunk]) -> BM25Okapi | None:
        """Build the character-4-gram BM25 index over the same text as :meth:`_build_bm25`.

        A third RRF list that matches sub-word: it recovers the morphological variants and
        the typos the word index cannot see. It bridges nothing across scripts — `داکر` and
        `docker` share no n-gram whatsoever, which stays :data:`app.shared.fa_words.SYNONYMS`'
        job.
        """
        if not chunks:
            return None
        # ponytail: measured +69.7 MB RSS and +1.0 s at load for 943k grams over 3500 chunks
        # (2.9 ms/query vs 1.4 ms for the word index), against a ~150 MB budget on a small
        # Liara plan. If the corpus grows past that, the downgrade is a single corpus-wide
        # dict[ngram, set[vocab_word]] (a few MB) used at query time to expand each query
        # token with vocabulary words sharing >= 60% of its n-grams, feeding the existing
        # word index instead of a second one.
        corpus = [
            char_ngrams(f"{chunk.text} {chunk.title} {chunk.heading}") or [chunk.id]
            for chunk in chunks
        ]
        return BM25Okapi(corpus)

    def _load_matrix(self, path: Path, chunks: list[Chunk]) -> np.ndarray | None:
        """Load the embedding matrix aligned to ``chunks``, or None for BM25-only mode."""
        if not chunks or not path.is_file():
            logger.warning("embeddings_missing", path=str(path), mode="bm25_only")
            return None
        try:
            data = np.load(path)
            ids = [str(i) for i in data["ids"]]
            vectors = np.asarray(data["vectors"], dtype=np.float32)
        except (OSError, ValueError, KeyError) as exc:
            logger.warning("embeddings_unreadable", error=str(exc), mode="bm25_only")
            return None
        if len(ids) != len(chunks) or vectors.shape[0] != len(chunks):
            logger.warning(
                "embeddings_count_mismatch",
                embeddings=len(ids),
                chunks=len(chunks),
                mode="bm25_only",
            )
            return None
        # A matrix built by a different EMBED_MODEL than the one answering queries would
        # still multiply cleanly if the widths happened to match, silently returning
        # nonsense rankings. Refuse the obvious case rather than trust it.
        width = int(vectors.shape[1]) if vectors.ndim == 2 else 0
        if width != settings.embed_dim:
            logger.warning(
                "embeddings_dim_mismatch",
                matrix_dim=width,
                configured_dim=settings.embed_dim,
                embed_model=settings.embed_model,
                mode="bm25_only",
                hint="regenerate data/embeddings.npz with the configured EMBED_MODEL",
            )
            return None
        # The width guard only catches a *different-sized* model. A same-width model, or a
        # matrix embedded from an older chunks.jsonl, multiplies cleanly and ranks nonsense,
        # so ingest stamps the model name and the corpus hash into the npz and they are
        # re-checked here against the file actually on disk. Values from the npz are never
        # logged — only whether each side agreed.
        if "meta" in data.files:
            try:
                meta = json.loads(str(data["meta"]))
            except ValueError:
                meta = None
            model_ok = isinstance(meta, dict) and meta.get("embed_model") == settings.embed_model
            corpus_ok = isinstance(meta, dict) and meta.get("corpus_sha256") == self._corpus_sha
            if not model_ok or not corpus_ok:
                logger.warning(
                    "embeddings_meta_mismatch",
                    model_ok=model_ok,
                    corpus_ok=corpus_ok,
                    configured_model=settings.embed_model,
                    corpus_sha=self._corpus_sha[:12],
                    mode="bm25_only",
                    hint="re-run ingest/reingest.sh to rebuild embeddings.npz for this corpus",
                )
                return None
        else:
            # An npz written before provenance existed. Tolerated on purpose: a deploy
            # landing between this code and the first re-ingest must keep dense retrieval.
            logger.warning("embeddings_meta_absent", mode="hybrid", hint="re-run ingest")
        chunk_ids = [chunk.id for chunk in chunks]
        if ids != chunk_ids:
            row_of = {chunk_id: row for row, chunk_id in enumerate(ids)}
            try:
                order = [row_of[chunk_id] for chunk_id in chunk_ids]
            except KeyError:
                logger.warning("embeddings_id_mismatch", mode="bm25_only")
                return None
            vectors = vectors[order]
        return np.ascontiguousarray(vectors)

    # ----------------------------------------------------------------- properties

    @property
    def is_loaded(self) -> bool:
        """Whether :meth:`load` has run."""
        return self._loaded

    @property
    def chunk_count(self) -> int:
        """Number of indexed chunks."""
        return len(self._chunks)

    @property
    def has_embeddings(self) -> bool:
        """Whether dense search is available."""
        return self._matrix is not None

    @property
    def corpus_fingerprint(self) -> str:
        """SHA-256 of ``chunks.jsonl`` as loaded, or ``""`` when it could not be read.

        Doubles as a cache key component: an answer cached against one corpus can never be
        served after the corpus changes.
        """
        return self._corpus_sha

    @property
    def corpus_meta(self) -> dict[str, object]:
        """A copy of the provenance sidecar — commit, ingest time, model. Never text."""
        return dict(self._corpus_meta)

    # -------------------------------------------------------------------- search

    async def search(self, query: str, k: int = RETRIEVAL_TOP_K) -> list[Hit]:
        """Search the corpus with dense + BM25 candidates fused by RRF.

        The dense list is skipped entirely when there are no embeddings or when the
        embedding API is unavailable, in which case the result is pure BM25 with
        ``dense_score`` 0.0.

        Args:
            query: The user's raw question.
            k: How many hits to return.

        Returns:
            Up to ``k`` hits ordered by fused score, ``rank`` being their final position.
        """
        self.load()
        if not self._chunks or not query.strip():
            return []
        vector: list[float] | None = None
        if self._matrix is not None:
            # Imported lazily: llm_service lives in this package and must not import at
            # module scope, or the two modules would import each other.
            from app.domain.services.llm_service import llm_service

            vector = await llm_service.embed_query(query)
        # BM25 scoring and the `matrix @ q` matmul are pure CPU over the whole 3.5k-chunk
        # corpus. The app runs one gunicorn worker, so doing this inline would block the
        # only event loop and stall every in-flight SSE answer plus the health probe.
        return await run_in_threadpool(self._search_sync, query, k, vector)

    def _search_sync(self, query: str, k: int, vector: list[float] | None) -> list[Hit]:
        """Score and fuse the candidate lists. Pure CPU — always call off the event loop.

        Args:
            query: The user's raw question.
            k: How many hits to return.
            vector: The query embedding, or None in BM25-only mode.

        Returns:
            Up to ``k`` fused hits.
        """
        cosines: np.ndarray | None = None
        dense_ranked: list[int] = []
        if vector:
            cosines = self._cosines(vector)
            if cosines is not None:
                dense_ranked = _top_indices(cosines, RETRIEVAL_CANDIDATES)
        bm25_ranked = self._bm25_candidates(query, RETRIEVAL_CANDIDATES)
        ngram_ranked = self._ngram_candidates(query, NGRAM_CANDIDATES)
        return self._fuse([dense_ranked, bm25_ranked, ngram_ranked], cosines, k)

    def search_bm25(self, query: str, k: int) -> list[Hit]:
        """Search with BM25 only — synchronous and free of any network call.

        Args:
            query: The user's raw question.
            k: How many hits to return.

        Returns:
            Up to ``k`` hits whose ``score`` is the raw BM25 score and whose
            ``dense_score`` is 0.0.
        """
        self.load()
        if not self._chunks or not query.strip():
            return []
        scores = self._bm25_scores(query)
        if scores is None:
            return []
        hits: list[Hit] = []
        for rank, index in enumerate(_top_indices(scores, k)):
            score = float(scores[index])
            if score <= 0.0:
                break
            hits.append(Hit(chunk=self._chunks[index], score=score, dense_score=0.0, rank=rank))
        return hits

    def _cosines(self, vector: list[float]) -> np.ndarray | None:
        """Cosine similarity of a query vector against every chunk, in one matmul."""
        matrix = self._matrix
        if matrix is None:
            return None
        query = np.asarray(vector, dtype=np.float32)
        if query.ndim != 1 or query.shape[0] != matrix.shape[1]:
            logger.warning("embed_dim_mismatch", query=int(query.size), corpus=matrix.shape[1])
            return None
        norm = float(np.linalg.norm(query))
        if norm > 0.0:
            query = query / norm
        return matrix @ query

    def _bm25_scores(self, query: str) -> np.ndarray | None:
        """Raw BM25 scores for a synonym-expanded query, or None when unusable."""
        if self._bm25 is None:
            return None
        tokens = expand_query(tokenize(query))
        if not tokens:
            return None
        return np.asarray(self._bm25.get_scores(tokens), dtype=np.float32)

    def _bm25_candidates(self, query: str, k: int) -> list[int]:
        """Indices of the top BM25 documents, dropping zero-score ones."""
        scores = self._bm25_scores(query)
        if scores is None:
            return []
        return [index for index in _top_indices(scores, k) if scores[index] > 0.0]

    def _ngram_candidates(self, query: str, k: int) -> list[int]:
        """Indices of the top character-n-gram documents, dropping zero-score ones.

        Deliberately unexpanded: a synonym contributes word-level meaning, and turning it
        into n-grams would only add the grams of a word the page never spelled.
        """
        if self._bm25_ngram is None:
            return []
        grams = char_ngrams(query)
        if not grams:
            return []
        scores = np.asarray(self._bm25_ngram.get_scores(grams), dtype=np.float32)
        return [index for index in _top_indices(scores, k) if scores[index] > 0.0]

    def _fuse(
        self, ranked_lists: list[list[int]], cosines: np.ndarray | None, k: int
    ) -> list[Hit]:
        """Fuse candidate lists with Reciprocal Rank Fusion and build the hits."""
        fused: dict[int, float] = {}
        for ranked in ranked_lists:
            for position, index in enumerate(ranked):
                fused[index] = fused.get(index, 0.0) + 1.0 / (RRF_K + position + 1)
        if not fused:
            return []
        def _dense_of(index: int) -> float:
            return float(cosines[index]) if cosines is not None else 0.0

        ordered = sorted(fused.items(), key=lambda item: (-item[1], -_dense_of(item[0])))
        hits: list[Hit] = []
        for rank, (index, score) in enumerate(ordered[: max(k, 0)]):
            hits.append(
                Hit(
                    chunk=self._chunks[index],
                    score=float(score),
                    dense_score=_dense_of(index),
                    rank=rank,
                )
            )
        return hits

    # --------------------------------------------------------------------- pages

    def get_page(self, url: str, limit: int = PAGE_CHARS) -> str | None:
        """Return a whole documentation page as text, clamped to ``limit``.

        Args:
            url: Canonical docs.liara.ir URL of the page.
            limit: Character budget for the joined page. The default is the general
                reading budget; a caller that needs a whole long page — the config
                wizard and its `liara.json` reference — raises it deliberately.

        Returns:
            The page's chunks joined in id order, or None when the URL is unknown.
        """
        self.load()
        indices = self._resolve_url(url)
        if indices is None:
            return None
        parts: list[str] = []
        budget = limit
        for index in indices:
            text = self._chunks[index].text.strip()
            if not text:
                continue
            parts.append(text[:budget])
            budget -= len(parts[-1]) + 2
            if budget <= 0:
                break
        return "\n\n".join(parts)[:limit]

    def known_urls(self) -> set[str]:
        """Return every documentation URL present in the index."""
        self.load()
        return set(self._by_url)

    def docs_map(self) -> str:
        """Return a compact map of what the corpus contains, built once and cached.

        Each entry is the first two path segments of a page URL plus the number of pages
        under it, so the model can see which products and platforms exist and pick search
        terms accordingly. It deliberately carries **no document text**: only path
        segments that already passed the load-time :data:`ALLOWED_URL_PREFIX` allowlist,
        plus integer counts.

        Returns:
            ``"section/ (n) | section/ (n) | …"``, clamped at an entry boundary to
            :data:`DOCS_MAP_MAX_CHARS`, or an empty string when the index is empty.
        """
        self.load()
        if self._docs_map is not None:
            return self._docs_map
        counts: Counter[str] = Counter()
        rejected = 0
        for url in self._by_url:
            segments = [part for part in url[len(ALLOWED_URL_PREFIX) :].split("/") if part]
            if not segments:
                continue
            key = "/".join(segments[:2])
            if _SECTION_KEY_RE.match(key):
                counts[key] += 1
            else:
                rejected += 1
        if rejected:
            logger.warning("docs_map_rejected_section", count=rejected)
        rendered = " | ".join(f"{section}/ ({count})" for section, count in sorted(counts.items()))
        if len(rendered) > DOCS_MAP_MAX_CHARS:
            rendered = rendered[:DOCS_MAP_MAX_CHARS].rsplit(" | ", 1)[0]
            logger.warning("docs_map_truncated", sections=len(counts), chars=len(rendered))
        self._docs_map = rendered
        return rendered

    def _resolve_url(self, url: str) -> list[int] | None:
        """Look a URL up, tolerating a missing or extra trailing slash."""
        candidate = (url or "").strip()
        if not candidate:
            return None
        for variant in (candidate, candidate.rstrip("/"), candidate.rstrip("/") + "/"):
            indices = self._by_url.get(variant)
            if indices:
                return indices
        return None


retrieval_service = RetrievalService()
