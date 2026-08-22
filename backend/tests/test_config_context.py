"""Self-check for the documentation the `liara.json` wizard is given to write from.

The wizard is one LLM call over whatever ``gather_config_context`` collected, and it is
told to invent nothing — so a key missing from the context is a key the wizard *denies
exists*, in Persian, while citing the page that documents it. That failure is invisible
from the outside (valid JSON, real citations, HTTP 200), which is why it shipped: the
reference page is 15,381 characters and was being cut at 6,000 by a budget the fetch had
already clamped to 8,000 above it.

Reads the real corpus in ``data/`` and may make one embedding call per need (retrieval
degrades to BM25 without it, which is fine here). No database, no model call.

Usage (from ``backend/``)::

    .venv-uv/bin/python -m tests.test_config_context
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domain.services.citations import CitationRegistry  # noqa: E402
from app.domain.services.tools_service import (  # noqa: E402
    LIARA_JSON_PAGE_CHARS,
    _LIARA_JSON,
    gather_config_context,
)
from app.shared.enums import Platform  # noqa: E402

#: Opens every numbered snippet block — see `tools_service._source_line`.
_CITATION_HEADER_RE = re.compile(r"^\[\d+\] «", re.MULTILINE)

#: Keys documented on the reference page *after* the old 6000-char cut, in offset order.
#: Every one of them was denied by the wizard while the page it is on was cited.
_KEYS_PAST_THE_OLD_CUT = (
    "team-id",
    "pythonVersion",
    "timezone",
    "cron",
    "healthCheck",
    "disks",
    "mountTo",
    "envs",
    "collectStatic",
)


async def test_the_whole_reference_page_reaches_the_context() -> None:
    """Not a sample of keys — the last one on the page, or the cut simply moved."""
    context = await gather_config_context(Platform.DJANGO, None, CitationRegistry())
    missing = [key for key in _KEYS_PAST_THE_OLD_CUT if key not in context.content]
    assert not missing, missing


async def test_the_reference_budget_still_covers_the_page() -> None:
    """A page that grows past the budget silently truncates again, so measure it."""
    from app.domain.services.retrieval_service import retrieval_service

    page = retrieval_service.get_page(_LIARA_JSON, limit=LIARA_JSON_PAGE_CHARS + 1000) or ""
    assert page, _LIARA_JSON
    assert len(page) < LIARA_JSON_PAGE_CHARS, (len(page), LIARA_JSON_PAGE_CHARS)


async def test_the_same_text_is_never_cited_twice() -> None:
    """No citation may repeat text the model has already been shown.

    The invariant is about text, not URLs. Deduping on the URL alone was tried and is
    wrong: every page except the reference one is emitted truncated to
    ``CONFIG_PAGE_CHARS``, so the section a ``need`` asked for usually sits past the cut,
    and dropping it on a URL match kept the 1500 chars that do not answer the user and
    discarded the one chunk that does. Several sections of one long page under separate
    numbers is correct — a claim about ``collectStatic`` must cite the chunk that
    documents it, not the top of its page.
    """
    registry = CitationRegistry()
    result = await gather_config_context(
        Platform.DJANGO,
        ["متغیرهای محیطی", "دیسک برای فایل‌های آپلودی", "liara.json"],
        registry,
    )
    # Split on the citation header, not on blank lines: a block's own body contains them,
    # so `split("\n\n")` shreds one page into its paragraphs and compares those instead.
    parts = _CITATION_HEADER_RE.split(result.content)[1:]
    bodies = [part.strip() for part in parts if part.strip()]
    assert len(bodies) == registry.count, (len(bodies), registry.count)
    for i, body in enumerate(bodies):
        for j, other in enumerate(bodies):
            if i >= j:
                continue
            assert body[:200] not in other, f"citation {i + 1} is repeated inside {j + 1}"

    # The reference page IS emitted whole, so nothing can lie past its cut: any second
    # citation of it would be text the model already has.
    urls = [source["url"] for source in registry.all_sources()]
    assert urls.count(_LIARA_JSON) == 1, urls


def main() -> int:
    """Run every ``test_*`` in this module, reporting the first failure."""
    checks = [
        (name, value)
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for name, check in checks:
        try:
            asyncio.run(check())
        except AssertionError as exc:
            print(f"FAIL  {name}\n      {exc}")
            return 1
        print(f"ok  {name}")
    print(f"\n{len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
