"""Session rows and the small key/value profile attached to them."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.domain.models.session import Session
from app.domain.repositories.base import BaseRepository
#: Only these profile keys are ever persisted. Anything else the caller sends is dropped.
#: ``model`` used to live here — the chat model is now the operator's setting, not a
#: per-user preference, so there is no longer any client-supplied model id to allowlist.
PROFILE_KEYS: tuple[str, ...] = ("platform", "framework", "notes")

#: Hard clamp for every profile value.
PROFILE_VALUE_CHARS = 80


def _clean(key: str, value: Any) -> str | None:
    """Normalize one profile value, or None when it may not be stored.

    Args:
        key: One of :data:`PROFILE_KEYS`.
        value: The untrusted value, from a request body or from the stored blob.

    Returns:
        The trimmed, clamped value, or None when it is not a string or is empty.
    """
    if not isinstance(value, str):
        return None
    return value.strip()[:PROFILE_VALUE_CHARS] or None


def _parse_profile(raw: str | None) -> dict[str, str]:
    """Decode a stored profile blob, tolerating NULL and corrupt JSON.

    Args:
        raw: The raw ``profile_json`` column value.

    Returns:
        The allowlisted profile mapping; empty when the blob is missing or unusable.
    """
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    cleaned = {key: _clean(key, data.get(key)) for key in PROFILE_KEYS}
    return {key: value for key, value in cleaned.items() if value is not None}


class SessionRepository(BaseRepository[Session]):
    """Read/write access to a user's chat workspace."""

    model = Session

    async def _for_user(self, user_id: int) -> Session | None:
        """Fetch the session row owned by this user, or None."""
        result = await self.db.execute(select(Session).where(Session.user_id == user_id))
        return result.scalar_one_or_none()

    async def get_or_create_for_user(self, user_id: int) -> Session:
        """Return this user's session, creating it the first time they are seen.

        The uuid is minted here rather than accepted from the client, which is the whole
        point: it is an internal handle now, not a bearer token. The insert runs inside a
        savepoint so the loser of a race — the panel fires several requests at once on
        mount — loses only the savepoint and re-reads the winner's row.

        Args:
            user_id: Internal id of the signed-in user.

        Returns:
            The persisted session row.
        """
        existing = await self._for_user(user_id)
        if existing is not None:
            return existing

        row = Session(uuid=str(uuid4()), user_id=user_id)
        try:
            async with self.db.begin_nested():
                self.db.add(row)
                await self.db.flush()
        except IntegrityError:
            existing = await self._for_user(user_id)
            if existing is None:
                raise
            return existing
        return row

    async def get_profile(self, session: Session) -> dict:
        """Return the stored profile for a session.

        Args:
            session: The owning session row.

        Returns:
            A mapping limited to :data:`PROFILE_KEYS`; empty when nothing is stored.
        """
        return _parse_profile(session.profile_json)

    async def set_profile(self, session: Session, profile: dict) -> Session:
        """Merge caller-supplied values into the stored profile.

        Only :data:`PROFILE_KEYS` are considered and every value is clamped to
        :data:`PROFILE_VALUE_CHARS`, so an arbitrary payload can never mass-assign
        unexpected fields. ``None`` leaves a key untouched (PATCH semantics); an empty
        or whitespace-only string clears it.

        Args:
            session: The owning session row.
            profile: Caller-supplied, untrusted mapping.

        Returns:
            The same session row, flushed.
        """
        current = _parse_profile(session.profile_json)
        for key in PROFILE_KEYS:
            if key not in profile:
                continue
            value: Any = profile[key]
            if value is None or not isinstance(value, str):
                continue
            if not value.strip():
                current.pop(key, None)
                continue
            text = _clean(key, value)
            if text is not None:
                current[key] = text

        session.profile_json = json.dumps(current, ensure_ascii=False) if current else None
        await self.db.flush()
        return session
