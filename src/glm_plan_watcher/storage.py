"""运行产物存储：截图、HTML 快照、Playwright trace。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import Page

from glm_plan_watcher.models import TargetSpec


@dataclass(frozen=True)
class StorageManager:
    """集中管理运行产物路径和落盘。"""

    screenshot_dir: Path
    html_snapshot_dir: Path
    log_dir: Path

    def ensure_dirs(self) -> None:
        for directory in (self.screenshot_dir, self.html_snapshot_dir, self.log_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def screenshot_path(self, kind: str, target: TargetSpec | None = None) -> Path:
        return self._path(self.screenshot_dir, kind, target, ".png")

    def html_snapshot_path(self, kind: str, target: TargetSpec | None = None) -> Path:
        return self._path(self.html_snapshot_dir, kind, target, ".html")

    def trace_path(self, kind: str = "trace", target: TargetSpec | None = None) -> Path:
        return self._path(self.log_dir / "traces", kind, target, ".zip")

    async def save_screenshot(self, page: Page, kind: str, target: TargetSpec | None = None) -> Path:
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        path = self.screenshot_path(kind, target)
        await page.screenshot(path=path, full_page=True)
        return path

    async def save_html(self, page: Page, kind: str, target: TargetSpec | None = None) -> Path:
        self.html_snapshot_dir.mkdir(parents=True, exist_ok=True)
        path = self.html_snapshot_path(kind, target)
        path.write_text(await page.content(), encoding="utf-8")
        return path

    def _path(self, directory: Path, kind: str, target: TargetSpec | None, suffix: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        parts = [_timestamp(), _slug(kind)]
        if target is not None:
            parts.append(_slug(f"{target.billing_cycle.value}-{target.tier.value}"))
        return directory / ("_".join(parts) + suffix)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return slug.strip("-") or "artifact"
