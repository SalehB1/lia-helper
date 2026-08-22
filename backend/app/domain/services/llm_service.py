"""LLM access: streaming chat over OpenAI-compatible providers, plus query embedding.

AvalAI (``https://api.avalai.ir/v1``) is the primary provider and the only one used for
embeddings — it serves chat and ``/embeddings`` behind one key and is reachable from
Iranian infrastructure without a proxy. OpenRouter and Gemini's OpenAI-compatible endpoint
are optional chat fallbacks. All three speak the same protocol, so there is exactly one
streaming code path with a different ``base_url`` / key / model. Clients are built lazily:
importing this module never needs an API key.

Failover rule (load-bearing): an attempt may only be replaced by the next provider if it
failed *before* yielding its first chunk. Once anything reached the caller, a failure is
surfaced instead of restarting, so text can never be emitted twice.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import openai

from app.core.config import settings
from app.core.exceptions import ServiceUnavailableException
from app.core.logging import get_logger
from app.shared.constants import (
    EMBED_CACHE_SIZE,
    EMBED_RETRY_ATTEMPTS,
    EMBED_TIMEOUT_SECONDS,
    LLM_TIMEOUT_SECONDS,
)
from app.shared.model_catalog import valid_model
from app.shared.persian import normalize

logger = get_logger("llm")

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

PREFER_FALLBACK_SECONDS = 60.0
RETRY_DELAY_SECONDS = 0.4

_MSG_NOT_CONFIGURED = "سرویس هوش مصنوعی پیکربندی نشده است؛ کلید دسترسی تنظیم نشده."
_MSG_ALL_FAILED = "ارتباط با سرویس هوش مصنوعی برقرار نشد؛ کمی بعد دوباره تلاش کنید."
_MSG_MIDSTREAM = "ارتباط با سرویس هوش مصنوعی در میانهٔ پاسخ قطع شد."

_TRANSIENT_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})


@dataclass(slots=True)
class LLMChunk:
    """One piece of a streamed completion."""

    kind: str  # "token" | "tool_calls" | "usage" | "done"
    text: str = ""
    tool_calls: list[dict] | None = None
    usage: dict | None = None
    model: str = ""


@dataclass(slots=True)
class LLMResult:
    """A fully collected completion."""

    text: str
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    model: str = ""


@dataclass(frozen=True, slots=True)
class _Provider:
    """A configured OpenAI-compatible endpoint."""

    name: str
    base_url: str
    api_key: str
    model: str


# One client per provider name, shared by every LLMService instance, built on first use.
_clients: dict[str, openai.AsyncOpenAI] = {}
_embed_cache: OrderedDict[str, list[float]] = OrderedDict()
_prefer_fallback_until: float = 0.0


def _drop_client(name: str) -> None:
    """Discard a provider's cached client so the next call builds a fresh one.

    The cache has no other invalidation, the app runs a single worker, and there is only one
    provider configured in practice — so a connection pool that goes bad takes the whole
    assistant down until someone restarts the process. Chat hides that behind its composed
    fallback answer; the wizards show it plainly, which is how it was finally noticed.

    Closing is best-effort: the client is already being thrown away, and a transport that is
    wedged enough to need this is exactly the one whose close may also fail.

    Args:
        name: The provider name to evict.
    """
    client = _clients.pop(name, None)
    if client is None:
        return
    try:
        # ponytail: fire-and-forget close on the running loop. A rebuild happens a few times
        # at worst and constructing a client is cheap; add a cooldown if the log shows churn.
        asyncio.get_running_loop().create_task(client.close())
    except Exception as exc:  # noqa: BLE001 - never let cleanup mask the original failure
        logger.warning("llm_client_close_failed", provider=name, error=type(exc).__name__)
    logger.warning("llm_client_rebuilt", provider=name)


def _is_transient(exc: BaseException) -> bool:
    """Whether an exception is worth retrying on the same provider."""
    if isinstance(
        exc,
        (
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.RateLimitError,
            openai.InternalServerError,
            asyncio.TimeoutError,
            TimeoutError,
            ConnectionError,
        ),
    ):
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and (status in _TRANSIENT_STATUS or status >= 500)


def _usage_dict(usage: Any) -> dict:
    """Coerce a provider usage object into a plain JSON-safe dict."""
    dump = getattr(usage, "model_dump", None)
    if callable(dump):
        try:
            return {k: v for k, v in dump().items() if v is not None}
        except Exception:  # noqa: BLE001 - a malformed usage object must not kill the stream
            pass
    return {
        key: int(getattr(usage, key, 0) or 0)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }


def _accumulate_tool_call(acc: dict[int, dict], delta: Any) -> None:
    """Merge one fragmented ``delta.tool_calls[i]`` into the accumulator."""
    raw_index = getattr(delta, "index", None)
    index = int(raw_index) if isinstance(raw_index, int) else 0
    slot = acc.setdefault(index, {"id": "", "name": "", "arguments": ""})
    call_id = getattr(delta, "id", None)
    if call_id:
        slot["id"] = str(call_id)
    function = getattr(delta, "function", None)
    if function is None:
        return
    name = getattr(function, "name", None)
    # Providers either send the whole name once or fragment it; appending only when it
    # differs handles both without duplicating a re-sent full name.
    if name and name != slot["name"]:
        slot["name"] += str(name)
    arguments = getattr(function, "arguments", None)
    if arguments:
        slot["arguments"] += str(arguments)


def _finalize_tool_calls(acc: dict[int, dict]) -> list[dict]:
    """Turn the accumulator into ``[{"id","name","arguments": dict}]`` in index order."""
    calls: list[dict] = []
    for index in sorted(acc):
        slot = acc[index]
        raw = slot["arguments"].strip()
        try:
            parsed = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        calls.append(
            {
                "id": slot["id"] or f"call_{index}",
                "name": slot["name"],
                "arguments": parsed,
            }
        )
    return calls


async def _close_stream(stream: Any) -> None:
    """Best-effort close of a provider stream; never raises."""
    closer = getattr(stream, "close", None) or getattr(stream, "aclose", None)
    if closer is None:
        return
    try:
        result = closer()
        if inspect.isawaitable(result):
            await result
    except Exception:  # noqa: BLE001 - a failed close must not mask the response
        pass


def _l2_normalize(values: list[Any]) -> list[float]:
    """L2-normalize a raw embedding vector; returns ``[]`` for a zero/empty vector."""
    try:
        vector = [float(value) for value in values]
    except (TypeError, ValueError):
        return []
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0.0:
        return []
    return [value / norm for value in vector]


class LLMService:
    """Streaming chat and query embeddings with provider failover and graceful degradation."""

    @property
    def available(self) -> bool:
        """Whether at least one provider key is configured."""
        return settings.has_llm

    def _providers(self, model: str | None = None) -> list[_Provider]:
        """Configured providers, primary first unless the prefer-fallback window is open.

        Args:
            model: Optional per-call chat model coming from a user preference. It is
                re-validated against :class:`ChatModel` here — this is the last gate
                before the network call — and is applied to AvalAI only: the fallback
                providers use a different model namespace and would 404 on an AvalAI id.

        Returns:
            The providers to try, in order.
        """
        # The synchronous snapshot: this method has no database session, and the values it
        # needs are operator settings now, not environment variables.
        from app.domain.services.agent_settings import effective

        current = effective()
        chosen = valid_model(model)
        if model is not None and chosen is None:
            # Never log the value itself: it is attacker-controlled text.
            logger.warning("model_override_rejected", fallback=current.model_primary)
        order: list[_Provider] = []
        if settings.avalai_api_key:
            order.append(
                _Provider(
                    name="avalai",
                    base_url=settings.avalai_base_url,
                    api_key=settings.avalai_api_key,
                    model=chosen or current.model_primary,
                )
            )
        if settings.openrouter_api_key:
            order.append(
                _Provider(
                    name="openrouter",
                    base_url=OPENROUTER_BASE_URL,
                    api_key=settings.openrouter_api_key,
                    model=current.model_fallback,
                )
            )
        if settings.gemini_api_key:
            order.append(
                _Provider(
                    name="gemini",
                    base_url=GEMINI_BASE_URL,
                    api_key=settings.gemini_api_key,
                    model=current.model_fallback,
                )
            )
        if len(order) > 1 and time.monotonic() < _prefer_fallback_until:
            # Demote the primary for the cool-down window instead of paying its timeout on
            # every request in a burst.
            order.append(order.pop(0))
        return order

    def _client_for(self, provider: _Provider) -> openai.AsyncOpenAI:
        """Return the single cached client for this provider, building it on first use."""
        client = _clients.get(provider.name)
        if client is None:
            client = openai.AsyncOpenAI(
                api_key=provider.api_key,
                base_url=provider.base_url,
                # A bare float replaces the SDK's own connect timeout with this whole value,
                # so a blackholed TCP connect would cost the full ceiling per attempt rather
                # than failing fast. Connect is the one phase that should give up quickly.
                timeout=openai.Timeout(LLM_TIMEOUT_SECONDS, connect=5.0),
                max_retries=0,  # retries and failover are handled here, not by the SDK
            )
            _clients[provider.name] = client
        return client

    async def _stream_once(
        self,
        provider: _Provider,
        messages: list[dict],
        tools: list[dict] | None,
        tool_choice: str | None,
        timeout: float = LLM_TIMEOUT_SECONDS,
        reasoning: str | None = None,
    ) -> AsyncIterator[LLMChunk]:
        """Stream one attempt against one provider, translating deltas into `LLMChunk`s."""
        kwargs: dict[str, Any] = {
            "model": provider.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            # Per-request, not per-client: a turn now spends its budget across several model
            # attempts, and the cached client's own timeout is fixed for the process. The
            # SDK honours this override the same way `embed_query` already relies on.
            "timeout": timeout,
        }
        if tools:
            kwargs["tools"] = tools
            if tool_choice:
                kwargs["tool_choice"] = tool_choice
        # Only the GPT-5 family accepts this; the other allowlisted models 400 on it.
        from app.domain.services.agent_settings import effective

        # A caller that names an effort overrides the operator's: the setting is tuned for
        # answering a documentation question, and a six-word title is not that.
        effort = effective().reasoning_effort if reasoning is None else reasoning
        if effort and provider.model.startswith("gpt-5"):
            kwargs["reasoning_effort"] = effort

        stream = await self._client_for(provider).chat.completions.create(**kwargs)
        acc: dict[int, dict] = {}
        try:
            async for raw in stream:
                usage = getattr(raw, "usage", None)
                if usage is not None:
                    yield LLMChunk(kind="usage", usage=_usage_dict(usage), model=provider.model)
                # The usage frame arrives with choices == []; never index blindly.
                choices = getattr(raw, "choices", None) or []
                if not choices:
                    continue
                choice = choices[0]
                delta = getattr(choice, "delta", None)
                if delta is not None:
                    text = getattr(delta, "content", None)
                    if text:
                        yield LLMChunk(kind="token", text=str(text), model=provider.model)
                    for fragment in getattr(delta, "tool_calls", None) or []:
                        _accumulate_tool_call(acc, fragment)
                if getattr(choice, "finish_reason", None) == "tool_calls" and acc:
                    yield LLMChunk(
                        kind="tool_calls",
                        tool_calls=_finalize_tool_calls(acc),
                        model=provider.model,
                    )
                    acc.clear()
        finally:
            await _close_stream(stream)

        if acc:
            yield LLMChunk(
                kind="tool_calls", tool_calls=_finalize_tool_calls(acc), model=provider.model
            )
        yield LLMChunk(kind="done", model=provider.model)

    async def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        model: str | None = None,
        timeout: float = LLM_TIMEOUT_SECONDS,
        reasoning: str | None = None,
    ) -> AsyncIterator[LLMChunk]:
        """Stream a chat completion, retrying the primary once and then failing over.

        Args:
            messages: OpenAI-shaped chat messages.
            tools: Optional function-calling schemas.
            tool_choice: Optional ``"auto"`` / ``"none"`` / tool name.
            model: Optional chat model for this call. Honoured only for AvalAI and only
                when it is an allowlisted :class:`ChatModel` value; anything else falls
                back to ``settings.model_primary`` instead of reaching the provider.
            timeout: Per-attempt ceiling in seconds. The turn-level ladder sets this lower
                than the process default so one stalled provider cannot eat a whole turn.

        Yields:
            `LLMChunk`s of kind ``token``, ``tool_calls``, ``usage`` and a final ``done``.

        Raises:
            ServiceUnavailableException: No provider is configured, every provider failed,
                or the connection dropped after the first chunk had already been emitted.
        """
        global _prefer_fallback_until

        providers = self._providers(model)
        if not providers:
            raise ServiceUnavailableException(_MSG_NOT_CONFIGURED)

        # The first provider gets one retry; every other provider gets a single attempt.
        plan: list[tuple[_Provider, int]] = []
        for position, provider in enumerate(providers):
            for attempt in range(2 if position == 0 else 1):
                plan.append((provider, attempt))

        last_error: BaseException | None = None
        dead: set[str] = set()
        for provider, attempt in plan:
            if provider.name in dead:
                continue
            if attempt:
                await asyncio.sleep(RETRY_DELAY_SECONDS)
            emitted = False
            try:
                async for chunk in self._stream_once(
                    provider, messages, tools, tool_choice, timeout, reasoning
                ):
                    emitted = True
                    yield chunk
                return
            except Exception as exc:  # noqa: BLE001 - every provider error is handled here
                last_error = exc
                # Demote whoever was tried FIRST this call — that is the provider whose
                # timeout every request in a burst would otherwise pay. Keying this on
                # "openrouter" meant the window opened when a fallback failed and never
                # when the primary did, which is the exact inverse of the intent above.
                if provider.name == providers[0].name:
                    _prefer_fallback_until = time.monotonic() + PREFER_FALLBACK_SECONDS
                logger.warning(
                    "llm_attempt_failed",
                    provider=provider.name,
                    attempt=attempt,
                    emitted=emitted,
                    error=type(exc).__name__,
                    # The SDK wraps every underlying transport failure — a TLS error, a
                    # closed pool, a DNS failure — into one opaque APIConnectionError. The
                    # type name of the cause is the only thing that tells them apart, and
                    # without it a wedged process is undiagnosable. A type name carries no
                    # key material; the exception's *text* would (it embeds the URL).
                    cause=type(exc.__cause__).__name__ if exc.__cause__ else None,
                )
                # A connection-level failure on the RETRY (attempt is truthy) means both
                # tries died on transport, not on one bad request. The cached client is
                # process-lifetime and is never otherwise invalidated, so a pool that has
                # gone bad stays bad until someone restarts the app — which is exactly the
                # outage this app has been having. Rebuild it.
                if attempt and isinstance(
                    exc, (openai.APIConnectionError, openai.APITimeoutError)
                ):
                    _drop_client(provider.name)
                if emitted:
                    # Tokens already reached the caller: restarting would duplicate text.
                    raise ServiceUnavailableException(_MSG_MIDSTREAM) from exc
                if not _is_transient(exc):
                    dead.add(provider.name)

        raise ServiceUnavailableException(_MSG_ALL_FAILED) from last_error

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        timeout: float = LLM_TIMEOUT_SECONDS,
        reasoning: str | None = None,
    ) -> LLMResult:
        """Collect a full completion by draining `stream_chat`.

        Args:
            messages: OpenAI-shaped chat messages.
            tools: Optional function-calling schemas.
            model: Optional chat model for this call; same allowlist as `stream_chat`.
            timeout: Per-attempt ceiling in seconds. Callers with a human waiting on a
                single request should pass something well under the process default: this
                path retries once, so the default is two full timeouts back to back.

        Returns:
            The concatenated text, any tool calls, usage and the model that answered.

        Raises:
            ServiceUnavailableException: Same conditions as `stream_chat`.
        """
        parts: list[str] = []
        tool_calls: list[dict] = []
        usage: dict = {}
        answered_by = ""
        async for chunk in self.stream_chat(
            messages, tools, model=model, timeout=timeout, reasoning=reasoning
        ):
            answered_by = chunk.model or answered_by
            if chunk.kind == "token":
                parts.append(chunk.text)
            elif chunk.kind == "tool_calls" and chunk.tool_calls:
                tool_calls.extend(chunk.tool_calls)
            elif chunk.kind == "usage" and chunk.usage:
                usage = chunk.usage
        return LLMResult(text="".join(parts), tool_calls=tool_calls, usage=usage, model=answered_by)

    async def embed_query(self, text: str) -> list[float] | None:
        """Embed a search query via the OpenAI-compatible endpoint, never raising.

        Uses the same ``/embeddings`` route and the same model that ``ingest.py`` used to
        build ``data/embeddings.npz`` — a mismatch there would make the dot product
        meaningless, which is why both sides read ``EMBED_MODEL``.

        Retrieval degrades to BM25-only on None, so every failure path — missing key,
        network error, bad payload — must return None rather than propagate.

        Args:
            text: Raw user query.

        Returns:
            An L2-normalized vector of ``settings.embed_dim`` values, or None when
            embedding is unavailable.
        """
        key = normalize(text)
        if not key or not settings.has_embeddings:
            return None

        cached = _embed_cache.get(key)
        if cached is not None:
            _embed_cache.move_to_end(key)
            return cached

        values: list[float] | None = None
        for attempt in range(1, EMBED_RETRY_ATTEMPTS + 1):
            try:
                client = self._client_for(
                    _Provider(
                        name="avalai-embed",
                        base_url=settings.avalai_base_url,
                        api_key=settings.avalai_api_key,
                        model=settings.embed_model,
                    )
                )
                response = await client.embeddings.create(
                    model=settings.embed_model,
                    input=[text],
                    timeout=EMBED_TIMEOUT_SECONDS,
                )
                values = list(response.data[0].embedding)
                break
            except Exception as exc:  # noqa: BLE001 - embedding is optional by design
                # Log the type only: exception text can echo the request, which carries the key.
                # The cause's type name is safe and is the only clue to what really broke.
                logger.warning(
                    "embed_query_failed",
                    error=type(exc).__name__,
                    cause=type(exc.__cause__).__name__ if exc.__cause__ else None,
                    attempt=attempt,
                )
                # Same wedged-pool problem as the chat path, and worse here because the failure
                # is silent: retrieval simply degrades to BM25 with nothing but this line to say
                # so. Rebuild the client rather than degrading for the life of the process.
                transient = isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError))
                if transient:
                    _drop_client("avalai-embed")
                # Only a connection-shaped failure is worth a second attempt; a 401 or a bad
                # model name would fail identically and just double the user's wait.
                if not transient:
                    return None
        if values is None:
            return None

        vector = _l2_normalize(values if isinstance(values, list) else [])
        if not vector:
            return None
        _embed_cache[key] = vector
        while len(_embed_cache) > EMBED_CACHE_SIZE:
            _embed_cache.popitem(last=False)
        return vector


llm_service = LLMService()
