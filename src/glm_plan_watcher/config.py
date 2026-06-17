"""配置：YAML + 环境变量（pydantic-settings）。

一个 config = 一个账号 = 一个浏览器会话；可选 targets 列表支持该账号下多个目标顺序轮询。

加载优先级（高 → 低）：环境变量(GLM_WATCHER__*) > YAML 文件 > 默认值。
敏感项（如 webhook_url）建议走环境变量，不要写进 YAML。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from glm_plan_watcher.exceptions import ConfigError
from glm_plan_watcher.models import BillingCycle, TargetSpec, Tier

DEFAULT_URL = "https://www.bigmodel.cn/glm-coding"


class NotifyConfig(BaseModel):
    """通知通道开关。"""

    console: bool = True
    desktop: bool = False
    webhook_url: str = ""


class AppConfig(BaseSettings):
    """应用配置。

    字段语义见 README / config.example.yaml。
    """

    model_config = SettingsConfigDict(
        env_prefix="GLM_WATCHER__",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # —— 目标 ——
    url: str = DEFAULT_URL
    billing_cycle: BillingCycle = BillingCycle.monthly
    tier: Tier = Tier.Pro
    targets: list[TargetSpec] = Field(default_factory=list)

    # —— 循环节奏（不激进 + jitter）——
    refresh_interval_seconds: float = 90.0
    refresh_jitter_seconds: float = 30.0
    max_checks: int = 0  # 0 = 无限

    # —— 浏览器 ——
    headless: bool = False
    user_data_dir: Path = Path("user_data/default")
    enable_trace: bool = False

    # —— 产物目录 ——
    screenshot_dir: Path = Path("screenshots")
    html_snapshot_dir: Path = Path("snapshots")
    log_dir: Path = Path("logs")

    # —— 命中后行为 ——
    # 默认点击「购买/订阅入口」后暂停等待人工；仅点入口，绝不自动完成支付/确认/验证码/风控。
    auto_click_entry: bool = True
    dry_run: bool = False  # True 时只检测不点击

    # —— 通知 ——
    notify: NotifyConfig = Field(default_factory=NotifyConfig)

    @field_validator("refresh_interval_seconds", "refresh_jitter_seconds")
    @classmethod
    def _non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("必须为非负数")
        return v

    @field_validator("max_checks")
    @classmethod
    def _max_checks_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("max_checks 必须 >= 0（0 表示无限）")
        return v

    @property
    def target(self) -> TargetSpec:
        return TargetSpec(billing_cycle=self.billing_cycle, tier=self.tier)

    @property
    def target_specs(self) -> list[TargetSpec]:
        """账号级目标列表；未配置 targets 时保持旧版单目标行为。"""
        return self.targets or [self.target]

    def ensure_dirs(self) -> None:
        """创建运行所需的产物目录与登录态目录。"""
        for d in (self.screenshot_dir, self.html_snapshot_dir, self.log_dir, self.user_data_dir):
            Path(d).mkdir(parents=True, exist_ok=True)


class _YamlBackedAppConfig(AppConfig):
    """把 YAML 作为低于环境变量的配置源。"""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (env_settings, dotenv_settings, init_settings, file_secret_settings)


def load_config(path: str | Path | None) -> AppConfig:
    """从 YAML 文件加载配置；环境变量仍可覆盖。

    传入 None 时仅用默认值 + 环境变量。
    """
    data: dict[str, Any] = {}
    if path is not None:
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"配置文件不存在：{p}")
        try:
            loaded = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:  # pragma: no cover - 取决于文件内容
            raise ConfigError(f"配置文件解析失败：{p}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ConfigError(f"配置文件顶层应为映射(dict)：{p}")
        data = loaded
    try:
        return _YamlBackedAppConfig(**data)
    except Exception as exc:  # pydantic ValidationError 等
        raise ConfigError(f"配置非法：{exc}") from exc


def dump_default_yaml() -> str:
    """生成 init-config 用的默认 YAML 文本（带中文注释）。"""
    return _DEFAULT_YAML


_DEFAULT_YAML = """\
# GLM Coding Plan Watcher 配置（一个 config = 一个账号 = 一个浏览器会话）
# 敏感项（webhook 等）建议放到 .env，用 GLM_WATCHER__NOTIFY__WEBHOOK_URL 覆盖。

url: "https://www.bigmodel.cn/glm-coding"

# 目标：billing_cycle = monthly | quarterly | yearly ; tier = Lite | Pro | Max
# 兼容旧版单目标配置：未设置 targets 时使用下方顶层 billing_cycle/tier。
billing_cycle: monthly
tier: Pro

# 可选：账号级多目标顺序轮询。配置后会忽略顶层 billing_cycle/tier。
# targets:
#   - billing_cycle: monthly
#     tier: Pro
#   - billing_cycle: yearly
#     tier: Max

# 循环节奏（秒）。多目标会顺序检测一轮后统一等待 interval ± random(0, jitter)，不激进。
refresh_interval_seconds: 90
refresh_jitter_seconds: 30
max_checks: 0            # 0 = 无限循环；多目标时表示账号级扫描轮数

# 浏览器
headless: false          # 首次登录请用 false（可视化）；watch 可设 true
user_data_dir: "user_data/default"   # 登录态持久化目录（不存账号密码）
enable_trace: false      # 打开后保存 Playwright trace 便于排障

# 产物目录
screenshot_dir: "screenshots"
html_snapshot_dir: "snapshots"
log_dir: "logs"

# 命中后行为
# auto_click_entry: 命中可购买时是否自动点击「购买/订阅入口」按钮（仅点入口，随后暂停等人工）。
# dry_run: true 时只检测不点击。最终支付必须人工确认。
auto_click_entry: true
dry_run: false

notify:
  console: true
  desktop: false
  webhook_url: ""        # 建议改用 .env 中的 GLM_WATCHER__NOTIFY__WEBHOOK_URL
"""
