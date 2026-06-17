from __future__ import annotations

from pathlib import Path

import pytest
from playwright.async_api import Page

from glm_plan_watcher.detector import DomDetector, classify_button, looks_like_auth_required
from glm_plan_watcher.models import BillingCycle, ButtonState, TargetSpec, Tier
from glm_plan_watcher.site_adapter import GlmSiteAdapter

FIXTURES_DIR = Path(__file__).parent / "fixtures"


async def load_fixture(page: Page, name: str) -> None:
    await page.set_content((FIXTURES_DIR / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("text", "attrs", "expected"),
    [
        ("立即订阅", {"visible": "true", "enabled": "true"}, ButtonState.available),
        ("立即购买", {"visible": "true", "enabled": "true"}, ButtonState.available),
        ("暂时售罄 ｜06月18日 10:00 补货", {"disabled": "disabled"}, ButtonState.sold_out),
        ("处理中", {"disabled": "disabled"}, ButtonState.disabled),
        ("处理中", {"aria-disabled": "true"}, ButtonState.disabled),
        ("处理中", {"class": "el-button is-disabled disabled"}, ButtonState.disabled),
        ("敬请期待", {"visible": "true", "enabled": "true"}, ButtonState.unavailable),
        ("", {"visible": "true", "enabled": "true"}, ButtonState.unavailable),
    ],
)
def test_classify_button(text: str, attrs: dict[str, str], expected: ButtonState) -> None:
    assert classify_button(text, attrs).state is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("请登录后继续", True),
        ("登录 / 注册", True),
        ("套餐卡片正常展示", False),
    ],
)
def test_looks_like_auth_required(text: str, expected: bool) -> None:
    assert looks_like_auth_required(text) is expected


@pytest.mark.browser
async def test_dom_detector_available(page) -> None:
    await load_fixture(page, "available.html")

    result = await DomDetector(adapter=GlmSiteAdapter()).detect(
        page,
        TargetSpec(billing_cycle=BillingCycle.monthly, tier=Tier.Pro),
    )

    assert result.state is ButtonState.available
    assert result.available is True
    assert result.button_text == "特惠订阅"


@pytest.mark.browser
async def test_dom_detector_sold_out_real_button_markup(page) -> None:
    await load_fixture(page, "sold_out.html")

    result = await DomDetector(adapter=GlmSiteAdapter()).detect(
        page,
        TargetSpec(billing_cycle=BillingCycle.monthly, tier=Tier.Pro),
    )

    assert result.state is ButtonState.sold_out
    assert result.available is False
    assert "暂时售罄" in result.button_text
    assert result.attrs["disabled"] == "disabled"
    assert "is-disabled" in result.attrs["class"]


@pytest.mark.browser
async def test_dom_detector_auth_required_when_cards_missing(page) -> None:
    await load_fixture(page, "auth_required.html")
    detector = DomDetector(adapter=GlmSiteAdapter())

    async def no_wait(_page: Page, timeout_ms: int = 15_000) -> None:
        return None

    detector.wait_for_content = no_wait  # type: ignore[method-assign]
    result = await detector.detect(page, TargetSpec(billing_cycle=BillingCycle.monthly, tier=Tier.Pro))

    assert result.state is ButtonState.auth_required
    assert result.available is False
    assert "login-required" in result.reason


@pytest.mark.browser
@pytest.mark.parametrize(
    ("tier", "reason_part"),
    [
        (Tier.Lite, "disabled attribute"),
        (Tier.Pro, "aria-disabled"),
        (Tier.Max, "disabled token"),
    ],
)
async def test_dom_detector_disabled_variants(page, tier: Tier, reason_part: str) -> None:
    await load_fixture(page, "unavailable.html")

    result = await DomDetector(adapter=GlmSiteAdapter()).detect(
        page,
        TargetSpec(billing_cycle=BillingCycle.monthly, tier=tier),
    )

    assert result.state is ButtonState.disabled
    assert result.available is False
    assert reason_part in result.reason
