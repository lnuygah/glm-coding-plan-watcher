"""通知通道。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from glm_plan_watcher.config import NotifyConfig
from glm_plan_watcher.logging_setup import get_logger
from glm_plan_watcher.models import CheckResult


@dataclass(frozen=True)
class NotificationArtifacts:
    screenshot: Path | None = None
    html: Path | None = None


class Notifier:
    """多通道通知；任何通道失败都降级为 warning。"""

    def __init__(self, config: NotifyConfig, logger: logging.Logger | None = None) -> None:
        self.config = config
        self.logger = logger or get_logger("notifier")

    async def notify_available(
        self,
        result: CheckResult,
        artifacts: NotificationArtifacts | None = None,
    ) -> None:
        artifacts = artifacts or NotificationArtifacts()
        payload = _build_payload(result, artifacts)

        if self.config.console:
            self._notify_console(result, artifacts)
        if self.config.desktop:
            self._notify_desktop(result)
        if self.config.webhook_url:
            await self._notify_webhook(payload)

    def _notify_console(self, result: CheckResult, artifacts: NotificationArtifacts) -> None:
        paths = []
        if artifacts.screenshot:
            paths.append(f"screenshot={artifacts.screenshot}")
        if artifacts.html:
            paths.append(f"html={artifacts.html}")
        suffix = f" ({', '.join(paths)})" if paths else ""
        self.logger.warning(
            "目标可购买：%s，按钮文本：%s%s",
            result.target.describe(),
            result.button_text,
            suffix,
        )

    def _notify_desktop(self, result: CheckResult) -> None:
        try:
            from plyer import notification  # type: ignore[import-not-found]

            notification.notify(
                title="GLM Coding Plan 可购买",
                message=f"{result.target.describe()}：{result.button_text}",
                timeout=10,
            )
        except Exception as exc:
            self.logger.warning("桌面通知失败，已降级：%s", exc)

    async def _notify_webhook(self, payload: dict[str, Any]) -> None:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(self.config.webhook_url, json=payload)
                response.raise_for_status()
        except Exception as exc:
            self.logger.warning("Webhook 通知失败，已降级：%s", exc)


def _build_payload(result: CheckResult, artifacts: NotificationArtifacts) -> dict[str, Any]:
    return {
        "event": "glm_plan_available",
        "target": result.target.describe(),
        "billing_cycle": result.target.billing_cycle.value,
        "tier": result.target.tier.value,
        "button_state": result.state.value,
        "button_text": result.button_text,
        "reason": result.reason,
        "checked_at": result.checked_at.isoformat(),
        "artifacts": {
            "screenshot": str(artifacts.screenshot) if artifacts.screenshot else "",
            "html": str(artifacts.html) if artifacts.html else "",
        },
    }
