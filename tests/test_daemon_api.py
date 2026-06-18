from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from glm_plan_watcher.daemon.app import create_app
from glm_plan_watcher.daemon.ingest import EventBroadcaster
from glm_plan_watcher.daemon.security import DaemonHandshake, write_handshake
from glm_plan_watcher.db import Repository
from glm_plan_watcher.models import WatchEvent

TOKEN = "test-token"
AUTH_HEADERS = {"Authorization": f"Bearer {TOKEN}"}


class FakeSupervisor:
    def __init__(self) -> None:
        self.started: list[int] = []
        self.stopped: list[int] = []
        self.discarded: list[int] = []
        self.logins: list[tuple[int, bool]] = []
        self.handoffs: list[tuple[int, int | None, bool, bool]] = []

    async def start_worker(self, account_id: int) -> dict[str, object]:
        self.started.append(account_id)
        return {"account_id": account_id, "status": "running", "pid": 123}

    async def stop_worker(self, account_id: int) -> dict[str, object]:
        self.stopped.append(account_id)
        return {"account_id": account_id, "status": "stopped", "pid": None}

    async def discard_account(self, account_id: int) -> None:
        self.discarded.append(account_id)

    def worker_status(self, account_id: int) -> dict[str, object]:
        return {"account_id": account_id, "status": "stopped", "pid": None}

    def list_workers(self) -> list[dict[str, object]]:
        return []

    async def start_login_session(
        self,
        account_id: int,
        restore_worker: bool = False,
    ) -> dict[str, object]:
        self.logins.append((account_id, restore_worker))
        return {"account_id": account_id, "status": "login", "pid": 456}

    async def start_handoff_session(
        self,
        account_id: int,
        target_id: int | None = None,
        click_entry: bool = False,
        restore_worker: bool = False,
    ) -> dict[str, object]:
        self.handoffs.append((account_id, target_id, click_entry, restore_worker))
        return {
            "account_id": account_id,
            "status": "handoff",
            "pid": 789,
            "target_id": target_id,
            "click_entry": click_entry,
        }


def test_daemon_accounts_targets_events_and_workers(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")
    supervisor = FakeSupervisor()
    client = TestClient(
        create_app(  # type: ignore[arg-type]
            repository=repo,
            broadcaster=EventBroadcaster(),
            supervisor=supervisor,
            token=TOKEN,
        )
    )

    account = client.post(
        "/accounts",
        headers=AUTH_HEADERS,
        json={"display_name": "main", "user_data_dir": str(tmp_path / "profile")},
    ).json()
    target = client.post(
        f"/accounts/{account['id']}/targets",
        headers=AUTH_HEADERS,
        json={
            "billing_cycle": "monthly",
            "tier": "Pro",
            "interval": 60,
            "jitter": 20,
            "active_window_start": "10:00",
            "active_window_end": "10:30",
            "active_timezone": "Asia/Shanghai",
            "active_interval_seconds": 3,
            "active_jitter_seconds": 1,
            "idle_interval_seconds": 600,
            "on_hit_handoff": True,
        },
    ).json()
    event = WatchEvent(
        check_index=1,
        target="连续包月 / Pro",
        button_state="sold_out",
        message="sold out",
    )
    repo.insert_event(account["id"], event)

    assert client.get("/accounts", headers=AUTH_HEADERS).json()[0]["display_name"] == "main"
    assert (
        client.get(f"/accounts/{account['id']}/targets", headers=AUTH_HEADERS).json()[0]["id"]
        == target["id"]
    )
    assert client.get(f"/targets/{target['id']}", headers=AUTH_HEADERS).json()["tier"] == "Pro"
    assert (
        client.get(f"/targets/{target['id']}", headers=AUTH_HEADERS).json()[
            "active_window_start"
        ]
        == "10:00"
    )
    assert (
        client.patch(
            f"/targets/{target['id']}",
            headers=AUTH_HEADERS,
            json={"enabled": False, "active_interval_seconds": 4},
        ).json()["enabled"]
        is False
    )
    assert (
        client.get(f"/targets/{target['id']}", headers=AUTH_HEADERS).json()[
            "active_interval_seconds"
        ]
        == 4
    )
    assert (
        client.get(
            "/events",
            headers=AUTH_HEADERS,
            params={"account_id": account["id"]},
        ).json()[0]["message"]
        == "sold out"
    )
    assert (
        client.post(f"/accounts/{account['id']}/worker/start", headers=AUTH_HEADERS).json()[
            "status"
        ]
        == "running"
    )
    assert (
        client.post(f"/accounts/{account['id']}/worker/stop", headers=AUTH_HEADERS).json()["status"]
        == "stopped"
    )
    assert (
        client.post(f"/accounts/{account['id']}/login", headers=AUTH_HEADERS, json={}).json()[
            "status"
        ]
        == "login"
    )
    assert (
        client.post(
            f"/accounts/{account['id']}/handoff",
            headers=AUTH_HEADERS,
            json={"target_id": target["id"], "click_entry": False},
        ).json()["status"]
        == "handoff"
    )
    assert supervisor.started == [account["id"]]
    assert supervisor.stopped == [account["id"]]
    assert supervisor.logins == [(account["id"], False)]
    assert supervisor.handoffs == [(account["id"], target["id"], False, False)]


def test_daemon_auth_and_health_exemption(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")
    client = TestClient(create_app(repository=repo, token=TOKEN))

    assert client.get("/health").status_code == 200
    assert client.get("/accounts").status_code == 401
    assert client.get("/accounts", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/accounts", headers=AUTH_HEADERS).status_code == 200


def test_target_visible_in_window_field_create_default_and_patch(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")
    client = TestClient(create_app(repository=repo, token=TOKEN))

    account = client.post(
        "/accounts", headers=AUTH_HEADERS, json={"display_name": "main"}
    ).json()

    # 未传 visible_in_window → 默认 False，且出现在创建响应里。
    created = client.post(
        f"/accounts/{account['id']}/targets",
        headers=AUTH_HEADERS,
        json={"billing_cycle": "monthly", "tier": "Pro"},
    ).json()
    assert created["visible_in_window"] is False

    # 创建时显式置 True。
    created_true = client.post(
        f"/accounts/{account['id']}/targets",
        headers=AUTH_HEADERS,
        json={"billing_cycle": "yearly", "tier": "Max", "visible_in_window": True},
    ).json()
    assert created_true["visible_in_window"] is True

    # PATCH 切换并出现在 GET /targets 响应里。
    patched = client.patch(
        f"/targets/{created['id']}",
        headers=AUTH_HEADERS,
        json={"visible_in_window": True},
    ).json()
    assert patched["visible_in_window"] is True
    assert (
        client.get(f"/targets/{created['id']}", headers=AUTH_HEADERS).json()["visible_in_window"]
        is True
    )
    listed = client.get(f"/accounts/{account['id']}/targets", headers=AUTH_HEADERS).json()
    assert all("visible_in_window" in row for row in listed)


def test_create_account_without_path_auto_manages_profile(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")
    client = TestClient(create_app(repository=repo, token=TOKEN))

    # 只填名字（不传 user_data_dir）→ 201 且自动分配 profiles/ 下目录。
    response = client.post("/accounts", headers=AUTH_HEADERS, json={"display_name": "auto"})
    assert response.status_code == 201
    profile = Path(response.json()["user_data_dir"])
    assert profile.parent == tmp_path / "profiles"
    assert profile.is_dir()

    # 传空白字符串也走自动分配。
    blank = client.post(
        "/accounts", headers=AUTH_HEADERS, json={"display_name": "blank", "user_data_dir": "   "}
    )
    assert blank.status_code == 201
    assert Path(blank.json()["user_data_dir"]).parent == tmp_path / "profiles"


def test_delete_account_api_discards_and_removes_account(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")
    supervisor = FakeSupervisor()
    client = TestClient(
        create_app(  # type: ignore[arg-type]
            repository=repo,
            broadcaster=EventBroadcaster(),
            supervisor=supervisor,
            token=TOKEN,
        )
    )
    account = client.post("/accounts", headers=AUTH_HEADERS, json={"display_name": "delete"}).json()

    response = client.delete(f"/accounts/{account['id']}", headers=AUTH_HEADERS)

    assert response.status_code == 204
    assert supervisor.discarded == [account["id"]]
    assert client.get("/accounts", headers=AUTH_HEADERS).json() == []


def test_delete_missing_account_is_idempotent(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")
    supervisor = FakeSupervisor()
    client = TestClient(
        create_app(  # type: ignore[arg-type]
            repository=repo,
            broadcaster=EventBroadcaster(),
            supervisor=supervisor,
            token=TOKEN,
        )
    )

    response = client.delete("/accounts/999", headers=AUTH_HEADERS)

    assert response.status_code == 204
    assert supervisor.discarded == []


def test_delete_account_purges_only_managed_profiles(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")
    client = TestClient(create_app(repository=repo, token=TOKEN))
    managed = client.post("/accounts", headers=AUTH_HEADERS, json={"display_name": "managed"}).json()
    managed_profile = Path(managed["user_data_dir"])
    (managed_profile / "marker").write_text("session", encoding="utf-8")

    response = client.delete(
        f"/accounts/{managed['id']}",
        headers=AUTH_HEADERS,
        params={"purge_profile": True},
    )

    assert response.status_code == 204
    assert not managed_profile.exists()

    external_profile = tmp_path / "external-profile"
    external_profile.mkdir()
    external = client.post(
        "/accounts",
        headers=AUTH_HEADERS,
        json={"display_name": "external", "user_data_dir": str(external_profile)},
    ).json()

    response = client.delete(
        f"/accounts/{external['id']}",
        headers=AUTH_HEADERS,
        params={"purge_profile": True},
    )

    assert response.status_code == 204
    assert external_profile.is_dir()


def test_daemon_cors_headers_for_tauri_origin(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")
    client = TestClient(create_app(repository=repo, token=TOKEN))

    response = client.get(
        "/accounts",
        headers={
            **AUTH_HEADERS,
            "Origin": "tauri://localhost",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_daemon_cors_preflight_is_not_blocked_by_auth(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")
    client = TestClient(create_app(repository=repo, token=TOKEN))

    response = client.options(
        "/accounts",
        headers={
            "Origin": "tauri://localhost",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_daemon_auto_generates_token_when_missing(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")
    app = create_app(repository=repo)

    assert isinstance(app.state.token, str)
    assert len(app.state.token) > 20


def test_daemon_websocket_requires_token(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")
    client = TestClient(create_app(repository=repo, token=TOKEN))

    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws/events"):
        pass

    with client.websocket_connect(f"/ws/events?token={TOKEN}"):
        pass


def test_write_handshake_file(tmp_path: Path) -> None:
    path = tmp_path / "daemon.handshake.json"

    write_handshake(path, DaemonHandshake(host="127.0.0.1", port=12345, token=TOKEN))

    assert path.read_text(encoding="utf-8").strip().startswith("{")
    assert TOKEN in path.read_text(encoding="utf-8")
