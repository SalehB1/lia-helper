"""The tiny per-session profile the assistant personalizes answers with."""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import limiter
from app.api.cbv import SlashInferringRouter, cbv
from app.core.database import get_db_session
from app.core.deps import user_session
from app.domain.models.session import Session as SessionModel
from app.domain.repositories.session_repo import SessionRepository
from app.domain.schemas.profile import ProfileResponse, ProfileUpdate
from app.shared.constants import RATE_LIMIT_DEFAULT

router = SlashInferringRouter()


@cbv(router)
class ProfileEndpoints:
    """Read and merge the calling session's profile."""

    db: AsyncSession = Depends(get_db_session)
    session: SessionModel = Depends(user_session)

    @router.get("", response_model=ProfileResponse, summary="Read the session profile")
    @limiter.limit(RATE_LIMIT_DEFAULT)
    async def read(self, request: Request) -> ProfileResponse:
        """Return what the assistant remembers about this session.

        Args:
            request: Required by the rate limiter to identify the caller.

        Returns:
            The stored profile; every field is null when nothing has been saved.
        """
        profile = await SessionRepository(self.db).get_profile(self.session)
        return ProfileResponse(**profile)

    @router.patch("", response_model=ProfileResponse, summary="Update the session profile")
    @limiter.limit(RATE_LIMIT_DEFAULT)
    async def update(self, request: Request, payload: ProfileUpdate) -> ProfileResponse:
        """Merge the supplied fields into the stored profile.

        Omitted fields are left untouched; sending an empty string clears a field.

        Args:
            request: Required by the rate limiter to identify the caller.
            payload: The fields to change.

        Returns:
            The profile as it stands after the merge.
        """
        repository = SessionRepository(self.db)
        await repository.set_profile(self.session, payload.model_dump(exclude_unset=True))
        profile = await repository.get_profile(self.session)
        return ProfileResponse(**profile)
