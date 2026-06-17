from __future__ import annotations

import json
from io import StringIO
from typing import Any

import pytest
from playwright.async_api import Page

from glm_plan_watcher.config import AppConfig
from glm_plan_watcher.detector import DetectorStrategy
from glm_plan_watcher.models import BillingCycle, ButtonState, CheckResult, TargetSpec, Tier
from glm_plan_watcher.watcher import AccountWatcher


class FakeDetector(DetectorStrategy):
    def __init__(self, state: ButtonState = ButtonState.sold_out) -> None:
        self.state = state
        self.calls: list[TargetSpec] = []

    async def detect(self, page: Page, target: TargetSpec) -> CheckResult:
        self.calls.append(target)
        return CheckResult(target=target, state=self.state, reason="fake result")


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


def _json_lines(output: StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.getvalue().splitlines()]
