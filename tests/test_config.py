from __future__ import annotations

from pathlib import Path

import pytest

from glm_plan_watcher.config import dump_default_yaml, load_config
from glm_plan_watcher.models import BillingCycle, TargetSpec, Tier


def test_load_config_yaml(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
billing_cycle: yearly
tier: Max
refresh_interval_seconds: 10
active_window_start: "10:00"
active_window_end: "10:30"
active_timezone: "Asia/Shanghai"
active_interval_seconds: 3
active_jitter_seconds: 1
idle_interval_seconds: 600
notify:
  console: false
  webhook_url: "https://example.test/hook"
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.billing_cycle is BillingCycle.yearly
    assert config.tier is Tier.Max
    assert config.refresh_interval_seconds == 10
    assert config.active_window_start == "10:00"
    assert config.notify.console is False
    assert config.notify.webhook_url == "https://example.test/hook"
    assert config.target_specs == [
        TargetSpec(
            billing_cycle=BillingCycle.yearly,
            tier=Tier.Max,
            active_window_start="10:00",
            active_window_end="10:30",
            active_timezone="Asia/Shanghai",
            active_interval_seconds=3,
            active_jitter_seconds=1,
            idle_interval_seconds=600,
        ),
    ]


def test_load_config_targets_list(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
billing_cycle: yearly
tier: Max
targets:
  - billing_cycle: monthly
    tier: Lite
    active_window_start: "10:00"
    active_window_end: "10:30"
  - billing_cycle: quarterly
    tier: Pro
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.target_specs == [
        TargetSpec(
            billing_cycle=BillingCycle.monthly,
            tier=Tier.Lite,
            active_window_start="10:00",
            active_window_end="10:30",
        ),
        TargetSpec(billing_cycle=BillingCycle.quarterly, tier=Tier.Pro),
    ]


def test_env_overrides_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("tier: Lite\nnotify:\n  webhook_url: https://yaml.example/hook\n", encoding="utf-8")
    monkeypatch.setenv("GLM_WATCHER__TIER", "Pro")
    monkeypatch.setenv("GLM_WATCHER__NOTIFY__WEBHOOK_URL", "https://env.example/hook")

    config = load_config(path)

    assert config.tier is Tier.Pro
    assert config.notify.webhook_url == "https://env.example/hook"


def test_dump_default_yaml_contains_safety_defaults() -> None:
    text = dump_default_yaml()

    assert "auto_click_entry: true" in text
    assert "dry_run: false" in text
    assert "active_window_start" in text
    assert "最终支付必须人工确认" in text
