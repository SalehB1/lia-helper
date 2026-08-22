"""Reading the token counts stored beside each answer, and pricing them.

``messages.usage_json`` is written by the chat service and read back by the admin cost
report. It is a side-car blob rather than columns, so every read has to tolerate what a blob
can be: absent, empty, invalid JSON, a JSON value that is not an object, or an object whose
values are the wrong type. None of those may raise — a single corrupt row must never take
down the whole report.

The keys have been spelled two ways over the life of this app: the chat service normalizes to
camelCase, and an older reader looked for snake_case and silently found nothing, which is why
the token counters read zero for months. Both spellings are accepted here so that class of
bug cannot come back.
"""

from __future__ import annotations

import json
from typing import NamedTuple

from app.shared.model_catalog import MODEL_CATALOG, valid_model
from app.shared.enums import ChatModel

#: Bucket for tokens whose model is unknown — a row written before the model was recorded,
#: or one naming a model that is no longer allowlisted. Deliberately a value no model id can
#: collide with, so it cannot be confused for a real one on the wire.
UNKNOWN_MODEL = ""

#: Shown for the unknown bucket. Honest rather than tidy: these tokens were really spent, we
#: just cannot say on what, and therefore cannot price them.
UNKNOWN_LABEL = "نامشخص"


class TokenCount(NamedTuple):
    """Prompt and completion tokens for one model."""

    prompt: int = 0
    completion: int = 0

    def __add__(self, other: TokenCount) -> TokenCount:  # type: ignore[override]
        """Sum two counts, so callers can fold a list without a special case."""
        return TokenCount(self.prompt + other.prompt, self.completion + other.completion)


def _int(value: object) -> int:
    """Coerce one stored token count to an int, refusing anything that is not a number.

    ``bool`` is excluded explicitly: it is a subclass of ``int`` in Python, so a stored
    ``true`` would otherwise silently count as one token.
    """
    if isinstance(value, bool):
        return 0
    return int(value) if isinstance(value, (int, float)) else 0


def _counts(blob: dict) -> TokenCount:
    """Read prompt/completion tokens out of one usage object, either spelling."""
    prompt = _int(blob.get("promptTokens", blob.get("prompt_tokens")))
    completion = _int(blob.get("completionTokens", blob.get("completion_tokens")))
    return TokenCount(prompt, completion)


def read_usage(raw: str | None) -> dict[str, TokenCount]:
    """Split one stored usage blob into token counts per model.

    Args:
        raw: The ``usage_json`` column value; may be NULL, empty or corrupt.

    Returns:
        A mapping of model id to its token counts, empty for anything unusable. A blob
        carrying ``byModel`` — written once a turn could escalate across the model ladder and
        bill two models — is split across those models; anything older collapses onto the
        single ``model`` key it recorded, or :data:`UNKNOWN_MODEL` when it recorded none.
    """
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}

    by_model = data.get("byModel")
    if isinstance(by_model, dict) and by_model:
        split: dict[str, TokenCount] = {}
        for name, blob in by_model.items():
            if not isinstance(blob, dict):
                continue
            # An id outside the allowlist is bucketed, never echoed: this blob is a database
            # value, and a hand-edited row must not be able to put an arbitrary string into
            # an API response.
            key = valid_model(name) or UNKNOWN_MODEL
            split[key] = split.get(key, TokenCount()) + _counts(blob)
        if split:
            return split

    counts = _counts(data)
    if counts == TokenCount():
        return {}
    return {valid_model(data.get("model")) or UNKNOWN_MODEL: counts}


def cost_usd(model: str, tokens: TokenCount) -> float | None:
    """Price one model's tokens in USD, or None when it cannot be priced.

    ``None`` rather than ``0.0`` is the whole point. Zero is a claim — "these tokens were
    free" — and for an uncatalogued or unknown model that claim is false. The report renders
    the difference rather than quietly adding nothing to the total.

    Args:
        model: An allowlisted model id, or :data:`UNKNOWN_MODEL`.
        tokens: The counts to price.

    Returns:
        Cost in USD, or None when the model has no published price here.
    """
    try:
        info = MODEL_CATALOG[ChatModel(model)]
    except (ValueError, KeyError):
        return None
    return (tokens.prompt * info.input_price + tokens.completion * info.output_price) / 1_000_000


def label_for(model: str) -> str:
    """Return the display name for a model id, falling back to the honest unknown label."""
    try:
        return MODEL_CATALOG[ChatModel(model)].label
    except (ValueError, KeyError):
        return UNKNOWN_LABEL
