"""Playwright 浏览器运行时封装。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from glm_plan_watcher.config import AppConfig
from glm_plan_watcher.exceptions import BrowserError
from glm_plan_watcher.models import TargetSpec
from glm_plan_watcher.storage import StorageManager


@dataclass
class BrowserSession:
    """持久化 Chromium 上下文。

    使用 Playwright persistent context 保存登录态到 user_data_dir；不保存账号密码，也不隐藏自动化。
    """

    config: AppConfig
    storage: StorageManager
    playwright: Playwright | None = None
    context: BrowserContext | None = None
    page: Page | None = None
    _trace_path: Path | None = None

    async def __aenter__(self) -> BrowserSession:
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.close()

    async def start(self) -> Page:
        self.config.ensure_dirs()
        self.storage.ensure_dirs()
        try:
            self.playwright = await async_playwright().start()
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.config.user_data_dir),
                headless=self.config.headless,
                locale="zh-CN",
                viewport={"width": 1440, "height": 1200},
            )
            if self.config.enable_trace:
                await self.context.tracing.start(screenshots=True, snapshots=True, sources=True)
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            return self.page
        except Exception as exc:
            await self.close()
            raise BrowserError(f"浏览器启动失败：{exc}") from exc

    async def goto(self, url: str) -> Page:
        page = self.require_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            return page
        except Exception as exc:
            raise BrowserError(f"页面打开失败：{url}: {exc}") from exc

    async def capture_artifacts(self, kind: str, target: TargetSpec | None = None) -> tuple[Path, Path]:
        page = self.require_page()
        screenshot = await self.storage.save_screenshot(page, kind, target)
        html = await self.storage.save_html(page, kind, target)
        return screenshot, html

    async def close(self) -> None:
        if self.context is not None:
            if self.config.enable_trace:
                self._trace_path = self.storage.trace_path()
                try:
                    await self.context.tracing.stop(path=self._trace_path)
                except Exception:
                    self._trace_path = None
            await self.context.close()
            self.context = None
        if self.playwright is not None:
            await self.playwright.stop()
            self.playwright = None

    def require_page(self) -> Page:
        if self.page is None:
            raise BrowserError("浏览器页面尚未初始化")
        return self.page

    @property
    def trace_path(self) -> Path | None:
        return self._trace_path


def make_storage(config: AppConfig) -> StorageManager:
    return StorageManager(
        screenshot_dir=config.screenshot_dir,
        html_snapshot_dir=config.html_snapshot_dir,
        log_dir=config.log_dir,
    )


async def launch_persistent_session(config: AppConfig) -> BrowserSession:
    """启动持久化浏览器会话。"""

    session = BrowserSession(config=config, storage=make_storage(config))
    await session.start()
    return session
