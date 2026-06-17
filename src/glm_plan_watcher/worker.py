"""Subprocess entrypoint for daemon-managed account workers."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from glm_plan_watcher.config import load_config
from glm_plan_watcher.logging_setup import setup_logging
from glm_plan_watcher.watcher import Watcher


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a daemon-managed GLM account worker.")
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config.log_dir)
    return asyncio.run(Watcher(config).run())


if __name__ == "__main__":
    raise SystemExit(main())
