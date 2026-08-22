"""Storage for the operator-editable agent overrides."""

from __future__ import annotations

import json

from sqlalchemy import select

from app.core.logging import get_logger
from app.domain.models.app_setting import AppSetting
from app.domain.repositories.base import BaseRepository

logger = get_logger("settings_repo")


class SettingsRepository(BaseRepository[AppSetting]):
    """Reads and writes the ``app_settings`` key/value rows.

    Unlike every other repository here there is nothing to scope by: these rows are global
    operator configuration, not user data. Authorization lives entirely in the one
    dependency that guards the admin router.
    """

    model = AppSetting

    async def get_all(self) -> dict:
        """Return every stored override as a decoded dict.

        A row whose JSON no longer parses is skipped rather than raised on: a bad row must
        degrade to "no override for that key", never take the chat path down with it.

        Returns:
            ``{key: value}`` for every readable row.
        """
        rows = (await self.db.execute(select(AppSetting))).scalars().all()
        stored: dict = {}
        for row in rows:
            try:
                stored[row.key] = json.loads(row.value_json)
            except (TypeError, ValueError):
                logger.warning("app_setting_unreadable", key=row.key)
        return stored

    async def upsert(self, values: dict) -> None:
        """Merge overrides in, one row per key.

        Args:
            values: Already-validated ``{key: value}`` pairs. A value of ``None`` deletes
                the row, which is how an override is reverted to its ``.env`` default.
        """
        existing = {
            row.key: row for row in (await self.db.execute(select(AppSetting))).scalars().all()
        }
        for key, value in values.items():
            row = existing.get(key)
            if value is None:
                if row is not None:
                    await self.db.delete(row)
                continue
            encoded = json.dumps(value, ensure_ascii=False)
            if row is None:
                self.db.add(AppSetting(key=key, value_json=encoded))
            else:
                row.value_json = encoded
        await self.db.flush()
