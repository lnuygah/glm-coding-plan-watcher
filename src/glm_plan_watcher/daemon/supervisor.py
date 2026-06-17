"""Account worker process supervision."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import yaml

from glm_plan_watcher.config import DEFAULT_URL
from glm_plan_watcher.daemon.ingest import EventBroadcaster, ingest_stream
from glm_plan_watcher.db import Repository

MIN_INTERVAL_SECONDS = 30.0
MIN_JITTER_SECONDS = 5.0
INITIAL_RESTART_BACKOFF_SECONDS = 1.0
MAX_RESTART_BACKOFF_SECONDS = 60.0


class ProcessLike(Protocol):
    pid: int
    returncode: int | None
    stdout: asyncio.StreamReader | None

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...


ProcessFactory = Callable[[Sequence[str]], Awaitable[ProcessLike]]


@dataclass
class WorkerHandle:
    account_id: int
    process: ProcessLike
    config_path: Path
    desired_running: bool = True
    restart_count: int = 0
    ingest_task: asyncio.Task[int] | None = None
    monitor_task: asyncio.Task[None] | None = None


class WorkerAlreadyRunningError(RuntimeError):
    """Raised when an account worker is already running."""


class WorkerSupervisor:
    """Supervises one account worker subprocess per account."""

    def __init__(
        self,
        repository: Repository,
        broadcaster: EventBroadcaster,
        process_factory: ProcessFactory | None = None,
        runtime_dir: Path | None = None,
        min_interval_seconds: float = MIN_INTERVAL_SECONDS,
        min_jitter_seconds: float = MIN_JITTER_SECONDS,
    ) -> None:
        self.repository = repository
        self.broadcaster = broadcaster
        self.process_factory = process_factory or _default_process_factory
        self.runtime_dir = runtime_dir or repository.path.parent / "worker-configs"
        self.min_interval_seconds = min_interval_seconds
        self.min_jitter_seconds = min_jitter_seconds
        self._handles: dict[int, WorkerHandle] = {}
        self._restart_counts: dict[int, int] = {}

    async def start_worker(self, account_id: int) -> dict[str, object]:
        existing = self._handles.get(account_id)
        if existing is not None and existing.process.returncode is None and existing.desired_running:
            raise WorkerAlreadyRunningError(f"worker already running for account {account_id}")

        config_path = self.materialize_config(account_id)
        command = [sys.executable, "-m", "glm_plan_watcher.worker", "--config", str(config_path)]
        process = await self.process_factory(command)
        handle = WorkerHandle(
            account_id=account_id,
            process=process,
            config_path=config_path,
            restart_count=self._restart_counts.get(account_id, 0),
        )
        self._handles[account_id] = handle

        started_at = datetime.now(UTC).isoformat()
        self.repository.upsert_worker(
            account_id,
            pid=process.pid,
            status="running",
            started_at=started_at,
            last_heartbeat_at=None,
        )
        self.repository.update_account(account_id, status="running")

        if process.stdout is not None:
            handle.ingest_task = asyncio.create_task(
                ingest_stream(process.stdout, self.repository, self.broadcaster, account_id)
            )
        handle.monitor_task = asyncio.create_task(self._monitor_process(handle))
        return self.worker_status(account_id)

    async def stop_worker(self, account_id: int) -> dict[str, object]:
        handle = self._handles.get(account_id)
        if handle is not None:
            handle.desired_running = False
            if handle.process.returncode is None:
                handle.process.terminate()
                try:
                    await asyncio.wait_for(handle.process.wait(), timeout=10)
                except TimeoutError:
                    handle.process.kill()
                    await handle.process.wait()
            if handle.ingest_task is not None:
                handle.ingest_task.cancel()
            self._handles.pop(account_id, None)
            self._restart_counts.pop(account_id, None)

        self.repository.update_worker_status(account_id, status="stopped", pid=None)
        self.repository.update_account(account_id, status="stopped")
        return self.worker_status(account_id)

    def worker_status(self, account_id: int) -> dict[str, object]:
        worker = self.repository.get_worker(account_id)
        if worker is None:
            return {"account_id": account_id, "status": "stopped", "pid": None}
        return worker

    def list_workers(self) -> list[dict[str, object]]:
        return self.repository.list_workers()

    def materialize_config(self, account_id: int) -> Path:
        account = self.repository.get_account(account_id)
        targets = self.repository.list_targets(account_id=account_id, enabled_only=True)
        if not targets:
            raise ValueError(f"account {account_id} has no enabled targets")

        interval, jitter = self.clamped_schedule(targets)
        first = targets[0]
        payload = {
            "url": DEFAULT_URL,
            "billing_cycle": first["billing_cycle"],
            "tier": first["tier"],
            "targets": [
                {"billing_cycle": target["billing_cycle"], "tier": target["tier"]}
                for target in targets
            ],
            "refresh_interval_seconds": interval,
            "refresh_jitter_seconds": jitter,
            "max_checks": 0,
            "headless": True,
            "user_data_dir": account["user_data_dir"],
            "enable_trace": False,
            "auto_click_entry": all(bool(target["auto_click_entry"]) for target in targets),
            "dry_run": any(bool(target["dry_run"]) for target in targets),
            "notify": {"console": True, "desktop": False, "webhook_url": ""},
        }
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        path = self.runtime_dir / f"account-{account_id}.yaml"
        path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return path

    def clamped_schedule(self, targets: list[dict[str, object]]) -> tuple[float, float]:
        requested_interval = min(float(target["interval"]) for target in targets)
        requested_jitter = min(float(target["jitter"]) for target in targets)
        return (
            max(self.min_interval_seconds, requested_interval),
            max(self.min_jitter_seconds, requested_jitter),
        )

    def compute_backoff(self, restart_count: int) -> float:
        return min(
            MAX_RESTART_BACKOFF_SECONDS,
            INITIAL_RESTART_BACKOFF_SECONDS * (2**max(0, restart_count)),
        )

    async def _monitor_process(self, handle: WorkerHandle) -> None:
        returncode = await handle.process.wait()
        if handle.ingest_task is not None:
            with suppress(asyncio.CancelledError):
                await handle.ingest_task

        if not handle.desired_running:
            self.repository.update_worker_status(handle.account_id, status="stopped", pid=None)
            self.repository.update_account(handle.account_id, status="stopped")
            return

        if returncode == 0:
            self.repository.update_worker_status(handle.account_id, status="exited", pid=None)
            self.repository.update_account(handle.account_id, status="exited")
            self._handles.pop(handle.account_id, None)
            return

        self.repository.update_worker_status(handle.account_id, status="crashed", pid=None)
        self.repository.update_account(handle.account_id, status="crashed")
        delay = self.compute_backoff(handle.restart_count)
        self._restart_counts[handle.account_id] = handle.restart_count + 1
        await asyncio.sleep(delay)
        if handle.desired_running:
            self._handles.pop(handle.account_id, None)
            await self.start_worker(handle.account_id)


async def _default_process_factory(command: Sequence[str]) -> ProcessLike:
    return await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

