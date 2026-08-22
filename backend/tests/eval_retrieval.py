"""Retrieval quality harness — measures recall on real Persian questions.

Answer quality is the largest share of this project's judging weight, so it is measured
rather than asserted. Run it after any change to chunking, normalization, the synonym
tables, or the fusion strategy, and again after generating ``data/embeddings.npz`` to see
what dense retrieval actually buys.

Usage (from ``backend/``)::

    ../.venv/bin/python -m tests.eval_retrieval

Ground truth is deliberately coarse: a hit counts when the URL contains any of the expected
fragments. That tolerates the corpus being reorganized upstream while still catching a real
regression. ``out_of_scope`` questions have no ground truth — they exist to show the
separation between a question the corpus can answer and one it cannot.

``paraphrase`` questions carry the same ``expect_url_contains`` as the literal question they
restate, but are worded the way a user actually asks: colloquial, and deliberately avoiding
the vocabulary the matching doc uses in its own headings. They isolate the complaint that
retrieval "only matches literal keywords and cannot infer" — the LITERAL-vs-PARAPHRASE gap
printed at the bottom of the report is that complaint as a number.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domain.services.retrieval_service import retrieval_service  # noqa: E402

QUESTIONS_PATH = Path(__file__).with_name("eval_questions.json")
LITERAL_KINDS = ("simple", "troubleshoot", "complex")
PARAPHRASE_KIND = "paraphrase"
SCORED_KINDS = (*LITERAL_KINDS, PARAPHRASE_KIND)

# Two floors instead of one pooled floor, on purpose.
#
# The pooled 0.7 used to cover 20 literal questions. Pooling the harder paraphrases in with
# them would break the guard in both directions: paraphrase misses eat the headroom that was
# meant to catch a literal regression, and once the pool is big enough a literal regression
# hides inside the paraphrase slack (65/85 clears a pooled 0.7 floor even if literal recall
# collapses from 51/55 to 39/55). So each pool is guarded separately.
#
# Both floors sit one to five misses BELOW the worst measured run, not at a target:
# paraphrases are legitimately harder today and this harness exists to state that gap
# honestly, not to fail until the retrieval architecture changes.
#
# Measured on the 100-question set (85 scored), hybrid, three consecutive runs after the
# character n-gram list and the breadcrumb re-ingest landed: literal 51/55 (93%) every run,
# paraphrase 18, 18, 19 out of 30 (60-63%). Paraphrase moved by one because an embedding
# call can still time out and silently degrade that one query to BM25 — the ceiling is now
# 3s with one retry rather than 20s, so the tail is bounded, not eliminated.
#
# PARAPHRASE_FLOOR 0.57 → floor 17, one miss under the worst observed 18. It was 0.44
# (floor 13) when paraphrase scored 14/30; leaving it there after the n-gram list took the
# number to 18 would have let a five-question regression of that very change pass silently,
# which is not a guard. LITERAL_FLOOR was 0.7 → floor 38 for the same reason, while literal
# has not moved off 51/55 in any run: 0.85 → floor 46 keeps five misses of slack for
# upstream reorganising a URL, and finally fails on the collapse this block was written to
# catch. Raise both the moment the architecture improves the number again.
LITERAL_FLOOR = 0.85
PARAPHRASE_FLOOR = 0.57


def _load_questions() -> list[dict]:
    """Read the question set that ships next to this file."""
    return json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))


async def main() -> int:
    """Run every question, print a per-category report and return an exit code."""
    retrieval_service.load()
    if not retrieval_service.chunk_count:
        print("no corpus loaded — run: ../.venv/bin/python ingest/ingest.py --skip-embeddings")
        return 1

    mode = "hybrid" if retrieval_service.has_embeddings else "BM25-only"
    print(f"corpus={retrieval_service.chunk_count} chunks  mode={mode}\n")

    hits_at_1: dict[str, list[bool]] = {}
    hits_at_5: dict[str, list[bool]] = {}
    timings: list[float] = []
    misses: list[tuple[str, str, str]] = []
    oos_scores: list[float] = []

    for item in _load_questions():
        question, expected, kind = item["q"], item["expect_url_contains"], item["kind"]
        started = time.perf_counter()
        results = await retrieval_service.search(question, k=5)
        timings.append((time.perf_counter() - started) * 1000)
        urls = [hit.chunk.url.lower() for hit in results]

        if kind == "out_of_scope":
            top = f"{results[0].score:.4f}" if results else "no hits"
            if results:
                oos_scores.append(results[0].score)
            print(f"[out-of-scope] {question}\n    top={top}")
            continue

        at_1 = bool(urls) and any(fragment in urls[0] for fragment in expected)
        at_5 = any(fragment in url for url in urls for fragment in expected)
        hits_at_1.setdefault(kind, []).append(at_1)
        hits_at_5.setdefault(kind, []).append(at_5)
        if not at_5:
            misses.append((kind, question, urls[0] if urls else "-"))
        tag = "PARA" if kind == PARAPHRASE_KIND else "    "
        print(f"[{'OK  ' if at_5 else 'MISS'}]{tag} {question}\n    {urls[0] if urls else '-'}")

    def _rate(kinds: tuple[str, ...]) -> tuple[list[bool], list[bool]]:
        ones = [v for kind in kinds for v in hits_at_1.get(kind, [])]
        fives = [v for kind in kinds for v in hits_at_5.get(kind, [])]
        return ones, fives

    def _line(label: str, ones: list[bool], fives: list[bool]) -> None:
        pct = 100.0 * sum(fives) / len(fives) if fives else 0.0
        print(
            f"{label:14s} recall@1={sum(ones)}/{len(ones)}  "
            f"recall@5={sum(fives)}/{len(fives)}  ({pct:.0f}%)"
        )

    print("\n" + "=" * 60)
    for kind in LITERAL_KINDS:
        ones, fives = _rate((kind,))
        if fives:
            _line(kind, ones, fives)
    literal_1, literal_5 = _rate(LITERAL_KINDS)
    _line("LITERAL", literal_1, literal_5)

    # Printed on its own, below the literal block: the paraphrase number is the one the
    # architecture change is meant to move, so it must not average away into the total.
    print("-" * 60)
    para_1, para_5 = _rate((PARAPHRASE_KIND,))
    _line(PARAPHRASE_KIND, para_1, para_5)
    if literal_5 and para_5:
        literal_pct = 100.0 * sum(literal_5) / len(literal_5)
        para_pct = 100.0 * sum(para_5) / len(para_5)
        print(f"{'GAP':14s} literal - paraphrase = {literal_pct - para_pct:+.0f} pts recall@5")
    print("-" * 60)

    all_1, all_5 = _rate(SCORED_KINDS)
    _line("OVERALL", all_1, all_5)
    timings.sort()
    print(f"latency ms     median={timings[len(timings) // 2]:.1f}  max={timings[-1]:.1f}")
    if oos_scores:
        mean_oos = sum(oos_scores) / len(oos_scores)
        print(f"{'out-of-scope':14s} top score max={max(oos_scores):.4f}  mean={mean_oos:.4f}")

    if misses:
        print("\nmisses:")
        for kind, question, got in misses:
            print(f"  - [{kind}] {question}\n      got: {got}")

    # Guards against a real regression, not a target to chase. Recorded baselines on the
    # 100-question set, hybrid: literal recall@1 43/55 and recall@5 51/55; paraphrase
    # recall@1 9/30 and recall@5 14/30. (On the previous 38-question set: literal 18/20
    # BM25-only, 19/20 hybrid; paraphrase 6/11 BM25-only, 4-6/11 hybrid.) Growing the set
    # nearly threefold did not close the gap — that 93%-vs-47% split is the finding this
    # harness exists to publish.
    failed = False
    for label, fives, ratio in (
        ("literal", literal_5, LITERAL_FLOOR),
        ("paraphrase", para_5, PARAPHRASE_FLOOR),
    ):
        floor = int(len(fives) * ratio)
        if sum(fives) < floor:
            print(f"\nFAIL: {label} recall@5 {sum(fives)}/{len(fives)} is below the {floor} floor")
            failed = True
    if failed:
        return 1
    print("\nok")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
