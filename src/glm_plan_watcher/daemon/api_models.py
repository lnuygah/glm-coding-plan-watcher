"""Pydantic request models for the local daemon API."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from glm_plan_watcher.models import BillingCycle, Tier


class AccountCreate(BaseModel):
    display_name: str = Field(min_length=1)
    user_data_dir: str = Field(min_length=1)


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


class TargetUpdate(BaseModel):
    billing_cycle: BillingCycle | None = None
    tier: Tier | None = None
    enabled: bool | None = None
    interval: float | None = None
    jitter: float | None = None
    dry_run: bool | None = None
    auto_click_entry: bool | None = None


class ServeOptions(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765
    db_path: Path = Path("daemon.sqlite3")
