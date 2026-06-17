"""套餐可购买性检测。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass

from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from glm_plan_watcher.models import ButtonState, CheckResult, TargetSpec
from glm_plan_watcher.selectors import (
    AVAILABLE_KEYWORDS,
    BILLING_CYCLE_LABELS,
    CSS_BUY_BUTTON,
    CSS_FALLBACK_BUTTON,
    CSS_PACKAGE_CARD,
    CSS_PACKAGE_CARD_BOX,
    CSS_PACKAGE_LIST,
    CSS_PACKAGE_TITLE,
    CSS_SWITCH_TAB_ACTIVE_CLASS,
    CSS_SWITCH_TAB_BOX,
    CSS_SWITCH_TAB_ITEM,
    DISABLED_CLASS_TOKENS,
    SOLD_OUT_KEYWORDS,
    UNAVAILABLE_KEYWORDS,
)


@dataclass(frozen=True)
class ButtonClassification:
    """纯函数按钮判定结果。"""

    state: ButtonState
    reason: str


class DetectorStrategy(ABC):
    """检测策略接口。"""

    @abstractmethod
    async def detect(self, page: Page, target: TargetSpec) -> CheckResult:
        """检测目标套餐当前按钮状态。"""


def classify_button(text: str, attrs: Mapping[str, str]) -> ButtonClassification:
    """根据按钮文本和属性快照判定状态，不依赖浏览器。"""

    normalized_text = " ".join(text.split())
    lowered_text = normalized_text.lower()
    class_tokens = set((attrs.get("class") or "").lower().replace("|", " ").split())

    if _contains_any(normalized_text, SOLD_OUT_KEYWORDS):
        return ButtonClassification(ButtonState.sold_out, "button text contains sold-out keyword")

    if _contains_any(normalized_text, UNAVAILABLE_KEYWORDS):
        return ButtonClassification(
            ButtonState.unavailable,
            "button text contains unavailable keyword",
        )

    if _is_truthy_attr(attrs.get("disabled")):
        return ButtonClassification(ButtonState.disabled, "button has disabled attribute")

    if (attrs.get("aria-disabled") or "").lower() == "true":
        return ButtonClassification(ButtonState.disabled, "button has aria-disabled=true")

    if class_tokens.intersection(DISABLED_CLASS_TOKENS):
        return ButtonClassification(ButtonState.disabled, "button class contains disabled token")

    if (attrs.get("visible") or "true").lower() == "false":
        return ButtonClassification(ButtonState.disabled, "button is not visible")

    if (attrs.get("enabled") or "true").lower() == "false":
        return ButtonClassification(ButtonState.disabled, "button is not enabled")

    if any(keyword.lower() in lowered_text for keyword in AVAILABLE_KEYWORDS):
        return ButtonClassification(ButtonState.available, "button text contains available keyword")

    if not normalized_text:
        return ButtonClassification(ButtonState.unavailable, "button text is empty")

    return ButtonClassification(ButtonState.unavailable, "button text did not match known keywords")


class DomDetector(DetectorStrategy):
    """基于真实 DOM 的三段定位检测。"""

    async def detect(self, page: Page, target: TargetSpec) -> CheckResult:
        await self.ensure_billing_cycle(page, target)
        card = await self.find_tier_card(page, target)
        if card is None:
            return CheckResult(
                target=target,
                state=ButtonState.not_found,
                reason=f"tier card not found: {target.tier.value}",
            )

        button = await self.find_entry_button(card)
        if button is None:
            return CheckResult(
                target=target,
                state=ButtonState.not_found,
                reason=f"entry button not found: {target.tier.value}",
            )

        text = await _safe_inner_text(button)
        attrs = await _collect_button_attrs(button)
        classification = classify_button(text, attrs)
        return CheckResult(
            target=target,
            state=classification.state,
            button_text=text,
            reason=classification.reason,
            attrs=attrs,
        )

    async def ensure_billing_cycle(self, page: Page, target: TargetSpec) -> None:
        """切到目标计费周期；fixture 或页面缺 tab 时保持当前页面。"""

        label = BILLING_CYCLE_LABELS[target.billing_cycle]
        tab_box = page.locator(CSS_SWITCH_TAB_BOX)

        if await tab_box.count() > 0:
            tab = tab_box.locator(CSS_SWITCH_TAB_ITEM).filter(has_text=label).first
            if await tab.count() == 0:
                return
            class_name = await tab.get_attribute("class") or ""
            if CSS_SWITCH_TAB_ACTIVE_CLASS not in class_name.split():
                await tab.click()
                await _wait_for_tab_render(page)
            return

        fallback = page.get_by_text(label, exact=True).first
        if await fallback.count() > 0:
            await fallback.click()
            await _wait_for_tab_render(page)

    async def find_tier_card(self, page: Page, target: TargetSpec) -> Locator | None:
        """在套餐列表内按卡片标题精确定位目标卡。"""

        package_list = page.locator(CSS_PACKAGE_LIST).first
        if await package_list.count() == 0:
            return None

        cards = package_list.locator(CSS_PACKAGE_CARD_BOX)
        for index in range(await cards.count()):
            card = cards.nth(index)
            title = card.locator(CSS_PACKAGE_TITLE).first
            if await title.count() == 0:
                continue
            if (await _safe_inner_text(title)).strip() == target.tier.value:
                return card

        fallback = package_list.locator(
            f"{CSS_PACKAGE_CARD}:has(.package-card-title:has-text('{target.tier.value}'))"
        ).first
        if await fallback.count() > 0:
            return fallback
        return None

    async def find_entry_button(self, card: Locator) -> Locator | None:
        """在卡片作用域内定位购买/订阅入口按钮。"""

        button = card.locator(CSS_BUY_BUTTON).first
        if await button.count() > 0:
            return button

        fallback = card.locator(CSS_FALLBACK_BUTTON).first
        if await fallback.count() > 0:
            return fallback
        return None

    async def click_entry_button(self, page: Page, target: TargetSpec) -> bool:
        """点击目标套餐入口按钮；只点入口，不处理支付或风控流程。"""

        await self.ensure_billing_cycle(page, target)
        card = await self.find_tier_card(page, target)
        if card is None:
            return False
        button = await self.find_entry_button(card)
        if button is None:
            return False
        await button.click()
        return True

    async def debug_snapshot(self, page: Page) -> dict[str, object]:
        """输出页面关键 selector 的文本和属性，供 debug-selectors 使用。"""

        tabs: list[dict[str, str]] = []
        for index in range(await page.locator(CSS_SWITCH_TAB_ITEM).count()):
            tab = page.locator(CSS_SWITCH_TAB_ITEM).nth(index)
            tabs.append(
                {
                    "text": await _safe_inner_text(tab),
                    "class": await tab.get_attribute("class") or "",
                }
            )

        cards: list[dict[str, str]] = []
        for index in range(await page.locator(CSS_PACKAGE_CARD_BOX).count()):
            card = page.locator(CSS_PACKAGE_CARD_BOX).nth(index)
            title = card.locator(CSS_PACKAGE_TITLE).first
            button = await self.find_entry_button(card)
            attrs = await _collect_button_attrs(button) if button is not None else {}
            cards.append(
                {
                    "title": await _safe_inner_text(title) if await title.count() else "",
                    "button_text": await _safe_inner_text(button) if button is not None else "",
                    "button_attrs": str(attrs),
                }
            )

        return {"tabs": tabs, "cards": cards}


class ApiDetector(DetectorStrategy):
    """预留 API 检测接口。

    当前不实现后端库存/下单接口轮询，避免越过公开页面与合规边界。后续若官方提供稳定、
    合规的查询接口，可在这里实现与 DomDetector 相同的 CheckResult 输出契约。
    """

    async def detect(self, page: Page, target: TargetSpec) -> CheckResult:
        raise NotImplementedError("ApiDetector is reserved for a compliant official API")


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _is_truthy_attr(value: str | None) -> bool:
    if value is None:
        return False
    return value.lower() not in {"false", "0"}


async def _collect_button_attrs(button: Locator) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for name in ("disabled", "aria-disabled", "class", "name", "role"):
        value = await button.get_attribute(name)
        if value is not None:
            attrs[name] = value
    attrs["visible"] = str(await button.is_visible()).lower()
    attrs["enabled"] = str(await button.is_enabled()).lower()
    return attrs


async def _safe_inner_text(locator: Locator | None) -> str:
    if locator is None:
        return ""
    try:
        return " ".join((await locator.inner_text(timeout=1_000)).split())
    except PlaywrightTimeoutError:
        return ""


async def _wait_for_tab_render(page: Page) -> None:
    with suppress(PlaywrightTimeoutError):
        await page.wait_for_load_state("networkidle", timeout=5_000)
    await page.wait_for_timeout(500)
