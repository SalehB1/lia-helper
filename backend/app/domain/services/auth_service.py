"""Signing in, and proving who is signed in on every later request.

Two rules shape everything here.

**Every rejection is identical.** An unknown username, a wrong password and a deactivated
account all produce the same 401 with the same Persian message, and all three pay the same
~100 ms of scrypt — otherwise the response time answers "does this account exist?" for free.

**Privilege is never read from the token.** The cookie proves a uuid; the role is fetched
from the database on every request. A role baked into a JWT can only ever be stale, and a
user deactivated a minute ago must lose access now, not in twelve hours.
"""

from __future__ import annotations

import asyncio
import hmac

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.exceptions import (
    ServiceUnavailableException,
    UnauthorizedException,
    ValidationException,
)
from app.core.logging import get_logger
from app.core.security import (
    MIN_PASSWORD_CHARS,
    hash_password,
    issue_token,
    password_epoch,
    read_token,
    verify_password,
)
from app.domain.models.user import User
from app.domain.repositories.user_repo import UserRepository

logger = get_logger("auth")

#: A well-formed scrypt hash of nothing anyone can type (the key is all zeros), verified when
#: the username is unknown so a missing account pays the same KDF cost as a real one. It must
#: stay PARSEABLE — `verify_password` rejects a malformed value on sight and answers
#: instantly, which is exactly the timing oracle this closes.
DUMMY_PASSWORD_HASH = f"scrypt$16384$8$1${'00' * 16}${'00' * 32}"

BAD_CREDENTIALS = "نام کاربری یا رمز عبور درست نیست."
NOT_SIGNED_IN = "برای این کار باید وارد شوید."
NOT_SUPERUSER = "فقط مدیر ارشد اجازهٔ این کار را دارد."
WEAK_PASSWORD = f"رمز عبور باید دست‌کم {MIN_PASSWORD_CHARS} کاراکتر باشد."
REGISTRATION_CLOSED = "ساخت حساب فعلاً ممکن نیست. بعداً دوباره تلاش کنید."


async def login(db: AsyncSession, username: str, password: str) -> tuple[User, str]:
    """Verify a username and password, and mint a session token.

    Args:
        db: Request-scoped session.
        username: Raw username from the request body.
        password: Raw password from the request body.

    Returns:
        The signed-in user and the token to put in the session cookie.

    Raises:
        UnauthorizedException: Unknown user, wrong password, or a deactivated account —
            indistinguishable from each other on the wire and in timing.
    """
    user = await UserRepository(db).by_username(username.strip())
    stored = user.password_hash if user else DUMMY_PASSWORD_HASH
    # scrypt is ~16 MiB and ~100 ms of CPU: off the event loop, always. Computed FIRST and
    # unconditionally — a short-circuit here would hand back an unknown username in
    # microseconds and tell an attacker exactly which names are real.
    matched = await asyncio.to_thread(verify_password, password, stored)
    if not matched or user is None or not user.is_active:
        logger.info("login_refused", username_len=len(username or ""))
        raise UnauthorizedException(BAD_CREDENTIALS)
    logger.info("login_ok", user=user.uuid, superuser=user.is_superuser)
    return user, issue_token(user.uuid, user.password_hash)


async def register(
    db: AsyncSession, username: str, password: str, display_name: str | None
) -> User:
    """Create an account that cannot sign in until an admin activates it.

    The order of the steps is the security-relevant part.

    The bootstrap guard comes first. ``ensure_bootstrap_user`` returns early once *any* user
    row exists, so on a fresh database the first person to register would take that slot and
    permanently lock out the only account able to approve anyone — including themselves.

    The password is hashed **before** the username is looked at, so a taken name and a free
    one both pay the same ~100 ms. The duplicate error is still returned rather than hidden:
    a registrant who can never learn their name is taken can never register at all. That
    weakens the anti-enumeration property login has, deliberately and knowingly, and
    ``RATE_LIMIT_REGISTER`` is what bounds the cost of exploiting it.

    Args:
        db: Request-scoped session.
        username: Already-validated username from the request body.
        password: Raw password from the request body.
        display_name: Optional human name.

    Returns:
        The pending account.

    Raises:
        ServiceUnavailableException: No admin exists yet to approve anyone.
        ValidationException: The username is taken.
    """
    if await UserRepository(db).count_active_superusers() < 1:
        logger.warning("registration_refused", reason="no_superuser")
        raise ServiceUnavailableException(REGISTRATION_CLOSED)

    password_hash = await asyncio.to_thread(hash_password, password)
    user = await UserRepository(db).create(
        username=username.strip(),
        password_hash=password_hash,
        is_superuser=False,
        is_active=False,
        display_name=display_name,
    )
    await db.commit()
    # Never the username itself: it is attacker-controlled text on a public route.
    logger.info("registration_received", user=user.uuid, username_len=len(username or ""))
    return user


async def authenticate(db: AsyncSession, token: str | None) -> User:
    """Resolve the operator a session cookie proves.

    Args:
        db: Request-scoped session.
        token: The raw cookie value, or None when it was absent.

    Returns:
        The user row, re-read from the database rather than trusted from the claims.

    Raises:
        UnauthorizedException: No token, an unusable token, a user that no longer exists,
            one that has been deactivated, or one whose password has changed since the token
            was minted. Fail closed at every branch.
    """
    claims = read_token(token)
    if claims is None:
        raise UnauthorizedException(NOT_SIGNED_IN)
    user_uuid, epoch = claims
    user = await UserRepository(db).by_uuid(user_uuid)
    # The password fingerprint is what makes a reset an actual eviction. Without it a stolen
    # cookie outlives both the victim changing their password and a superuser force-resetting
    # the account — which is precisely the incident-response action, so it has to work.
    if (
        user is None
        or not user.is_active
        or not hmac.compare_digest(epoch, password_epoch(user.password_hash))
    ):
        raise UnauthorizedException(NOT_SIGNED_IN)
    return user


async def change_password(db: AsyncSession, user: User, current: str, new: str) -> None:
    """Change a user's own password after re-checking the current one.

    Args:
        db: Request-scoped session.
        user: The signed-in operator.
        current: Their existing password.
        new: The replacement.

    Raises:
        UnauthorizedException: ``current`` does not match.
        ValidationException: ``new`` is too short.
    """
    matched = await asyncio.to_thread(verify_password, current, user.password_hash)
    if not matched:
        raise UnauthorizedException(BAD_CREDENTIALS)
    if len(new) < MIN_PASSWORD_CHARS:
        raise ValidationException(WEAK_PASSWORD)
    user.password_hash = await asyncio.to_thread(hash_password, new)
    await db.flush()
    logger.info("password_changed", user=user.uuid)


async def ensure_bootstrap_user() -> None:
    """Create the first superuser, once, while the users table is empty.

    Emptiness — not "this username is absent" — is the condition on purpose. Keying on the
    username would resurrect a deliberately deleted account and re-apply a rotated password
    on every boot.

    Never logs the password. Every refusal is logged, because a silent skip here leaves the
    admin surface unreachable and the log line is the operator's only warning.
    """
    async with AsyncSessionLocal() as db:
        if await UserRepository(db).count() > 0:
            logger.info("bootstrap_superuser_skipped", reason="users_exist")
            return
        password = settings.bootstrap_admin_password
        if not password:
            logger.warning("bootstrap_superuser_skipped", reason="no_password")
            return
        if len(password) < MIN_PASSWORD_CHARS:
            logger.error(
                "bootstrap_superuser_rejected",
                reason="password_too_short",
                minimum=MIN_PASSWORD_CHARS,
            )
            return
        user = await UserRepository(db).create(
            username=settings.bootstrap_admin_user.strip() or "admin",
            password_hash=await asyncio.to_thread(hash_password, password),
            is_superuser=True,
            display_name=None,
        )
        await db.commit()
        logger.info("bootstrap_superuser_created", user=user.uuid, username=user.username)
