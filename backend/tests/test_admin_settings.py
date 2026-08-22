"""Self-check for the live settings overrides behind the admin gate.

The gate itself — who may reach these routes at all — is asserted in ``tests.test_auth``.
What matters here is the second layer: the knobs an operator can change are the assistant's
own spending limits, so an out-of-range number or a model that is not on the allowlist must
be rejected on the way IN and on the way OUT. A row written by hand with sqlite3 must never
widen a budget, which is why validation lives in the loader and not only in the schema.

Usage (from ``backend/``)::

    .venv-uv/bin/python -m tests.test_admin_settings
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import httpx  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings  # noqa: E402
from app.domain.models.base import Base  # noqa: E402
from app.domain.repositories.settings_repo import SettingsRepository  # noqa: E402
from app.domain.services import agent_settings as agent_module  # noqa: E402
from app.domain.services.agent_settings import agent_settings, clean_overrides  # noqa: E402
from app.shared.constants import AGENT_MAX_LLM_CALLS  # noqa: E402

STEP_TIMEOUT = 30.0
SECRET = "test-secret-not-the-real-one-0123456789abcdef"
OPERATOR = ("operator", "operator-password-long")


@asynccontextmanager
async def _client() -> AsyncIterator[httpx.AsyncClient]:
    """Yield a signed-in client bound to the real app, on a throwaway database."""
    import main
    from app.core.database import get_db_session
    from app.core.security import hash_password
    from app.domain.repositories.user_repo import UserRepository

    with tempfile.TemporaryDirectory() as folder:
        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(folder) / 'test.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

        async def _override() -> AsyncIterator[AsyncSession]:
            async with factory() as db:
                yield db
                await db.commit()

        async with factory() as db:
            await UserRepository(db).create(
                username=OPERATOR[0],
                password_hash=hash_password(OPERATOR[1]),
                # The agent settings are a superuser surface: they set the model ladder and
                # the spending budgets, and `log_level` raised far enough turns the platform
                # log into a disclosure primitive. An ordinary user is a chat user.
                is_superuser=True,
            )
            await db.commit()

        previous = settings.auth_secret
        settings.auth_secret = SECRET
        main.app.dependency_overrides[get_db_session] = _override
        agent_module.invalidate()
        try:
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                signed_in = await client.post(
                    "/api/v1/auth/login",
                    json={"username": OPERATOR[0], "password": OPERATOR[1]},
                )
                assert signed_in.status_code == 200, signed_in.text
                yield client
        finally:
            settings.auth_secret = previous
            main.app.dependency_overrides.pop(get_db_session, None)
            agent_module.invalidate()
            await engine.dispose()


@asynccontextmanager
async def _database() -> AsyncIterator[AsyncSession]:
    """Yield a bare session for the loader-level checks."""
    with tempfile.TemporaryDirectory() as folder:
        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(folder) / 'test.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        agent_module.invalidate()
        async with factory() as db:
            yield db
        agent_module.invalidate()
        await engine.dispose()


# --------------------------------------------------------------------------- checks


async def test_the_screen_never_shows_a_secret() -> None:
    """It lists tunables and where each value came from — never a key or the signing secret."""
    async with _client() as client:
        response = await client.get("/api/v1/admin/settings")
        assert response.status_code == 200, response.text
        body = response.text
        assert SECRET not in body, "the signing secret was echoed back"
        for secret in (settings.avalai_api_key, settings.openrouter_api_key):
            assert not secret or secret not in body, "a provider key leaked"
        keys = {record["key"] for record in response.json()["settings"]}
        assert {"toolRounds", "ladder", "escalation"} <= keys, keys


async def test_an_override_takes_effect_without_a_restart() -> None:
    """A PUT must change the very next turn's budget, not the next deploy's."""
    async with _client() as client:
        response = await client.put(
            "/api/v1/admin/settings", json={"toolRounds": 5}
        )
        assert response.status_code == 200, response.text
        record = next(r for r in response.json()["settings"] if r["key"] == "toolRounds")
        assert (record["value"], record["source"]) == (5, "db"), record

        reverted = await client.put(
            "/api/v1/admin/settings", json={"toolRounds": None}
        )
        record = next(r for r in reverted.json()["settings"] if r["key"] == "toolRounds")
        assert record["source"] == "default", record


async def test_the_boundary_refuses_what_would_widen_the_budget() -> None:
    """Out-of-range numbers and unknown fields are rejected at the schema."""
    async with _client() as client:
        for payload in ({"toolRounds": 99}, {"maxLlmCalls": 0}, {"turnBudgetSeconds": 99999}):
            response = await client.put("/api/v1/admin/settings", json=payload)
            assert response.status_code == 422, (payload, response.status_code)
        unknown = await client.put(
            "/api/v1/admin/settings", json={"secretBackdoor": True}
        )
        assert unknown.status_code == 422, unknown.text


async def test_a_ladder_may_only_name_allowlisted_models() -> None:
    """The ladder is a list of model ids that reach a provider, so it is an allowlist."""
    async with _client() as client:
        response = await client.put(
            "/api/v1/admin/settings",
            json={"ladder": ["gpt-4.1-mini", "../../etc/passwd", "not-a-model"]},
        )
        assert response.status_code == 200, response.text
        record = next(r for r in response.json()["settings"] if r["key"] == "ladder")
        assert record["value"] == ["gpt-4.1-mini"], record

        # A ladder with nothing allowlisted in it is not an empty ladder — a turn with no
        # models cannot run — so it is dropped and whatever was in force stays in force.
        rejected = await client.put(
            "/api/v1/admin/settings", json={"ladder": ["not-a-model"]}
        )
        record = next(r for r in rejected.json()["settings"] if r["key"] == "ladder")
        assert record["value"] == ["gpt-4.1-mini"], record


async def test_a_hand_written_row_cannot_widen_the_budget() -> None:
    """The loader re-validates: writing sqlite directly is not a way around the bounds."""
    async with _database() as db:
        await SettingsRepository(db).upsert(
            {"max_llm_calls": 9999, "ladder": ["nonsense"], "tool_rounds": 4}
        )
        await db.commit()
        agent_module.invalidate()

        effective = await agent_settings(db)
        assert effective.max_llm_calls == AGENT_MAX_LLM_CALLS, effective.max_llm_calls
        assert "nonsense" not in effective.ladder, effective.ladder
        assert effective.tool_rounds == 4, "a legal override was dropped"


async def test_clean_overrides_rejects_the_obvious_shapes() -> None:
    """Booleans are not numbers, strings are not numbers, unknown keys are not settings."""
    assert clean_overrides({"tool_rounds": True}) == {}
    assert clean_overrides({"tool_rounds": "4"}) == {}
    assert clean_overrides({"nope": 1}) == {}
    assert clean_overrides({"escalation": False}) == {"escalation": False}
    assert clean_overrides({"tool_rounds": 3}) == {"tool_rounds": 3}



async def test_every_setting_except_the_secrets_is_stored_in_the_database() -> None:
    """The owner's requirement: settings live in the database, keys stay in the environment."""
    async with _client() as client:
        keys = {r["key"] for r in (await client.get("/api/v1/admin/settings")).json()["settings"]}
        # Everything an operator is expected to tune.
        assert {
            "modelPrimary",
            "modelFallback",
            "reasoningEffort",
            "agentMode",
            "streamEnabled",
            "logLevel",
            "ladder",
            "toolRounds",
        } <= keys, keys
        # And nothing that is a secret or a bootstrap value.
        for forbidden in ("avalaiApiKey", "authSecret", "databasePath", "allowedOrigins",
                          "cookieSecure", "bootstrapAdminPassword", "embedModel"):
            assert forbidden not in keys, f"{forbidden} must not be settable from the panel"


async def test_a_stored_model_choice_actually_reaches_the_provider_layer() -> None:
    """A setting nothing reads is not a setting. This is the end of that wire."""
    from app.domain.services.agent_settings import effective

    async with _client() as client:
        saved = await client.put("/api/v1/admin/settings", json={"modelPrimary": "gpt-4.1-nano"})
        assert saved.status_code == 200, saved.text
        assert effective().model_primary == "gpt-4.1-nano", effective().model_primary

async def _run() -> int:
    """Run every check in this module and report."""
    checks: list[tuple[str, Callable[[], Awaitable[None]]]] = [
        (name, value)
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for name, check in checks:
        await asyncio.wait_for(check(), STEP_TIMEOUT)
        print(f"ok  {name}")
    print(f"\n{len(checks)} checks passed")
    return 0


def main() -> int:
    """Entry point: run the async checks on a fresh event loop."""
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
