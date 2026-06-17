"""日志初始化。"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

LOGGER_NAME = "glm_plan_watcher"


def setup_logging(log_dir: Path, verbose: bool = False) -> logging.Logger:
    """配置项目 logger：Rich 人类输出 + rotating file。"""

    log_dir.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    logger.handlers.clear()

    file_handler = RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )

    # 人类可读日志走 stderr，保持 stdout 仅有 watch 的 WatchEvent JSON 行（父进程契约）。
    rich_handler = RichHandler(
        console=Console(stderr=True),
        show_time=True,
        show_path=False,
        rich_tracebacks=True,
        markup=False,
    )
    rich_handler.setLevel(level)
    rich_handler.setFormatter(logging.Formatter("%(message)s"))

    logger.addHandler(file_handler)
    logger.addHandler(rich_handler)
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    if name:
        return logging.getLogger(f"{LOGGER_NAME}.{name}")
    return logging.getLogger(LOGGER_NAME)
