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
from glm_plan_watcher.models import BillingCycle, TargetSpec, Tier, WatchEvent

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


@dataclass
class HeadfulHandle:
    account_id: int
    process: ProcessLike
    kind: str
    config_path: Path
    restore_worker: bool
    target: str = "account"
    monitor_task: asyncio.Task[None] | None = None


class WorkerAlreadyRunningError(RuntimeError):
    """Raised when an account worker is already running."""


class ProfileInUseError(RuntimeError):
    """Raised when the account profile is already held by another process."""


class WorkerSupervisor:
    """Supervises one account worker subprocess per account."""

    def __init__(
        self,
        repository: Repository,
        broadcaster: EventBroadcaster,
        process_factory: ProcessFactory | None = None,
        headful_launcher: ProcessFactory | None = None,
        runtime_dir: Path | None = None,
        min_interval_seconds: float = MIN_INTERVAL_SECONDS,
        min_jitter_seconds: float = MIN_JITTER_SECONDS,
    ) -> None:
        self.repository = repository
        self.broadcaster = broadcaster
        self.process_factory = process_factory or _default_process_factory
        self.headful_launcher = headful_launcher or _default_process_factory
        self.runtime_dir = runtime_dir or repository.path.parent / "worker-configs"
        self.min_interval_seconds = min_interval_seconds
        self.min_jitter_seconds = min_jitter_seconds
        self._handles: dict[int, WorkerHandle] = {}
        self._headful_handles: dict[int, HeadfulHandle] = {}
        self._restart_counts: dict[int, int] = {}

    async def start_worker(self, account_id: int) -> dict[str, object]:
        headful = self._headful_handles.get(account_id)
        if headful is not None and headful.process.returncode is None:
            raise ProfileInUseError(f"account profile is in visible {headful.kind} session")

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

    async def start_login_session(
        self,
        account_id: int,
        restore_worker: bool = False,
    ) -> dict[str, object]:
        return await self._start_headful_session(
            account_id=account_id,
            kind="login",
            restore_worker=restore_worker,
            target_id=None,
            click_entry=False,
        )

    async def start_handoff_session(
        self,
        account_id: int,
        target_id: int | None = None,
        click_entry: bool = False,
        restore_worker: bool = False,
    ) -> dict[str, object]:
        return await self._start_headful_session(
            account_id=account_id,
            kind="handoff",
            restore_worker=restore_worker,
            target_id=target_id,
            click_entry=click_entry,
        )

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

    def materialize_headful_config(
        self,
        account_id: int,
        target_id: int | None = None,
    ) -> tuple[Path, str]:
        account = self.repository.get_account(account_id)
        target = self._resolve_handoff_target(account_id, target_id)
        payload = {
            "url": DEFAULT_URL,
            "billing_cycle": target.billing_cycle.value,
            "tier": target.tier.value,
            "targets": [
                {"billing_cycle": target.billing_cycle.value, "tier": target.tier.value},
            ],
            "refresh_interval_seconds": self.min_interval_seconds,
            "refresh_jitter_seconds": self.min_jitter_seconds,
            "max_checks": 0,
            "headless": False,
            "user_data_dir": account["user_data_dir"],
            "enable_trace": False,
            "auto_click_entry": False,
            "dry_run": True,
            "notify": {"console": True, "desktop": False, "webhook_url": ""},
        }
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        suffix = f"target-{target_id}" if target_id is not None else "default"
        path = self.runtime_dir / f"account-{account_id}-headful-{suffix}.yaml"
        path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return path, target.describe()

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

    async def _start_headful_session(
        self,
        account_id: int,
        kind: str,
        restore_worker: bool,
        target_id: int | None,
        click_entry: bool,
    ) -> dict[str, object]:
        existing = self._headful_handles.get(account_id)
        if existing is not None and existing.process.returncode is None:
            raise ProfileInUseError(f"account profile is already in visible {existing.kind} session")

        await self.stop_worker(account_id)
        config_path, target = self.materialize_headful_config(account_id, target_id)
        command = [
            sys.executable,
            "-m",
            "glm_plan_watcher.headful",
            kind,
            "--config",
            str(config_path),
        ]
        if kind == "handoff" and click_entry:
            command.append("--click-entry")
        process = await self.headful_launcher(command)
        handle = HeadfulHandle(
            account_id=account_id,
            process=process,
            kind=kind,
            config_path=config_path,
            restore_worker=restore_worker,
            target=target if kind == "handoff" else "account",
        )
        self._headful_handles[account_id] = handle
        self.repository.update_account(account_id, status=kind)
        await self._record_daemon_event(
            account_id,
            event_type=kind,
            action="started",
            target=handle.target,
            message=f"visible {kind} session started",
        )
        handle.monitor_task = asyncio.create_task(self._monitor_headful_process(handle))
        return {
            "account_id": account_id,
            "status": kind,
            "pid": process.pid,
            "target": handle.target,
            "restore_worker": restore_worker,
            "click_entry": click_entry,
        }

    async def _monitor_headful_process(self, handle: HeadfulHandle) -> None:
        returncode = await handle.process.wait()
        self._headful_handles.pop(handle.account_id, None)
        self.repository.update_account(handle.account_id, status="stopped")
        await self._record_daemon_event(
            handle.account_id,
            event_type=handle.kind,
            action="ended",
            target=handle.target,
            message=f"visible {handle.kind} session ended with code {returncode}",
        )
        if handle.restore_worker:
            await self.start_worker(handle.account_id)

    async def _record_daemon_event(
        self,
        account_id: int,
        event_type: str,
        action: str,
        target: str,
        message: str,
    ) -> int:
        event = WatchEvent(
            type=event_type,
            check_index=0,
            target=target,
            button_state=event_type,
            action=action,
            available=False,
            message=message,
        )
        event_id = self.repository.insert_event(account_id, event)
        await self.broadcaster.broadcast(
            account_id,
            {"event_id": event_id, "event": event.model_dump(mode="json")},
        )
        return event_id

    def _resolve_handoff_target(self, account_id: int, target_id: int | None) -> TargetSpec:
        if target_id is not None:
            row = self.repository.get_target(target_id)
            if row["account_id"] != account_id:
                raise ValueError(f"target {target_id} does not belong to account {account_id}")
            return TargetSpec(
                billing_cycle=BillingCycle(row["billing_cycle"]),
                tier=Tier(row["tier"]),
            )

        targets = self.repository.list_targets(account_id=account_id, enabled_only=True)
        if not targets:
            return TargetSpec(billing_cycle=BillingCycle.monthly, tier=Tier.Pro)
        row = targets[0]
        return TargetSpec(
            billing_cycle=BillingCycle(row["billing_cycle"]),
            tier=Tier(row["tier"]),
        )


async def _default_process_factory(command: Sequence[str]) -> ProcessLike:
    return await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
