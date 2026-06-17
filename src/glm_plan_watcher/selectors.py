"""页面选择器与文案词表集中维护。"""

from __future__ import annotations

from glm_plan_watcher.models import BillingCycle

CSS_SWITCH_TAB_BOX = "#switchTabBox"
CSS_SWITCH_TAB_ITEM = ".switch-tab-item"
CSS_SWITCH_TAB_ACTIVE_CLASS = "active"

CSS_PACKAGE_LIST = ".package-list"
CSS_PACKAGE_CARD_BOX = ".package-card-box"
CSS_PACKAGE_CARD = ".package-card"
CSS_PACKAGE_TITLE = ".package-card-title span.font-prompt"
CSS_BUY_BUTTON = "button.buy-btn"
CSS_FALLBACK_BUTTON = "button, a[role='button']"

BILLING_CYCLE_LABELS: dict[BillingCycle, str] = {
    BillingCycle.monthly: "连续包月",
    BillingCycle.quarterly: "连续包季",
    BillingCycle.yearly: "连续包年",
}

SOLD_OUT_KEYWORDS = (
    "售罄",
    "补货",
)

UNAVAILABLE_KEYWORDS = (
    "暂不可用",
    "暂不可购买",
    "已订阅",
    "不可购买",
    "敬请期待",
    "即将上线",
)

AVAILABLE_KEYWORDS = (
    "购买",
    "立即购买",
    "订阅",
    "立即订阅",
    "升级",
    "续费",
    "开通",
)

DISABLED_CLASS_TOKENS = (
    "disabled",
    "is-disabled",
)
