"""User-facing metadata for every chat model in :class:`ChatModel`.

Pure data plus helpers. The catalog exists so the panel can show the quality-versus-cost
trade-off in Persian instead of raw provider ids; the *authorization* of a model id is
:class:`ChatModel` itself, never this table.

Prices are the provider's own published rates in USD per million tokens, copied from the
``pricing`` field of its ``GET /v1/models`` response. The cost band shown to users is
**derived from those numbers**, not hand-assigned — an earlier hand-assigned set had
GPT-5 Mini labelled expensive when it is in fact cheaper than GPT-4.1 Mini for this
workload, which is exactly the kind of error a derived value cannot make.

Verify the ids and prices against the live provider with::

    ../.venv/bin/python -m tests.test_model_allowlist
"""

from __future__ import annotations

from typing import NamedTuple

from app.core.config import settings
from app.shared.enums import ChatModel

#: Price bands shown next to each model, derived from `estimated_cost_per_1k`.
TIER_CHEAP = "ارزان"
TIER_BALANCED = "متعادل"
TIER_EXPENSIVE = "گران"

#: Tokens in one representative grounded turn, measured on a real request: the system
#: prompt plus eight retrieved chunks in, one complete Persian answer out. Retrieval-
#: augmented turns are input-heavy, so input price dominates the ranking — a model with a
#: cheap input rate wins here even if its output rate looks high.
TYPICAL_INPUT_TOKENS = 2694
TYPICAL_OUTPUT_TOKENS = 251

#: Cost boundaries in USD per 1,000 turns, applied to `estimated_cost_per_1k`.
CHEAP_BELOW = 1.00
EXPENSIVE_ABOVE = 2.00


class ModelInfo(NamedTuple):
    """Display metadata and provider pricing for one chat model."""

    label: str
    description: str
    input_price: float  # USD per 1M input tokens
    output_price: float  # USD per 1M output tokens


MODEL_CATALOG: dict[ChatModel, ModelInfo] = {
    ChatModel.GPT_5_MINI: ModelInfo(
        label="GPT-5 Mini",
        description="نسل جدیدتر و دقیق‌تر، و برای این کاربرد از GPT-4.1 Mini هم ارزان‌تر.",
        input_price=0.25,
        output_price=2.0,
    ),
    ChatModel.GEMINI_2_5_FLASH: ModelInfo(
        label="Gemini 2.5 Flash",
        description="کیفیت فارسی بسیار خوب با پنجرهٔ متن بزرگ.",
        input_price=0.3,
        output_price=2.5,
    ),
    ChatModel.GPT_4_1_MINI: ModelInfo(
        label="GPT-4.1 Mini",
        description="پایدار و قابل‌اعتماد در فراخوانی ابزارها.",
        input_price=0.4,
        output_price=1.6,
    ),
    ChatModel.GPT_4O_MINI: ModelInfo(
        label="GPT-4o Mini",
        description="نسل قدیمی‌تر اما کم‌هزینه و کارآمد.",
        input_price=0.15,
        output_price=0.6,
    ),
    ChatModel.GPT_5_NANO: ModelInfo(
        label="GPT-5 Nano",
        description="ارزان‌ترین گزینه؛ برای پرسش‌های کوتاه و روزمره کافی است.",
        input_price=0.05,
        output_price=0.4,
    ),
    ChatModel.GPT_4_1_NANO: ModelInfo(
        label="GPT-4.1 Nano",
        description="بسیار کم‌هزینه و سریع؛ مناسب پرسش‌های ساده.",
        input_price=0.1,
        output_price=0.4,
    ),
    ChatModel.CLAUDE_HAIKU_4_5: ModelInfo(
        label="Claude Haiku 4.5",
        description="در پیروی از دستورالعمل و رعایت قالب پاسخ بسیار دقیق، ولی گران‌تر.",
        input_price=1.0,
        output_price=5.0,
    ),
    ChatModel.GPT_4_1: ModelInfo(
        label="GPT-4.1",
        description="مدل کامل؛ گران‌ترین گزینه و برای این کاربرد معمولاً لازم نیست.",
        input_price=2.0,
        output_price=8.0,
    ),
}


def estimated_cost_per_1k(info: ModelInfo) -> float:
    """Return the USD cost of 1,000 typical grounded turns on this model.

    Args:
        info: The model's pricing metadata.

    Returns:
        Cost in USD, using the measured token profile of one real answer.
    """
    per_turn = (
        TYPICAL_INPUT_TOKENS * info.input_price + TYPICAL_OUTPUT_TOKENS * info.output_price
    ) / 1_000_000
    return round(per_turn * 1000, 2)


def tier_for(info: ModelInfo) -> str:
    """Return the Persian price band for a model, derived from its real cost."""
    cost = estimated_cost_per_1k(info)
    if cost < CHEAP_BELOW:
        return TIER_CHEAP
    if cost > EXPENSIVE_ABOVE:
        return TIER_EXPENSIVE
    return TIER_BALANCED


def valid_model(value: object) -> str | None:
    """Return ``value`` as an allowlisted model id, or None when it is not one.

    This is the single gate every layer reuses — schema, repository, chat service and the
    LLM client — so an unknown or malformed id fails closed everywhere identically.

    Args:
        value: Anything a client may have sent, including None.

    Returns:
        The canonical model id string, or None when it is not in :class:`ChatModel`.
    """
    try:
        return ChatModel(value).value
    except (ValueError, TypeError):
        return None


def list_models() -> list[dict]:
    """Return the catalog as API-ready records, cheapest first.

    Returns:
        One record per :class:`ChatModel` with ``id``, ``label``, ``description``,
        ``tier``, ``costPer1k`` and ``default`` — the latter true for
        ``settings.model_primary``. Ordering is by real cost so the panel's list reads as
        a price ladder without the frontend needing to sort.
    """
    # Imported here, not at module scope: agent_settings imports `valid_model` from this
    # module, so the pair would be a circular import at load time.
    from app.domain.services.agent_settings import effective

    records = [
        {
            "id": model.value,
            "label": info.label,
            "description": info.description,
            "tier": tier_for(info),
            "costPer1k": estimated_cost_per_1k(info),
            "default": model.value == effective().model_primary,
        }
        for model, info in MODEL_CATALOG.items()
    ]
    records.sort(key=lambda record: record["costPer1k"])
    return records
