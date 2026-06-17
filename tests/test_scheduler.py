from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from glm_plan_watcher.models import BillingCycle, ButtonState, CheckResult, TargetSpec, Tier
from glm_plan_watcher.scheduler import MIN_INTERVAL_SECONDS, SchedulerPolicy, parse_restock_datetime

TARGET = TargetSpec(billing_cycle=BillingCycle.monthly, tier=Tier.Pro)


def result(text: str, state: ButtonState = ButtonState.sold_out) -> CheckResult:
    return CheckResult(target=TARGET, state=state, button_text=text)


def test_parse_restock_datetime() -> None:
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    parsed = parse_restock_datetime("暂时售罄 ｜06月18日 10:00 补货", now=now)

    assert parsed == datetime(2026, 6, 18, 10, 0, tzinfo=UTC)


def test_parse_restock_datetime_invalid_text_returns_none() -> None:
    now = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

    assert parse_restock_datetime("暂时售罄，稍后再来", now=now) is None


def test_parse_restock_datetime_cross_year() -> None:
    now = datetime(2026, 12, 31, 20, 0, tzinfo=UTC)

    parsed = parse_restock_datetime("01月01日 10:00 补货", now=now)

    assert parsed == datetime(2027, 1, 1, 10, 0, tzinfo=UTC)


def test_scheduler_near_hint_clamps_to_minimum() -> None:
    now = datetime(2026, 6, 18, 9, 55, tzinfo=UTC)
    policy = SchedulerPolicy(
        base_interval_seconds=90,
        jitter_seconds=10,
        now_fn=lambda: now,
        random_fn=lambda _low, _high: -10,
    )

    assert policy.next_delay([result("06月18日 10:00 补货")]) == MIN_INTERVAL_SECONDS


def test_scheduler_far_hint_relaxes_but_keeps_jitter() -> None:
    now = datetime(2026, 6, 17, 10, 0, tzinfo=UTC)
    policy = SchedulerPolicy(
        base_interval_seconds=90,
        jitter_seconds=5,
        now_fn=lambda: now,
        random_fn=lambda _low, _high: 5,
    )

    assert policy.next_delay([result("06月19日 10:00 补货")]) == 3605


def test_scheduler_mid_hint_does_not_poll_below_minimum() -> None:
    now = datetime(2026, 6, 18, 8, 0, tzinfo=UTC)
    policy = SchedulerPolicy(
        base_interval_seconds=10,
        jitter_seconds=0,
        now_fn=lambda: now,
    )

    assert policy.next_delay([result("06月18日 10:00 补货")]) == MIN_INTERVAL_SECONDS


def test_scheduler_no_hint_falls_back_to_interval_with_jitter() -> None:
    policy = SchedulerPolicy(
        base_interval_seconds=120,
        jitter_seconds=15,
        random_fn=lambda _low, _high: -5,
    )

    assert policy.next_delay([result("已售罄，暂无时间")]) == 115


def test_scheduler_ignores_non_sold_out_hints() -> None:
    policy = SchedulerPolicy(
        base_interval_seconds=120,
        jitter_seconds=0,
    )

    assert policy.next_delay([result("06月18日 10:00 补货", ButtonState.disabled)]) == 120


def test_scheduler_active_window_uses_fast_interval() -> None:
    now = datetime(2026, 6, 17, 10, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
    policy = SchedulerPolicy(
        base_interval_seconds=120,
        jitter_seconds=0,
        active_window_start="10:00",
        active_window_end="10:30",
        active_timezone="Asia/Shanghai",
        active_interval_seconds=3,
        active_jitter_seconds=0,
        now_fn=lambda: now,
    )

    assert policy.next_delay([]) == 3


def test_scheduler_active_window_clamps_too_low_interval_and_jitter() -> None:
    now = datetime(2026, 6, 17, 10, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
    policy = SchedulerPolicy(
        base_interval_seconds=120,
        jitter_seconds=0,
        active_window_start="10:00",
        active_window_end="10:30",
        active_timezone="Asia/Shanghai",
        active_interval_seconds=0.1,
        active_jitter_seconds=2,
        now_fn=lambda: now,
        random_fn=lambda _low, _high: -2,
    )

    assert policy.next_delay([]) == 1


def test_scheduler_outside_active_window_uses_idle_or_next_window() -> None:
    now = datetime(2026, 6, 17, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    policy = SchedulerPolicy(
        base_interval_seconds=120,
        jitter_seconds=0,
        active_window_start="10:00",
        active_window_end="10:30",
        active_timezone="Asia/Shanghai",
        idle_interval_seconds=600,
        active_jitter_seconds=0,
        now_fn=lambda: now,
    )

    assert policy.next_delay([]) == 600

    near_start = datetime(2026, 6, 17, 9, 59, 55, tzinfo=ZoneInfo("Asia/Shanghai"))
    policy.now_fn = lambda: near_start
    assert policy.next_delay([]) == 5


def test_scheduler_outside_active_window_clamps_too_low_idle_interval() -> None:
    now = datetime(2026, 6, 17, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    policy = SchedulerPolicy(
        base_interval_seconds=120,
        jitter_seconds=0,
        active_window_start="10:00",
        active_window_end="10:30",
        active_timezone="Asia/Shanghai",
        idle_interval_seconds=1,
        active_jitter_seconds=0,
        now_fn=lambda: now,
    )

    assert policy.next_delay([]) == MIN_INTERVAL_SECONDS


def test_scheduler_active_window_supports_cross_midnight() -> None:
    now = datetime(2026, 6, 18, 0, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    policy = SchedulerPolicy(
        base_interval_seconds=120,
        jitter_seconds=0,
        active_window_start="23:00",
        active_window_end="01:00",
        active_timezone="Asia/Shanghai",
        active_interval_seconds=3,
        active_jitter_seconds=0,
        now_fn=lambda: now,
    )

    assert policy.next_delay([]) == 3


def test_scheduler_active_window_uses_explicit_timezone() -> None:
    now = datetime(2026, 6, 17, 2, 5, tzinfo=UTC)
    policy = SchedulerPolicy(
        base_interval_seconds=120,
        jitter_seconds=0,
        active_window_start="10:00",
        active_window_end="10:30",
        active_timezone="Asia/Shanghai",
        active_interval_seconds=3,
        active_jitter_seconds=0,
        now_fn=lambda: now,
    )

    assert policy.next_delay([]) == 3


def test_scheduler_in_active_window_predicate() -> None:
    inside = datetime(2026, 6, 17, 10, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
    outside = datetime(2026, 6, 17, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    policy = SchedulerPolicy(base_interval_seconds=120, jitter_seconds=0)

    assert (
        policy.in_active_window(
            start_text="10:00",
            end_text="10:30",
            timezone_name="Asia/Shanghai",
            now=inside,
        )
        is True
    )
    assert (
        policy.in_active_window(
            start_text="10:00",
            end_text="10:30",
            timezone_name="Asia/Shanghai",
            now=outside,
        )
        is False
    )
    # 未配置/无法解析的时段视为「永不在窗口内」。
    assert (
        policy.in_active_window(start_text="", end_text="", timezone_name="", now=inside) is False
    )


def test_scheduler_in_active_window_cross_midnight() -> None:
    policy = SchedulerPolicy(base_interval_seconds=120, jitter_seconds=0)
    now = datetime(2026, 6, 18, 0, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    assert (
        policy.in_active_window(
            start_text="23:00",
            end_text="01:00",
            timezone_name="Asia/Shanghai",
            now=now,
        )
        is True
    )


def test_scheduler_uses_target_specific_active_window() -> None:
    now = datetime(2026, 6, 17, 10, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
    target = TargetSpec(
        billing_cycle=BillingCycle.monthly,
        tier=Tier.Pro,
        active_window_start="10:00",
        active_window_end="10:30",
        active_timezone="Asia/Shanghai",
        active_interval_seconds=4,
        active_jitter_seconds=0,
    )
    policy = SchedulerPolicy(
        base_interval_seconds=120,
        jitter_seconds=0,
        now_fn=lambda: now,
    )

    assert policy.next_delay([CheckResult(target=target, state=ButtonState.sold_out)]) == 4
