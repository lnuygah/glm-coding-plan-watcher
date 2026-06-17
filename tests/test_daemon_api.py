from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from glm_plan_watcher.daemon.app import create_app
from glm_plan_watcher.daemon.ingest import EventBroadcaster
from glm_plan_watcher.db import Repository
from glm_plan_watcher.models import WatchEvent


class FakeSupervisor:
    def __init__(self) -> None:
        self.started: list[int] = []
        self.stopped: list[int] = []

    async def start_worker(self, account_id: int) -> dict[str, object]:
        self.started.append(account_id)
        return {"account_id": account_id, "status": "running", "pid": 123}

    async def stop_worker(self, account_id: int) -> dict[str, object]:
        self.stopped.append(account_id)
        return {"account_id": account_id, "status": "stopped", "pid": None}

    def worker_status(self, account_id: int) -> dict[str, object]:
        return {"account_id": account_id, "status": "stopped", "pid": None}

    def list_workers(self) -> list[dict[str, object]]:
        return []


def test_daemon_accounts_targets_events_and_workers(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")
    supervisor = FakeSupervisor()
    client = TestClient(
        create_app(repository=repo, broadcaster=EventBroadcaster(), supervisor=supervisor)  # type: ignore[arg-type]
    )

    account = client.post(
        "/accounts",
        json={"display_name": "main", "user_data_dir": str(tmp_path / "profile")},
    ).json()
    target = client.post(
        f"/accounts/{account['id']}/targets",
        json={"billing_cycle": "monthly", "tier": "Pro", "interval": 60, "jitter": 20},
    ).json()
    event = WatchEvent(
        check_index=1,
        target="连续包月 / Pro",
        button_state="sold_out",
        message="sold out",
    )
    repo.insert_event(account["id"], event)

    assert client.get("/accounts").json()[0]["display_name"] == "main"
    assert client.get(f"/accounts/{account['id']}/targets").json()[0]["id"] == target["id"]
    assert client.get(f"/targets/{target['id']}").json()["tier"] == "Pro"
    assert client.patch(f"/targets/{target['id']}", json={"enabled": False}).json()["enabled"] is False
    assert client.get("/events", params={"account_id": account["id"]}).json()[0]["message"] == "sold out"
    assert client.post(f"/accounts/{account['id']}/worker/start").json()["status"] == "running"
    assert client.post(f"/accounts/{account['id']}/worker/stop").json()["status"] == "stopped"
    assert supervisor.started == [account["id"]]
    assert supervisor.stopped == [account["id"]]
