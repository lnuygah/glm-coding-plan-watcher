"""Safe account-level scheduling policy."""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from glm_plan_watcher.models import ButtonState, CheckResult

MIN_INTERVAL_SECONDS = 30.0
MIN_JITTER_SECONDS = 5.0

_RESTOCK_RE = re.compile(
    r"(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日\s*"
    r"(?P<hour>\d{1,2})\s*:\s*(?P<minute>\d{2})\s*补货"
)


def parse_restock_datetime(text: str, now: datetime | None = None) -> datetime | None:
    """Parse Chinese restock text into a candidate datetime.

    The page only exposes month/day/hour/minute, so the current year is assumed.
    If that candidate is clearly behind the current date, it is treated as a
    cross-year hint. Recent past values stay in the current year so the caller
    can tighten to the minimum interval instead of blindly waiting a year.
    """

    match = _RESTOCK_RE.search(text)
    if match is None:
        return None

    base = now or datetime.now(UTC)
    tzinfo = base.tzinfo
    try:
        candidate = datetime(
            year=base.year,
            month=int(match.group("month")),
            day=int(match.group("day")),
            hour=int(match.group("hour")),
            minute=int(match.group("minute")),
            tzinfo=tzinfo,
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
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC)
    random_fn: Callable[[float, float], float] = random.uniform

    def next_delay(self, results: Sequence[CheckResult] = ()) -> float:
        """Return next delay in seconds, clamped to the safe minimum."""

        base = self._base_delay_from_restock_hints(results)
        if base is None:
            base = self.base_interval_seconds

        jittered = base + self._jitter()
        return max(self.min_interval_seconds, jittered)

    def _base_delay_from_restock_hints(self, results: Sequence[CheckResult]) -> float | None:
        now = self.now_fn()
        hints = [
            hint
            for result in results
            if result.state is ButtonState.sold_out
            for hint in [parse_restock_datetime(result.button_text, now=now)]
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
