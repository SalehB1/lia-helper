"""Message access.

Messages are reachable only through an internal ``conversation_id``, which callers can
obtain solely from :meth:`ConversationRepository.get_scoped` — so ownership is already
proven by the time we get here.

Messages form a tree (see :class:`~app.domain.models.message.Message`). Every branch
question here is answered by loading the conversation's rows **once** and walking them in
memory: a conversation is a few hundred rows at most, so a recursive CTE would buy nothing
and cost a second dialect to reason about. Every walk is bounded by the row count and
guarded by a ``seen`` set, so a corrupt parent link cannot hang a request.
"""

from __future__ import annotations

import json
from typing import Any, NamedTuple

from sqlalchemy import select, update

from app.domain.models.message import Message
from app.domain.repositories.base import BaseRepository
from app.shared.constants import HISTORY_WINDOW
from app.shared.enums import MessageRole

#: Ceiling on the rows loaded to answer one branch question.
#: ponytail: whole tree in memory; swap the walk for a recursive CTE if conversations
#: ever get long enough for this to matter.
MAX_TREE_ROWS = 500


def _dump(value: Any) -> str | None:
    """Serialize a JSON side-car column, returning None for empty or unserializable input."""
    if not value:
        return None
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return None


def _walk_up(parents: dict[int, int | None], leaf: int | None) -> list[int]:
    """Follow parent links from a leaf up to its root.

    Args:
        parents: Message id to parent id, for one conversation.
        leaf: Id to start from; unknown or None yields an empty chain.

    Returns:
        The ids on that branch, root first. A cycle (including a self-referencing row)
        stops the walk instead of repeating.
    """
    chain: list[int] = []
    seen: set[int] = set()
    current = leaf
    while current is not None and current in parents and current not in seen:
        seen.add(current)
        chain.append(current)
        current = parents[current]
    chain.reverse()
    return chain


def _versions(rows: list[Message]) -> dict[int, tuple[int, int]]:
    """Map every message id to its 1-based position among its siblings and their count.

    Args:
        rows: One conversation's messages, id-ascending.

    Returns:
        ``{message id: (version index, version count)}``. Siblings are the rows sharing a
        ``parent_id`` — the versions of one question or one answer.
    """
    groups: dict[int | None, list[int]] = {}
    for row in rows:
        groups.setdefault(row.parent_id, []).append(row.id)
    return {
        message_id: (position + 1, len(ids))
        for ids in groups.values()
        for position, message_id in enumerate(ids)
    }


class BranchNode(NamedTuple):
    """One message on the active branch, with what the UI needs to place it in the tree."""

    message: Message
    parent_uuid: str | None
    version_index: int
    version_count: int


class MessageRepository(BaseRepository[Message]):
    """Reads and writes for conversation turns."""

    model = Message

    async def create(
        self,
        conversation_id: int,
        role: MessageRole,
        content: str,
        sources: list[dict] | None = None,
        tool_trace: list[dict] | None = None,
        usage: dict | None = None,
        parent_id: int | None = None,
    ) -> Message:
        """Persist one turn of a conversation.

        Args:
            conversation_id: Internal id of the owning conversation.
            role: Who produced the message.
            content: Message body.
            sources: Citation records to store as JSON, if any.
            tool_trace: Tool invocation records to store as JSON, if any.
            usage: Token usage record to store as JSON, if any.
            parent_id: Message this one follows; None makes it a root of the tree. The
                caller must have resolved it inside this same conversation.

        Returns:
            The persisted message.
        """
        return await self.add(
            Message(
                conversation_id=conversation_id,
                parent_id=parent_id,
                role=role,
                content=content,
                sources_json=_dump(sources),
                tool_trace_json=_dump(tool_trace),
                usage_json=_dump(usage),
            )
        )

    async def rewrite(
        self,
        message_id: int,
        content: str,
        sources: list[dict] | None = None,
        tool_trace: list[dict] | None = None,
        usage: dict | None = None,
    ) -> None:
        """Overwrite one message's body and its three JSON side-cars.

        The counterpart of :meth:`create`, and the only update path in this class. A turn
        writes its assistant row once, at the commit point, and rewrites it as more text is
        released — always the whole current text, never a delta, because ``_AnswerBuffer``
        rewrites its own released text when a tool round closes.

        Keyed by the internal id rather than by an ORM instance on purpose: the caller holds
        the handle across its own commits, and the first attribute read on an expired
        instance inside a background task is a ``MissingGreenlet``. An int cannot expire.

        Args:
            message_id: Internal id of the row to rewrite — one this same turn created.
            content: The full body to store, replacing what is there.
            sources: Citation records, replacing what is there.
            tool_trace: Tool invocation records, replacing what is there.
            usage: Token usage record, replacing what is there.
        """
        await self.db.execute(
            update(Message)
            .where(Message.id == message_id)
            .values(
                content=content,
                sources_json=_dump(sources),
                tool_trace_json=_dump(tool_trace),
                usage_json=_dump(usage),
            )
        )
        await self.db.flush()

    async def get_scoped(self, message_uuid: str, conversation_id: int) -> Message | None:
        """Fetch a message only if it belongs to this conversation.

        The conversation id itself is obtainable only from a session-scoped fetch, so this
        is the second half of the authorization: a message uuid from another session's
        conversation resolves to nothing here, exactly like one that never existed.

        Args:
            message_uuid: Public message identifier.
            conversation_id: Internal id of the conversation that must own it.

        Returns:
            The message, or None when it is unknown or lives in another conversation.
        """
        result = await self.db.execute(
            select(Message).where(
                Message.uuid == message_uuid,
                Message.conversation_id == conversation_id,
            )
        )
        return result.scalar_one_or_none()

    async def has_children(self, conversation_id: int, message_id: int) -> bool:
        """Whether anything in this conversation hangs under ``message_id``.

        Args:
            conversation_id: Internal id of the owning conversation.
            message_id: The message to test, already resolved inside that conversation.

        Returns:
            True when at least one row names it as parent.
        """
        result = await self.db.execute(
            select(Message.id)
            .where(
                Message.conversation_id == conversation_id,
                Message.parent_id == message_id,
            )
            .limit(1)
        )
        return result.first() is not None

    async def list_for_conversation(
        self, conversation_id: int, limit: int = MAX_TREE_ROWS
    ) -> list[Message]:
        """List a conversation's messages — every branch — in insertion order.

        Args:
            conversation_id: Internal id of the owning conversation.
            limit: Maximum rows to return.

        Returns:
            Messages, oldest first.
        """
        # Window the NEWEST rows, not the oldest. Taking the oldest `limit` rows silently
        # drops the active leaf out of the window the moment a conversation outgrows the
        # cap, and every walk built on this list then falls back to row `limit`: the newest
        # turns vanish from the transcript, the model stops seeing what was just said, and
        # each further question grafts itself under that row as a phantom "version".
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))

    async def resolve_leaf(self, conversation_id: int, active_message_id: int | None) -> int | None:
        """Return the id the conversation's branch actually ends at.

        Args:
            conversation_id: Internal id of the owning conversation.
            active_message_id: The stored pointer, which may be NULL (a conversation from
                before branching) or dangling (its message was deleted).

        Returns:
            The active leaf when it is a live row of this conversation, else the newest
            message, else None for an empty conversation.
        """
        rows = await self.list_for_conversation(conversation_id)
        ids = {row.id for row in rows}
        if active_message_id in ids:
            return active_message_id
        return rows[-1].id if rows else None

    async def active_branch(
        self, conversation_id: int, active_message_id: int | None
    ) -> list[BranchNode]:
        """Return the branch currently on screen, oldest first, with version metadata.

        Args:
            conversation_id: Internal id of the owning conversation.
            active_message_id: The conversation's stored active leaf.

        Returns:
            One :class:`BranchNode` per message on the active branch. Abandoned versions
            and their descendants are left out.
        """
        rows = await self.list_for_conversation(conversation_id)
        by_id = {row.id: row for row in rows}
        leaf = active_message_id if active_message_id in by_id else (rows[-1].id if rows else None)
        versions = _versions(rows)
        chain = _walk_up({row.id: row.parent_id for row in rows}, leaf)
        nodes: list[BranchNode] = []
        for message_id in chain:
            row = by_id[message_id]
            parent = by_id.get(row.parent_id) if row.parent_id is not None else None
            index, count = versions.get(message_id, (1, 1))
            nodes.append(BranchNode(row, parent.uuid if parent else None, index, count))
        return nodes

    async def branch_lengths(self, leaves: dict[int, int | None]) -> dict[int, int]:
        """Count the messages on each conversation's active branch, in one query.

        The listing shows this as ``messageCount``, so it must agree with what opening the
        conversation renders: abandoned versions are not on the branch and are not counted.

        Args:
            leaves: ``{conversation id: active_message_id}`` for conversations already
                proven to belong to the caller. A NULL or dangling leaf falls back to the
                conversation's newest message, exactly like :meth:`active_branch`.

        Returns:
            A mapping of conversation id to branch length; conversations with no messages
            are absent.
        """
        if not leaves:
            return {}
        # Bounded like list_for_conversation, and for the same two reasons: the sidebar
        # count must agree with the transcript the detail endpoint returns, and this runs
        # for up to a page of conversations on every list request against a single worker.
        result = await self.db.execute(
            select(Message.conversation_id, Message.id, Message.parent_id)
            .where(Message.conversation_id.in_(list(leaves)))
            .order_by(Message.id.desc())
            .limit(MAX_TREE_ROWS * len(leaves))
        )
        parents: dict[int, dict[int, int | None]] = {}
        newest: dict[int, int] = {}
        for conversation_id, message_id, parent_id in result.all():
            parents.setdefault(conversation_id, {})[message_id] = parent_id
            # Explicit max: the rows arrive newest-first, so "the last one iterated" would
            # be the oldest.
            newest[conversation_id] = max(newest.get(conversation_id, 0), message_id)
        lengths: dict[int, int] = {}
        for conversation_id, rows in parents.items():
            leaf = leaves.get(conversation_id)
            if leaf not in rows:
                leaf = newest[conversation_id]
            lengths[conversation_id] = len(_walk_up(rows, leaf))
        return lengths

    async def parent_uuid_of(self, message: Message) -> str | None:
        """Return the public uuid of a message's parent, if it has one.

        Args:
            message: A message already resolved through a scoped accessor.

        Returns:
            The parent's uuid, or None when the message is a root of the tree.
        """
        if message.parent_id is None:
            return None
        result = await self.db.execute(
            select(Message.uuid).where(
                Message.id == message.parent_id,
                Message.conversation_id == message.conversation_id,
            )
        )
        return result.scalar_one_or_none()

    async def version_of(self, message: Message) -> tuple[int, int]:
        """Return a message's 1-based index among its versions and how many there are.

        Args:
            message: A message already resolved through a scoped accessor.

        Returns:
            ``(version index, version count)``.
        """
        result = await self.db.execute(
            select(Message.id)
            .where(
                Message.conversation_id == message.conversation_id,
                Message.parent_id.is_(None)
                if message.parent_id is None
                else Message.parent_id == message.parent_id,
            )
            .order_by(Message.id.asc())
        )
        ids = [row[0] for row in result.all()]
        return (ids.index(message.id) + 1 if message.id in ids else 1), len(ids) or 1

    async def sibling_at(self, message: Message, index: int) -> Message | None:
        """Return the version of ``message`` sitting in the 1-based slot ``index``.

        Args:
            message: A message already resolved through a scoped accessor; its sibling
                group is the one searched.
            index: The 1-based slot to select.

        Returns:
            The sibling occupying that slot, or None when the group is shorter — an index
            past the end is a stale client, not an error worth distinguishing from an
            unknown message.
        """
        result = await self.db.execute(
            select(Message)
            .where(
                Message.conversation_id == message.conversation_id,
                Message.parent_id.is_(None)
                if message.parent_id is None
                else Message.parent_id == message.parent_id,
            )
            .order_by(Message.id.asc())
        )
        rows = list(result.scalars().all())
        return rows[index - 1] if 1 <= index <= len(rows) else None

    async def deepest_leaf(self, conversation_id: int, message_id: int) -> int:
        """Follow the newest child from a message down to the end of that branch.

        Selecting an old version means "continue from here": the branch it belongs to is
        whatever was written under it most recently.

        Args:
            conversation_id: Internal id of the owning conversation.
            message_id: Where to start, already resolved inside this conversation.

        Returns:
            The id of the deepest descendant reachable by always taking the newest child,
            or ``message_id`` itself when it has none.
        """
        rows = await self.list_for_conversation(conversation_id)
        newest_child: dict[int, int] = {}
        for row in rows:  # id-ascending, so the last write per parent wins
            if row.parent_id is not None:
                newest_child[row.parent_id] = row.id
        current = message_id
        seen = {current}
        while (child := newest_child.get(current)) is not None and child not in seen:
            seen.add(child)
            current = child
        return current

    async def question_parent(self, conversation_id: int, leaf_id: int | None) -> int | None:
        """Return the parent a NEW question should get when it lands on ``leaf_id``.

        A question never parents another question. A leaf that is itself a user message is
        an attempt nobody answered — the turn died before the assistant row was written, or
        the version switcher parked the branch on one — so the next question belongs
        *beside* it as another version of the same slot, not under it. Chaining
        user-under-user would hand the provider two consecutive user turns and put a
        question and an answer in the same sibling group.

        Args:
            conversation_id: Internal id of the owning conversation.
            leaf_id: The branch end the new question lands on, or None for an empty tree.

        Returns:
            The id to store as ``parent_id``, or None when the question is a new root.
        """
        if leaf_id is None:
            return None
        result = await self.db.execute(
            select(Message.role, Message.parent_id).where(
                Message.id == leaf_id, Message.conversation_id == conversation_id
            )
        )
        row = result.first()
        if row is None:
            return None
        role, parent_id = row
        return parent_id if role is MessageRole.USER else leaf_id

    async def recent_history(
        self,
        conversation_id: int,
        leaf_id: int | None = None,
        limit: int = HISTORY_WINDOW,
    ) -> list[Message]:
        """Return the tail of one branch, oldest first.

        This walks the ACTIVE BRANCH rather than the newest rows of the conversation: after
        a regenerate or an edit those are two different things, and taking the newest rows
        would feed the model abandoned versions of the very question it is answering.

        Args:
            conversation_id: Internal id of the owning conversation.
            leaf_id: Branch to walk up from; None uses the conversation's newest message.
            limit: Size of the trailing window.

        Returns:
            The last ``limit`` messages of that branch, oldest first.
        """
        rows = await self.list_for_conversation(conversation_id)
        by_id = {row.id: row for row in rows}
        leaf = leaf_id if leaf_id in by_id else (rows[-1].id if rows else None)
        chain = _walk_up({row.id: row.parent_id for row in rows}, leaf)
        return [by_id[message_id] for message_id in chain[-limit:]]

    async def all_usage(self) -> list[str]:
        """Every stored usage blob in the database, for the admin cost report.

        The one deliberately unscoped accessor in this class. It is reachable only from the
        superuser-gated ``/admin/usage`` route, and it reads nothing but token counts — no
        message text, no conversation, no owner. Keep it that way: widening the SELECT here
        would turn one aggregate into a way to read everyone's chats.

        Returns:
            The non-NULL ``usage_json`` values, unparsed.
        """
        # ponytail: every blob is pulled into Python and json.loads'd — ~70 bytes and one
        # parse each, fine to roughly 50k answered turns. Past that, aggregate in SQL with
        # GROUP BY json_extract(usage_json, '$.model'); note that json_extract raises
        # "malformed JSON" on a corrupt row and would 500 the whole report, so the tolerant
        # read in app/shared/usage.py has to move into that query with it.
        result = await self.db.execute(
            select(Message.usage_json).where(Message.usage_json.is_not(None))
        )
        return [raw for raw in result.scalars().all() if raw]
