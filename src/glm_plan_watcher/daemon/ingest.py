"""WatchEvent ingestion and WebSocket broadcasting."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterable, Iterable
from contextlib import asynccontextmanager
from typing import Any

from pydantic import ValidationError

from glm_plan_watcher.db import Repository
from glm_plan_watcher.models import WatchEvent


class EventBroadcaster:
    """In-process fan-out for daemon WebSocket clients."""

    def __init__(self) -> None:
        self._subscribers: set[tuple[asyncio.Queue[dict[str, Any]], int | None]] = set()

    @asynccontextmanager
    async def subscribe(self, account_id: int | None = None) -> Any:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        subscription = (queue, account_id)
        self._subscribers.add(subscription)
        try:
            yield queue
        finally:
            self._subscribers.discard(subscription)

    async def broadcast(self, account_id: int, payload: dict[str, Any]) -> None:
        message = {"account_id": account_id, **payload}
        for queue, filter_account_id in list(self._subscribers):
            if filter_account_id is None or filter_account_id == account_id:
                await queue.put(message)


def parse_watch_event_line(line: str) -> tuple[WatchEvent, str] | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        data = json.loads(stripped)
        return WatchEvent.model_validate(data), stripped
    except (json.JSONDecodeError, ValidationError):
        return None


async def persist_watch_event(
    repository: Repository,
    broadcaster: EventBroadcaster,
    account_id: int,
    event: WatchEvent,
    raw_json: str | None = None,
) -> int:
    event_id = repository.insert_event(account_id, event, raw_json)
    if event.type == "heartbeat":
        repository.update_worker_heartbeat(account_id, event.ts.isoformat())
    await broadcaster.broadcast(
        account_id,
        {
            "event_id": event_id,
            "event": event.model_dump(mode="json"),
        },
    )
    return event_id


async def ingest_lines(
    lines: Iterable[str] | AsyncIterable[str],
    repository: Repository,
    broadcaster: EventBroadcaster,
    account_id: int,
) -> int:
    count = 0
    async for line in _aiter_lines(lines):
        parsed = parse_watch_event_line(line)
        if parsed is None:
            continue
        event, raw_json = parsed
        await persist_watch_event(repository, broadcaster, account_id, event, raw_json)
        count += 1
    return count


async def ingest_stream(
    stream: asyncio.StreamReader,
    repository: Repository,
    broadcaster: EventBroadcaster,
    account_id: int,
) -> int:
    count = 0
    while True:
        line = await stream.readline()
        if not line:
            break
        parsed = parse_watch_event_line(line.decode("utf-8", errors="replace"))
        if parsed is None:
            continue
        event, raw_json = parsed
        await persist_watch_event(repository, broadcaster, account_id, event, raw_json)
        count += 1
    return count


async def _aiter_lines(lines: Iterable[str] | AsyncIterable[str]) -> AsyncIterable[str]:
    if hasattr(lines, "__aiter__"):
        async for line in lines:  # type: ignore[union-attr]
            yield line
        return
    for line in lines:  # type: ignore[union-attr]
        yield line
