"""Storage for panel operators.

Every lookup filters inside the query. There is no "load them all and pick in Python" path,
because the day one of these lists is rendered to a non-superuser is the day that shortcut
becomes a disclosure.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import ValidationException
from app.domain.models.user import User
from app.domain.repositories.base import BaseRepository

DUPLICATE_USERNAME = "این نام کاربری قبلاً ثبت شده است."


class UserRepository(BaseRepository[User]):
    """Reads and writes the ``users`` table. Flushes, never commits."""

    model = User

    async def by_username(self, username: str) -> User | None:
        """Find an operator by username, or None."""
        result = await self.db.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    async def by_uuid(self, user_uuid: str) -> User | None:
        """Find an operator by public uuid, or None.

        Resolving a uuid proves the row exists and nothing else — the caller still has to
        be authorized to act on it.
        """
        result = await self.db.execute(select(User).where(User.uuid == user_uuid))
        return result.scalar_one_or_none()

    async def list_all(self) -> list[User]:
        """Every account, pending approvals first, then by username.

        The ``is_active`` clause is the whole approval queue: self-registration creates
        inactive rows, SQLite sorts ``0`` before ``1``, and the panel's existing activation
        toggle does the rest. No separate endpoint, no separate screen.
        """
        result = await self.db.execute(select(User).order_by(User.is_active, User.username))
        return list(result.scalars().all())

    async def count(self) -> int:
        """How many operators exist at all — the bootstrap condition."""
        result = await self.db.execute(select(func.count()).select_from(User))
        return int(result.scalar_one())

    async def count_active_superusers(self) -> int:
        """How many superusers can still sign in.

        The guard behind "you cannot remove the last superuser". Counted in SQL against the
        same predicate the login path uses, so it cannot drift from what ``is_active`` means.
        """
        result = await self.db.execute(
            select(func.count())
            .select_from(User)
            .where(User.is_superuser.is_(True), User.is_active.is_(True))
        )
        return int(result.scalar_one())

    async def create(
        self,
        *,
        username: str,
        password_hash: str,
        is_superuser: bool = False,
        is_active: bool = True,
        display_name: str | None = None,
    ) -> User:
        """Insert an account.

        The insert runs inside a savepoint so a duplicate username loses only the savepoint,
        not the caller's whole transaction.

        Args:
            username: Already-trimmed username; unique.
            password_hash: Output of ``security.hash_password``.
            is_superuser: Whether they may also manage other accounts.
            is_active: False creates a pending account that cannot sign in until an admin
                activates it — the shape self-registration needs.
            display_name: Optional human name for the header.

        Returns:
            The persisted row.

        Raises:
            ValidationException: The username is taken.
        """
        row = User(
            username=username,
            password_hash=password_hash,
            is_superuser=is_superuser,
            is_active=is_active,
            display_name=display_name or None,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(row)
                await self.db.flush()
        except IntegrityError:
            raise ValidationException(DUPLICATE_USERNAME) from None
        return row
