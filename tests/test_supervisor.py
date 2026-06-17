from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml

from glm_plan_watcher.daemon.ingest import EventBroadcaster
from glm_plan_watcher.daemon.supervisor import WorkerAlreadyRunningError, WorkerSupervisor
from glm_plan_watcher.db import Repository


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
    assert data["targets"] == [{"billing_cycle": "monthly", "tier": "Pro"}]


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
    factory.processes[0].finish(1)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert len(factory.processes) == 2
    assert repo.get_worker(account_id)["status"] == "running"

    await supervisor.stop_worker(account_id)


def test_supervisor_compute_backoff_caps(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "daemon.sqlite3")
    supervisor = WorkerSupervisor(repo, EventBroadcaster())

    assert supervisor.compute_backoff(0) == 1.0
    assert supervisor.compute_backoff(10) == 60.0
