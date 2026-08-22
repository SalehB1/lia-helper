"""Server-sent event framing plus a heartbeat wrapper for long-lived streams."""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from typing import AsyncIterator

from app.shared.constants import SSE_PING_SECONDS

PING_FRAME = ": ping\n\n"


@dataclass(slots=True)
class SSEEvent:
    """One server-sent event.

    Attributes:
        event: Event name — ``meta``, ``tool``, ``token``, ``sources``, ``suggestions``,
            ``done`` or ``error``.
        data: JSON-serializable payload.
    """

    event: str
    data: dict

    def encode(self) -> str:
        """Render the event as an SSE wire frame.

        Returns:
            ``"event: <name>\\ndata: <json>\\n\\n"`` with Persian text left unescaped.
        """
        return f"event: {self.event}\ndata: {json.dumps(self.data, ensure_ascii=False)}\n\n"


async def with_heartbeat(
    gen: AsyncIterator[SSEEvent],
    interval: float = SSE_PING_SECONDS,
) -> AsyncIterator[str]:
    """Yield encoded SSE frames, injecting a comment ping while the source is idle.

    A ping is emitted whenever ``interval`` seconds pass without an event, which keeps
    proxies from closing the connection. The pending ``__anext__`` task is shielded from
    the timeout and awaited again on the next pass, so no source event is ever dropped.

    Args:
        gen: Source of events; closed before this iterator returns.
        interval: Seconds of silence that trigger a ping.

    Yields:
        Encoded event frames and ``": ping\\n\\n"`` comment frames.
    """
    task: asyncio.Task[SSEEvent] | None = None
    try:
        while True:
            if task is None:
                task = asyncio.ensure_future(gen.__anext__())
            try:
                event = await asyncio.wait_for(asyncio.shield(task), interval)
            except asyncio.TimeoutError:
                yield PING_FRAME
                continue
            except StopAsyncIteration:
                task = None
                break
            task = None
            yield event.encode()
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        aclose = getattr(gen, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(Exception):
                await aclose()
