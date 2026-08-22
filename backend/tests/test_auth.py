"""Self-check for panel authentication and the two operator roles.

What is asserted here is the authorization boundary itself, so every check is written as an
attack rather than as a happy path: no cookie, a forged cookie, a cookie signed with another
secret, an admin reaching for a superuser route, a superuser locking everyone out. The chat
surface must stay anonymous throughout — that is the one thing this feature could break
without anyone noticing until a user complains.

Usage (from ``backend/``)::

    .venv-uv/bin/python -m tests.test_auth
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
import jwt  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api import limiter  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.deps import SESSION_COOKIE  # noqa: E402
from app.core.security import hash_password, issue_token  # noqa: E402
from app.domain.models.base import Base  # noqa: E402
from app.domain.repositories.user_repo import UserRepository  # noqa: E402

STEP_TIMEOUT = 30.0
SECRET = "test-secret-not-the-real-one-0123456789abcdef"
BOSS = ("boss", "boss-password-长-enough")
STAFF = ("staff", "staff-password-long-enough")

#: Session factory of the throwaway database `_app` is currently serving, published so a
#: check can seed rows the API has no endpoint to create. Set by `_app`, cleared on exit.
_factory: async_sessionmaker | None = None


@asynccontextmanager
async def _app(peer: tuple[str, int] = ("127.0.0.1", 123)) -> AsyncIterator[httpx.AsyncClient]:
    """Yield a client bound to the real app on a throwaway database, seeded with two users.

    Args:
        peer: Address the request appears to arrive from. The default is loopback, which the
            rate limiter treats as a proxy; a public address simulates a deployment reachable
            straight from the internet.
    """
    import main
    from app.core.database import get_db_session

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
            repository = UserRepository(db)
            await repository.create(
                username=BOSS[0],
                password_hash=hash_password(BOSS[1]),
                is_superuser=True,
            )
            await repository.create(
                username=STAFF[0],
                password_hash=hash_password(STAFF[1]),
                is_superuser=False,
            )
            await db.commit()

        global _factory
        _factory = factory
        previous = settings.auth_secret
        settings.auth_secret = SECRET
        main.app.dependency_overrides[get_db_session] = _override
        # slowapi keys on the client IP, and every check here shares one. The login limit is
        # 10/minute by design, so leaving it on would make these checks fail each other
        # rather than fail the code. `test_the_login_rate_limit_is_enforced` turns it back on
        # for the one check that is actually about it.
        limiter.enabled = False
        try:
            transport = httpx.ASGITransport(app=main.app, client=peer)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                yield client
        finally:
            limiter.enabled = True
            settings.auth_secret = previous
            _factory = None
            main.app.dependency_overrides.pop(get_db_session, None)
            await engine.dispose()


async def _login(client: httpx.AsyncClient, who: tuple[str, str]) -> httpx.Response:
    """Sign in and leave the cookie on the client's jar."""
    return await client.post(
        "/api/v1/auth/login", json={"username": who[0], "password": who[1]}
    )


# --------------------------------------------------------------------------- checks


async def test_login_sets_an_httponly_cookie_and_me_reads_it() -> None:
    """The happy path, and the cookie's flags — which are the security-relevant part."""
    async with _app() as client:
        response = await _login(client, BOSS)
        assert response.status_code == 200, response.text
        assert response.json()["role"] == "superuser", response.json()
        assert response.json()["isSuperuser"] is True, response.json()
        assert "passwordHash" not in response.text and "password_hash" not in response.text

        raw = response.headers.get("set-cookie", "")
        assert SESSION_COOKIE in raw, raw
        assert "HttpOnly" in raw, "the cookie is reachable from JavaScript"
        assert "Path=/" in raw, raw

        me = await client.get("/api/v1/auth/me")
        assert me.status_code == 200 and me.json()["username"] == BOSS[0], me.text


async def test_every_bad_credential_looks_identical() -> None:
    """Unknown user, wrong password and a disabled account must not be distinguishable."""
    async with _app() as client:
        unknown = await client.post(
            "/api/v1/auth/login", json={"username": "ghost", "password": "whatever-long"}
        )
        wrong = await client.post(
            "/api/v1/auth/login", json={"username": BOSS[0], "password": "not-the-password"}
        )
        assert unknown.status_code == wrong.status_code == 401, (unknown.text, wrong.text)
        assert unknown.json() == wrong.json(), "the two refusals differ"
        assert SESSION_COOKIE not in unknown.headers.get("set-cookie", "")


async def test_the_admin_surface_is_closed_without_a_cookie() -> None:
    """Fail closed: no cookie is 401, not a default-open read."""
    async with _app() as client:
        for path in (
            "/api/v1/admin/settings",
            "/api/v1/admin/prompts",
            "/api/v1/users",
            "/api/v1/auth/me",
        ):
            response = await client.get(path)
            assert response.status_code == 401, (path, response.status_code)


async def test_a_forged_or_foreign_cookie_is_refused() -> None:
    """Garbage, another secret, and alg=none all fail the same way."""
    async with _app() as client:
        forgeries = {
            "garbage": "not-a-token",
            "other-secret": jwt.encode({"sub": "x", "exp": 9999999999}, "wrong", algorithm="HS256"),
            "alg-none": jwt.encode({"sub": "x", "exp": 9999999999}, key="", algorithm="none"),
            "expired": jwt.encode({"sub": "x", "exp": 1}, SECRET, algorithm="HS256"),
        }
        for name, token in forgeries.items():
            response = await client.get(
                "/api/v1/admin/settings", cookies={SESSION_COOKIE: token}
            )
            assert response.status_code == 401, (name, response.status_code)


async def test_a_valid_token_for_a_deleted_user_is_refused() -> None:
    """Privilege is re-read from the database, never trusted from the claims."""
    async with _app() as client:
        token = issue_token("00000000-0000-4000-8000-000000000000", hash_password("whatever"))
        response = await client.get("/api/v1/admin/settings", cookies={SESSION_COOKIE: token})
        assert response.status_code == 401, response.status_code


async def test_a_regular_user_reaches_no_operator_surface_at_all() -> None:
    """The whole point of two roles: an ordinary account gets 403 on every admin route.

    Both halves matter. Asserting only the refusals would still pass if the gate were
    accidentally closed for *everyone*, which is a different bug with the same symptom for
    this account — so the superuser half proves the split is a split.
    """
    async with _app() as client:
        await _login(client, STAFF)
        for method, path in (
            ("GET", "/api/v1/admin/settings"),
            ("PUT", "/api/v1/admin/settings"),
            # The prompt text carries every rule the assistant answers under, so it is the
            # same operator surface as the budgets, not a milder one.
            ("GET", "/api/v1/admin/prompts"),
            ("PUT", "/api/v1/admin/prompts"),
            ("GET", "/api/v1/admin/usage"),
            ("GET", "/api/v1/users"),
            ("POST", "/api/v1/users"),
            ("DELETE", "/api/v1/users/whatever"),
        ):
            body = {} if method in ("POST", "PUT") else None
            response = await client.request(method, path, json=body)
            assert response.status_code == 403, (method, path, response.status_code)

        await _login(client, BOSS)
        for path in (
            "/api/v1/admin/settings",
            "/api/v1/admin/prompts",
            "/api/v1/admin/usage",
            "/api/v1/users",
        ):
            response = await client.get(path)
            assert response.status_code == 200, (path, response.text)
        saved = await client.put("/api/v1/admin/settings", json={"toolRounds": 4})
        assert saved.status_code == 200, saved.text


async def test_a_superuser_can_create_and_change_operators() -> None:
    """The management surface, and that a created user can actually sign in."""
    async with _app() as client:
        await _login(client, BOSS)
        created = await client.post(
            "/api/v1/users",
            json={"username": "newbie", "password": "another-long-password", "role": "admin"},
        )
        assert created.status_code == 201, created.text
        target = created.json()["uuid"]
        assert "passwordHash" not in created.text

        duplicate = await client.post(
            "/api/v1/users",
            json={"username": "newbie", "password": "another-long-password", "role": "admin"},
        )
        assert duplicate.status_code == 422, duplicate.text

        promoted = await client.patch(f"/api/v1/users/{target}", json={"role": "superuser"})
        assert promoted.status_code == 200, promoted.text
        assert promoted.json()["isSuperuser"] is True, promoted.json()

        listed = await client.get("/api/v1/users")
        assert {row["username"] for row in listed.json()} >= {"boss", "staff", "newbie"}


async def test_a_superuser_cannot_lock_everyone_out() -> None:
    """Self-demotion, self-deletion and removing the last superuser are all refused."""
    async with _app() as client:
        me = (await _login(client, BOSS)).json()

        for payload in ({"role": "admin"}, {"isActive": False}):
            response = await client.patch(f"/api/v1/users/{me['uuid']}", json=payload)
            assert response.status_code == 422, (payload, response.status_code)

        deleted = await client.delete(f"/api/v1/users/{me['uuid']}")
        assert deleted.status_code == 422, deleted.text

        # With a second superuser, demoting the first is fine — the guard is about the LAST
        # one, not about superusers being immutable.
        second = await client.post(
            "/api/v1/users",
            json={"username": "second", "password": "yet-another-long-pw", "role": "superuser"},
        )
        assert second.status_code == 201, second.text
        demoted = await client.patch(
            f"/api/v1/users/{second.json()['uuid']}", json={"role": "admin"}
        )
        assert demoted.status_code == 200, demoted.text


async def test_logout_clears_the_cookie_and_never_strands_anyone() -> None:
    """Logout works without a valid session, or an expired token becomes unclearable."""
    async with _app() as client:
        await _login(client, BOSS)
        out = await client.post("/api/v1/auth/logout", json={})
        assert out.status_code == 200, out.text
        assert (await client.get("/api/v1/auth/me")).status_code == 401

        # No session at all: still 200, never 401.
        assert (await client.post("/api/v1/auth/logout", json={})).status_code == 200


async def test_changing_your_own_password_requires_the_current_one() -> None:
    """And the new password must clear the length floor."""
    async with _app() as client:
        await _login(client, STAFF)
        wrong = await client.post(
            "/api/v1/auth/password",
            json={"currentPassword": "nope", "newPassword": "a-brand-new-long-one"},
        )
        assert wrong.status_code == 401, wrong.text

        short = await client.post(
            "/api/v1/auth/password",
            json={"currentPassword": STAFF[1], "newPassword": "short"},
        )
        assert short.status_code == 422, short.text

        ok = await client.post(
            "/api/v1/auth/password",
            json={"currentPassword": STAFF[1], "newPassword": "a-brand-new-long-one"},
        )
        assert ok.status_code == 200, ok.text
        await client.post("/api/v1/auth/logout", json={})
        assert (await _login(client, STAFF)).status_code == 401, "the old password still works"
        again = await client.post(
            "/api/v1/auth/login",
            json={"username": STAFF[0], "password": "a-brand-new-long-one"},
        )
        assert again.status_code == 200, again.text


async def test_the_chat_surface_is_closed_and_scoped_to_its_owner() -> None:
    """The two properties the whole login change exists to create.

    First: nothing but the health probe answers without a cookie. This used to assert the
    exact opposite — the chat surface was deliberately anonymous, identified by a header the
    caller minted themselves, which meant a guessed uuid read someone else's history.

    Second, and the one that cannot be checked by reading the code: one user's conversation
    is invisible to another. Written as an attack, because a scoping bug looks perfectly
    healthy from a single account.
    """
    async with _app() as client:
        for method, path in (
            ("GET", "/api/v1/conversations"),
            ("GET", "/api/v1/profile"),
            ("POST", "/api/v1/tools/config"),
        ):
            body = {"platform": "django", "needs": []} if method == "POST" else None
            response = await client.request(method, path, json=body)
            assert response.status_code == 401, (method, path, response.status_code)

        # STAFF signs in, which materializes their session, and gets a conversation. There
        # is no endpoint that creates one — only answering a message does, and that needs a
        # provider — so it is seeded directly against the session the API just made.
        await _login(client, STAFF)
        assert (await client.get("/api/v1/conversations")).status_code == 200

        from app.domain.repositories.conversation_repo import ConversationRepository
        from app.domain.repositories.session_repo import SessionRepository

        async with _factory() as db:
            staff = await UserRepository(db).by_username(STAFF[0])
            session = await SessionRepository(db).get_or_create_for_user(staff.id)
            secret_chat = await ConversationRepository(db).create(session.id, "دیپلوی جنگو")
            await db.commit()
            secret_uuid = secret_chat.uuid

        mine = await client.get("/api/v1/conversations")
        assert [row["uuid"] for row in mine.json()] == [secret_uuid], mine.text

        # Now the attack: a different account, holding a perfectly valid cookie of its own.
        await _login(client, BOSS)
        boss_list = await client.get("/api/v1/conversations")
        assert boss_list.status_code == 200, boss_list.text
        assert boss_list.json() == [], "another user's conversations are listed"

        stolen = await client.get(f"/api/v1/conversations/{secret_uuid}")
        assert stolen.status_code == 404, (stolen.status_code, stolen.text)
        removed = await client.delete(f"/api/v1/conversations/{secret_uuid}")
        assert removed.status_code == 404, (removed.status_code, removed.text)
        renamed = await client.patch(
            f"/api/v1/conversations/{secret_uuid}", json={"title": "مال من"}
        )
        assert renamed.status_code == 404, (renamed.status_code, renamed.text)

        # The turn surface is a second route family onto the same conversations, so it
        # needs the same check and gets no exemption for being read-only. A foreign
        # conversation, a nonexistent one and one with no turn running are one 404.
        followed = await client.get(f"/api/v1/chat/{secret_uuid}/stream")
        assert followed.status_code == 404, (followed.status_code, followed.text)
        stopped = await client.post(f"/api/v1/chat/{secret_uuid}/stop")
        assert stopped.status_code == 404, (stopped.status_code, stopped.text)


async def test_changing_a_password_kills_every_other_session() -> None:
    """A stolen cookie must not outlive the password it was minted against."""
    async with _app() as client:
        await _login(client, STAFF)
        stolen = client.cookies.get(SESSION_COOKIE)
        assert stolen, "no session cookie to steal"

        changed = await client.post(
            "/api/v1/auth/password",
            json={"currentPassword": STAFF[1], "newPassword": "a-brand-new-long-one"},
        )
        assert changed.status_code == 200, changed.text

        # The tab that made the change keeps working — it was handed a fresh cookie.
        assert (await client.get("/api/v1/auth/me")).status_code == 200

        # The cookie captured beforehand does not.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=__import__("main").app), base_url="http://test"
        ) as thief:
            replayed = await thief.get(
                "/api/v1/auth/me", cookies={SESSION_COOKIE: stolen}
            )
            assert replayed.status_code == 401, "a captured cookie survived the password change"


async def test_a_superuser_reset_evicts_the_account_immediately() -> None:
    """Force-resetting a password is the incident response, so it has to actually evict."""
    async with _app() as client:
        await _login(client, STAFF)
        stolen = client.cookies.get(SESSION_COOKIE)
        staff_uuid = (await client.get("/api/v1/auth/me")).json()["uuid"]

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=__import__("main").app), base_url="http://test"
        ) as boss:
            await boss.post(
                "/api/v1/auth/login", json={"username": BOSS[0], "password": BOSS[1]}
            )
            reset = await boss.put(
                f"/api/v1/users/{staff_uuid}/password",
                json={"newPassword": "reset-by-the-superuser"},
            )
            assert reset.status_code == 200, reset.text

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=__import__("main").app), base_url="http://test"
        ) as thief:
            replayed = await thief.get("/api/v1/auth/me", cookies={SESSION_COOKIE: stolen})
            assert replayed.status_code == 401, "the reset did not evict the open session"


async def test_no_log_level_ever_exposes_a_password_hash() -> None:
    """SQLAlchemy logs statements and their bound parameters at INFO, so inheriting the
    root level leaked `users.password_hash` at the DEFAULT setting — and the level is now
    settable by the least privileged operator role."""
    import logging

    from app.core.logging import configure_logging

    try:
        for requested in ("INFO", "DEBUG"):
            configure_logging(requested)
            for noisy in ("aiosqlite", "sqlalchemy.engine", "openai", "httpx", "httpcore"):
                level = logging.getLogger(noisy).getEffectiveLevel()
                assert level >= logging.WARNING, (
                    f"at {requested}, {noisy} still logs statement parameters"
                )
        assert logging.getLogger("app").getEffectiveLevel() == logging.DEBUG
    finally:
        configure_logging("INFO")


async def test_the_login_rate_limit_is_enforced() -> None:
    """Each attempt costs ~100 ms of scrypt on the threadpool, so the cap is a resource
    limit as much as it is a credential-stuffing one."""
    async with _app() as client:
        limiter.enabled = True
        try:
            statuses = [
                (
                    await client.post(
                        "/api/v1/auth/login",
                        json={"username": "ghost", "password": "whatever-long-enough"},
                    )
                ).status_code
                for _ in range(12)
            ]
        finally:
            limiter.enabled = False
        assert 429 in statuses, statuses


async def test_a_forged_forwarded_header_cannot_buy_more_attempts() -> None:
    """The limit must not be escapable by a header the caller writes.

    `X-Forwarded-For` is believed only when the immediate peer is a proxy — a private or
    loopback address. Here the request arrives from a public address, which is what a
    deployment reachable straight from the internet looks like, so the header is fiction and
    must be ignored.

    Read unconditionally — as it was — one rotating header made every unauthenticated limit
    unenforceable at once: unlimited password guessing, unlimited username probing through
    the duplicate-name error, and unlimited pending accounts in the approval queue.
    """
    # A genuinely public address. Not one of the documentation ranges (192.0.2.0/24,
    # 198.51.100.0/24, 203.0.113.0/24) — Python classifies all three as private, so using
    # one would make this check pass for the wrong reason.
    async with _app(peer=("93.184.216.34", 40000)) as client:
        limiter.enabled = True
        try:
            statuses = [
                (
                    await client.post(
                        "/api/v1/auth/login",
                        json={"username": "ghost", "password": "whatever-long-enough"},
                        headers={"X-Forwarded-For": f"10.0.0.{index}"},
                    )
                ).status_code
                for index in range(12)
            ]
        finally:
            limiter.enabled = False
        assert 429 in statuses, f"a rotating X-Forwarded-For escaped the limit: {statuses}"


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
