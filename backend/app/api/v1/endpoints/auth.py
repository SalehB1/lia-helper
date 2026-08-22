"""Signing in and out of the panel.

The session travels in an httpOnly cookie, so the token is unreachable from JavaScript and
an XSS in the panel cannot exfiltrate it. Every route that changes state takes a JSON body:
``application/json`` is not a CORS-simple content type, so the browser must preflight, and
the preflight is refused for any origin outside ``ALLOWED_ORIGINS``. **That preflight is the
CSRF defence — no route here may ever accept a form body or read its input from the query
string**, or the protection disappears silently.
"""

from __future__ import annotations

from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import limiter
from app.api.cbv import SlashInferringRouter, cbv
from app.core.config import settings
from app.core.database import get_db_session
from app.core.deps import SESSION_COOKIE, current_user
from app.core.security import TOKEN_TTL_SECONDS, issue_token
from app.domain.models.user import User
from app.domain.repositories.session_repo import SessionRepository
from app.domain.schemas.user import (
    LoginRequest,
    LogoutRequest,
    PasswordChange,
    RegisterRequest,
    UserResponse,
)
from app.domain.services import auth_service
from app.shared.constants import RATE_LIMIT_DEFAULT, RATE_LIMIT_LOGIN, RATE_LIMIT_REGISTER

router = SlashInferringRouter()

#: The one thing a successful registration says. Fixed text: anything derived from the row
#: would tell an anonymous caller something about the account that was just created.
REGISTERED_PENDING = "درخواست ثبت شد؛ پس از تأیید مدیر می‌توانید وارد شوید."

#: Attributes shared by set and delete. A browser only drops a cookie whose attributes match
#: what it stored, so these must never be spelled out twice and drift apart.
_COOKIE = {"key": SESSION_COOKIE, "httponly": True, "path": "/"}


def _cookie_kwargs() -> dict:
    """Cookie attributes for this deployment.

    ``secure``/``samesite`` come from the environment rather than the database: the panel and
    the API are separate origins in production, which needs ``SameSite=None; Secure``, while
    localhost needs neither — and a wrong value stored in the database would lock every
    operator out of the screen that could fix it.
    """
    return {
        **_COOKIE,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
    }


@cbv(router)
class AuthEndpoints:
    """Sign in, sign out, and read the current operator."""

    db: AsyncSession = Depends(get_db_session)

    @router.post("/login", response_model=UserResponse, summary="Sign in")
    @limiter.limit(RATE_LIMIT_LOGIN)
    async def login(
        self, request: Request, response: Response, payload: LoginRequest
    ) -> UserResponse:
        """Verify credentials and set the session cookie.

        Args:
            request: Required by the rate limiter to identify the caller.
            response: Carries the ``Set-Cookie`` header back.
            payload: Username and password.

        Returns:
            The signed-in operator.

        Raises:
            UnauthorizedException: Wrong credentials or a deactivated account — one
                indistinguishable 401 for every cause.
        """
        user, token = await auth_service.login(self.db, payload.username, payload.password)
        # Materialize the chat workspace here rather than letting the first API call create
        # it: the panel fires /conversations, /profile and /models in parallel the moment it
        # mounts, and all three would otherwise race the same insert.
        await SessionRepository(self.db).get_or_create_for_user(user.id)
        response.set_cookie(value=token, max_age=TOKEN_TTL_SECONDS, **_cookie_kwargs())
        return UserResponse.model_validate(user)

    @router.post("/register", status_code=202, summary="Ask for an account")
    @limiter.limit(RATE_LIMIT_REGISTER)
    async def register(self, request: Request, payload: RegisterRequest) -> dict:
        """Create a pending account for an admin to approve.

        No cookie is set and no account detail is returned — the caller cannot sign in yet,
        and echoing the row back would hand out its uuid and activation state for free. The
        answer is the same fixed message every time it succeeds.

        Args:
            request: Required by the rate limiter to identify the caller.
            payload: Desired username, password and optional display name.

        Returns:
            ``{"success": True}`` with the Persian "waiting for approval" message.

        Raises:
            ServiceUnavailableException: There is no admin yet to approve anyone.
            ValidationException: The username is taken.
        """
        await auth_service.register(
            self.db, payload.username, payload.password, payload.display_name
        )
        return {"success": True, "message": REGISTERED_PENDING}

    @router.post("/logout", summary="Sign out")
    @limiter.limit(RATE_LIMIT_DEFAULT)
    async def logout(self, request: Request, response: Response, payload: LogoutRequest) -> dict:
        """Clear the session cookie.

        Deliberately unauthenticated and idempotent: someone whose token already expired
        must still be able to clear it, and answering 401 here would strand them with a
        cookie the browser keeps sending.

        It still takes a JSON body — an empty object — and that is not decoration. A POST
        with no body is a CORS-simple request, so any page on the internet could fire it and
        sign an operator out; requiring ``application/json`` forces a preflight, which is
        refused for every origin outside ``ALLOWED_ORIGINS``.

        Args:
            request: Required by the rate limiter to identify the caller.
            response: Carries the cookie deletion back.
            payload: An empty object. Present to force the preflight.

        Returns:
            ``{"success": True}``, always.
        """
        response.delete_cookie(**_cookie_kwargs())
        return {"success": True}

    @router.get("/me", response_model=UserResponse, summary="The signed-in operator")
    @limiter.limit(RATE_LIMIT_DEFAULT)
    async def me(self, request: Request, user: User = Depends(current_user)) -> UserResponse:
        """Return who the cookie proves the caller to be.

        Args:
            request: Required by the rate limiter to identify the caller.
            user: Resolved from the session cookie.

        Returns:
            The operator, including the role the panel uses to decide what to show.
        """
        return UserResponse.model_validate(user)

    @router.post("/password", summary="Change your own password")
    @limiter.limit(RATE_LIMIT_LOGIN)
    async def change_password(
        self,
        request: Request,
        response: Response,
        payload: PasswordChange,
        user: User = Depends(current_user),
    ) -> dict:
        """Replace the caller's own password after re-checking the current one.

        Args:
            request: Required by the rate limiter to identify the caller.
            payload: Current and new password.
            user: Resolved from the session cookie.

        Returns:
            ``{"success": True}``, with a freshly issued cookie.

        Raises:
            UnauthorizedException: The current password is wrong.
            ValidationException: The new password is too short.
        """
        await auth_service.change_password(
            self.db, user, payload.current_password, payload.new_password
        )
        await self.db.commit()
        # Every session for this account, including this one, is now invalid — the token
        # carries a fingerprint of the old hash. Re-issue for the caller only: the whole
        # point is that a stolen cookie elsewhere stops working.
        response.set_cookie(
            value=issue_token(user.uuid, user.password_hash),
            max_age=TOKEN_TTL_SECONDS,
            **_cookie_kwargs(),
        )
        return {"success": True}
