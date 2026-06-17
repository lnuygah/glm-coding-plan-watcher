"""监控循环与 worker JSON 行协议。"""

from __future__ import annotations

import asyncio
import logging
import random
import signal
import sys
from datetime import datetime, timedelta, timezone
from typing import TextIO

from glm_plan_watcher.browser import BrowserSession, make_storage
from glm_plan_watcher.config import AppConfig
from glm_plan_watcher.detector import DetectorStrategy, DomDetector
from glm_plan_watcher.logging_setup import get_logger
from glm_plan_watcher.models import ButtonState, CheckResult, WatchEvent
from glm_plan_watcher.notifier import NotificationArtifacts, Notifier


class Watcher:
    """一个 config 对应一个目标与一个进程。"""

    def __init__(
        self,
        config: AppConfig,
        detector: DetectorStrategy | None = None,
        notifier: Notifier | None = None,
        logger: logging.Logger | None = None,
        stdout: TextIO | None = None,
    ) -> None:
        self.config = config
        self.detector = detector or DomDetector()
        self.notifier = notifier or Notifier(config.notify)
        self.logger = logger or get_logger("watcher")
        self.stdout = stdout or sys.stdout
        self._stop = asyncio.Event()
        self._last_state: ButtonState | None = None

    async def run(self) -> int:
        self._install_signal_handlers()
        storage = make_storage(self.config)
        check_index = 0

        async with BrowserSession(self.config, storage) as session:
            page = await session.goto(self.config.url)
            while not self._stop.is_set():
                check_index += 1
                try:
                    result = await self.detector.detect(page, self.config.target)
                    await self._handle_state_change(session, result)

                    if result.available:
                        await self._handle_hit(session, result, check_index)
                        if self._stop.is_set():
                            self._emit_shutdown(check_index)
                        return 0

                    if self._max_checks_reached(check_index):
                        self._emit_event(result, check_index, action="none", message="max checks reached")
                        return 0

                    delay = self._next_delay_seconds()
                    next_refresh_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
                    self._emit_event(
                        result,
                        check_index,
                        action="wait",
                        next_refresh_at=next_refresh_at,
                    )
                    self.logger.info(
                        "第 %s 次检测：%s，%s；下次刷新约 %.1f 秒后",
                        check_index,
                        result.target.describe(),
                        result.state.value,
                        delay,
                    )
                    if await self._sleep_or_stop(delay):
                        break
                    await page.reload(wait_until="domcontentloaded")
                except Exception as exc:
                    self.logger.exception("检测循环异常：%s", exc)
                    await session.capture_artifacts("error", self.config.target)
                    delay = self._next_delay_seconds()
                    next_refresh_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
                    self._emit_raw_event(
                        WatchEvent(
                            type="error",
                            check_index=check_index,
                            target=self.config.target.describe(),
                            button_state="error",
                            available=False,
                            action="wait",
                            next_refresh_at=next_refresh_at,
                            message=str(exc),
                        )
                    )
                    if await self._sleep_or_stop(delay):
                        break
                    await page.reload(wait_until="domcontentloaded")

        self._emit_shutdown(check_index)
        return 0

    def request_stop(self) -> None:
        self._stop.set()

    async def _handle_hit(
        self,
        session: BrowserSession,
        result: CheckResult,
        check_index: int,
    ) -> None:
        screenshot, html = await session.capture_artifacts("hit", result.target)
        artifacts = NotificationArtifacts(screenshot=screenshot, html=html)
        await self.notifier.notify_available(result, artifacts)

        if self.config.dry_run:
            self._emit_event(result, check_index, event_type="hit", action="dry_run")
            self.logger.warning("dry-run 命中：只检测不点击")
            return

        if self.config.auto_click_entry and isinstance(self.detector, DomDetector):
            clicked = await self.detector.click_entry_button(session.require_page(), result.target)
            action = "clicked_entry" if clicked else "none"
            message = "entry clicked; waiting for manual payment" if clicked else "entry button not clicked"
            self._emit_event(result, check_index, event_type="hit", action=action, message=message)
            if clicked:
                self.logger.warning("已点击购买/订阅入口；保持浏览器打开，等待人工完成后续步骤")
                await self._pause_for_manual()
            return

        self._emit_event(result, check_index, event_type="hit", action="none")

    async def _handle_state_change(self, session: BrowserSession, result: CheckResult) -> None:
        if self._last_state is result.state:
            return
        previous = self._last_state.value if self._last_state else "initial"
        self._last_state = result.state
        await session.storage.save_html(session.require_page(), "state-change", result.target)
        self.logger.warning(
            "状态变化：%s -> %s（%s）",
            previous,
            result.state.value,
            result.reason,
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

    def _emit_shutdown(self, check_index: int) -> None:
        self._emit_raw_event(
            WatchEvent(
                type="shutdown",
                check_index=check_index,
                target=self.config.target.describe(),
                button_state="shutdown",
                available=False,
                action="none",
                message="received stop signal",
            )
        )

    def _next_delay_seconds(self) -> float:
        interval = self.config.refresh_interval_seconds
        jitter = self.config.refresh_jitter_seconds
        if jitter <= 0:
            return max(1.0, interval)
        return max(1.0, interval + random.uniform(-jitter, jitter))

    def _max_checks_reached(self, check_index: int) -> bool:
        return self.config.max_checks > 0 and check_index >= self.config.max_checks

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
