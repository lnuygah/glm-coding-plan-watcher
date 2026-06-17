from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from playwright.async_api import Page, async_playwright

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
async def page() -> AsyncIterator[Page]:
    playwright = await async_playwright().start()
    try:
        try:
            browser = await playwright.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Playwright chromium unavailable: {exc}")
        page = await browser.new_page()
        try:
            yield page
        finally:
            await browser.close()
    finally:
        await playwright.stop()


async def load_fixture(page: Page, name: str) -> None:
    await page.set_content((FIXTURES_DIR / name).read_text(encoding="utf-8"))
