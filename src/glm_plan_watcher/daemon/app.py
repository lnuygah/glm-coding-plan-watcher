"""FastAPI application for the local daemon."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from glm_plan_watcher.daemon.api_models import (
    AccountCreate,
    AccountUpdate,
    HandoffRequest,
    LoginRequest,
    TargetCreate,
    TargetUpdate,
)
from glm_plan_watcher.daemon.ingest import EventBroadcaster
from glm_plan_watcher.daemon.security import (
    authorized_bearer,
    authorized_ws_token,
    resolve_token,
)
from glm_plan_watcher.daemon.supervisor import (
    ProfileInUseError,
    WorkerAlreadyRunningError,
    WorkerSupervisor,
)
from glm_plan_watcher.db import Repository


def create_app(
    db_path: str | Path = Path("daemon.sqlite3"),
    repository: Repository | None = None,
    broadcaster: EventBroadcaster | None = None,
    supervisor: WorkerSupervisor | None = None,
    token: str | None = None,
) -> FastAPI:
    repo = repository or Repository(db_path)
    events = broadcaster or EventBroadcaster()
    workers = supervisor or WorkerSupervisor(repo, events)
    daemon_token = resolve_token(token)

    app = FastAPI(title="GLM Plan Watcher Daemon")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.repository = repo
    app.state.broadcaster = events
    app.state.supervisor = workers
    app.state.token = daemon_token

    @app.middleware("http")
    async def require_bearer_token(request: Request, call_next: Any) -> JSONResponse:
        if request.method == "OPTIONS" or request.url.path == "/health":
            return await call_next(request)
        if not authorized_bearer(request.headers.get("authorization"), daemon_token):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "missing or invalid bearer token"},
            )
        return await call_next(request)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/accounts")
    def list_accounts() -> list[dict[str, Any]]:
        return repo.list_accounts()

    @app.post("/accounts", status_code=status.HTTP_201_CREATED)
    def create_account(payload: AccountCreate) -> dict[str, Any]:
        try:
            return repo.create_account(payload.display_name, payload.user_data_dir)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/accounts/{account_id}")
    def get_account(account_id: int) -> dict[str, Any]:
        try:
            return repo.get_account(account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.patch("/accounts/{account_id}")
    def update_account(account_id: int, payload: AccountUpdate) -> dict[str, Any]:
        try:
            return repo.update_account(account_id, **payload.model_dump(exclude_unset=True))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_account(
        account_id: int,
        purge_profile: bool = Query(False),
    ) -> None:
        account = _get_optional_account(repo, account_id)
        if account is not None:
            await workers.discard_account(account_id)
            if purge_profile:
                _purge_managed_profile(repo, account["user_data_dir"])
        repo.delete_account(account_id)

    @app.get("/accounts/{account_id}/targets")
    def list_targets(account_id: int) -> list[dict[str, Any]]:
        return repo.list_targets(account_id=account_id)

    @app.post("/accounts/{account_id}/targets", status_code=status.HTTP_201_CREATED)
    def create_target(account_id: int, payload: TargetCreate) -> dict[str, Any]:
        try:
            return repo.create_target(
                account_id=account_id,
                billing_cycle=payload.billing_cycle.value,
                tier=payload.tier.value,
                enabled=payload.enabled,
                interval=payload.interval,
                jitter=payload.jitter,
                dry_run=payload.dry_run,
                auto_click_entry=payload.auto_click_entry,
                active_window_start=payload.active_window_start,
                active_window_end=payload.active_window_end,
                active_timezone=payload.active_timezone,
                active_interval_seconds=payload.active_interval_seconds,
                active_jitter_seconds=payload.active_jitter_seconds,
                idle_interval_seconds=payload.idle_interval_seconds,
                on_hit_handoff=payload.on_hit_handoff,
                visible_in_window=payload.visible_in_window,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/targets/{target_id}")
    def update_target(target_id: int, payload: TargetUpdate) -> dict[str, Any]:
        data = payload.model_dump(exclude_unset=True)
        if "billing_cycle" in data:
            data["billing_cycle"] = data["billing_cycle"].value
        if "tier" in data:
            data["tier"] = data["tier"].value
        try:
            return repo.update_target(target_id, **data)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/targets/{target_id}")
    def get_target(target_id: int) -> dict[str, Any]:
        try:
            return repo.get_target(target_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/targets/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_target(target_id: int) -> None:
        repo.delete_target(target_id)

    @app.get("/events")
    def list_events(
        account_id: int | None = None,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ) -> list[dict[str, Any]]:
        return repo.list_events(account_id=account_id, limit=limit, offset=offset)

    @app.get("/workers")
    def list_workers() -> list[dict[str, object]]:
        return workers.list_workers()

    @app.get("/workers/{account_id}")
    def get_worker(account_id: int) -> dict[str, object]:
        return workers.worker_status(account_id)

    @app.post("/accounts/{account_id}/worker/start")
    async def start_worker(account_id: int) -> dict[str, object]:
        try:
            return await workers.start_worker(account_id)
        except WorkerAlreadyRunningError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/accounts/{account_id}/worker/stop")
    async def stop_worker(account_id: int) -> dict[str, object]:
        return await workers.stop_worker(account_id)

    @app.post("/accounts/{account_id}/login")
    async def login(account_id: int, payload: LoginRequest | None = None) -> dict[str, object]:
        try:
            request = payload or LoginRequest()
            return await workers.start_login_session(
                account_id,
                restore_worker=request.restore_worker,
            )
        except ProfileInUseError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/accounts/{account_id}/handoff")
    async def handoff(account_id: int, payload: HandoffRequest | None = None) -> dict[str, object]:
        try:
            request = payload or HandoffRequest()
            return await workers.start_handoff_session(
                account_id,
                target_id=request.target_id,
                click_entry=request.click_entry,
                restore_worker=request.restore_worker,
            )
        except ProfileInUseError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.websocket("/ws/events")
    async def websocket_events(websocket: WebSocket, account_id: int | None = None) -> None:
        if not authorized_ws_token(
            websocket.query_params.get("token"),
            websocket.headers.get("sec-websocket-protocol"),
            daemon_token,
        ):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await websocket.accept()
        try:
            async with events.subscribe(account_id=account_id) as queue:
                while True:
                    payload = await queue.get()
                    await websocket.send_json(payload)
        except WebSocketDisconnect:
            return

    return app


def _get_optional_account(repo: Repository, account_id: int) -> dict[str, Any] | None:
    try:
        return repo.get_account(account_id)
    except KeyError:
        return None


def _purge_managed_profile(repo: Repository, user_data_dir: str) -> None:
    profile = Path(user_data_dir).expanduser().resolve(strict=False)
    profiles_root = repo.profiles_dir.expanduser().resolve(strict=False)
    if profile == profiles_root or profiles_root not in profile.parents:
        return
    shutil.rmtree(profile, ignore_errors=True)
