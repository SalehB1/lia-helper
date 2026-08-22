"""Settings for how the assistant answers, and what it has cost so far.

Every route here is gated by one dependency declared on the router, so a route added later
cannot forget it. **Superusers only.** Ordinary users are chat users, not operators: these
knobs set the model ladder and the spending budgets, and ``log_level`` raised far enough
turns the platform log into a disclosure primitive.

Nothing in the responses is a secret. Provider keys, the signing secret and the database path
never appear here — only the tunables an operator is expected to touch, and aggregate token
counts that belong to no single user.
"""

from __future__ import annotations

from fastapi import Depends, Request
from pydantic.alias_generators import to_camel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import limiter, rate_limit_key
from app.api.cbv import SlashInferringRouter, cbv
from app.core.database import get_db_session
from app.core.deps import require_superuser
from app.domain.models.user import User
from app.core.logging import configure_logging, get_logger
from app.domain.repositories.message_repo import MessageRepository
from app.domain.repositories.settings_repo import SettingsRepository
from app.domain.schemas.admin import (
    AdminPromptsResponse,
    AdminPromptsUpdate,
    AdminSettingsResponse,
    AdminSettingsUpdate,
    ModelUsageRecord,
    PromptRejection,
    UsageResponse,
)
from app.domain.services import prompt_settings
from app.domain.services.agent_settings import (
    agent_settings,
    clean_overrides,
    describe,
    refresh,
    stored_overrides,
)
from app.shared.constants import RATE_LIMIT_DEFAULT
from app.shared.usage import TokenCount, cost_usd, label_for, read_usage

logger = get_logger("admin")

#: Money is rounded once, on the way out. Every intermediate sum stays full precision, so a
#: thousand sub-cent turns do not round away to nothing one at a time.
_COST_DIGITS = 6

router = SlashInferringRouter(dependencies=[Depends(require_superuser)])


@cbv(router)
class AdminEndpoints:
    """Read and change the agent's budgets and model ladder."""

    db: AsyncSession = Depends(get_db_session)
    #: The router already gates every route on this; declaring it here again costs one cached
    #: dependency resolution and gives the audit line a real actor. `client=` is a network
    #: address the caller can influence — it is context, never identity.
    actor: User = Depends(require_superuser)

    @router.get("/usage", response_model=UsageResponse, summary="Tokens and cost spent so far")
    @limiter.limit(RATE_LIMIT_DEFAULT)
    async def usage(self, request: Request) -> UsageResponse:
        """Total the tokens every answer has cost, broken down by model.

        All-time and aggregate only. No query parameters — no admin route may read input from
        the query string, which is what keeps the JSON preflight a real CSRF defence — and no
        per-user breakdown, because who asked how many questions is disclosure this report
        does not need in order to answer "what has this cost".

        Args:
            request: Required by the rate limiter to identify the caller.

        Returns:
            Grand totals plus one record per model, most expensive first.
        """
        totals: dict[str, TokenCount] = {}
        turns: dict[str, int] = {}
        answered = 0
        for raw in await MessageRepository(self.db).all_usage():
            split = read_usage(raw)
            if not split:
                continue
            answered += 1
            for model, tokens in split.items():
                totals[model] = totals.get(model, TokenCount()) + tokens
                turns[model] = turns.get(model, 0) + 1

        rows = [
            ModelUsageRecord(
                model=model,
                label=label_for(model),
                turns=turns[model],
                prompt_tokens=tokens.prompt,
                completion_tokens=tokens.completion,
                cost_usd=cost_usd(model, tokens),
            )
            for model, tokens in totals.items()
        ]
        rows.sort(key=lambda row: (row.cost_usd or 0.0, row.prompt_tokens), reverse=True)

        return UsageResponse(
            total_cost_usd=round(sum(row.cost_usd or 0.0 for row in rows), _COST_DIGITS),
            prompt_tokens=sum(row.prompt_tokens for row in rows),
            completion_tokens=sum(row.completion_tokens for row in rows),
            turns=answered,
            by_model=[
                row.model_copy(
                    update={"cost_usd": None if row.cost_usd is None
                            else round(row.cost_usd, _COST_DIGITS)}
                )
                for row in rows
            ],
        )

    @router.get("/settings", response_model=AdminSettingsResponse, summary="Read agent settings")
    @limiter.limit(RATE_LIMIT_DEFAULT)
    async def read(self, request: Request) -> AdminSettingsResponse:
        """Return every knob with the value in force and whether it is an override.

        Args:
            request: Required by the rate limiter to identify the caller.

        Returns:
            One record per knob, including its permitted range.
        """
        effective = await agent_settings(self.db)
        return AdminSettingsResponse(
            settings=describe(effective, await stored_overrides(self.db))
        )

    @router.put("/settings", response_model=AdminSettingsResponse, summary="Change agent settings")
    @limiter.limit(RATE_LIMIT_DEFAULT)
    async def update(self, request: Request, payload: AdminSettingsUpdate) -> AdminSettingsResponse:
        """Merge overrides in and apply them to the very next turn.

        A value of ``null`` deletes that override, reverting the knob to its ``.env``
        default. Values are re-validated here even though the schema already bounded them,
        because the loader must agree with the writer about what is legal.

        Args:
            request: Required by the rate limiter, and for the audit line.
            payload: The knobs to change.

        Returns:
            The settings as they stand after the merge.
        """
        requested = payload.model_dump(exclude_unset=True)
        accepted = clean_overrides(requested)
        rejected = sorted(set(requested) - set(accepted))
        await SettingsRepository(self.db).upsert(accepted)
        await self.db.commit()
        # Only after the commit: a cache dropped before it would be refilled from the old
        # rows by any turn racing this request. `refresh` also republishes the snapshot the
        # synchronous readers use, so the change lands on the very next request.
        effective_now = await refresh(self.db)
        if "log_level" in accepted:
            # structlog picks the new level up immediately; the web server's own access log
            # was fixed when the process started and genuinely needs a restart. The panel
            # says so rather than pretending otherwise.
            configure_logging(effective_now.log_level)
        # These are the agent's own spending limits, so who changed what must be traceable.
        logger.info(
            "admin_settings_changed",
            keys=sorted(accepted),
            rejected=rejected,
            values={key: list(value) if isinstance(value, tuple) else value
                    for key, value in accepted.items()},
            actor=self.actor.uuid,
            client=rate_limit_key(request),
        )
        return AdminSettingsResponse(
            settings=describe(effective_now, await stored_overrides(self.db)),
            # Named, not swallowed: a save the server only half-applied must not look to the
            # operator like a save that worked.
            rejected=[to_camel(key) for key in rejected],
        )

    @router.get("/prompts", response_model=AdminPromptsResponse, summary="Read the prompts")
    @limiter.limit(RATE_LIMIT_DEFAULT)
    async def read_prompts(self, request: Request) -> AdminPromptsResponse:
        """Return each editable prompt with the text in force and its contract.

        The text in force, never the stored row: a row the loader rejected is not what the
        assistant is answering under, and showing it would tell the operator their edit
        landed when it did not.

        Args:
            request: Required by the rate limiter to identify the caller.

        Returns:
            One record per prompt, including the compiled default and the fragments an edit
            may not remove.
        """
        return AdminPromptsResponse(
            prompts=prompt_settings.describe(await prompt_settings.prompts_for(self.db))
        )

    @router.put("/prompts", response_model=AdminPromptsResponse, summary="Change the prompts")
    @limiter.limit(RATE_LIMIT_DEFAULT)
    async def update_prompts(
        self, request: Request, payload: AdminPromptsUpdate
    ) -> AdminPromptsResponse:
        """Replace one or more prompts, applying to the very next answer.

        ``null`` or a blank string reverts that prompt to the compiled default by deleting
        its row, which is also the recovery path for a bad edit. A text missing a
        load-bearing rule is refused and named; the prompt it would have replaced stays in
        force.

        Args:
            request: Required by the rate limiter, and for the audit line.
            payload: The prompts to change.

        Returns:
            The prompts as they stand after the write, plus anything refused.
        """
        before = await prompt_settings.prompts_for(self.db)
        accepted, rejections = prompt_settings.review(payload.model_dump(exclude_unset=True))
        await SettingsRepository(self.db).upsert(
            {f"{prompt_settings.STORAGE_PREFIX}{key}": text for key, text in accepted.items()}
        )
        await self.db.commit()
        # Only after the commit, as with the settings: a cache dropped before it would be
        # refilled from the old rows by any turn racing this request.
        current = await prompt_settings.refresh(self.db)
        # This is the text carrying every rule the assistant answers under, so who changed
        # which prompt and when must be traceable. Digests, never the text: it is 7 KB of the
        # verbatim wording of every internal rule, and the log level is settable from the
        # panel — writing it here would rebuild by hand the disclosure primitive
        # `configure_logging` pins the SQL loggers to WARNING to avoid.
        logger.info(
            "admin_prompts_changed",
            keys=sorted(accepted),
            reverted=sorted(key for key, text in accepted.items() if text is None),
            rejected=[rejection["key"] for rejection in rejections],
            reasons=[reason for rejection in rejections for reason in rejection["reasons"]],
            chars={key: len(getattr(current, key)) for key in accepted},
            digest={key: prompt_settings.digest(getattr(current, key)) for key in accepted},
            previous={key: prompt_settings.digest(getattr(before, key)) for key in accepted},
            actor=self.actor.uuid,
            client=rate_limit_key(request),
        )
        return AdminPromptsResponse(
            prompts=prompt_settings.describe(current),
            # camelCase, the spelling the screen sent and the one it renders labels by.
            rejected=[
                PromptRejection(key=to_camel(rejection["key"]), reasons=rejection["reasons"])
                for rejection in rejections
            ],
        )
