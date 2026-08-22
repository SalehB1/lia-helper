"""Managing panel operators. Superuser only.

The gate is declared on the router, not on each route, so a route added later cannot forget
it. Note what that gate is NOT: resolving ``{user_uuid}`` to a row proves the row exists and
nothing more — authorization is the router dependency, and the four guards below are what
stop a superuser from locking everyone (including themselves) out.

Every route takes a JSON body or no body at all. ``application/json`` forces a CORS
preflight, and that preflight is this surface's CSRF defence — **no route here may ever
accept a form body or read input from the query string.**
"""

from __future__ import annotations

import asyncio

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import limiter
from app.api.cbv import SlashInferringRouter, cbv
from app.core.database import get_db_session
from app.core.deps import require_superuser
from app.core.exceptions import NotFoundException, ValidationException
from app.core.logging import get_logger
from app.core.security import hash_password
from app.domain.models.user import User
from app.domain.repositories.user_repo import UserRepository
from app.domain.schemas.user import PasswordReset, UserCreate, UserResponse, UserUpdate
from app.shared.constants import RATE_LIMIT_DEFAULT, RATE_LIMIT_LOGIN
from app.shared.enums import UserRole

logger = get_logger("users")

router = SlashInferringRouter(dependencies=[Depends(require_superuser)])

NO_USER = "این کاربر پیدا نشد."
SELF_ROLE = "سطح دسترسی خودتان را نمی‌توانید عوض کنید."
SELF_DISABLE = "حساب خودتان را نمی‌توانید حذف یا غیرفعال کنید."
LAST_SUPERUSER = "آخرین مدیر ارشد را نمی‌شود حذف یا غیرفعال کرد."


@cbv(router)
class UserEndpoints:
    """List, create, change and remove operators."""

    db: AsyncSession = Depends(get_db_session)
    actor: User = Depends(require_superuser)

    async def _target(self, user_uuid: str) -> User:
        """Resolve a user by public uuid, or 404."""
        user = await UserRepository(self.db).by_uuid(user_uuid)
        if user is None:
            raise NotFoundException(NO_USER)
        return user

    async def _guard_last_superuser(self, target: User) -> None:
        """Refuse a change that would leave nobody able to manage users.

        Args:
            target: The account about to be demoted, deactivated or deleted.

        Raises:
            ValidationException: ``target`` is the last superuser who can still sign in.
        """
        if not target.is_superuser or not target.is_active:
            return
        if await UserRepository(self.db).count_active_superusers() <= 1:
            raise ValidationException(LAST_SUPERUSER)

    async def _assert_someone_is_left(self) -> None:
        """Re-count AFTER the change and refuse if it emptied the role.

        The check before the change is a courtesy that produces a good error message; this
        one is the actual guarantee. Two concurrent requests can each see two superusers and
        each remove a different one, and only a count taken after the write — inside this
        transaction, so it sees our own change — catches that. Raising here rolls the
        request back before commit.

        Raises:
            ValidationException: The change left no superuser who can sign in.
        """
        await self.db.flush()
        if await UserRepository(self.db).count_active_superusers() < 1:
            raise ValidationException(LAST_SUPERUSER)

    @router.get("", response_model=list[UserResponse], summary="List operators")
    @limiter.limit(RATE_LIMIT_DEFAULT)
    async def index(self, request: Request) -> list[UserResponse]:
        """Every operator, ordered by username.

        Args:
            request: Required by the rate limiter to identify the caller.

        Returns:
            The operators. The password hash is not part of the response model, so it
            cannot be serialized here even by accident.
        """
        rows = await UserRepository(self.db).list_all()
        return [UserResponse.model_validate(row) for row in rows]

    @router.post("", response_model=UserResponse, status_code=201, summary="Create an operator")
    @limiter.limit(RATE_LIMIT_LOGIN)
    async def create(self, request: Request, payload: UserCreate) -> UserResponse:
        """Add an operator.

        Args:
            request: Required by the rate limiter to identify the caller.
            payload: Username, password, role and optional display name.

        Returns:
            The new operator.

        Raises:
            ValidationException: The username is already taken.
        """
        user = await UserRepository(self.db).create(
            username=payload.username.strip(),
            # Hashing is ~100 ms of CPU: off the event loop, like every other scrypt call.
            password_hash=await asyncio.to_thread(hash_password, payload.password),
            is_superuser=payload.role is UserRole.SUPERUSER,
            display_name=(payload.display_name or "").strip() or None,
        )
        await self.db.commit()
        logger.info(
            "user_created",
            actor=self.actor.uuid,
            target=user.uuid,
            superuser=user.is_superuser,
        )
        return UserResponse.model_validate(user)

    @router.patch("/{user_uuid}", response_model=UserResponse, summary="Change an operator")
    @limiter.limit(RATE_LIMIT_DEFAULT)
    async def update(self, request: Request, user_uuid: str, payload: UserUpdate) -> UserResponse:
        """Change a role, activation state or display name.

        Args:
            request: Required by the rate limiter to identify the caller.
            user_uuid: Public uuid of the target.
            payload: The fields to change; omitted fields are left alone.

        Returns:
            The operator after the change.

        Raises:
            NotFoundException: No such user.
            ValidationException: The change would demote or disable your own account, or
                remove the last superuser who can still sign in.
        """
        target = await self._target(user_uuid)
        changes = payload.model_dump(exclude_unset=True)

        if "role" in changes and changes["role"] is not None:
            if target.uuid == self.actor.uuid:
                raise ValidationException(SELF_ROLE)
            promoting = UserRole(changes["role"]) is UserRole.SUPERUSER
            if not promoting:
                await self._guard_last_superuser(target)
            target.is_superuser = promoting

        if "is_active" in changes and changes["is_active"] is not None:
            if not changes["is_active"]:
                if target.uuid == self.actor.uuid:
                    raise ValidationException(SELF_DISABLE)
                await self._guard_last_superuser(target)
            target.is_active = bool(changes["is_active"])

        if "display_name" in changes:
            target.display_name = (changes["display_name"] or "").strip() or None

        await self._assert_someone_is_left()
        await self.db.commit()
        logger.info(
            "user_updated",
            actor=self.actor.uuid,
            target=target.uuid,
            superuser=target.is_superuser,
            active=target.is_active,
        )
        return UserResponse.model_validate(target)

    @router.put("/{user_uuid}/password", summary="Reset an operator's password")
    @limiter.limit(RATE_LIMIT_LOGIN)
    async def reset_password(
        self, request: Request, user_uuid: str, payload: PasswordReset
    ) -> dict:
        """Set someone else's password.

        No current-password check: a superuser resetting an account is exactly the case
        where the current password is unavailable.

        Args:
            request: Required by the rate limiter to identify the caller.
            user_uuid: Public uuid of the target.
            payload: The new password.

        Returns:
            ``{"success": True}``.

        Raises:
            NotFoundException: No such user.
        """
        target = await self._target(user_uuid)
        target.password_hash = await asyncio.to_thread(hash_password, payload.new_password)
        await self.db.commit()
        logger.info("user_password_reset", actor=self.actor.uuid, target=target.uuid)
        return {"success": True}

    @router.delete("/{user_uuid}", status_code=204, summary="Delete an operator")
    @limiter.limit(RATE_LIMIT_DEFAULT)
    async def destroy(self, request: Request, user_uuid: str) -> None:
        """Remove an operator.

        Args:
            request: Required by the rate limiter to identify the caller.
            user_uuid: Public uuid of the target.

        Raises:
            NotFoundException: No such user.
            ValidationException: Deleting yourself, or the last superuser.
        """
        target = await self._target(user_uuid)
        if target.uuid == self.actor.uuid:
            raise ValidationException(SELF_DISABLE)
        await self._guard_last_superuser(target)
        uuid_for_log = target.uuid
        await UserRepository(self.db).delete(target)
        await self._assert_someone_is_left()
        await self.db.commit()
        logger.info("user_deleted", actor=self.actor.uuid, target=uuid_for_log)
