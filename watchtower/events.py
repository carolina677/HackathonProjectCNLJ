"""In-process event bus. Every agent decision becomes an event; the dashboard streams them over SSE."""

import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import AsyncIterator

_subscribers: set[asyncio.Queue] = set()
_history: deque[dict] = deque(maxlen=2000)


def emit(portal_id: str | None, kind: str, data: dict) -> dict:
    ev = {"ts": datetime.now(timezone.utc).isoformat(), "portal_id": portal_id, "kind": kind, "data": data}
    _history.append(ev)
    for q in list(_subscribers):
        try:
            q.put_nowait(ev)
        except asyncio.QueueFull:
            pass
    return ev


def history(portal_id: str | None = None, kinds: set[str] | None = None, limit: int = 300) -> list[dict]:
    out = [e for e in _history if (portal_id is None or e["portal_id"] == portal_id)
           and (kinds is None or e["kind"] in kinds)]
    return out[-limit:]


async def subscribe() -> AsyncIterator[dict]:
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    _subscribers.add(q)
    try:
        while True:
            yield await q.get()
    finally:
        _subscribers.discard(q)
