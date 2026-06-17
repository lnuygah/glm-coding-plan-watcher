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
.venv/bin/glm-plan serve \
  --host 127.0.0.1 \
  --port 0 \
  --db daemon.sqlite3 \
  --handshake daemon.handshake.json
```

The daemon only binds to localhost by default. `--port 0` asks the OS for a free port and writes
`{"host", "port", "token"}` to the handshake file. If `--token` or
`GLM_WATCHER_DAEMON_TOKEN` is absent, the daemon generates a per-launch bearer token. Only `/health`
is unauthenticated; all REST calls and `/ws/events` require the token.

## Tauri Dev

The Tauri shell starts the daemon automatically with:

```text
glm-plan serve --host 127.0.0.1 --port 0 --db <app-data>/daemon.sqlite3 --token <uuid> --handshake <app-data>/daemon.handshake.json
```

Make sure `.venv/bin` is on `PATH`, or provide an explicit binary:

```bash
export GLM_WATCHER_DAEMON_BIN="$(pwd)/.venv/bin/glm-plan"
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
- The UI reads the local handshake file through a Tauri command, then communicates with daemon REST
  using `Authorization: Bearer <token>` and `/ws/events?token=<token>`.
- Login and payment handoff always stop the headless worker first, then launch a headful session
  using the same `user_data_dir`.
- Payment handoff never confirms payment, solves verification, or bypasses risk checks.

## Generated Output

Do not commit generated outputs:

- `node_modules/`
- `src-tauri/target/`
- `daemon.handshake.json`
- `*.handshake.json`
- `sidecar/build/`
- `sidecar/dist/`
- PyInstaller `build/` and `dist/`
