"""The streaming chat endpoints.

Answers as `text/event-stream` by default. When ``STREAM_ENABLED`` is off — the documented
escape hatch for a proxy that buffers or breaks SSE — the very same generator is drained
and returned as one JSON object, so both terminals share one code path.

**The response is a follower, not the driver.** The turn itself runs on a task owned by
``chat_runs``, so closing this connection does not stop the answer; it only stops watching
it. That is what ``GET /{conversation_uuid}/stream`` is for — come back and pick the same
turn up — and what ``POST /{conversation_uuid}/stop`` is for, since aborting the fetch no
longer stops anything.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import limiter, user_rate_limit_key
from app.api.cbv import SlashInferringRouter, cbv
from app.core.config import settings
from app.core.database import get_db_session
from app.core.deps import user_session
from app.domain.models.session import Session as SessionModel
from app.core.exceptions import NotFoundException
from app.domain.repositories.conversation_repo import ConversationRepository
from app.domain.schemas.chat import ChatRequest
from app.domain.services import chat_runs
from app.shared.constants import RATE_LIMIT_CHAT, RATE_LIMIT_DEFAULT
from app.shared.sse import SSEEvent, with_heartbeat
from app.domain.services.agent_settings import effective

router = SlashInferringRouter()

#: ``X-Accel-Buffering`` is what stops Liara's nginx from holding the whole stream until
#: the generator finishes; without it the panel sees nothing for the entire answer.
SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}

#: One message for every way a re-attach can miss: unknown conversation, someone else's,
#: one whose turn finished long enough ago to be forgotten, one that never ran. They are
#: deliberately indistinguishable — the caller learns whether THEY have a turn here and
#: nothing about whether anyone else does.
_MSG_NO_RUN = "این گفت‌وگو پیدا نشد."


async def _peeked(gen: AsyncIterator[SSEEvent]) -> AsyncIterator[SSEEvent]:
    """Pull the first event eagerly, then replay it in front of the rest.

    Everything the chat service can raise before it emits its first event (an unknown
    conversation uuid, a full conversation quota) must become a normal JSON error
    response. Once the response body has started, a raise would only truncate the stream.

    Args:
        gen: The chat service's event generator.

    Returns:
        An equivalent generator whose first item has already been produced.

    Raises:
        AppException: Whatever the chat service raises while producing the first event.
    """
    try:
        first = await gen.__anext__()
    except StopAsyncIteration:
        first = None

    async def replay() -> AsyncIterator[SSEEvent]:
        if first is not None:
            yield first
            async for event in gen:
                yield event

    return replay()


@cbv(router)
class ChatEndpoints:
    """One turn of the assistant conversation."""

    db: AsyncSession = Depends(get_db_session)
    session: SessionModel = Depends(user_session)

    @router.post(
        "/stream",
        summary="Stream one assistant answer as SSE",
        response_class=StreamingResponse,
        responses={
            200: {"content": {"text/event-stream": {}}},
            404: {"description": "Conversation not found for this session"},
        },
    )
    @limiter.limit(RATE_LIMIT_CHAT, key_func=user_rate_limit_key)
    async def stream(self, request: Request, payload: ChatRequest) -> Response:
        """Answer a user message, streaming tokens, tool status, sources and citations.

        Args:
            request: Required by the rate limiter to identify the caller.
            payload: The user turn plus the conversation it belongs to, if any.

        Returns:
            A `text/event-stream` response, or — when ``STREAM_ENABLED`` is false — a
            single JSON object holding the same events in order.
        """
        events = await _peeked(
            chat_runs.start(
                user_id=self.session.user_id,
                session_uuid=self.session.uuid,
                content=payload.content,
                conversation_uuid=payload.conversation_uuid,
                parent_uuid=payload.parent_uuid,
                # Present-and-null is an edit of the first question; absent is "append to
                # the branch on screen". Only the request itself can tell the two apart.
                parent_given="parent_uuid" in payload.model_fields_set,
                supersedes=payload.supersedes,
            )
        )
        # The turn owns its own session now, so this one has nothing left to do. Committing
        # here returns its pooled connection for the whole length of the stream; holding it
        # would pin two of the pool's fifteen per attached turn instead of one.
        await self.db.commit()
        if not effective().stream_enabled:
            collected = [{"event": e.event, "data": e.data} async for e in events]
            return JSONResponse({"success": True, "events": collected})
        return StreamingResponse(
            with_heartbeat(events),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    async def _run_for(self, conversation_uuid: str) -> chat_runs._Run:
        """Resolve the caller's own in-flight turn for this conversation, or 404.

        Two independent checks, in this order and no other:

        1. The conversation is fetched with the ownership filter **inside the query**, so a
           uuid belonging to somebody else never resolves at all.
        2. The run is then matched on the session's own uuid as well, so even a registry
           entry that somehow named a foreign conversation could not be followed.

        Every miss — unknown, foreign, already forgotten, never ran — answers the identical
        404, so the route says nothing about turns the caller does not own.
        """
        conversation = await ConversationRepository(self.db).get_scoped(
            conversation_uuid, self.session.id
        )
        if conversation is None:
            raise NotFoundException(_MSG_NO_RUN)
        run = chat_runs.live(conversation.uuid, self.session.uuid)
        if run is None:
            raise NotFoundException(_MSG_NO_RUN)
        return run

    @router.get(
        "/{conversation_uuid}/stream",
        summary="Re-attach to a turn that is already running",
        response_class=StreamingResponse,
        responses={
            200: {"content": {"text/event-stream": {}}},
            404: {"description": "No turn of this caller's is running for that conversation"},
        },
    )
    @limiter.limit(RATE_LIMIT_DEFAULT, key_func=user_rate_limit_key)
    async def follow(self, request: Request, conversation_uuid: str) -> Response:
        """Watch a turn that is already generating, from its first event.

        Deliberately **not** rate limited as a chat turn: `RATE_LIMIT_CHAT` is a spend
        ceiling, and a refresh spends nothing — charging it would 429 a user out of an
        answer they have already paid for.

        Read-only and idempotent: following consumes no event, disturbs no other follower
        and resets no timer, so several tabs may watch one answer. It reads no query string,
        only a path parameter, which is what keeps a GET safe under `enforce_origin`.

        Args:
            request: Required by the rate limiter to identify the caller.
            conversation_uuid: Public conversation identifier.

        Returns:
            A `text/event-stream` replaying the turn so far and then following it live.

        Raises:
            NotFoundException: No turn of this caller's is running for that conversation.
        """
        run = await self._run_for(conversation_uuid)
        events = chat_runs.follow(run)
        await self.db.commit()
        if not effective().stream_enabled:
            collected = [{"event": e.event, "data": e.data} async for e in events]
            return JSONResponse({"success": True, "events": collected})
        return StreamingResponse(
            with_heartbeat(events),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    @router.post(
        "/{conversation_uuid}/stop",
        status_code=204,
        summary="Stop a turn that is already running",
        responses={404: {"description": "No turn of this caller's is running for that conversation"}},
    )
    @limiter.limit(RATE_LIMIT_DEFAULT, key_func=user_rate_limit_key)
    async def stop(self, request: Request, conversation_uuid: str) -> Response:
        """Ask the assistant to stop writing.

        Closing the stream no longer does this — that is the whole point of the turn owning
        its own task — so without this route the stop button would be a hide button that
        kept on spending. Whatever already passed the commit point is still persisted.

        Args:
            request: Required by the rate limiter to identify the caller.
            conversation_uuid: Public conversation identifier.

        Returns:
            An empty 204 response.

        Raises:
            NotFoundException: No turn of this caller's is running for that conversation.
        """
        run = await self._run_for(conversation_uuid)
        chat_runs.cancel(run, "user_stop")
        return Response(status_code=204)
