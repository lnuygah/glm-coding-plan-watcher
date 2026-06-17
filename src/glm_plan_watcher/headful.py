"""Headful login and payment handoff subprocess entrypoint."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from glm_plan_watcher.browser import BrowserSession, make_storage
from glm_plan_watcher.config import AppConfig, load_config
from glm_plan_watcher.detector import DomDetector
from glm_plan_watcher.logging_setup import setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a visible browser handoff session.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    login_parser = subparsers.add_parser("login", help="Open a visible login session.")
    login_parser.add_argument("--config", required=True, type=Path)

    handoff_parser = subparsers.add_parser("handoff", help="Open a visible payment handoff session.")
    handoff_parser.add_argument("--config", required=True, type=Path)
    handoff_parser.add_argument("--click-entry", action="store_true")

    args = parser.parse_args()
    config = load_config(args.config)
    config.headless = False
    setup_logging(config.log_dir)

    if args.command == "login":
        return asyncio.run(run_login(config))
    if args.command == "handoff":
        return asyncio.run(run_handoff(config, click_entry=args.click_entry))
    raise ValueError(f"unknown command: {args.command}")


async def run_login(config: AppConfig) -> int:
    """Open the site visibly and wait until the user closes the browser."""

    async with BrowserSession(config, make_storage(config)) as session:
        await session.goto(config.url)
        await _wait_for_context_close(session)
    return 0


async def run_handoff(config: AppConfig, click_entry: bool = False) -> int:
    """Open a visible payment handoff session.

    When `click_entry` is true, this clicks only the product purchase/subscription entry.
    It never handles payment confirmation, CAPTCHA, risk pages, or login automation.
    """

    async with BrowserSession(config, make_storage(config)) as session:
        page = await session.goto(config.url)
        if click_entry:
            await DomDetector().click_entry_button(page, config.target_specs[0])
        await _wait_for_context_close(session)
    return 0


async def _wait_for_context_close(session: BrowserSession) -> None:
    closed = asyncio.Event()
    if session.context is None:
        return
    session.context.on("close", lambda *_: closed.set())
    await closed.wait()


if __name__ == "__main__":
    raise SystemExit(main())
