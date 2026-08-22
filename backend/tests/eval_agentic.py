"""Does letting the model write the search query beat searching the user's raw sentence?

``eval_retrieval`` measures the index alone: it feeds each question verbatim to
``retrieval_service.search``. That is the old architecture, where a mandatory pre-retrieval
ran on the user's own words before the model ever saw them, and it is why paraphrased
questions score far below literal ones — the corpus says «متغیر محیطی», the user says «رمزها
رو کجا بذارم».

The current architecture searches only through the model's own tool call, so the query is
whatever the model composes after reading prompt rule ۱. This harness measures that: same
questions, same index, but the query is produced by the model first. The difference between
the two runs is what the refactor actually bought.

Costs one cheap LLM call per question. Usage (from ``backend/``)::

    .venv-uv/bin/python -m tests.eval_agentic          # paraphrase questions only
    .venv-uv/bin/python -m tests.eval_agentic --all    # every scored question
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.domain.services.llm_service import llm_service  # noqa: E402
from app.domain.services.retrieval_service import retrieval_service  # noqa: E402

QUESTIONS_PATH = Path(__file__).with_name("eval_questions.json")

#: Rule ۱ of the system prompt, reduced to the single decision this harness measures.
_REWRITE_PROMPT = (
    "تو دستیار مستندات لیارا هستی و می‌خواهی در مستندات جست‌وجو کنی. برای پرسش زیر فقط و "
    "فقط عبارت جست‌وجو را بنویس — جملهٔ کاربر را عیناً تکرار نکن؛ از واژه‌های مستنداتی، نام "
    "سرویس یا پلتفرم لیارا و اصطلاح فنی انگلیسی در کنار فارسی استفاده کن. خروجی فقط یک خط."
)


async def _rewrite(question: str, attempts: int = 3) -> str:
    """Ask the model for the search query it would send for this question.

    A dropped stream falls back to the raw question, which scores the harness *against*
    the rewrite — a flaky connection can only understate the benefit, never inflate it.
    """
    for attempt in range(attempts):
        parts: list[str] = []
        try:
            async for chunk in llm_service.stream_chat(
                [
                    {"role": "system", "content": _REWRITE_PROMPT},
                    {"role": "user", "content": question},
                ],
                None,
                None,
                model=settings.model_primary,
            ):
                if chunk.kind == "token":
                    parts.append(chunk.text)
        except Exception as exc:  # noqa: BLE001 - a flaky link must not end the run
            print(f"    (rewrite attempt {attempt + 1} failed: {type(exc).__name__})")
            await asyncio.sleep(1.0 * (attempt + 1))
            continue
        text = "".join(parts).strip()
        if text:
            return text.splitlines()[0][:300]
    return question


async def _recall(query: str, expected: list[str]) -> tuple[bool, bool, str]:
    """Return ``(hit@1, hit@5, top_url)`` for one query."""
    results = await retrieval_service.search(query, k=5)
    urls = [hit.chunk.url.lower() for hit in results]
    at_1 = bool(urls) and any(fragment in urls[0] for fragment in expected)
    at_5 = any(fragment in url for url in urls for fragment in expected)
    return at_1, at_5, urls[0] if urls else "-"


async def main() -> int:
    """Score raw-question retrieval against model-rewritten retrieval."""
    retrieval_service.load()
    if not retrieval_service.chunk_count:
        print("no corpus loaded — run ingest first")
        return 1
    if not settings.has_llm:
        print("no LLM key configured — this harness needs one")
        return 1

    everything = "--all" in sys.argv
    items = [
        item
        for item in json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
        if item["kind"] != "out_of_scope" and (everything or item["kind"] == "paraphrase")
    ]
    mode = "hybrid" if retrieval_service.has_embeddings else "BM25-only"
    print(f"corpus={retrieval_service.chunk_count} chunks  mode={mode}  questions={len(items)}\n")

    raw_5 = agent_5 = raw_1 = agent_1 = 0
    fixed: list[tuple[str, str]] = []
    broke: list[tuple[str, str]] = []

    for item in items:
        question, expected = item["q"], item["expect_url_contains"]
        r1, r5, _ = await _recall(question, expected)
        query = await _rewrite(question)
        a1, a5, a_top = await _recall(query, expected)
        raw_1, raw_5 = raw_1 + r1, raw_5 + r5
        agent_1, agent_5 = agent_1 + a1, agent_5 + a5
        if a5 and not r5:
            fixed.append((question, query))
        elif r5 and not a5:
            broke.append((question, query))
        flag = "FIXED " if (a5 and not r5) else ("BROKE " if (r5 and not a5) else "      ")
        print(f"[{'OK  ' if a5 else 'MISS'}]{flag}{question}\n    → «{query}»\n    {a_top}")

    total = len(items)
    print("\n" + "=" * 60)
    print(f"{'raw question':16s} recall@1={raw_1}/{total}  recall@5={raw_5}/{total}")
    print(f"{'model query':16s} recall@1={agent_1}/{total}  recall@5={agent_5}/{total}")
    print(f"{'delta':16s} recall@1={agent_1 - raw_1:+d}      recall@5={agent_5 - raw_5:+d}")
    if fixed:
        print(f"\nfixed by rewriting ({len(fixed)}):")
        for question, query in fixed:
            print(f"  - {question}\n      → {query}")
    if broke:
        print(f"\nregressed by rewriting ({len(broke)}):")
        for question, query in broke:
            print(f"  - {question}\n      → {query}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
