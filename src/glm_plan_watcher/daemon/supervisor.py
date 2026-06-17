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
from glm_plan_watcher.scheduler import MIN_INTERVAL_SECONDS, MIN_JITTER_SECONDS

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
        self._auto_handoff_tasks: set[asyncio.Task[None]] = set()
        # worker spawn 在后台进行：_starting 占位互斥，_spawn_tasks 持有任务引用避免被 GC。
        self._starting: set[int] = set()
        self._spawn_tasks: set[asyncio.Task[None]] = set()
        # 每账号正在进行的后台 spawn 任务，供 stop_worker 在启动 login/handoff 前 drain，
        # 避免后台 spawn 出来的 worker 与紧随其后的 headful 会话抢占同一 profile。
        self._spawn_tasks_by_account: dict[int, asyncio.Task[None]] = {}

    async def start_worker(self, account_id: int) -> dict[str, object]:
        """请求启动账号 worker，并尽快返回。

        关键修复：进程 spawn（asyncio.create_subprocess_exec）在打包 sidecar/冷启动解释器下可能
        耗时数秒；若把整个 HTTP 响应阻塞到 spawn 完成，WebKit webview 会中止 fetch（GUI 报
        “启动 失败: Load failed”）。因此这里只做廉价的互斥校验与配置物化（可同步 400），把
        worker 标记为 "starting" 后立即返回，真正的 spawn + 管线接线放到后台任务里完成；就绪状态
        通过 worker status 的 heartbeat（→ running）和事件流对外暴露。
        """

        headful = self._headful_handles.get(account_id)
        if headful is not None and headful.process.returncode is None:
            raise ProfileInUseError(f"account profile is in visible {headful.kind} session")

        existing = self._handles.get(account_id)
        if existing is not None and existing.process.returncode is None and existing.desired_running:
            raise WorkerAlreadyRunningError(f"worker already running for account {account_id}")
        if account_id in self._starting:
            raise WorkerAlreadyRunningError(f"worker already starting for account {account_id}")

        # materialize_config 是同步的，且会在缺少 enabled targets 时抛 ValueError——保持同步抛出，
        # 让路由把它映射成 400，而不是吞进后台任务。
        config_path = self.materialize_config(account_id)

        # 立刻把 worker 标记为 starting，并占位互斥，避免后台 spawn 期间重复启动或被 headful 抢占。
        self._starting.add(account_id)
        self.repository.upsert_worker(
            account_id,
            pid=None,
            status="starting",
            started_at=datetime.now(UTC).isoformat(),
            last_heartbeat_at=None,
        )
        self.repository.update_account(account_id, status="starting")

        task = asyncio.create_task(self._spawn_worker_process(account_id, config_path))
        self._spawn_tasks.add(task)
        self._spawn_tasks_by_account[account_id] = task

        def _clear_spawn(finished: asyncio.Task[None], aid: int = account_id) -> None:
            self._spawn_tasks.discard(finished)
            if self._spawn_tasks_by_account.get(aid) is finished:
                self._spawn_tasks_by_account.pop(aid, None)

        task.add_done_callback(_clear_spawn)
        return self.worker_status(account_id)

    async def _spawn_worker_process(self, account_id: int, config_path: Path) -> None:
        """后台执行真正的进程 spawn 与管线接线（ingest/monitor）。"""

        command = [sys.executable, "-m", "glm_plan_watcher.worker", "--config", str(config_path)]
        try:
            process = await self.process_factory(command)
        except Exception as exc:
            self._starting.discard(account_id)
            self.repository.update_worker_status(account_id, status="crashed", pid=None)
            self.repository.update_account(account_id, status="crashed")
            await self._record_daemon_event(
                account_id,
                event_type="worker",
                action="failed",
                target="account",
                message=f"worker spawn failed: {exc}",
            )
            return

        # spawn 期间若被 stop_worker 取消（占位已被移除），不接管这个进程，终止并 await 回收，
        # 避免遗留进程继续占用 profile。
        if account_id not in self._starting:
            await self._terminate_process(process)
            return

        handle = WorkerHandle(
            account_id=account_id,
            process=process,
            config_path=config_path,
            restart_count=self._restart_counts.get(account_id, 0),
        )
        self._handles[account_id] = handle
        self._starting.discard(account_id)

        self.repository.upsert_worker(
            account_id,
            pid=process.pid,
            status="running",
            started_at=datetime.now(UTC).isoformat(),
            last_heartbeat_at=None,
        )
        self.repository.update_account(account_id, status="running")

        if process.stdout is not None:
            handle.ingest_task = asyncio.create_task(
                ingest_stream(
                    process.stdout,
                    self.repository,
                    self.broadcaster,
                    account_id,
                    on_event=self._handle_worker_event,
                )
            )
        handle.monitor_task = asyncio.create_task(self._monitor_process(handle))

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
        # 若 spawn 还在后台进行，清除占位：_spawn_worker_process 完成后发现自己已被取消，会终止刚
        # spawn 出来的进程而不接管，避免 stop 之后又冒出一个 worker。
        self._starting.discard(account_id)
        # drain 正在进行的后台 spawn：清除占位后 await 它结束——它会发现 _starting 已清除并终止刚
        # spawn 的进程，或已注册 handle（下面统一拆除）。这样 login/handoff 在 stop 之后不会与残余
        # worker 抢占同一 profile。
        spawn_task = self._spawn_tasks_by_account.get(account_id)
        if spawn_task is not None:
            with suppress(asyncio.CancelledError):
                await spawn_task

        handle = self._handles.get(account_id)
        if handle is not None:
            handle.desired_running = False
            await self._terminate_process(handle.process)
            if handle.ingest_task is not None:
                handle.ingest_task.cancel()
            self._handles.pop(account_id, None)
            self._restart_counts.pop(account_id, None)

        self.repository.update_worker_status(account_id, status="stopped", pid=None)
        self.repository.update_account(account_id, status="stopped")
        return self.worker_status(account_id)

    async def _terminate_process(self, process: asyncio.subprocess.Process) -> None:
        """终止子进程并回收：先 terminate，超时再 kill，始终 await wait，避免遗留僵尸进程。"""

        if process.returncode is not None:
            return
        with suppress(Exception):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except TimeoutError:
            with suppress(Exception):
                process.kill()
            with suppress(Exception):
                await process.wait()

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
                {
                    "billing_cycle": target["billing_cycle"],
                    "tier": target["tier"],
                    "active_window_start": target["active_window_start"],
                    "active_window_end": target["active_window_end"],
                    "active_timezone": target["active_timezone"],
                    "active_interval_seconds": target["active_interval_seconds"],
                    "active_jitter_seconds": target["active_jitter_seconds"],
                    "idle_interval_seconds": target["idle_interval_seconds"],
                    "dry_run": target["dry_run"],
                    "auto_click_entry": target["auto_click_entry"],
                    "visible_in_window": target["visible_in_window"],
                }
                for target in targets
            ],
            "refresh_interval_seconds": interval,
            "refresh_jitter_seconds": jitter,
            "max_checks": 0,
            "headless": True,
            "user_data_dir": account["user_data_dir"],
            "enable_trace": False,
            # Headless daemon workers only detect/report. Purchase entry clicks happen in
            # explicit visible handoff sessions so the user can complete payment manually.
            "auto_click_entry": False,
            "dry_run": False,
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

    async def _handle_worker_event(
        self,
        account_id: int,
        event: WatchEvent,
        _event_id: int,
    ) -> None:
        if event.type != "hit" or not event.available:
            return
        # visible-in-window 模式下 worker 自身就是可见浏览器，命中时已就地点击入口并在等待人工
        # 付款（事件带 action="clicked_entry"）。此时再起 handoff 会 stop_worker 把可见浏览器杀掉，
        # 违背"省去重启延迟"的设计——直接跳过。窗口外（未点击）仍走正常 handoff。
        if event.action == "clicked_entry":
            return

        target = self._matching_target_row(account_id, event.target)
        if target is None:
            return
        if not bool(target["on_hit_handoff"]) or bool(target["dry_run"]):
            return

        task = asyncio.create_task(
            self._auto_handoff_after_hit(
                account_id=account_id,
                target_id=int(target["id"]),
                click_entry=bool(target["auto_click_entry"]),
            )
        )
        self._auto_handoff_tasks.add(task)
        task.add_done_callback(self._auto_handoff_tasks.discard)

    async def _auto_handoff_after_hit(
        self,
        account_id: int,
        target_id: int,
        click_entry: bool,
    ) -> None:
        try:
            await self.start_handoff_session(
                account_id,
                target_id=target_id,
                click_entry=click_entry,
                restore_worker=False,
            )
        except Exception as exc:
            await self._record_daemon_event(
                account_id,
                event_type="handoff",
                action="skipped",
                target=f"target:{target_id}",
                message=f"auto handoff failed: {exc}",
            )

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

    def _matching_target_row(self, account_id: int, target_label: str) -> dict[str, object] | None:
        for row in self.repository.list_targets(account_id=account_id, enabled_only=True):
            spec = TargetSpec(
                billing_cycle=BillingCycle(row["billing_cycle"]),
                tier=Tier(row["tier"]),
            )
            if spec.describe() == target_label:
                return row
        return None


async def _default_process_factory(command: Sequence[str]) -> ProcessLike:
    return await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
