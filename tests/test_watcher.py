from __future__ import annotations

import json
from datetime import datetime
from io import StringIO
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from playwright.async_api import Page

from glm_plan_watcher.config import AppConfig
from glm_plan_watcher.detector import DetectorStrategy, DomDetector
from glm_plan_watcher.models import BillingCycle, ButtonState, CheckResult, TargetSpec, Tier
from glm_plan_watcher.scheduler import SchedulerPolicy
from glm_plan_watcher.watcher import AccountWatcher


class ClickRecordingDomDetector(DomDetector):
    """DomDetector 子类，记录是否就地点击入口（不触碰浏览器）。"""

    def __init__(self, state: ButtonState) -> None:
        super().__init__()
        self.state = state
        self.clicked: list[TargetSpec] = []

    async def detect(self, page: Page, target: TargetSpec) -> CheckResult:
        return CheckResult(target=target, state=self.state, reason="fake")

    async def click_entry_button(self, page: Any, target: TargetSpec) -> bool:
        self.clicked.append(target)
        return True


class FakeDetector(DetectorStrategy):
    def __init__(
        self,
        state: ButtonState = ButtonState.sold_out,
        button_text: str = "",
    ) -> None:
        self.state = state
        self.button_text = button_text
        self.calls: list[TargetSpec] = []

    async def detect(self, page: Page, target: TargetSpec) -> CheckResult:
        self.calls.append(target)
        return CheckResult(
            target=target,
            state=self.state,
            button_text=self.button_text,
            reason="fake result",
        )


class RecordingScheduler:
    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.results: list[CheckResult] = []

    def next_delay(self, results: list[CheckResult]) -> float:
        self.results = list(results)
        return self.delay


@pytest.mark.asyncio
async def test_account_watcher_emits_events_for_targets_in_order() -> None:
    targets = [
        TargetSpec(billing_cycle=BillingCycle.monthly, tier=Tier.Lite),
        TargetSpec(billing_cycle=BillingCycle.yearly, tier=Tier.Max),
    ]
    output = StringIO()
    detector = FakeDetector()
    config = AppConfig(targets=targets, max_checks=1)

    code = await AccountWatcher(config, detector=detector, stdout=output).monitor(
        page=object(),
        session=None,
    )

    events = _json_lines(output)
    assert code == 0
    assert detector.calls == targets
    assert [event["type"] for event in events] == ["check", "check", "heartbeat"]
    assert [event["target"] for event in events[:2]] == [
        "连续包月 / Lite",
        "连续包年 / Max",
    ]
    assert events[2]["target"] == "account"
    assert events[2]["button_state"] == "heartbeat"
    assert events[2]["check_index"] == 2


@pytest.mark.asyncio
async def test_account_watcher_emits_heartbeat_for_single_target() -> None:
    output = StringIO()
    config = AppConfig(
        billing_cycle=BillingCycle.quarterly,
        tier=Tier.Pro,
        max_checks=1,
    )

    await AccountWatcher(config, detector=FakeDetector(), stdout=output).monitor(
        page=object(),
        session=None,
    )

    events = _json_lines(output)
    assert events[-1]["type"] == "heartbeat"
    assert events[-1]["target"] == "连续包季 / Pro"
    assert events[-1]["message"] == "max checks reached"


@pytest.mark.asyncio
async def test_account_watcher_uses_scheduler_policy_for_delay() -> None:
    output = StringIO()
    scheduler = RecordingScheduler(delay=42)
    config = AppConfig(max_checks=2)
    watcher = AccountWatcher(
        config,
        detector=FakeDetector(button_text="06月18日 10:00 补货"),
        stdout=output,
        scheduler_policy=scheduler,  # type: ignore[arg-type]
    )

    async def stop_after_emit(_delay: float) -> bool:
        return True

    watcher._sleep_or_stop = stop_after_emit  # type: ignore[method-assign]
    await watcher.monitor(page=object(), session=None)

    events = _json_lines(output)
    assert scheduler.results[0].button_text == "06月18日 10:00 补货"
    assert events[0]["next_refresh_at"] is not None
    assert events[1]["type"] == "heartbeat"


@pytest.mark.asyncio
async def test_account_watcher_honors_target_level_dry_run_on_hit() -> None:
    target = TargetSpec(
        billing_cycle=BillingCycle.monthly,
        tier=Tier.Pro,
        dry_run=True,
    )
    output = StringIO()
    config = AppConfig(targets=[target])

    code = await AccountWatcher(
        config,
        detector=FakeDetector(state=ButtonState.available),
        stdout=output,
    ).monitor(page=object(), session=None)

    events = _json_lines(output)
    assert code == 0
    assert events[0]["type"] == "hit"
    assert events[0]["action"] == "dry_run"


class _FakeSession:
    """无浏览器的最小 session 替身：满足 _handle_hit 对 capture_artifacts 的调用。"""

    async def capture_artifacts(self, kind: str, target: TargetSpec) -> tuple[None, None]:
        return None, None


class _SilentNotifier:
    async def notify_available(self, result: CheckResult, artifacts: Any) -> None:
        return None


def _visible_window_target(dry_run: bool = False) -> TargetSpec:
    return TargetSpec(
        billing_cycle=BillingCycle.monthly,
        tier=Tier.Pro,
        active_window_start="10:00",
        active_window_end="10:30",
        active_timezone="Asia/Shanghai",
        visible_in_window=True,
        dry_run=dry_run,
    )


def _fixed_scheduler(now: datetime) -> SchedulerPolicy:
    return SchedulerPolicy(base_interval_seconds=120, jitter_seconds=0, now_fn=lambda: now)


def test_watcher_desired_headless_flips_inside_visible_window() -> None:
    target = _visible_window_target()
    config = AppConfig(targets=[target], auto_click_entry=False)

    inside = datetime(2026, 6, 17, 10, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
    watcher_inside = AccountWatcher(
        config,
        detector=FakeDetector(),
        stdout=StringIO(),
        scheduler_policy=_fixed_scheduler(inside),
    )
    assert watcher_inside._target_visible_now(target) is True
    assert watcher_inside._desired_headless() is False

    outside = datetime(2026, 6, 17, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    watcher_outside = AccountWatcher(
        config,
        detector=FakeDetector(),
        stdout=StringIO(),
        scheduler_policy=_fixed_scheduler(outside),
    )
    assert watcher_outside._target_visible_now(target) is False
    # 时段外保持 headless（仅检测）。
    assert watcher_outside._desired_headless() is True


def test_watcher_visible_window_disabled_stays_headless() -> None:
    # visible_in_window=False（默认）即便在时段内也保持 headless。
    target = TargetSpec(
        billing_cycle=BillingCycle.monthly,
        tier=Tier.Pro,
        active_window_start="10:00",
        active_window_end="10:30",
        active_timezone="Asia/Shanghai",
        visible_in_window=False,
    )
    inside = datetime(2026, 6, 17, 10, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
    watcher = AccountWatcher(
        AppConfig(targets=[target]),
        detector=FakeDetector(),
        stdout=StringIO(),
        scheduler_policy=_fixed_scheduler(inside),
    )
    assert watcher._target_visible_now(target) is False
    assert watcher._desired_headless() is True


@pytest.mark.asyncio
async def test_watcher_clicks_entry_in_place_inside_visible_window() -> None:
    target = _visible_window_target()
    inside = datetime(2026, 6, 17, 10, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
    detector = ClickRecordingDomDetector(ButtonState.available)
    output = StringIO()
    # config.auto_click_entry=False（与 daemon headless worker 一致），点击仅靠 visible_in_window。
    watcher = AccountWatcher(
        AppConfig(targets=[target], auto_click_entry=False),
        detector=detector,
        notifier=_SilentNotifier(),  # type: ignore[arg-type]
        stdout=output,
        scheduler_policy=_fixed_scheduler(inside),
    )
    watcher._pause_for_manual = _noop_pause  # type: ignore[method-assign]

    await watcher._handle_hit(
        _FakeSession(),  # type: ignore[arg-type]
        page=object(),
        result=CheckResult(target=target, state=ButtonState.available),
        check_index=1,
    )

    assert detector.clicked == [target]
    events = _json_lines(output)
    assert events[0]["type"] == "hit"
    assert events[0]["action"] == "clicked_entry"


@pytest.mark.asyncio
async def test_watcher_dry_run_suppresses_click_even_inside_visible_window() -> None:
    target = _visible_window_target(dry_run=True)
    inside = datetime(2026, 6, 17, 10, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
    detector = ClickRecordingDomDetector(ButtonState.available)
    output = StringIO()
    watcher = AccountWatcher(
        AppConfig(targets=[target], auto_click_entry=False),
        detector=detector,
        notifier=_SilentNotifier(),  # type: ignore[arg-type]
        stdout=output,
        scheduler_policy=_fixed_scheduler(inside),
    )

    # dry_run 下 _target_visible_now 必须为 False（绝不点击）。
    assert watcher._target_visible_now(target) is False

    await watcher._handle_hit(
        _FakeSession(),  # type: ignore[arg-type]
        page=object(),
        result=CheckResult(target=target, state=ButtonState.available),
        check_index=1,
    )

    assert detector.clicked == []
    events = _json_lines(output)
    assert events[0]["type"] == "hit"
    assert events[0]["action"] == "dry_run"


async def _noop_pause() -> None:
    return None


def _json_lines(output: StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.getvalue().splitlines()]
