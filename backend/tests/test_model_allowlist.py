"""Check every allowlisted model actually exists on the provider.

This exists because it caught a real bug: ``claude-haiku-4`` was added to the allowlist from
a docs page, but the provider's real id is ``claude-haiku-4-5``. Nothing else would have
noticed — the id is only rejected at request time, and only for the user who picked that
model from the UI.

Skips cleanly when ``AVALAI_API_KEY`` is unset, so it is safe to run anywhere::

    ../.venv/bin/python -m tests.test_model_allowlist
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import openai  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.shared.enums import ChatModel  # noqa: E402
from app.shared.model_catalog import list_models  # noqa: E402


def check_catalog_covers_enum() -> None:
    """Every ChatModel must have catalog metadata, and exactly one must be the default."""
    options = list_models()
    ids = {option["id"] for option in options}
    missing = {member.value for member in ChatModel} - ids
    assert not missing, f"models missing from the catalog: {sorted(missing)}"
    defaults = [option["id"] for option in options if option.get("default")]
    assert len(defaults) == 1, f"expected exactly one default model, got {defaults}"
    assert defaults[0] == settings.model_primary
    for option in options:
        assert option.get("label"), f"{option['id']} has no label"
        assert option.get("description"), f"{option['id']} has no description"
        assert option.get("tier"), f"{option['id']} has no cost tier"
    print(f"ok  catalog covers all {len(ids)} models, default={defaults[0]}")


async def check_models_exist_on_provider() -> None:
    """Every allowlisted id must appear in the provider's own model list."""
    if not settings.avalai_api_key:
        print("skip  AVALAI_API_KEY not set — provider check skipped")
        return
    client = openai.AsyncOpenAI(
        api_key=settings.avalai_api_key, base_url=settings.avalai_base_url, timeout=30.0
    )
    available = {model.id for model in (await client.models.list()).data}
    missing = [member.value for member in ChatModel if member.value not in available]
    assert not missing, f"allowlisted but absent from the provider: {missing}"
    assert settings.embed_model in available, f"embed model absent: {settings.embed_model}"
    print(f"ok  all {len(list(ChatModel))} chat models + {settings.embed_model} exist upstream")


def main() -> None:
    """Run both checks."""
    check_catalog_covers_enum()
    asyncio.run(check_models_exist_on_provider())
    print("ok")


if __name__ == "__main__":
    main()
