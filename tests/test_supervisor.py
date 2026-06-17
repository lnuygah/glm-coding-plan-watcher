from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml

from glm_plan_watcher.daemon.ingest import EventBroadcaster
from glm_plan_watcher.daemon.supervisor import (
    ProfileInUseError,
    WorkerAlreadyRunningError,
    WorkerSupervisor,
)
from glm_plan_watcher.db import Repository
from glm_plan_watcher.models import WatchEvent


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.stdout = None
        self.command: Sequence[str] | None = None
        self._waiter: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    def terminate(self) -> None:
        self.finish(0)

    def kill(self) -> None:
        self.finish(-9)

    async def wait(self) -> int:
        return await self._waiter

    def finish(self, returncode: int) -> None:
        if not self._waiter.done():
            self.returncode = returncode
            self._waiter.set_result(returncode)


class FakeProcessFactory:
    def __init__(self) -> None:
        self.processes: list[FakeProcess] = []
        self.commands: list[Sequence[str]] = []

    async def __call__(self, command: Sequence[str]) -> FakeProcess:
        process = FakeProcess(pid=1000 + len(self.processes))
        process.command = command
        self.processes.append(process)
        self.commands.append(command)
        return process


async def _wait_for_spawn(supervisor: WorkerSupervisor) -> None:
    """start_worker 现在把进程 spawn 放到后台任务里；测试需等这些任务跑完再断言。"""

    for _ in range(10):
        tasks = [task for task in supervisor._spawn_tasks if not task.done()]  # noqa: SLF001
        if not tasks:
            return
        await asyncio.gather(*tasks)
    return


def seed_account(repo: Repository, tmp_path: Path, interval: float = 1, jitter: float = 0) -> int:
    account = repo.create_account("main", str(tmp_path / "profile"))
    repo.create_target(
        account["id"],
        billing_cycle="monthly",
        tier="Pro",
        interval=interval,
        jitter=jitter,
    )
    return int(account["id"])


@pytest.mark.asyncio
async def test_supervisor_rejects_same_account_concurrency(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")
    account_id = seed_account(repo, tmp_path)
    factory = FakeProcessFactory()
    supervisor = WorkerSupervisor(repo, EventBroadcaster(), process_factory=factory)

    await supervisor.start_worker(account_id)
    with pytest.raises(WorkerAlreadyRunningError):
        await supervisor.start_worker(account_id)

    await supervisor.stop_worker(account_id)


def test_supervisor_materializes_clamped_worker_config(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")
    account_id = seed_account(repo, tmp_path, interval=1, jitter=0)
    supervisor = WorkerSupervisor(repo, EventBroadcaster(), runtime_dir=tmp_path / "runtime")

    config_path = supervisor.materialize_config(account_id)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert data["headless"] is True
    assert data["refresh_interval_seconds"] == 30.0
    assert data["refresh_jitter_seconds"] == 5.0
    assert data["auto_click_entry"] is False
    assert data["dry_run"] is False
    assert data["targets"][0]["billing_cycle"] == "monthly"
    assert data["targets"][0]["tier"] == "Pro"
    assert data["targets"][0]["active_interval_seconds"] == 3.0
    assert data["targets"][0]["dry_run"] is False


@pytest.mark.asyncio
async def test_supervisor_restarts_crashed_worker_with_backoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")
    account_id = seed_account(repo, tmp_path)
    factory = FakeProcessFactory()
    supervisor = WorkerSupervisor(repo, EventBroadcaster(), process_factory=factory)
    monkeypatch.setattr(supervisor, "compute_backoff", lambda _count: 0)

    await supervisor.start_worker(account_id)
    await _wait_for_spawn(supervisor)
    factory.processes[0].finish(1)
    # 让 _monitor_process 跑完 backoff 并触发重启的后台 spawn。
    for _ in range(5):
        await asyncio.sleep(0)
    await _wait_for_spawn(supervisor)

    assert len(factory.processes) == 2
    assert repo.get_worker(account_id)["status"] == "running"

    await supervisor.stop_worker(account_id)


def test_supervisor_compute_backoff_caps(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")
    supervisor = WorkerSupervisor(repo, EventBroadcaster())

    assert supervisor.compute_backoff(0) == 1.0
    assert supervisor.compute_backoff(10) == 60.0


@pytest.mark.asyncio
async def test_login_stops_headless_worker_before_headful_session(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")
    account_id = seed_account(repo, tmp_path)
    headless_factory = FakeProcessFactory()
    headful_factory = FakeProcessFactory()
    broadcaster = EventBroadcaster()
    supervisor = WorkerSupervisor(
        repo,
        broadcaster,
        process_factory=headless_factory,
        headful_launcher=headful_factory,
    )

    await supervisor.start_worker(account_id)
    await _wait_for_spawn(supervisor)
    async with broadcaster.subscribe(account_id=account_id) as queue:
        result = await supervisor.start_login_session(account_id)
        message = await queue.get()

    assert headless_factory.processes[0].returncode == 0
    assert result["status"] == "login"
    assert headful_factory.commands[0][1:4] == ["-m", "glm_plan_watcher.headful", "login"]
    assert message["event"]["type"] == "login"
    assert repo.list_events(account_id=account_id)[0]["type"] == "login"

    headful_factory.processes[0].finish(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_handoff_stops_worker_and_does_not_click_by_default(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")
    account_id = seed_account(repo, tmp_path)
    headless_factory = FakeProcessFactory()
    headful_factory = FakeProcessFactory()
    supervisor = WorkerSupervisor(
        repo,
        EventBroadcaster(),
        process_factory=headless_factory,
        headful_launcher=headful_factory,
    )

    await supervisor.start_worker(account_id)
    await _wait_for_spawn(supervisor)
    result = await supervisor.start_handoff_session(account_id)

    assert headless_factory.processes[0].returncode == 0
    assert result["status"] == "handoff"
    assert result["click_entry"] is False
    assert "--click-entry" not in headful_factory.commands[0]
    assert repo.list_events(account_id=account_id)[0]["type"] == "handoff"

    headful_factory.processes[0].finish(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_handoff_can_request_entry_click_without_payment_automation(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")
    account_id = seed_account(repo, tmp_path)
    headful_factory = FakeProcessFactory()
    supervisor = WorkerSupervisor(repo, EventBroadcaster(), headful_launcher=headful_factory)

    await supervisor.start_handoff_session(account_id, click_entry=True)

    assert "--click-entry" in headful_factory.commands[0]

    headful_factory.processes[0].finish(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_auto_handoff_stops_headless_worker_after_available_hit(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")
    account_id = seed_account(repo, tmp_path)
    headless_factory = FakeProcessFactory()
    headful_factory = FakeProcessFactory()
    supervisor = WorkerSupervisor(
        repo,
        EventBroadcaster(),
        process_factory=headless_factory,
        headful_launcher=headful_factory,
    )

    await supervisor.start_worker(account_id)
    await _wait_for_spawn(supervisor)
    await supervisor._handle_worker_event(  # noqa: SLF001 - targeted supervisor behavior test
        account_id,
        WatchEvent(
            type="hit",
            check_index=1,
            target="连续包月 / Pro",
            button_state="available",
            available=True,
        ),
        1,
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert headless_factory.processes[0].returncode == 0
    assert len(headful_factory.commands) == 1
    assert "--click-entry" in headful_factory.commands[0]

    headful_factory.processes[0].finish(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_auto_handoff_skips_dry_run_target(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")
    account = repo.create_account("main", str(tmp_path / "profile"))
    repo.create_target(
        account["id"],
        billing_cycle="monthly",
        tier="Pro",
        dry_run=True,
    )
    account_id = int(account["id"])
    headless_factory = FakeProcessFactory()
    headful_factory = FakeProcessFactory()
    supervisor = WorkerSupervisor(
        repo,
        EventBroadcaster(),
        process_factory=headless_factory,
        headful_launcher=headful_factory,
    )

    await supervisor.start_worker(account_id)
    await _wait_for_spawn(supervisor)
    await supervisor._handle_worker_event(  # noqa: SLF001 - targeted supervisor behavior test
        account_id,
        WatchEvent(
            type="hit",
            check_index=1,
            target="连续包月 / Pro",
            button_state="available",
            available=True,
        ),
        1,
    )
    await asyncio.sleep(0)

    assert headful_factory.commands == []

    await supervisor.stop_worker(account_id)


@pytest.mark.asyncio
async def test_auto_handoff_skipped_when_worker_clicked_in_place(tmp_path: Path) -> None:
    # visible-in-window worker 已就地点击（event.action=clicked_entry）并在等待人工付款；
    # daemon 不应再起 handoff，否则会 stop_worker 杀掉那个正在付款的可见浏览器。
    repo = Repository(tmp_path / "daemon.sqlite3")
    account = repo.create_account("main", str(tmp_path / "profile"))
    repo.create_target(
        account["id"],
        billing_cycle="monthly",
        tier="Pro",
        visible_in_window=True,
    )
    account_id = int(account["id"])
    headless_factory = FakeProcessFactory()
    headful_factory = FakeProcessFactory()
    supervisor = WorkerSupervisor(
        repo,
        EventBroadcaster(),
        process_factory=headless_factory,
        headful_launcher=headful_factory,
    )

    await supervisor.start_worker(account_id)
    await _wait_for_spawn(supervisor)
    await supervisor._handle_worker_event(  # noqa: SLF001 - targeted supervisor behavior test
        account_id,
        WatchEvent(
            type="hit",
            check_index=1,
            target="连续包月 / Pro",
            button_state="available",
            available=True,
            action="clicked_entry",
        ),
        1,
    )
    await asyncio.sleep(0)

    assert headful_factory.commands == []
    # 原 headless（其实是 visible-in-window）worker 仍存活，未被 stop。
    assert headless_factory.processes[0].returncode is None

    await supervisor.stop_worker(account_id)


@pytest.mark.asyncio
async def test_handoff_immediately_after_start_drains_background_spawn(tmp_path: Path) -> None:
    # start 后立即 handoff（不等后台 spawn）：stop_worker 必须 drain 在飞的 spawn，确保后台
    # spawn 出来的 worker 被终止回收，不与紧随其后的 headful 会话抢占同一 profile。
    repo = Repository(tmp_path / "daemon.sqlite3")
    account_id = seed_account(repo, tmp_path)
    headless_factory = FakeProcessFactory()
    headful_factory = FakeProcessFactory()
    supervisor = WorkerSupervisor(
        repo,
        EventBroadcaster(),
        process_factory=headless_factory,
        headful_launcher=headful_factory,
    )

    await supervisor.start_worker(account_id)
    result = await supervisor.start_handoff_session(account_id)

    assert result["status"] == "handoff"
    assert account_id not in supervisor._starting  # noqa: SLF001
    # 后台 spawn 产出的 headless 进程被终止回收（returncode 已设），没有遗留并发占用 profile。
    assert len(headless_factory.processes) == 1
    assert headless_factory.processes[0].returncode is not None
    assert len(headful_factory.commands) == 1
    assert account_id not in supervisor._handles  # noqa: SLF001

    headful_factory.processes[0].finish(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_profile_mutex_blocks_worker_start_during_headful_session(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")
    account_id = seed_account(repo, tmp_path)
    headful_factory = FakeProcessFactory()
    supervisor = WorkerSupervisor(repo, EventBroadcaster(), headful_launcher=headful_factory)

    await supervisor.start_login_session(account_id)
    with pytest.raises(ProfileInUseError):
        await supervisor.start_worker(account_id)

    headful_factory.processes[0].finish(0)
    await asyncio.sleep(0)
