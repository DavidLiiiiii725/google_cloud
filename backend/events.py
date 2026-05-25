"""In-process pub/sub for streaming agent log entries to SSE clients."""
from __future__ import annotations
import asyncio
import time
from collections import deque
from typing import AsyncIterator
from . import db

_subscribers: list[asyncio.Queue] = []
_recent: deque = deque(maxlen=200)


def emit(tag: str, message: str, **extra):
    """Emit an agent log entry. Tag is one of DETECT, REASON, HITL, ACT, AUTO, INFO."""
    entry = {"ts": time.time(), "tag": tag, "message": message, **extra}
    _recent.append(entry)
    try:
        db.agent_logs().insert_one(dict(entry))
    except Exception as e:
        print(f"[events] log insert failed: {e}")
    for q in list(_subscribers):
        try:
            q.put_nowait(entry)
        except asyncio.QueueFull:
            pass
    print(f"[{tag}] {message}")


def recent(limit: int = 50) -> list[dict]:
    return list(_recent)[-limit:]


async def subscribe() -> AsyncIterator[dict]:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.append(q)
    try:
        # replay recent entries on connect so the terminal isn't blank
        for entry in list(_recent)[-20:]:
            await q.put(entry)
        while True:
            entry = await q.get()
            yield entry
    finally:
        if q in _subscribers:
            _subscribers.remove(q)
