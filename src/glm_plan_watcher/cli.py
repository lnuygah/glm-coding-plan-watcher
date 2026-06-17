"""Typer CLI。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from glm_plan_watcher.browser import BrowserSession, make_storage
from glm_plan_watcher.config import AppConfig, dump_default_yaml, load_config
from glm_plan_watcher.detector import DomDetector
from glm_plan_watcher.logging_setup import setup_logging
from glm_plan_watcher.models import CheckResult
from glm_plan_watcher.watcher import Watcher

app = typer.Typer(no_args_is_help=True, help="GLM Coding Plan 套餐可购买性监控工具。")


@app.command("init-config")
def init_config(
    output: Annotated[Path, typer.Option("--output", "-o", help="输出配置文件路径")] = Path(
        "config.yaml"
    ),
    force: Annotated[bool, typer.Option("--force", help="覆盖已存在文件")] = False,
) -> None:
    """写出默认配置。"""

    if output.exists() and not force:
        raise typer.BadParameter(f"文件已存在：{output}（使用 --force 覆盖）")
    output.write_text(dump_default_yaml(), encoding="utf-8")
    typer.echo(f"已写出配置：{output}")


@app.command()
def login(
    config: Annotated[Path, typer.Option("--config", "-c", help="配置文件路径")] = Path(
        "config.yaml"
    ),
) -> None:
    """打开页面并等待手动登录，登录态保存到 user_data_dir。"""

    cfg = load_config(config)
    cfg.headless = False
    setup_logging(cfg.log_dir)
    try:
        asyncio.run(_login(cfg))
    except KeyboardInterrupt:
        typer.echo("已结束登录流程；登录态保存在 user_data_dir。")


@app.command()
def check(
    config: Annotated[Path, typer.Option("--config", "-c", help="配置文件路径")] = Path(
        "config.yaml"
    ),
    headful: Annotated[bool, typer.Option("--headful", help="强制使用可视化浏览器")] = False,
) -> None:
    """执行单次检测，退出码 0=可购买，1=不可购买/未找到。"""

    cfg = load_config(config)
    if headful:
        cfg.headless = False
    setup_logging(cfg.log_dir)
    result = asyncio.run(_check_once(cfg))
    _echo_result(result)
    raise typer.Exit(0 if result.available else 1)


@app.command()
def watch(
    config: Annotated[Path, typer.Option("--config", "-c", help="配置文件路径")] = Path(
        "config.yaml"
    ),
    headful: Annotated[bool, typer.Option("--headful", help="强制使用可视化浏览器")] = False,
) -> None:
    """循环监控，向 stdout 输出单行 JSON WatchEvent。"""

    cfg = load_config(config)
    if headful:
        cfg.headless = False
    setup_logging(cfg.log_dir)
    code = asyncio.run(Watcher(cfg).run())
    raise typer.Exit(code)


@app.command("debug-selectors")
def debug_selectors(
    config: Annotated[Path, typer.Option("--config", "-c", help="配置文件路径")] = Path(
        "config.yaml"
    ),
    headful: Annotated[bool, typer.Option("--headful", help="强制使用可视化浏览器")] = False,
) -> None:
    """打开页面并输出关键 selector 的文本/属性，同时保存当前 HTML。"""

    cfg = load_config(config)
    if headful:
        cfg.headless = False
    setup_logging(cfg.log_dir, verbose=True)
    snapshot = asyncio.run(_debug_selectors(cfg))
    typer.echo(json.dumps(snapshot, ensure_ascii=False, indent=2))


@app.command()
def serve(
    host: Annotated[str, typer.Option("--host", help="监听地址；默认仅本机")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="监听端口")] = 8765,
    db_path: Annotated[Path, typer.Option("--db", help="SQLite 数据库路径")] = Path(
        "daemon.sqlite3"
    ),
) -> None:
    """启动本地 FastAPI daemon。"""

    if host != "127.0.0.1":
        typer.echo("警告：daemon 默认应只绑定 127.0.0.1；请确认你确实需要暴露到其它地址。")
    import uvicorn

    from glm_plan_watcher.daemon.app import create_app

    uvicorn.run(create_app(db_path=db_path), host=host, port=port)


async def _login(config: AppConfig) -> None:
    async with BrowserSession(config, make_storage(config)) as session:
        await session.goto(config.url)
        context = session.context
        typer.echo(
            "浏览器已打开。请在浏览器中手动登录；\n"
            "完成后【直接关闭浏览器窗口】即可保存登录态（也可在此终端按 Ctrl+C 结束）。"
        )
        # 不依赖 stdin（在 `!`/管道等非交互环境下 typer.prompt 会立即 Aborted）。
        # 改为等待用户关闭浏览器：持久化 context 关闭时登录态已写入 user_data_dir。
        closed = asyncio.Event()
        if context is not None:
            context.on("close", lambda *_: closed.set())
        await closed.wait()
    typer.echo("登录流程结束，登录态已保存到 user_data_dir。")


async def _check_once(config: AppConfig) -> CheckResult:
    detector = DomDetector()
    async with BrowserSession(config, make_storage(config)) as session:
        page = await session.goto(config.url)
        return await detector.detect(page, config.target)


async def _debug_selectors(config: AppConfig) -> dict[str, object]:
    detector = DomDetector()
    async with BrowserSession(config, make_storage(config)) as session:
        page = await session.goto(config.url)
        html_path = await session.storage.save_html(page, "debug-selectors", config.target)
        snapshot = await detector.debug_snapshot(page)
        snapshot["html_path"] = str(html_path)
        return snapshot


def _echo_result(result: CheckResult) -> None:
    typer.echo(
        json.dumps(
            {
                "target": result.target.describe(),
                "available": result.available,
                "button_state": result.state.value,
                "button_text": result.button_text,
                "reason": result.reason,
                "attrs": result.attrs,
                "checked_at": result.checked_at.isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
