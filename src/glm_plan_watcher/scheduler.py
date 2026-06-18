"""Safe account-level scheduling policy."""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from glm_plan_watcher.models import ButtonState, CheckResult

MIN_INTERVAL_SECONDS = 30.0
MIN_JITTER_SECONDS = 5.0
ACTIVE_MIN_INTERVAL_SECONDS = 1.0
DEFAULT_ACTIVE_INTERVAL_SECONDS = 3.0
DEFAULT_ACTIVE_JITTER_SECONDS = 1.0
DEFAULT_IDLE_INTERVAL_SECONDS = 600.0
DEFAULT_RESTOCK_TIMEZONE = "Asia/Shanghai"

_HHMM_RE = re.compile(r"^(?P<hour>\d{1,2}):(?P<minute>\d{2})$")

_RESTOCK_RE = re.compile(
    r"(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日\s*"
    r"(?P<hour>\d{1,2})\s*:\s*(?P<minute>\d{2})\s*补货"
)


def parse_restock_datetime(
    text: str,
    now: datetime | None = None,
    timezone_name: str = DEFAULT_RESTOCK_TIMEZONE,
) -> datetime | None:
    """Parse Chinese restock text into a candidate datetime.

    The page only exposes month/day/hour/minute, so the current year is assumed.
    If that candidate is clearly behind the current date, it is treated as a
    cross-year hint. Recent past values stay in the current year so the caller
    can tighten to the minimum interval instead of blindly waiting a year.

    Restock copy comes from bigmodel.cn's Chinese page, so an empty timezone
    falls back to the site timezone (Asia/Shanghai) instead of the process/UTC
    timezone used by the worker scheduler.
    """

    match = _RESTOCK_RE.search(text)
    if match is None:
        return None

    zone = _resolve_restock_timezone(timezone_name)
    current = now or datetime.now(UTC)
    base = current.replace(tzinfo=zone) if current.tzinfo is None else current.astimezone(zone)
    try:
        candidate = datetime(
            year=base.year,
            month=int(match.group("month")),
            day=int(match.group("day")),
            hour=int(match.group("hour")),
            minute=int(match.group("minute")),
            tzinfo=zone,
        )
    except ValueError:
        return None

    if candidate < base - timedelta(days=1):
        try:
            candidate = candidate.replace(year=base.year + 1)
        except ValueError:
            return None
    return candidate


@dataclass
class SchedulerPolicy:
    """Compute the next account-level delay without aggressive polling."""

    base_interval_seconds: float
    jitter_seconds: float
    min_interval_seconds: float = MIN_INTERVAL_SECONDS
    near_window_seconds: float = 10 * 60.0
    far_window_seconds: float = 6 * 60 * 60.0
    max_hint_interval_seconds: float = 60 * 60.0
    active_window_start: str = ""
    active_window_end: str = ""
    active_timezone: str = ""
    active_interval_seconds: float = DEFAULT_ACTIVE_INTERVAL_SECONDS
    active_jitter_seconds: float = DEFAULT_ACTIVE_JITTER_SECONDS
    idle_interval_seconds: float = DEFAULT_IDLE_INTERVAL_SECONDS
    active_min_interval_seconds: float = ACTIVE_MIN_INTERVAL_SECONDS
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC)
    random_fn: Callable[[float, float], float] = random.uniform

    def next_delay(self, results: Sequence[CheckResult] = ()) -> float:
        """Return next delay in seconds, clamped to the safe minimum."""

        active_delay = self._active_window_delay(results)
        if active_delay is not None:
            return active_delay

        base = self._base_delay_from_restock_hints(
            results,
            timezone_name=self.active_timezone,
            prefer_target_timezone=True,
        )
        if base is None:
            base = self.base_interval_seconds

        jittered = base + self._jitter()
        return max(self.min_interval_seconds, jittered)

    def in_active_window(
        self,
        *,
        start_text: str,
        end_text: str,
        timezone_name: str,
        now: datetime | None = None,
    ) -> bool:
        """Pure predicate: is ``now`` inside the configured sale window?

        Returns ``False`` when start/end are unset or unparseable, so callers can treat an
        absent window as "never in window". This lets the worker detect window boundaries with
        the exact same timezone-aware, cross-midnight logic the cadence uses.
        """

        start = _parse_hhmm(start_text)
        end = _parse_hhmm(end_text)
        if start is None or end is None:
            return False
        local_now = _localize_now(now or self.now_fn(), timezone_name)
        return _in_window(local_now.time(), start, end)

    def _base_delay_from_restock_hints(
        self,
        results: Sequence[CheckResult],
        *,
        timezone_name: str = "",
        prefer_target_timezone: bool = True,
    ) -> float | None:
        now = self.now_fn()
        hints = [
            hint
            for result in results
            if result.state is ButtonState.sold_out
            for hint in [
                parse_restock_datetime(
                    result.button_text,
                    now=now,
                    timezone_name=(
                        result.target.active_timezone
                        if prefer_target_timezone and result.target.active_timezone.strip()
                        else timezone_name
                    ),
                )
            ]
            if hint is not None
        ]
        if not hints:
            return None

        hint = min(hints, key=lambda candidate: abs((candidate - now).total_seconds()))
        delta_seconds = (hint - now).total_seconds()
        if delta_seconds <= self.near_window_seconds:
            return self.min_interval_seconds

        if delta_seconds <= self.far_window_seconds:
            return max(self.min_interval_seconds, min(self.base_interval_seconds, delta_seconds / 6.0))

        relaxed = max(self.base_interval_seconds, delta_seconds / 8.0)
        return min(self.max_hint_interval_seconds, relaxed)

    def _jitter(self) -> float:
        if self.jitter_seconds <= 0:
            return 0.0
        return self.random_fn(-self.jitter_seconds, self.jitter_seconds)

    def _active_window_delay(self, results: Sequence[CheckResult]) -> float | None:
        """Return active-window cadence if any configured window applies.

        Active-window fast polling is opt-in and clamped to a separate hard minimum. Very low
        intervals can trigger site rate limits/risk controls and reduce the chance of purchase, so
        the minimum deliberately never reaches zero.
        """

        now = self.now_fn()
        candidates: list[float] = []
        policy_candidate = self._delay_from_window_values(
            start_text=self.active_window_start,
            end_text=self.active_window_end,
            timezone_name=self.active_timezone,
            active_interval_seconds=self.active_interval_seconds,
            active_jitter_seconds=self.active_jitter_seconds,
            idle_interval_seconds=self.idle_interval_seconds,
            hint_delay=self._base_delay_from_restock_hints(
                results,
                timezone_name=self.active_timezone,
                prefer_target_timezone=False,
            ),
            now=now,
        )
        if policy_candidate is not None:
            candidates.append(policy_candidate)

        for result in results:
            target = result.target
            target_candidate = self._delay_from_window_values(
                start_text=target.active_window_start,
                end_text=target.active_window_end,
                timezone_name=target.active_timezone,
                active_interval_seconds=target.active_interval_seconds,
                active_jitter_seconds=target.active_jitter_seconds,
                idle_interval_seconds=target.idle_interval_seconds,
                hint_delay=self._base_delay_from_restock_hints(
                    [result],
                    timezone_name=target.active_timezone,
                    prefer_target_timezone=True,
                ),
                now=now,
            )
            if target_candidate is not None:
                candidates.append(target_candidate)

        return min(candidates) if candidates else None

    def _delay_from_window_values(
        self,
        *,
        start_text: str,
        end_text: str,
        timezone_name: str,
        active_interval_seconds: float,
        active_jitter_seconds: float,
        idle_interval_seconds: float,
        hint_delay: float | None,
        now: datetime,
    ) -> float | None:
        start = _parse_hhmm(start_text)
        end = _parse_hhmm(end_text)
        if start is None or end is None:
            return None

        local_now = _localize_now(now, timezone_name)
        if _in_window(local_now.time(), start, end):
            base = max(self.active_min_interval_seconds, active_interval_seconds)
            lower_bound = self.active_min_interval_seconds
        else:
            seconds_to_start = _seconds_until_next_start(local_now, start, end)
            base = min(
                max(self.min_interval_seconds, idle_interval_seconds),
                seconds_to_start,
            )
            lower_bound = max(
                self.active_min_interval_seconds,
                min(self.min_interval_seconds, seconds_to_start),
            )

        if hint_delay is not None:
            base = min(base, hint_delay)

        jittered = base + _bounded_jitter(active_jitter_seconds, self.random_fn)
        return max(lower_bound, jittered)


def _parse_hhmm(value: str) -> time | None:
    match = _HHMM_RE.match(value.strip())
    if match is None:
        return None
    try:
        return time(hour=int(match.group("hour")), minute=int(match.group("minute")))
    except ValueError:
        return None


def _localize_now(now: datetime, timezone_name: str) -> datetime:
    zone = _resolve_timezone(timezone_name)
    if now.tzinfo is None:
        return now.replace(tzinfo=zone)
    return now.astimezone(zone)


def _resolve_timezone(timezone_name: str) -> tzinfo:
    if timezone_name.strip():
        try:
            return ZoneInfo(timezone_name.strip())
        except ZoneInfoNotFoundError:
            return datetime.now().astimezone().tzinfo or UTC
    return datetime.now().astimezone().tzinfo or UTC


def _resolve_restock_timezone(timezone_name: str) -> tzinfo:
    name = timezone_name.strip() or DEFAULT_RESTOCK_TIMEZONE
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_RESTOCK_TIMEZONE)


def _in_window(current: time, start: time, end: time) -> bool:
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


def _seconds_until_next_start(now: datetime, start: time, end: time) -> float:
    today_start = now.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    if start == end:
        return 0.0
    if _in_window(now.time(), start, end):
        return 0.0
    if start < end:
        next_start = today_start if now < today_start else today_start + timedelta(days=1)
        return max(0.0, (next_start - now).total_seconds())

    # Cross-midnight window, e.g. 23:00-01:00. Outside means after end and before start.
    next_start = today_start if now < today_start else today_start + timedelta(days=1)
    return max(0.0, (next_start - now).total_seconds())


def _bounded_jitter(
    jitter_seconds: float,
    random_fn: Callable[[float, float], float],
) -> float:
    if jitter_seconds <= 0:
        return 0.0
    return random_fn(-jitter_seconds, jitter_seconds)
