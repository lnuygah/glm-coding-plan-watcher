"""PyInstaller entrypoint for the Tauri sidecar daemon."""

from __future__ import annotations

from glm_plan_watcher.cli import app

if __name__ == "__main__":
    app(prog_name="glm-plan")
