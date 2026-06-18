"""Pydantic request models for the local daemon API."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from glm_plan_watcher.models import BillingCycle, Tier


class AccountCreate(BaseModel):
    display_name: str = Field(min_length=1)
    # 留空 = daemon 自动管理 profile 目录；也可显式传入以导入已有 profile。
    user_data_dir: str | None = None


class AccountUpdate(BaseModel):
    display_name: str | None = None
    user_data_dir: str | None = None
    status: str | None = None
    last_login_at: str | None = None


class TargetCreate(BaseModel):
    billing_cycle: BillingCycle
    tier: Tier
    enabled: bool = True
    interval: float = 90.0
    jitter: float = 30.0
    dry_run: bool = False
    auto_click_entry: bool = True
    active_window_start: str = ""
    active_window_end: str = ""
    active_timezone: str = ""
    active_interval_seconds: float = Field(default=3.0, ge=0)
    active_jitter_seconds: float = Field(default=1.0, ge=0)
    idle_interval_seconds: float = Field(default=600.0, ge=0)
    on_hit_handoff: bool = True
    visible_in_window: bool = False


class TargetUpdate(BaseModel):
    billing_cycle: BillingCycle | None = None
    tier: Tier | None = None
    enabled: bool | None = None
    interval: float | None = None
    jitter: float | None = None
    dry_run: bool | None = None
    auto_click_entry: bool | None = None
    active_window_start: str | None = None
    active_window_end: str | None = None
    active_timezone: str | None = None
    active_interval_seconds: float | None = Field(default=None, ge=0)
    active_jitter_seconds: float | None = Field(default=None, ge=0)
    idle_interval_seconds: float | None = Field(default=None, ge=0)
    on_hit_handoff: bool | None = None
    visible_in_window: bool | None = None


class ServeOptions(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765
    db_path: Path = Path("daemon.sqlite3")


class LoginRequest(BaseModel):
    restore_worker: bool = False


class HandoffRequest(BaseModel):
    target_id: int | None = None
    click_entry: bool = False
    restore_worker: bool = False
