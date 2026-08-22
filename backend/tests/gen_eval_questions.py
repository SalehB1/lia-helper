"""One-shot generator for eval question candidates — the raw material for the golden set.

``eval_questions.json`` was hand-written and small. Growing it to ~100 items by hand is the
kind of work a model does well and a human then has to distrust, so this script produces
*candidates* only: it never writes ``eval_questions.json``. Its output goes to
``eval_candidates.json`` and a human curates from there, dropping anything that leaks the
page's own heading vocabulary (which would inflate literal recall and make the harness lie),
anything whose ``expect_url_contains`` is ambiguous, and anything the page cannot answer.

Costs one cheap LLM call per sampled page — a few cents for a full run. Usage (from
``backend/``, and behind a local proxy ``NO_PROXY`` must list the provider host or every
call hangs)::

    NO_PROXY=api.avalai.ir .venv-uv/bin/python -m tests.gen_eval_questions
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.domain.services.llm_service import llm_service  # noqa: E402
from app.domain.services.retrieval_service import ALLOWED_URL_PREFIX  # noqa: E402
from app.domain.services.retrieval_service import retrieval_service  # noqa: E402

OUT_PATH = Path(__file__).with_name("eval_candidates.json")
#: Pages to sample. Proportional to each ``docs_map()`` section's share of the corpus, so the
#: sample mirrors what the corpus actually contains, and capped so no one section dominates.
TARGET_PAGES = 75
MAX_PER_SECTION = 3
PAGE_BUDGET_CHARS = 4000
CONCURRENCY = 4
SEED = 1402

#: The inverse of ``eval_agentic._REWRITE_PROMPT``: that one turns a question into doc
#: vocabulary, this one must turn a doc into a question that carries none of it.
_PROMPT = (
    "تو آزمونگر مستندات لیارا هستی. متن یک صفحهٔ مستندات را می‌خوانی و دقیقاً دو پرسش "
    "فارسی می‌سازی که هر دو فقط با همین صفحه پاسخ داده شوند:\n"
    "۱) literal — پرسش مستقیم و فنی یک کاربر، با همان واژگان مستنداتی صفحه.\n"
    "۲) paraphrase — همان نیاز، اما محاوره‌ای و از زبان کاربری که این صفحه را ندیده است. "
    "در این پرسش هیچ‌یک از واژه‌های عنوان صفحه و خطوط سرتیتر (خطوط آغازشده با #) را به کار "
    "نبر، نه فارسی و نه انگلیسی؛ مسئله یا هدف کاربر را توصیف کن، نه نام قابلیت را.\n"
    "هیچ‌کدام از دو پرسش نباید به «این صفحه» یا «متن بالا» ارجاع دهد؛ باید مستقل و "
    "قابل‌پرسیدن در یک چت باشند. خروجی فقط یک شیء JSON در یک خط:\n"
    '{"literal": "…", "paraphrase": "…"}'
)


def _sample_pages() -> list[str]:
    """Pick pages spread proportionally over the sections ``docs_map`` reports.

    Returns:
        Page URLs, deterministic for a fixed corpus because the RNG is seeded.
    """
    sections = {entry.split("/ (")[0] for entry in retrieval_service.docs_map().split(" | ")}
    by_section: dict[str, list[str]] = defaultdict(list)
    for url in sorted(retrieval_service.known_urls()):
        parts = [part for part in url[len(ALLOWED_URL_PREFIX) :].split("/") if part]
        key = "/".join(parts[:2])
        if key in sections:
            by_section[key].append(url)
    total = sum(len(urls) for urls in by_section.values())
    rng = random.Random(SEED)
    picked: list[str] = []
    for urls in by_section.values():
        quota = min(MAX_PER_SECTION, round(TARGET_PAGES * len(urls) / total), len(urls))
        picked.extend(rng.sample(urls, quota))
    return sorted(picked)


def _slug(url: str, corpus: list[str]) -> str | None:
    """Return the shortest trailing path fragment that matches this page and no other.

    Args:
        url: The page to identify.
        corpus: Every known page URL, lowercased.

    Returns:
        A fragment usable as ``expect_url_contains``, or None when even four segments are
        ambiguous — such a page cannot be scored and is skipped.
    """
    parts = [part for part in url.lower()[len(ALLOWED_URL_PREFIX) :].split("/") if part]
    for depth in (1, 2, 3, 4):
        fragment = "/".join(parts[-depth:])
        if sum(fragment in candidate for candidate in corpus) == 1:
            return fragment
    return None


def _parse(text: str) -> tuple[str, str] | None:
    """Pull ``(literal, paraphrase)`` out of a model reply, tolerating fences and prose."""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    literal = str(data.get("literal") or "").strip()
    paraphrase = str(data.get("paraphrase") or "").strip()
    return (literal, paraphrase) if literal and paraphrase else None


async def _ask(url: str, gate: asyncio.Semaphore) -> tuple[str, str] | None:
    """Ask the model for one literal and one colloquial question about ``url``."""
    page = retrieval_service.get_page(url)
    if not page:
        return None
    async with gate:
        try:
            result = await llm_service.complete(
                [
                    {"role": "system", "content": _PROMPT},
                    {"role": "user", "content": f"URL: {url}\n\n{page[:PAGE_BUDGET_CHARS]}"},
                ],
                model=settings.model_primary,
            )
        except Exception as exc:  # noqa: BLE001 - one flaky page must not end the run
            print(f"    ({type(exc).__name__} on {url})")
            return None
    return _parse(result.text)


async def main() -> int:
    """Sample pages, generate two candidates each and write ``eval_candidates.json``."""
    retrieval_service.load()
    if not retrieval_service.chunk_count:
        print("no corpus loaded — run ingest first")
        return 1
    if not settings.has_llm:
        print("no LLM key configured — this generator needs one")
        return 1

    corpus = sorted(url.lower() for url in retrieval_service.known_urls())
    pages = [(url, _slug(url, corpus)) for url in _sample_pages()]
    pages = [(url, slug) for url, slug in pages if slug]
    print(f"corpus={retrieval_service.chunk_count} chunks  pages={len(pages)}\n")

    gate = asyncio.Semaphore(CONCURRENCY)
    replies = await asyncio.gather(*(_ask(url, gate) for url, _ in pages))
    candidates: list[dict] = []
    for (url, slug), reply in zip(pages, replies):
        if not reply:
            continue
        literal, paraphrase = reply
        candidates.append({"q": literal, "expect_url_contains": [slug], "kind": "simple"})
        candidates.append({"q": paraphrase, "expect_url_contains": [slug], "kind": "paraphrase"})
        print(f"{slug}\n  L: {literal}\n  P: {paraphrase}")
    OUT_PATH.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {len(candidates)} candidates from {len(candidates) // 2} pages → {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
