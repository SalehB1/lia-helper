"""Conversation history for the calling session.

Every read and write goes through :class:`ConversationRepository`'s scoped accessors, so a
conversation uuid that belongs to another session is indistinguishable from one that never
existed: both answer 404.
"""

from __future__ import annotations

from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import limiter
from app.api.cbv import SlashInferringRouter, cbv
from app.core.database import get_db_session
from app.core.deps import user_session
from app.core.exceptions import NotFoundException
from app.domain.models.conversation import Conversation
from app.domain.models.session import Session as SessionModel
from app.domain.repositories.conversation_repo import ConversationRepository
from app.domain.repositories.message_repo import MessageRepository
from app.domain.schemas.conversation import (
    ActiveMessageRequest,
    ConversationDetailResponse,
    ConversationRenameRequest,
    ConversationResponse,
    MessageResponse,
)
from app.domain.services import chat_runs
from app.shared.constants import RATE_LIMIT_DEFAULT

router = SlashInferringRouter()

CONVERSATION_NOT_FOUND = "این گفت‌وگو پیدا نشد."
MESSAGE_NOT_FOUND = "این پیام پیدا نشد."

#: Matches ``ConversationRepository.list_for_session``'s own default.
LIST_LIMIT = 50


@cbv(router)
class ConversationEndpoints:
    """List, read and delete the calling session's conversations."""

    db: AsyncSession = Depends(get_db_session)
    session: SessionModel = Depends(user_session)

    async def _detail(self, conversation: Conversation) -> ConversationDetailResponse:
        """Render a conversation and the branch it is currently pointing at.

        Args:
            conversation: A conversation already fetched through a scoped accessor.

        Returns:
            The conversation with the active branch, each message carrying its parent and
            its place among its versions. ``messageCount`` is the branch length, so it
            matches what the panel draws.
        """
        # Read BEFORE the branch query, and the ordering is the whole correctness argument:
        # if the turn commits in between, the branch below already contains the finished
        # answer AND the panel re-attaches, replays the retained log and repaints the same
        # text. The other order shows a question with nothing under it and never corrects.
        active_run = chat_runs.live_unfinished(conversation.uuid, self.session.uuid) is not None
        nodes = await MessageRepository(self.db).active_branch(
            conversation.id, conversation.active_message_id
        )
        return ConversationDetailResponse(
            uuid=conversation.uuid,
            title=conversation.title,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            message_count=len(nodes),
            active_run=active_run,
            messages=[
                MessageResponse.from_model(
                    node.message, node.parent_uuid, node.version_index, node.version_count
                )
                for node in nodes
            ],
        )

    async def _scoped(self, conversation_uuid: str) -> Conversation:
        """Fetch a conversation this session owns, or raise the same 404 as a missing one.

        Args:
            conversation_uuid: Public conversation identifier from the path.

        Returns:
            The conversation row.

        Raises:
            NotFoundException: The uuid is unknown or owned by a different session.
        """
        conversation = await ConversationRepository(self.db).get_scoped(
            conversation_uuid, self.session.id
        )
        if conversation is None:
            raise NotFoundException(CONVERSATION_NOT_FOUND)
        return conversation

    @router.get("", response_model=list[ConversationResponse], summary="List conversations")
    @limiter.limit(RATE_LIMIT_DEFAULT)
    async def list_conversations(self, request: Request) -> list[ConversationResponse]:
        """List this session's conversations, most recently updated first.

        Args:
            request: Required by the rate limiter to identify the caller.

        Returns:
            The conversations, each carrying the length of its active branch.
        """
        conversations = await ConversationRepository(self.db).list_for_session(
            self.session.id, limit=LIST_LIMIT
        )
        counts = await MessageRepository(self.db).branch_lengths(
            {conversation.id: conversation.active_message_id for conversation in conversations}
        )
        return [
            ConversationResponse(
                uuid=conversation.uuid,
                title=conversation.title,
                created_at=conversation.created_at,
                updated_at=conversation.updated_at,
                message_count=counts.get(conversation.id, 0),
            )
            for conversation in conversations
        ]

    @router.get(
        "/{conversation_uuid}",
        response_model=ConversationDetailResponse,
        summary="Read one conversation's active branch",
        responses={404: {"description": "Unknown conversation, or owned by another session"}},
    )
    @limiter.limit(RATE_LIMIT_DEFAULT)
    async def detail(self, request: Request, conversation_uuid: str) -> ConversationDetailResponse:
        """Return one conversation and the branch of messages currently selected.

        Args:
            request: Required by the rate limiter to identify the caller.
            conversation_uuid: Public conversation identifier.

        Returns:
            The conversation with the active branch in chronological order.

        Raises:
            NotFoundException: The uuid is unknown or owned by a different session.
        """
        return await self._detail(await self._scoped(conversation_uuid))

    @router.patch(
        "/{conversation_uuid}/active",
        response_model=ConversationDetailResponse,
        summary="Switch which version of a message the conversation follows",
        responses={404: {"description": "Unknown conversation or message, or owned by another"}},
    )
    @limiter.limit(RATE_LIMIT_DEFAULT)
    async def set_active(
        self, request: Request, conversation_uuid: str, payload: ActiveMessageRequest
    ) -> ConversationDetailResponse:
        """Select a message version and make its branch the displayed one.

        The conversation is resolved through the session and the message through that
        conversation, so a message uuid from anywhere else answers the same 404 as one that
        never existed. The new leaf is the deepest descendant of the chosen message,
        reached by always following the newest child — selecting an older question brings
        back whatever was last written under it.

        Args:
            request: Required by the rate limiter to identify the caller.
            conversation_uuid: Public conversation identifier.
            payload: The message version to switch to.

        Returns:
            The conversation with its newly active branch.

        Raises:
            NotFoundException: Either uuid is unknown or belongs to someone else.
        """
        conversation = await self._scoped(conversation_uuid)
        messages = MessageRepository(self.db)
        message = await messages.get_scoped(payload.message_uuid, conversation.id)
        if message is None:
            raise NotFoundException(MESSAGE_NOT_FOUND)
        if payload.version_index is not None:
            # The sibling group is reached through a message already scoped to this
            # conversation, so an index can only ever address versions the caller owns.
            message = await messages.sibling_at(message, payload.version_index)
            if message is None:
                raise NotFoundException(MESSAGE_NOT_FOUND)
        leaf = await messages.deepest_leaf(conversation.id, message.id)
        await ConversationRepository(self.db).set_active(conversation, leaf)
        return await self._detail(conversation)

    @router.patch(
        "/{conversation_uuid}",
        response_model=ConversationResponse,
        summary="Rename one conversation",
        responses={404: {"description": "Unknown conversation, or owned by another session"}},
    )
    @limiter.limit(RATE_LIMIT_DEFAULT)
    async def rename(
        self, request: Request, conversation_uuid: str, payload: ConversationRenameRequest
    ) -> ConversationResponse:
        """Give a conversation the name its owner wants.

        A generated name is a guess, so this is the correction — and once it is made,
        nothing overwrites it: the only writer that could, ``chat_service.write_title``,
        compares the stored title against what it read before asking the model and drops its
        own suggestion when they differ.

        The title is sanitised by the same ``_clean_title`` the other two writers use, so a
        control character or a bidi override cannot be stored from here either.

        Args:
            request: Required by the rate limiter to identify the caller.
            conversation_uuid: Public conversation identifier.
            payload: The new title.

        Returns:
            The conversation as it stands after the rename, carrying the stored title —
            which may be shorter than what was sent, and is what the sidebar must show.

        Raises:
            NotFoundException: The uuid is unknown or belongs to another session.
        """
        conversation = await self._scoped(conversation_uuid)
        conversations = ConversationRepository(self.db)
        await conversations.touch_title(conversation, payload.title)
        counts = await MessageRepository(self.db).branch_lengths(
            {conversation.id: conversation.active_message_id}
        )
        return ConversationResponse(
            uuid=conversation.uuid,
            title=conversation.title,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            message_count=counts.get(conversation.id, 0),
        )

    @router.delete(
        "/{conversation_uuid}",
        status_code=204,
        summary="Delete one conversation",
        responses={404: {"description": "Unknown conversation, or owned by another session"}},
    )
    @limiter.limit(RATE_LIMIT_DEFAULT)
    async def remove(self, request: Request, conversation_uuid: str) -> Response:
        """Delete a conversation and, by database cascade, its messages.

        Args:
            request: Required by the rate limiter to identify the caller.
            conversation_uuid: Public conversation identifier.

        Returns:
            An empty 204 response.

        Raises:
            NotFoundException: The uuid is unknown or owned by a different session.
        """
        deleted = await ConversationRepository(self.db).delete_scoped(
            conversation_uuid, self.session.id
        )
        if not deleted:
            raise NotFoundException(CONVERSATION_NOT_FOUND)
        return Response(status_code=204)
