"""Agent tools: documentation search, page reading, config help and log diagnosis.

Every tool returns a :class:`ToolResult` whose ``content`` is already wrapped in a
``<docs source="untrusted">`` envelope. Nothing here ever raises into the agent loop:
an unknown tool, malformed arguments or an empty result all come back as a single
explanatory Persian line the model can act on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.domain.services.citations import CitationRegistry
from app.domain.services.retrieval_service import Chunk, Hit, retrieval_service
from app.shared.constants import PAGE_CHARS, RAW_FUSE_K, RETRIEVAL_TOP_K, SNIPPET_CHARS
from app.shared.enums import Platform, ToolName
from app.shared.persian import normalize

logger = get_logger("tools")

# Per-page budget when several whole pages are stuffed into one config context.
CONFIG_PAGE_CHARS = 1500

#: The `liara.json` reference page gets a much larger slice than the platform guides around
#: it, because it is the only page that documents the keys of the very file the wizard is
#: asked to write. At 1500 chars the cut landed inside a run of ``liara init`` CLI examples
#: which that page fences as ```json — so the model was handed command-line flags labelled
#: as JSON and nothing else, and dutifully emitted `{"-n": …, "-P": …}` as the config file.
#: The real keys ("app", "platform", "port") start around char 2850.
#:
#: Raising it to 6000 was still not enough, because the keys are spread over the whole
#: page: the measured length is 15,381 chars and `disks` sits at 12,153, `envs` at 12,805,
#: `collectStatic` at 13,914 and the complete worked example at 14,878. Anything short of
#: the whole page makes the wizard deny keys that are plainly documented, so this budget is
#: sized to cover it with room for upstream edits — and it must be passed to `get_page`,
#: whose own default would otherwise clamp the page to 8000 before this is applied.
LIARA_JSON_PAGE_CHARS = 16000
# Hard clamps on model-supplied arguments.
MAX_QUERY_CHARS = 300
MAX_NEED_CHARS = 80
MAX_NEEDS = 10
MAX_SIGNATURE_CHARS = 200

DOCS_OPEN = '<docs source="untrusted">'
DOCS_CLOSE = "</docs>"

TOOL_LABELS: dict[ToolName, str] = {
    ToolName.SEARCH_DOCS: "جست‌وجو در مستندات",
    ToolName.READ_PAGE: "مطالعهٔ صفحهٔ مستندات",
    ToolName.GENERATE_CONFIG: "آماده‌سازی پیکربندی",
    ToolName.DIAGNOSE_LOG: "تحلیل لاگ خطا",
}

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": ToolName.SEARCH_DOCS.value,
            "description": (
                "Search the Liara documentation corpus (Persian and English) and return "
                "numbered snippets with their source URLs. Use it for every factual claim. "
                "Write the query in the user's own words plus the product name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query, Persian or English, max 300 chars.",
                    },
                    "k": {
                        "type": "integer",
                        "description": f"How many snippets to return (1-{RETRIEVAL_TOP_K}).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": ToolName.READ_PAGE.value,
            "description": (
                "Read one whole documentation page from the local corpus. The URL must be "
                "one that a previous search result returned; no other URL can be fetched."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Canonical docs.liara.ir URL taken from a search result.",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": ToolName.GENERATE_CONFIG.value,
            "description": (
                "Collect the canonical deployment docs for one platform (getting started, "
                "liara.json, environment variables) so a liara.json can be written for it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "platform": {
                        "type": "string",
                        "enum": [item.value for item in Platform],
                        "description": "Target Liara platform.",
                    },
                    "needs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Extra requirements such as disk, cron, websocket, static files. "
                            f"Max {MAX_NEEDS} short items."
                        ),
                    },
                },
                "required": ["platform"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": ToolName.DIAGNOSE_LOG.value,
            "description": (
                "Extract the error signature from a build or runtime log and fetch the "
                "documentation pages that explain that class of failure."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "log": {
                        "type": "string",
                        "description": "Raw log text pasted by the user.",
                    }
                },
                "required": ["log"],
            },
        },
    },
]

# Canonical entry points per platform. Every URL below is present in data/chunks.jsonl.
_LIARA_JSON = "https://docs.liara.ir/paas/liarajson/"
_GLOBAL_ENVS = "https://docs.liara.ir/paas/details/envs/"

PLATFORM_DOC_URLS: dict[Platform, list[str]] = {
    Platform.NODEJS: [
        "https://docs.liara.ir/paas/nodejs/quick-start/",
        "https://docs.liara.ir/paas/nodejs/getting-started/",
        "https://docs.liara.ir/paas/nodejs/how-tos/deploy-app/",
        "https://docs.liara.ir/paas/nodejs/how-tos/set-envs/",
        _LIARA_JSON,
    ],
    Platform.PYTHON: [
        "https://docs.liara.ir/paas/python/quick-start/",
        "https://docs.liara.ir/paas/python/getting-started/",
        "https://docs.liara.ir/paas/python/how-tos/deploy-app/",
        "https://docs.liara.ir/paas/python/how-tos/set-envs/",
        _LIARA_JSON,
    ],
    Platform.DJANGO: [
        "https://docs.liara.ir/paas/django/quick-start/",
        "https://docs.liara.ir/paas/django/getting-started/",
        "https://docs.liara.ir/paas/django/how-tos/deploy-app/",
        "https://docs.liara.ir/paas/django/how-tos/set-envs/",
        _LIARA_JSON,
    ],
    Platform.FLASK: [
        "https://docs.liara.ir/paas/flask/quick-start/",
        "https://docs.liara.ir/paas/flask/getting-started/",
        "https://docs.liara.ir/paas/flask/how-tos/deploy-app/",
        "https://docs.liara.ir/paas/flask/how-tos/set-envs/",
        _LIARA_JSON,
    ],
    Platform.FASTAPI: [
        "https://docs.liara.ir/paas/python/related-apps/fastapi/",
        "https://docs.liara.ir/paas/python/quick-start/",
        "https://docs.liara.ir/paas/python/how-tos/use-asgi/",
        "https://docs.liara.ir/paas/python/how-tos/set-envs/",
        _LIARA_JSON,
    ],
    Platform.LARAVEL: [
        "https://docs.liara.ir/paas/laravel/quick-start/",
        "https://docs.liara.ir/paas/laravel/getting-started/",
        "https://docs.liara.ir/paas/laravel/how-tos/deploy-app/",
        "https://docs.liara.ir/paas/laravel/how-tos/set-envs/",
        _LIARA_JSON,
    ],
    Platform.PHP: [
        "https://docs.liara.ir/paas/php/quick-start/",
        "https://docs.liara.ir/paas/php/getting-started/",
        "https://docs.liara.ir/paas/php/how-tos/deploy-app/",
        "https://docs.liara.ir/paas/php/how-tos/set-envs/",
        _LIARA_JSON,
    ],
    Platform.NEXTJS: [
        "https://docs.liara.ir/paas/nextjs/quick-start/",
        "https://docs.liara.ir/paas/nextjs/getting-started/",
        "https://docs.liara.ir/paas/nextjs/how-tos/deploy-app/",
        "https://docs.liara.ir/paas/nextjs/how-tos/set-envs/",
        _LIARA_JSON,
    ],
    Platform.REACT: [
        "https://docs.liara.ir/paas/react/quick-start/",
        "https://docs.liara.ir/paas/react/getting-started/",
        "https://docs.liara.ir/paas/react/how-tos/deploy-app/",
        "https://docs.liara.ir/paas/react/how-tos/set-envs/",
        _LIARA_JSON,
    ],
    Platform.VUE: [
        "https://docs.liara.ir/paas/vue/quick-start/",
        "https://docs.liara.ir/paas/vue/getting-started/",
        "https://docs.liara.ir/paas/vue/how-tos/deploy-app/",
        _GLOBAL_ENVS,
        _LIARA_JSON,
    ],
    Platform.ANGULAR: [
        "https://docs.liara.ir/paas/angular/quick-start/",
        "https://docs.liara.ir/paas/angular/getting-started/",
        "https://docs.liara.ir/paas/angular/how-tos/deploy-app/",
        _GLOBAL_ENVS,
        _LIARA_JSON,
    ],
    Platform.STATIC: [
        "https://docs.liara.ir/paas/static/quick-start/",
        "https://docs.liara.ir/paas/static/getting-started/",
        "https://docs.liara.ir/paas/static/how-tos/deploy-app/",
        "https://docs.liara.ir/paas/static/how-tos/customize-nginx/",
        _LIARA_JSON,
    ],
    Platform.DOCKER: [
        "https://docs.liara.ir/paas/docker/quick-start/",
        "https://docs.liara.ir/paas/docker/getting-started/",
        "https://docs.liara.ir/paas/docker/how-tos/deploy-app/",
        "https://docs.liara.ir/paas/docker/how-tos/set-envs/",
        _LIARA_JSON,
    ],
    Platform.GO: [
        "https://docs.liara.ir/paas/go/quick-start/",
        "https://docs.liara.ir/paas/go/getting-started/",
        "https://docs.liara.ir/paas/go/how-tos/deploy-app/",
        "https://docs.liara.ir/paas/go/how-tos/set-envs/",
        _LIARA_JSON,
    ],
    Platform.DOTNET: [
        "https://docs.liara.ir/paas/dotnet/quick-start/",
        "https://docs.liara.ir/paas/dotnet/getting-started/",
        "https://docs.liara.ir/paas/dotnet/how-tos/deploy-app/",
        "https://docs.liara.ir/paas/dotnet/how-tos/set-envs/",
        _LIARA_JSON,
    ],
}


@dataclass(slots=True)
class ToolResult:
    """Outcome of one tool call, ready to be handed back to the model."""

    content: str
    hits: list[Hit] = field(default_factory=list)
    label: str = ""
    meta: dict = field(default_factory=dict)


# --------------------------------------------------------------------- formatting

# Matches a real or forged <docs …> / </docs> tag so it can be defanged before it is
# placed inside our own envelope. Bounded so a stray "<" cannot swallow a paragraph.
_DOCS_TAG_RE = re.compile(r"<\s*/?\s*docs\b[^>\n]{0,80}>?", re.IGNORECASE)

# A line opening with `[n]` is the exact shape of a server-generated citation header (see
# `_source_line`). A corpus page that starts a line that way would be indistinguishable
# from one we emitted, letting a doc author mint a citation pointing anywhere.
_FORGED_CITATION_RE = re.compile(r"^([ \t]*)\[(\d{1,3})\]", re.MULTILINE)


def _neutralize(text: str) -> str:
    """Defang untrusted text so it cannot impersonate our own envelope.

    Escapes literal ``<docs>``/``</docs>`` tags so the envelope cannot be closed early,
    and breaks the leading ``[n]`` of any line that mimics a citation header so a
    citation number can only ever be minted by the registry.

    Args:
        text: Raw text taken from a corpus document.

    Returns:
        The same text with both impersonation vectors escaped.
    """
    escaped = _DOCS_TAG_RE.sub(
        lambda match: match.group(0).replace("<", "&lt;").replace(">", "&gt;"), text
    )
    return _FORGED_CITATION_RE.sub(r"\1&#91;\2]", escaped)


def _source_line(number: int, chunk: Chunk) -> str:
    """Render the ``[n] «title › heading» — url`` header line of one snippet."""
    url = _neutralize(chunk.url)
    title = _neutralize(chunk.title.strip()) or url
    heading = _neutralize(chunk.heading.strip())
    label = f"«{title} › {heading}»" if heading else f"«{title}»"
    return f"[{number}] {label} — {url}"


def _block(number: int, chunk: Chunk, text: str) -> str:
    """Render one numbered snippet block.

    The chunk body opens with its own ``## heading`` line, which the source line above
    already states — dropping it here buys back those characters of the snippet budget
    instead of spending them saying the same thing twice.
    """
    body = text.strip()
    leaf = chunk.heading.rsplit(" › ", 1)[-1].strip()
    if leaf and body.startswith(f"## {leaf}"):
        body = body[len(f"## {leaf}") :].lstrip("\n")
    return f"{_source_line(number, chunk)}\n{_neutralize(body)}"


def _wrap(blocks: list[str]) -> str:
    """Wrap rendered blocks in the untrusted-data envelope."""
    body = "\n\n".join(block for block in blocks if block.strip())
    return f"{DOCS_OPEN}\n{body}\n{DOCS_CLOSE}"


def wrap_untrusted(text: str) -> str:
    """Defang and wrap arbitrary corpus-derived text in the untrusted-data envelope.

    Args:
        text: Text that originated in the corpus, in any shape.

    Returns:
        The text inside the same envelope every tool result uses, so the rule that
        ``<docs>`` content is data rather than instructions covers it too.
    """
    return _wrap([_neutralize(text)])


def _note(message: str, label: str, meta: dict | None = None) -> ToolResult:
    """Build a result that carries a single explanatory line instead of documents.

    The message is defanged because several call sites echo a model-supplied argument
    back into it, and that argument is routinely copied out of a document the model just
    read — a note travels to the model as a bare ``role="tool"`` message, outside any
    envelope, so it must not be able to carry markup of its own.
    """
    return ToolResult(
        content=_neutralize(message), hits=[], label=label, meta={"error": True, **(meta or {})}
    )


# ------------------------------------------------------------------- corpus access


def _page_chunk(url: str) -> Chunk | None:
    """Return a representative chunk for a page so it can carry a citation.

    The retrieval contract only exposes ``get_page``/``known_urls``, so the real chunk
    (and therefore its title) is looked up opportunistically; when that is not possible
    a synthetic chunk carrying the URL is used instead.
    """
    by_url = getattr(retrieval_service, "_by_url", None)
    chunks = getattr(retrieval_service, "_chunks", None)
    if isinstance(by_url, dict) and isinstance(chunks, list):
        for variant in (url, url.rstrip("/"), url.rstrip("/") + "/"):
            indices = by_url.get(variant)
            if indices:
                found = chunks[indices[0]]
                return Chunk(
                    id=f"page:{found.url}",
                    url=found.url,
                    title=found.title,
                    heading="",
                    text="",
                    lang=found.lang,
                )
    slug = url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").strip()
    return Chunk(id=f"page:{url}", url=url, title=slug or url, heading="", text="", lang="fa")


def _clean_args(args: dict | None) -> dict:
    """Coerce whatever the model sent into a plain dict."""
    return args if isinstance(args, dict) else {}


def _as_text(value: object, limit: int) -> str:
    """Coerce an argument to a trimmed, clamped string."""
    if value is None:
        return ""
    return str(value).strip()[:limit]


# ------------------------------------------------------------------------- tools


async def _run_search(
    args: dict, registry: CitationRegistry, raw_query: str = ""
) -> ToolResult:
    """Execute ``search_docs``, optionally fusing in the user's own wording.

    Args:
        args: Model-supplied ``query`` and optional ``k``.
        registry: Citation registry shared by the whole turn.
        raw_query: The user's untouched question. When given and materially different from
            what the model wrote, it is searched too and its unseen hits are appended.
            Measured on this corpus: a model that rewrites «چطور کاربر جدید smtp ایجاد
            کنم؟» into documentation keywords can push the one page that answers it out of
            the top k entirely, while the raw sentence ranks it third. The two queries fail
            in different ways, so the union beats either alone.
    """
    label = TOOL_LABELS[ToolName.SEARCH_DOCS]
    query = _as_text(args.get("query"), MAX_QUERY_CHARS)
    if not query:
        return _note("پارامتر «query» خالی بود؛ لطفاً عبارت جست‌وجو را مشخص کن.", label)
    try:
        k = int(args.get("k") or RETRIEVAL_TOP_K)
    except (TypeError, ValueError):
        k = RETRIEVAL_TOP_K
    k = max(1, min(k, RETRIEVAL_TOP_K))

    hits = await retrieval_service.search(query, k=k)
    raw = _as_text(raw_query, MAX_QUERY_CHARS)
    if raw and normalize(raw) != normalize(query):
        seen = {hit.chunk.id for hit in hits}
        extra = await retrieval_service.search(raw, k=RAW_FUSE_K)
        hits = hits + [hit for hit in extra if hit.chunk.id not in seen][:RAW_FUSE_K]
    if not hits:
        return _note(
            f"برای «{query}» هیچ نتیجه‌ای در مستندات لیارا پیدا نشد؛ عبارت دیگری را امتحان کن.",
            label,
            {"query": query, "count": 0},
        )
    blocks = [
        _block(registry.assign(hit.chunk), hit.chunk, hit.chunk.text[:SNIPPET_CHARS])
        for hit in hits
    ]
    return ToolResult(
        content=_wrap(blocks),
        hits=hits,
        label=label,
        meta={"query": query, "count": len(hits)},
    )


async def _run_read_page(args: dict, registry: CitationRegistry) -> ToolResult:
    """Execute ``read_page`` against the local corpus only (no network fetch)."""
    label = TOOL_LABELS[ToolName.READ_PAGE]
    url = _as_text(args.get("url"), 500)
    if not url:
        return _note("پارامتر «url» خالی بود؛ آدرس صفحه را از نتایج جست‌وجو بردار.", label)
    known = retrieval_service.known_urls()
    if url not in known and url.rstrip("/") + "/" not in known:
        return _note(
            "این آدرس جزو مستندات لیارا نیست و خوانده نشد؛ فقط آدرس‌هایی که در نتایج "
            "جست‌وجو آمده‌اند قابل مطالعه‌اند.",
            label,
            {"url": url},
        )
    text = retrieval_service.get_page(url)
    if not text:
        return _note("این صفحه در نسخهٔ محلی مستندات محتوایی ندارد.", label, {"url": url})
    chunk = _page_chunk(url)
    if chunk is None:  # pragma: no cover - _page_chunk always returns a chunk
        return _note("خواندن این صفحه ممکن نشد.", label, {"url": url})
    number = registry.assign(chunk)
    return ToolResult(
        content=_wrap([_block(number, chunk, text[:PAGE_CHARS])]),
        hits=[],
        label=label,
        meta={"url": chunk.url, "chars": len(text)},
    )


async def gather_config_context(
    platform: Platform, needs: list[str] | None, registry: CitationRegistry
) -> ToolResult:
    """Collect the documentation needed to write a ``liara.json`` for one platform.

    Args:
        platform: Target Liara platform.
        needs: Extra requirements (disk, cron, websocket, …), clamped to 10 short items.
        registry: Citation registry; numbers are assigned before formatting.

    Returns:
        A :class:`ToolResult` whose content is the wrapped, numbered documentation.
    """
    label = TOOL_LABELS[ToolName.GENERATE_CONFIG]
    blocks: list[str] = []
    # url -> the text already emitted for it. A set of URLs is not enough: every page but
    # `liara.json` is emitted truncated to CONFIG_PAGE_CHARS, so the section a `need` asked
    # for is often past the cut, and skipping it on a URL match drops the one chunk the
    # user actually asked about while keeping the 1500 chars that do not answer them.
    emitted: dict[str, str] = {}

    for url in PLATFORM_DOC_URLS.get(platform, []):
        budget = LIARA_JSON_PAGE_CHARS if url == _LIARA_JSON else CONFIG_PAGE_CHARS
        text = retrieval_service.get_page(url, limit=budget)
        if not text:
            continue
        chunk = _page_chunk(url)
        if chunk is None or chunk.url in emitted:
            continue
        emitted[chunk.url] = text
        blocks.append(_block(registry.assign(chunk), chunk, text))

    clean_needs = [
        _as_text(need, MAX_NEED_CHARS) for need in (needs or []) if _as_text(need, MAX_NEED_CHARS)
    ][:MAX_NEEDS]
    hits: list[Hit] = []
    for need in clean_needs:
        for hit in await retrieval_service.search(f"{platform.value} {need}", k=2):
            snippet = hit.chunk.text[:SNIPPET_CHARS]
            already = emitted.get(hit.chunk.url)
            # A real duplicate is text the model has already been shown, not merely another
            # chunk of a page it has seen the top of.
            if already is not None and snippet[:200] in already:
                continue
            emitted[hit.chunk.url] = f"{already}\n{snippet}" if already else snippet
            hits.append(hit)
            blocks.append(_block(registry.assign(hit.chunk), hit.chunk, snippet))

    if not blocks:
        return _note(
            f"برای پلتفرم «{platform.value}» صفحه‌ای در نسخهٔ محلی مستندات پیدا نشد.",
            label,
            {"platform": platform.value},
        )
    return ToolResult(
        content=_wrap(blocks),
        hits=hits,
        label=label,
        meta={"platform": platform.value, "needs": clean_needs, "count": len(blocks)},
    )


async def gather_diagnosis_context(log: str, registry: CitationRegistry) -> ToolResult:
    """Collect the documentation that explains the error found in a log.

    Args:
        log: Raw build or runtime log pasted by the user.
        registry: Citation registry; numbers are assigned before formatting.

    Returns:
        A :class:`ToolResult` whose ``meta['signature']`` holds the extracted signature.
    """
    label = TOOL_LABELS[ToolName.DIAGNOSE_LOG]
    signature = extract_error_signature(log)
    if not signature:
        return _note("در این لاگ خط خطای مشخصی پیدا نشد؛ بخش انتهایی لاگ را بفرست.", label)

    hits = await retrieval_service.search(signature, k=RETRIEVAL_TOP_K)
    if not hits:
        return ToolResult(
            content=f"برای خطای «{signature}» صفحهٔ مرتبطی در مستندات لیارا پیدا نشد.",
            hits=[],
            label=label,
            meta={"signature": signature, "count": 0, "error": True},
        )
    blocks = [
        _block(registry.assign(hit.chunk), hit.chunk, hit.chunk.text[:SNIPPET_CHARS])
        for hit in hits
    ]
    return ToolResult(
        content=_wrap(blocks),
        hits=hits,
        label=label,
        meta={"signature": signature, "count": len(hits)},
    )


async def execute_tool(
    name: str, args: dict, registry: CitationRegistry, raw_query: str = ""
) -> ToolResult:
    """Dispatch one model-requested tool call.

    Args:
        name: Tool name as emitted by the model.
        args: Parsed arguments; anything unusable is clamped or reported, never raised.
        registry: Citation registry shared by the whole turn.
        raw_query: The user's own question, fused into ``search_docs`` only. The caller
            passes it once per turn, not once per round, so the same handful of chunks is
            not re-injected on every retry.

    Returns:
        A :class:`ToolResult`. Failures come back as a one-line Persian explanation.
    """
    payload = _clean_args(args)
    try:
        tool = ToolName(name)
    except ValueError:
        known = "، ".join(item.value for item in ToolName)
        return _note(
            f"ابزاری با نام «{name}» وجود ندارد. ابزارهای موجود: {known}.",
            "ابزار ناشناخته",
            {"tool": str(name)},
        )

    try:
        if tool is ToolName.SEARCH_DOCS:
            return await _run_search(payload, registry, raw_query)
        if tool is ToolName.READ_PAGE:
            return await _run_read_page(payload, registry)
        if tool is ToolName.GENERATE_CONFIG:
            raw_platform = _as_text(payload.get("platform"), 40).lower()
            try:
                platform = Platform(raw_platform)
            except ValueError:
                allowed = "، ".join(item.value for item in Platform)
                return _note(
                    f"پلتفرم «{raw_platform}» پشتیبانی نمی‌شود. یکی از این‌ها را بفرست: {allowed}.",
                    TOOL_LABELS[tool],
                )
            needs = payload.get("needs")
            return await gather_config_context(
                platform, needs if isinstance(needs, list) else None, registry
            )
        log_text = _as_text(payload.get("log"), 20000)
        if not log_text:
            return _note("پارامتر «log» خالی بود؛ متن لاگ را بفرست.", TOOL_LABELS[tool])
        return await gather_diagnosis_context(log_text, registry)
    except Exception:  # noqa: BLE001 - a tool must never break the agent loop
        logger.exception("tool_failed", tool=tool.value)
        return _note(
            "اجرای این ابزار با خطا مواجه شد؛ بدون آن ادامه بده یا جست‌وجوی دیگری امتحان کن.",
            TOOL_LABELS[tool],
            {"tool": tool.value},
        )


# -------------------------------------------------------------- error signatures

#: Widened from 40 lines, which was smaller than the input contract: the endpoint accepts
#: 6000 characters, so at ~20 characters a line the real error of a long pip or npm log sat
#: outside the window and could not be chosen at all.
LOG_WINDOW_LINES = 300

#: A line that *might* be the failure. The word boundaries are load-bearing: without them
#: the stack frame `at emitErrorNT (node:net:1934:8)` counted as an error line because the
#: substring "Error" hides inside the identifier.
_ERROR_LINE_RE = re.compile(
    r"\b(errors?|exception|failed|fatal|cannot|not found|refused|denied|traceback"
    r"|no space left|out of memory|killed|permission)\b"
    r"|\b(ModuleNotFound|EADDR|ENOENT|ENOSPC|ENOMEM|ECONNREFUSED|ETIMEDOUT)",
    re.IGNORECASE,
)

#: A line that names the actual thing that broke — an exception class, a module, a script,
#: a missing file — as opposed to the wrapper that reports that something broke. This is what
#: makes the *most specific* line win instead of the *last* one, which is the whole bug:
#: on a PaaS log the error is in the middle and the tail is shutdown noise, so
#: «Reason: Worker failed to boot.» beat `ModuleNotFoundError` and «DEPLOY FAILED» beat
#: `npm ERR! Missing script`.
#:
#: The `Error:`/`Exception:` half is deliberately case-sensitive — it is matching a class
#: name (`ModuleNotFoundError:`, `Error: pg_config …`), and `ERROR:`/`error:` is a severity
#: prefix that pip and every log shipper put in front of their closing summary line.
#:
#: Bare errno tokens are deliberately *not* listed. They read as specific but a platform
#: health-checker prints `connect ECONNREFUSED 127.0.0.1:80` on every failed boot, which
#: would then outrank «Application failed to respond on port 80» — measured: the second
#: retrieves the 502-bad-gateway page, the first retrieves a status-page how-to. The errnos
#: that do name a cause carry a phrase that is listed here (`no space left`, `no such
#: file`, `permission denied`), and `Error: listen EADDRINUSE …` matches on its class name.
_SPECIFIC_ERROR_RE = re.compile(
    r"\b\w*(?:Error|Exception):"
    r"|(?i:no module named|cannot find module|module not found|missing script"
    # Not the bare command name: Liara runs `collectstatic` on every Django deploy, so the
    # successful build step would be a candidate — and, being "specific", would win outright
    # over the real failure further down. Only the warning that it was skipped is a symptom.
    r"|no such file|permission denied|no space left|out of memory|not run collectstatic)"
)

#: Structure rather than cause: stack frames, source listings, pip's `note:` footer, the
#: traceback header, and the "a complete log … can be found in" pointer with the bare path
#: that follows it. They carry error words but say nothing, and one of them used to win.
_NOISE_LINE_RE = re.compile(
    r"^(?:at\s|File \"|\.{3}|note:|Traceback\b|A complete log\b)|^[\w.]*[/\\]\S*$",
    re.IGNORECASE,
)

#: Package managers prefix every line of their error output — informative or not — with
#: their own tag. Stripped before the genericity test, or `npm ERR! Cannot find module` is
#: judged on the prefix instead of on what it says.
_TOOL_PREFIX_RE = re.compile(r"(?i)^(?:npm|yarn|pnpm) (?:ERR!|error)\s*")

#: Lines that match `_ERROR_LINE_RE` but say nothing about what actually went wrong. Every
#: build tool ends a failed run with one, and because they come LAST they used to win the
#: "last error-ish line" contest every time — so a TypeScript compile error, a missing
#: module and a full disk all produced the identical signature
#: «error Command failed with exit code 1.», which then retrieved five unrelated pages and
#: was shown to the user as the extracted error. Checked *before* the scrubbers, so it
#: matches the raw wording the tool printed.
_GENERIC_ERROR_RE = re.compile(
    r"(?i)(command failed|exit code|non-?zero (?:exit )?code|ELIFECYCLE"
    r"|failed at the .*script|build failed|process exited|exited with|make: \*\*\*"
    r"|see the logs? above|a complete log of this run|failed to compile"
    r"|compilation failed|subprocess-exited-with-error)"
)


def _is_generic(line: str) -> bool:
    """Whether a line matches an error pattern while naming no actual cause."""
    return bool(_GENERIC_ERROR_RE.search(_TOOL_PREFIX_RE.sub("", line)))


def _is_noise(line: str) -> bool:
    """Whether a line is log structure — a frame, a footer, a path — rather than a cause."""
    return bool(_NOISE_LINE_RE.match(_TOOL_PREFIX_RE.sub("", line)))

# Applied in order: timestamps first, then structural noise, then paths, then hashes.
_SCRUBBERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), " "),
    (re.compile(r"\b\d{1,2}:\d{2}:\d{2}(?:[.,]\d+)?\b"), " "),
    (re.compile(r"(?i)\bline\s+\d+\b"), "line"),
    (re.compile(r"(?i)\bport\s+\d{2,5}\b"), "port"),
    (re.compile(r":\d{1,6}:\d{1,6}\b"), ""),
    (re.compile(r"(?:[A-Za-z]:\\|/)(?:[\w.@+~-]+[/\\])+[\w.@+~-]*"), "<path>"),
    (re.compile(r"(?<=[\w\].])[:.]\d{2,6}\b"), ""),
    (re.compile(r"\b(?:0x)?[0-9a-f]{7,}\b", re.IGNORECASE), "<hash>"),
    (re.compile(r"\s+"), " "),
]


def extract_error_signature(log: str) -> str:
    """Reduce a log to a short, searchable description of its failure.

    The signature is the only retrieval query the diagnosis path ever makes, so picking the
    wrong line does not degrade the answer — it sends the model documentation about a
    different failure, which it then correctly reports as "not in the docs".

    So the **most specific** candidate wins, not the most recent one. Scanning the window
    from the end, three preferences in order: a line naming a concrete symbol, module,
    script or errno; then any line that names *some* cause; then, only if every candidate is
    a content-free wrapper such as "command failed with exit code 1", that wrapper — a vague
    signature still beats telling the user no error was found. Stack frames, source
    listings and log-file pointers are excluded outright at every step, so nothing that is
    merely log structure can become the query. A log with no error-shaped line at all
    returns empty rather than its last line, which is how the user's own question stopped
    becoming the search query when they pasted a log with a sentence after it.

    Then strips paths, hashes, ports, timestamps and line numbers so the result generalises
    into a good search query.

    Fixed here rather than at the endpoint because ``gather_diagnosis_context`` is also
    reached from the agent loop's ``diagnose_log`` tool — one guard, both callers.

    Args:
        log: Raw log text.

    Returns:
        A signature of at most 200 characters, or an empty string when the log holds no
        line that looks like a failure.
    """
    lines = [line.strip() for line in (log or "").splitlines()]
    tail = [line for line in lines[-LOG_WINDOW_LINES:] if line]
    candidates = [
        line
        for line in reversed(tail)
        if (_ERROR_LINE_RE.search(line) or _SPECIFIC_ERROR_RE.search(line))
        and not _is_noise(line)
    ]
    if not candidates:
        return ""
    named = [line for line in candidates if not _is_generic(line)]
    candidate = next(
        (line for line in named if _SPECIFIC_ERROR_RE.search(line)),
        named[0] if named else candidates[0],
    )
    for pattern, replacement in _SCRUBBERS:
        candidate = pattern.sub(replacement, candidate)
    # `>` is not in the strip set: the path scrubber above inserts a literal `<path>`, and
    # stripping it turned «Not Found: <path>» into «Not Found: <path».
    return candidate.strip(" -:|\t")[:MAX_SIGNATURE_CHARS].strip()
