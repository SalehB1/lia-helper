"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.exceptions import ForbiddenException
from app.domain.models.session import Session as SessionModel
from app.domain.models.user import User
from app.domain.repositories.session_repo import SessionRepository
from app.domain.services.auth_service import NOT_SUPERUSER, authenticate

#: Name of the panel's session cookie. One constant, because a browser only drops a cookie
#: whose attributes match the one it stored — set and delete must agree exactly.
SESSION_COOKIE = "liara_session"


async def current_user(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """Resolve the signed-in operator from the session cookie.

    Args:
        request: Carries the cookie jar.
        db: Request-scoped database session.

    Returns:
        The operator's row, re-read from the database on every request so a deactivated
        account loses access immediately rather than when its token expires.

    Raises:
        UnauthorizedException: Missing, expired, tampered or foreign-secret cookie, or a
            user that has since been deleted or deactivated.
    """
    return await authenticate(db, request.cookies.get(SESSION_COOKIE))


async def user_session(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db_session),
) -> SessionModel:
    """Resolve the chat workspace belonging to the signed-in user.

    This replaced a dependency that read the workspace out of a client-supplied
    ``X-Session-Id`` header. The header was a device token that the app treated as an
    identity, so anyone who guessed a uuid read another person's conversations and profile.
    Deriving the row from the cookie instead means every downstream ``session_id`` filter —
    in ``conversation_repo``, ``message_repo`` and ``chat_service`` — became an ownership
    filter without a single query being rewritten.

    Args:
        user: The signed-in operator.
        db: Request-scoped database session.

    Returns:
        The user's session row, created on first use.

    Raises:
        UnauthorizedException: Nobody is signed in.
    """
    return await SessionRepository(db).get_or_create_for_user(user.id)


async def require_superuser(user: User = Depends(current_user)) -> User:
    """Only a superuser may pass.

    Returns:
        The operator.

    Raises:
        UnauthorizedException: Nobody is signed in.
        ForbiddenException: Signed in, but as an ordinary user. 403 rather than 404: the
            caller has already proved an identity, so hiding the route buys nothing and a
            404 would just send them hunting for a typo.
    """
    if not user.is_superuser:
        raise ForbiddenException(NOT_SUPERUSER)
    return user
