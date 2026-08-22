"""Self-check that ``ingest.py --resume`` can never keep a vector for text that changed.

Run it directly: ``python -m tests.test_resume_freshness`` from ``backend/``.
No framework, no network — every check is a plain assert against a temp npz.

The failure this guards is silent by construction: chunk ids are page-local positions, so
an upstream edit that leaves a page's chunk count alone reuses a vector built from text
that no longer exists, while the corpus hash stamped into the npz says the run was clean.
Dense retrieval then ranks on semantics the corpus no longer has, and nothing logs it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest.ingest import embed_text_of, load_existing, save_embeddings, text_sha  # noqa: E402

_DIM = 4


def _chunk(chunk_id: str, text: str, title: str = "عنوان", heading: str = "سرتیتر") -> dict:
    return {"id": chunk_id, "title": title, "heading": heading, "text": text}


def _write(out_dir: Path, chunks: list[dict], vectors: list[list[float]]) -> None:
    shas = [text_sha(embed_text_of(c)) for c in chunks]
    save_embeddings(out_dir, [c["id"] for c in chunks], vectors, None, shas)


def test_changed_text_is_not_reused() -> None:
    """An edit that leaves the chunk id alone must still invalidate that chunk's vector."""
    with TemporaryDirectory() as tmp:
        out = Path(tmp)
        old = _chunk("page#0000", "متن قدیمی")
        _write(out, [old], [[1.0] + [0.0] * (_DIM - 1)])

        cached = load_existing(out)
        edited = _chunk("page#0000", "متن جدید و کاملا متفاوت")
        assert text_sha(embed_text_of(old)) in cached, "unchanged text should be reusable"
        assert text_sha(embed_text_of(edited)) not in cached, "edited text reused a stale vector"


def test_moved_text_is_reused() -> None:
    """Content addressing widens reuse: the same text at a new position keeps its vector."""
    with TemporaryDirectory() as tmp:
        out = Path(tmp)
        _write(out, [_chunk("page#0000", "متن ثابت")], [[0.0, 1.0, 0.0, 0.0]])

        cached = load_existing(out)
        moved = _chunk("page#0007", "متن ثابت")  # same text, different page-local position
        assert text_sha(embed_text_of(moved)) in cached, "identical text should survive a move"


def test_heading_change_invalidates() -> None:
    """The breadcrumb is part of the embedding input, so changing it must re-embed."""
    with TemporaryDirectory() as tmp:
        out = Path(tmp)
        _write(out, [_chunk("page#0000", "متن", heading="داکر")], [[0.0, 0.0, 1.0, 0.0]])

        cached = load_existing(out)
        rehomed = _chunk("page#0000", "متن", heading="داکر › متغیرهای محیطی")
        assert text_sha(embed_text_of(rehomed)) not in cached, "heading change must re-embed"


def test_legacy_npz_rebuilds_from_scratch() -> None:
    """An npz written before content addressing has no hashes, so nothing may be reused."""
    with TemporaryDirectory() as tmp:
        out = Path(tmp)
        save_embeddings(out, ["page#0000"], [[1.0, 0.0, 0.0, 0.0]])  # no text_shas
        assert load_existing(out) == {}, "a hash-less npz must force a full re-embed"


if __name__ == "__main__":
    test_changed_text_is_not_reused()
    test_moved_text_is_reused()
    test_heading_change_invalidates()
    test_legacy_npz_rebuilds_from_scratch()
    print("ok")
