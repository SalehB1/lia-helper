"""Shared repository plumbing.

Repositories flush but never commit: the request-scoped session dependency owns the
transaction boundary.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.base import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """Minimal async repository: hold the session, add rows, delete rows."""

    model: type[ModelType]

    def __init__(self, db: AsyncSession) -> None:
        """Store the request-scoped session.

        Args:
            db: The active async session; the caller owns commit/rollback.
        """
        self.db = db

    async def add(self, obj: ModelType) -> ModelType:
        """Persist a new row and flush so its primary key is populated.

        Args:
            obj: The ORM instance to insert.

        Returns:
            The same instance, now flushed.
        """
        self.db.add(obj)
        await self.db.flush()
        return obj

    async def delete(self, obj: ModelType) -> None:
        """Delete a row and flush.

        Args:
            obj: The ORM instance to delete.
        """
        await self.db.delete(obj)
        await self.db.flush()
