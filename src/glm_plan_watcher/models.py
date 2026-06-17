"""领域模型与枚举。

这些类型不依赖 Playwright，便于在纯函数单测中复用。
- :class:`BillingCycle` / :class:`Tier`：目标维度。
- :class:`ButtonState`：购买按钮的判定结果。
- :class:`CheckResult`：一次检测的结构化结果。
- :class:`WatchEvent`：watch 循环写到 stdout 的 JSON 行 schema（父进程契约）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class BillingCycle(StrEnum):
    """计费周期。值与页面中文标签一一对应。"""

    monthly = "monthly"
    quarterly = "quarterly"
    yearly = "yearly"

    @property
    def cn_label(self) -> str:
        """页面 tab 上显示的中文文本。"""
        return {
            BillingCycle.monthly: "连续包月",
            BillingCycle.quarterly: "连续包季",
            BillingCycle.yearly: "连续包年",
        }[self]


class Tier(StrEnum):
    """套餐档位。值与页面卡片标题一致（大小写敏感）。"""

    Lite = "Lite"
    Pro = "Pro"
    Max = "Max"


class ButtonState(StrEnum):
    """购买按钮状态判定。"""

    available = "available"  # 可购买/可订阅
    sold_out = "sold_out"  # 售罄/补货
    disabled = "disabled"  # disabled / aria-disabled / class disabled
    unavailable = "unavailable"  # 其它不可用文案（已订阅/敬请期待等）
    auth_required = "auth_required"  # 未登录 / 登录墙，需要用户重新登录
    not_found = "not_found"  # 未定位到按钮

    @property
    def is_available(self) -> bool:
        return self is ButtonState.available


class TargetSpec(BaseModel):
    """一次监控的目标：周期 × 档位。"""

    billing_cycle: BillingCycle
    tier: Tier

    def describe(self) -> str:
        return f"{self.billing_cycle.cn_label} / {self.tier.value}"


class CheckResult(BaseModel):
    """单次检测的结构化结果。"""

    target: TargetSpec
    state: ButtonState
    button_text: str = ""
    reason: str = ""  # 判定依据，便于排障
    # 原始属性快照（disabled/aria-disabled/class/visible 等），用于调试与校准
    attrs: dict[str, str] = Field(default_factory=dict)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def available(self) -> bool:
        return self.state.is_available


class WatchEvent(BaseModel):
    """watch 循环每轮输出到 stdout 的单行 JSON（供 FastAPI 父进程消费）。

    字段保持稳定，作为父子进程之间的契约。
    """

    type: str = "check"  # check | hit | error | heartbeat | shutdown
    check_index: int
    target: str  # TargetSpec.describe()
    button_state: str
    button_text: str = ""
    action: str = "none"  # none | wait | clicked_entry | paused | dry_run
    available: bool = False
    next_refresh_at: datetime | None = None
    message: str = ""
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_json_line(self) -> str:
        """序列化为一行 JSON（不含换行）。"""
        return self.model_dump_json()
