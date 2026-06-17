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
        active_window_start="10:00",
        active_window_end="10:30",
        active_timezone="Asia/Shanghai",
        active_interval_seconds=3,
        active_jitter_seconds=1,
        idle_interval_seconds=600,
        on_hit_handoff=True,
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
    assert updated["active_window_start"] == "10:00"
    assert updated["on_hit_handoff"] is True

    worker = repo.upsert_worker(account["id"], pid=123, status="running", started_at="now")
    assert worker["pid"] == 123
    repo.update_worker_heartbeat(account["id"], "later")
    assert repo.get_worker(account["id"])["pid"] == 123
    assert repo.get_worker(account["id"])["last_heartbeat_at"] == "later"


def test_create_account_auto_manages_profile_dir(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")

    # 留空 user_data_dir → 自动分配 profiles/ 下的唯一目录并创建。
    account = repo.create_account("auto")
    profile = Path(account["user_data_dir"])
    assert profile.parent == tmp_path / "profiles"
    assert profile.is_dir()

    # 两个账号各自分到不同的 profile 目录（UNIQUE）。
    other = repo.create_account("auto2")
    assert other["user_data_dir"] != account["user_data_dir"]

    # 显式传入则尊重用户提供的路径（导入已有 profile），并去除首尾空白。
    imported = repo.create_account("imported", f"  {tmp_path / 'user_data' / 'default'}  ")
    assert imported["user_data_dir"] == str(tmp_path / "user_data" / "default")


def test_memory_repository_auto_profile_not_in_cwd() -> None:
    import tempfile

    repo = Repository(":memory:")
    account = repo.create_account("mem")
    profile = Path(account["user_data_dir"])

    assert profile.is_dir()
    # :memory: 的 profiles 目录在临时目录，不污染当前工作目录。
    assert profile.parent == repo.profiles_dir
    assert str(repo.profiles_dir).startswith(tempfile.gettempdir())
    assert repo.profiles_dir != Path.cwd() / "profiles"
