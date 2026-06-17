# Packaging And Tauri Development

P2 adds a minimal Tauri v2 menu-bar shell. The Python daemon remains the source of truth for
accounts, targets, workers, events, login, and handoff.

## Toolchain

Install these locally before building the macOS app:

- Rust and Cargo
- Node.js and npm
- Tauri CLI (`npm install`)
- Python 3.12
- Python dependencies:

```bash
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev,server]"
.venv/bin/playwright install chromium
```

For sidecar packaging:

```bash
.venv/bin/pip install -e ".[packaging]"
```

## Python Daemon

Run the daemon directly during backend development:

```bash
.venv/bin/glm-plan serve --host 127.0.0.1 --port 8765 --db daemon.sqlite3
```

The daemon only binds to localhost by default. The Tauri UI expects `http://127.0.0.1:8765`
unless the user changes the Daemon field.

## Tauri Dev

The Tauri shell starts the daemon automatically with:

```text
glm-plan serve --host 127.0.0.1 --port 8765 --db <app-data>/daemon.sqlite3
```

Make sure `.venv/bin` is on `PATH`, or provide an explicit binary:

```bash
export GLM_WATCHER_DAEMON_BIN="$(pwd)/.venv/bin/glm-plan"
export GLM_WATCHER_DAEMON_PORT=8765
npm install
npm run tauri:dev
```

Daemon binary lookup order:

1. `GLM_WATCHER_DAEMON_BIN`
2. bundled sidecar in Tauri resource/current-exe directories
3. `glm-plan` on `PATH`

## Sidecar Build

Build the daemon sidecar:

```bash
.venv/bin/pyinstaller packaging/glm-plan-daemon.spec --noconfirm --distpath sidecar/bin
```

This produces:

```text
sidecar/bin/glm-plan-daemon
```

Tauri expects a target-triple suffix for `externalBin`, for example:

```text
sidecar/bin/glm-plan-daemon-aarch64-apple-darwin
```

The repository includes a small macOS arm64 dev placeholder that delegates to `glm-plan` on PATH.
In a production packaging pipeline, replace it with the PyInstaller-built binary for the target
triple, then run:

```bash
npm run tauri:build
```

## Runtime Model

- The Tauri process starts/stops the local daemon.
- The UI communicates with daemon REST and `/ws/events`.
- Login and payment handoff always stop the headless worker first, then launch a headful session
  using the same `user_data_dir`.
- Payment handoff never confirms payment, solves verification, or bypasses risk checks.

## Generated Output

Do not commit generated outputs:

- `node_modules/`
- `src-tauri/target/`
- `sidecar/build/`
- `sidecar/dist/`
- PyInstaller `build/` and `dist/`
