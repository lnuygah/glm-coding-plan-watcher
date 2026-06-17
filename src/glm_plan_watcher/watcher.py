"""监控循环与 worker JSON 行协议。"""

from __future__ import annotations

import asyncio
import logging
import random
import signal
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TextIO

from glm_plan_watcher.browser import BrowserSession, make_storage
from glm_plan_watcher.config import AppConfig
from glm_plan_watcher.detector import DetectorStrategy, DomDetector
from glm_plan_watcher.logging_setup import get_logger
from glm_plan_watcher.models import CheckResult, TargetSpec, WatchEvent
from glm_plan_watcher.notifier import NotificationArtifacts, Notifier
from glm_plan_watcher.scheduler import SchedulerPolicy


@dataclass(frozen=True)
class _PendingEvent:
    check_index: int
    target: TargetSpec
    result: CheckResult | None = None
    error_message: str = ""


class AccountWatcher:
    """一个账号一个浏览器会话，顺序轮询该账号下的多个目标。"""

    def __init__(
        self,
        config: AppConfig,
        detector: DetectorStrategy | None = None,
        notifier: Notifier | None = None,
        logger: logging.Logger | None = None,
        stdout: TextIO | None = None,
        scheduler_policy: SchedulerPolicy | None = None,
    ) -> None:
        self.config = config
        self.detector = detector or DomDetector()
        self.notifier = notifier or Notifier(config.notify)
        self.logger = logger or get_logger("watcher")
        self.stdout = stdout or sys.stdout
        self.targets = config.target_specs
        self.scheduler_policy = scheduler_policy or SchedulerPolicy(
            base_interval_seconds=config.refresh_interval_seconds,
            jitter_seconds=config.refresh_jitter_seconds,
            active_window_start=config.active_window_start,
            active_window_end=config.active_window_end,
            active_timezone=config.active_timezone,
            active_interval_seconds=config.active_interval_seconds,
            active_jitter_seconds=config.active_jitter_seconds,
            idle_interval_seconds=config.idle_interval_seconds,
        )
        self._stop = asyncio.Event()
        self._last_state: dict[str, str] = {}

    async def run(self) -> int:
        """启动 persistent browser session 后进入账号级监控。"""

        self._install_signal_handlers()
        async with BrowserSession(self.config, make_storage(self.config)) as session:
            page = await session.goto(self.config.url)
            return await self.monitor(page, session)

    async def monitor(self, page: Any, session: BrowserSession | None = None) -> int:
        """复用型账号级监控协程。

        测试可注入 fake page / fake detector 运行核心调度逻辑；生产路径由 `run()` 提供
        同一个 persistent browser session。每轮按 targets 顺序检测，不并发刷新页面。
        """

        check_index = 0
        scan_index = 0

        while not self._stop.is_set():
            scan_index += 1
            pending_events: list[_PendingEvent] = []

            for target in self.targets:
                if self._stop.is_set():
                    break
                check_index += 1
                try:
                    result = await self.detector.detect(page, target)
                    await self._handle_state_change(session, result)

                    if result.available:
                        await self._handle_hit(session, page, result, check_index)
                        if self._stop.is_set():
                            self._emit_shutdown(check_index)
                        return 0

                    pending_events.append(
                        _PendingEvent(check_index=check_index, target=target, result=result)
                    )
                    self.logger.info(
                        "第 %s 次检测：%s，%s",
                        check_index,
                        result.target.describe(),
                        result.state.value,
                    )
                except Exception as exc:
                    self.logger.exception("检测循环异常：%s", exc)
                    if session is not None:
                        await session.capture_artifacts("error", target)
                    pending_events.append(
                        _PendingEvent(
                            check_index=check_index,
                            target=target,
                            error_message=str(exc),
                        )
                    )

            if self._stop.is_set():
                break

            if self._max_checks_reached(scan_index):
                for event in pending_events:
                    self._emit_pending_event(event, action="none", message="max checks reached")
                self._emit_heartbeat(check_index, action="none", message="max checks reached")
                return 0

            results = [event.result for event in pending_events if event.result is not None]
            delay = self._next_delay_seconds(results)
            next_refresh_at = datetime.now(UTC) + timedelta(seconds=delay)
            for event in pending_events:
                self._emit_pending_event(event, action="wait", next_refresh_at=next_refresh_at)
            self._emit_heartbeat(
                check_index,
                action="wait",
                next_refresh_at=next_refresh_at,
                message=f"scan round {scan_index} completed",
            )
            self.logger.info(
                "账号级第 %s 轮检测完成：%s 个目标；下次刷新约 %.1f 秒后",
                scan_index,
                len(self.targets),
                delay,
            )

            if await self._sleep_or_stop(delay):
                break
            if hasattr(page, "reload"):
                await page.reload(wait_until="domcontentloaded")

        self._emit_shutdown(check_index)
        return 0

    def request_stop(self) -> None:
        self._stop.set()

    async def _handle_hit(
        self,
        session: BrowserSession | None,
        page: Any,
        result: CheckResult,
        check_index: int,
    ) -> None:
        if session is not None:
            screenshot, html = await session.capture_artifacts("hit", result.target)
            artifacts = NotificationArtifacts(screenshot=screenshot, html=html)
            await self.notifier.notify_available(result, artifacts)

        target_dry_run = self.config.dry_run or result.target.dry_run
        if target_dry_run or session is None:
            action = "dry_run" if target_dry_run else "none"
            self._emit_event(result, check_index, event_type="hit", action=action)
            if target_dry_run:
                self.logger.warning("dry-run 命中：只检测不点击")
            return

        target_auto_click_entry = self.config.auto_click_entry and result.target.auto_click_entry
        if target_auto_click_entry and isinstance(self.detector, DomDetector):
            clicked = await self.detector.click_entry_button(page, result.target)
            action = "clicked_entry" if clicked else "none"
            message = "entry clicked; waiting for manual payment" if clicked else "entry button not clicked"
            self._emit_event(result, check_index, event_type="hit", action=action, message=message)
            if clicked:
                self.logger.warning("已点击购买/订阅入口；保持浏览器打开，等待人工完成后续步骤")
                await self._pause_for_manual()
            return

        self._emit_event(result, check_index, event_type="hit", action="none")

    async def _handle_state_change(
        self,
        session: BrowserSession | None,
        result: CheckResult,
    ) -> None:
        key = _target_key(result.target)
        previous_state = self._last_state.get(key)
        if previous_state == result.state.value:
            return
        previous = previous_state or "initial"
        self._last_state[key] = result.state.value
        if session is not None:
            await session.storage.save_html(session.require_page(), "state-change", result.target)
        self.logger.warning(
            "状态变化：%s -> %s（%s）",
            previous,
            result.state.value,
            result.reason,
        )

    def _emit_pending_event(
        self,
        event: _PendingEvent,
        action: str,
        next_refresh_at: datetime | None = None,
        message: str = "",
    ) -> None:
        if event.result is not None:
            self._emit_event(
                event.result,
                event.check_index,
                action=action,
                next_refresh_at=next_refresh_at,
                message=message,
            )
            return

        self._emit_raw_event(
            WatchEvent(
                type="error",
                check_index=event.check_index,
                target=event.target.describe(),
                button_state="error",
                available=False,
                action=action,
                next_refresh_at=next_refresh_at,
                message=message or event.error_message,
            )
        )

    def _emit_event(
        self,
        result: CheckResult,
        check_index: int,
        event_type: str = "check",
        action: str = "none",
        next_refresh_at: datetime | None = None,
        message: str = "",
    ) -> None:
        self._emit_raw_event(
            WatchEvent(
                type=event_type,
                check_index=check_index,
                target=result.target.describe(),
                button_state=result.state.value,
                button_text=result.button_text,
                action=action,
                available=result.available,
                next_refresh_at=next_refresh_at,
                message=message or result.reason,
            )
        )

    def _emit_raw_event(self, event: WatchEvent) -> None:
        print(event.to_json_line(), file=self.stdout, flush=True)

    def _emit_heartbeat(
        self,
        check_index: int,
        action: str,
        next_refresh_at: datetime | None = None,
        message: str = "",
    ) -> None:
        self._emit_raw_event(
            WatchEvent(
                type="heartbeat",
                check_index=check_index,
                target=self._account_event_target(),
                button_state="heartbeat",
                available=False,
                action=action,
                next_refresh_at=next_refresh_at,
                message=message,
            )
        )

    def _emit_shutdown(self, check_index: int) -> None:
        self._emit_raw_event(
            WatchEvent(
                type="shutdown",
                check_index=check_index,
                target=self._account_event_target(),
                button_state="shutdown",
                available=False,
                action="none",
                message="received stop signal",
            )
        )

    def _next_delay_seconds(self, results: Sequence[CheckResult] = ()) -> float:
        if self.scheduler_policy is not None:
            return self.scheduler_policy.next_delay(results)

        interval = self.config.refresh_interval_seconds
        jitter = self.config.refresh_jitter_seconds
        if jitter <= 0:
            return max(1.0, interval)
        return max(1.0, interval + random.uniform(-jitter, jitter))

    def _max_checks_reached(self, scan_index: int) -> bool:
        return self.config.max_checks > 0 and scan_index >= self.config.max_checks

    async def _sleep_or_stop(self, delay: float) -> bool:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=delay)
            return True
        except TimeoutError:
            return False

    async def _pause_for_manual(self) -> None:
        while not self._stop.is_set():
            await self._sleep_or_stop(3600)

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self.request_stop)
            except (NotImplementedError, RuntimeError):
                signal.signal(sig, lambda _signum, _frame: self.request_stop())

    def _account_event_target(self) -> str:
        if len(self.targets) == 1:
            return self.targets[0].describe()
        return "account"


class Watcher(AccountWatcher):
    """向后兼容旧 CLI/import 名称。"""


def _target_key(target: TargetSpec) -> str:
    return f"{target.billing_cycle.value}:{target.tier.value}"
