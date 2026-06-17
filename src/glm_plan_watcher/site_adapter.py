"""Lightweight site adapter for GLM-specific DOM details."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from glm_plan_watcher.config import DEFAULT_URL
from glm_plan_watcher.models import BillingCycle
from glm_plan_watcher.selectors import (
    AUTH_REQUIRED_KEYWORDS,
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
)


class SiteAdapter(Protocol):
    """Small contract for site-specific DOM details."""

    url: str
    switch_tab_box_selector: str
    switch_tab_item_selector: str
    switch_tab_active_class: str
    package_list_selector: str
    package_card_box_selector: str
    package_card_selector: str
    package_title_selector: str
    buy_button_selector: str
    fallback_button_selector: str

    def billing_cycle_label(self, cycle: BillingCycle) -> str:
        """Return the site label for a billing cycle."""
        ...

    def looks_like_auth_required(self, text: str) -> bool:
        """Return whether body text looks like a login-required page."""
        ...

    def tier_card_fallback_selector(self, tier_value: str) -> str:
        """Return a fallback selector for locating a tier card."""
        ...


@dataclass(frozen=True)
class GlmSiteAdapter:
    """GLM Coding Plan DOM adapter."""

    url: str = DEFAULT_URL
    switch_tab_box_selector: str = CSS_SWITCH_TAB_BOX
    switch_tab_item_selector: str = CSS_SWITCH_TAB_ITEM
    switch_tab_active_class: str = CSS_SWITCH_TAB_ACTIVE_CLASS
    package_list_selector: str = CSS_PACKAGE_LIST
    package_card_box_selector: str = CSS_PACKAGE_CARD_BOX
    package_card_selector: str = CSS_PACKAGE_CARD
    package_title_selector: str = CSS_PACKAGE_TITLE
    buy_button_selector: str = CSS_BUY_BUTTON
    fallback_button_selector: str = CSS_FALLBACK_BUTTON
    auth_required_keywords: tuple[str, ...] = AUTH_REQUIRED_KEYWORDS

    def billing_cycle_label(self, cycle: BillingCycle) -> str:
        return BILLING_CYCLE_LABELS[cycle]

    def looks_like_auth_required(self, text: str) -> bool:
        normalized_text = " ".join(text.split())
        return any(keyword in normalized_text for keyword in self.auth_required_keywords)

    def tier_card_fallback_selector(self, tier_value: str) -> str:
        return f"{self.package_card_selector}:has(.package-card-title:has-text('{tier_value}'))"
