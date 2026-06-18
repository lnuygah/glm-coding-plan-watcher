from __future__ import annotations

from pathlib import Path

import pytest

from glm_plan_watcher.daemon.ingest import EventBroadcaster, ingest_lines
from glm_plan_watcher.db import Repository
from glm_plan_watcher.models import WatchEvent


@pytest.mark.asyncio
async def test_ingest_lines_persists_events_and_broadcasts(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")
    account = repo.create_account("main", str(tmp_path / "profile"))
    broadcaster = EventBroadcaster()
    event = WatchEvent(
        type="heartbeat",
        check_index=2,
        target="account",
        button_state="heartbeat",
        action="wait",
        available=False,
        message="alive",
    )

    async with broadcaster.subscribe(account_id=account["id"]) as queue:
        count = await ingest_lines(
            ["not json", event.to_json_line()],
            repo,
            broadcaster,
            account["id"],
        )
        message = await queue.get()

    assert count == 1
    assert repo.list_events(account_id=account["id"])[0]["type"] == "heartbeat"
    assert repo.get_worker(account["id"])["last_heartbeat_at"] == event.ts.isoformat()
    assert message["account_id"] == account["id"]
    assert message["event"]["type"] == "heartbeat"


@pytest.mark.asyncio
async def test_ingest_lines_invokes_event_callback(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")
    account = repo.create_account("main", str(tmp_path / "profile"))
    broadcaster = EventBroadcaster()
    event = WatchEvent(
        type="hit",
        check_index=1,
        target="连续包月 / Pro",
        button_state="available",
        available=True,
    )
    seen: list[tuple[int, str, int]] = []

    async def on_event(account_id: int, watch_event: WatchEvent, event_id: int) -> None:
        seen.append((account_id, watch_event.type, event_id))

    count = await ingest_lines(
        [event.to_json_line()],
        repo,
        broadcaster,
        account["id"],
        on_event=on_event,
    )

    assert count == 1
    assert seen == [(account["id"], "hit", 1)]
