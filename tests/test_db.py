from __future__ import annotations

from pathlib import Path

from glm_plan_watcher.db import Repository
from glm_plan_watcher.models import WatchEvent


def test_repository_account_target_event_crud(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")

    account = repo.create_account("main", str(tmp_path / "profile"))
    target = repo.create_target(
        account["id"],
        billing_cycle="monthly",
        tier="Pro",
        interval=45,
        jitter=12,
    )
    event = WatchEvent(
        check_index=1,
        target="连续包月 / Pro",
        button_state="sold_out",
        button_text="暂时售罄",
        action="wait",
        available=False,
        message="test",
    )
    event_id = repo.insert_event(account["id"], event)
    artifact = repo.create_artifact(event_id, screenshot_path="screenshots/hit.png")

    assert repo.get_account(account["id"])["display_name"] == "main"
    assert repo.list_targets(account_id=account["id"]) == [target]
    assert repo.list_events(account_id=account["id"])[0]["button_state"] == "sold_out"
    assert artifact["event_id"] == event_id
    assert artifact["screenshot_path"] == "screenshots/hit.png"

    updated = repo.update_target(target["id"], enabled=False, dry_run=True)
    assert updated["enabled"] is False
    assert updated["dry_run"] is True

    worker = repo.upsert_worker(account["id"], pid=123, status="running", started_at="now")
    assert worker["pid"] == 123
    repo.update_worker_heartbeat(account["id"], "later")
    assert repo.get_worker(account["id"])["pid"] == 123
    assert repo.get_worker(account["id"])["last_heartbeat_at"] == "later"
