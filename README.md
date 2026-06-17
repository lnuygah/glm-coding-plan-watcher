English | [中文](README.zh-CN.md)

<!-- Keep README.md and README.zh-CN.md in sync when changing onboarding docs. -->

# glm-coding-plan-watcher

Personal availability monitor for [GLM Coding Plan](https://www.bigmodel.cn/glm-coding) plans.

Safety boundary: this project only monitors and notifies. Login and payment stay manual. It does
not store passwords, solve CAPTCHA/risk checks, bypass controls, or automatically complete payment.
When enabled, it clicks only the purchase/subscription entry once and then waits for the user.

## What You Can Run

- CLI: one-off `check`, manual `login`, continuous `watch`, selector debugging.
- Local daemon: FastAPI + SQLite + account-level headless workers, controlled through REST/WS.
- Tauri GUI dev shell: menu-bar app that starts the daemon and manages accounts/targets/workers.
- macOS `.app`: Tauri bundle with a PyInstaller Python sidecar.

## Prerequisites On macOS

CLI and daemon need only Python 3.11+ and Playwright Chromium. GUI development and `.app`
packaging additionally need Rust/Cargo and Node/npm.

Install Homebrew if needed:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Install the common toolchain:

```bash
brew install python@3.12
```

Install GUI/packaging tools only if you plan to use Tauri:

```bash
brew install rust node
```

Check your machine:

```bash
make doctor
```

## Quickstart: CLI In 4 Commands

```bash
git clone https://github.com/lnuygah/glm-coding-plan-watcher.git
cd glm-coding-plan-watcher
make setup
.venv/bin/glm-plan check --config config.yaml || true
```

`make setup` creates `.venv`, installs Python dependencies, installs Playwright Chromium, and writes
`config.yaml` if it does not already exist. The final `|| true` is intentional: `check` exits `1`
when the selected plan is not available.

## Configuration

The default config is `config.yaml`, generated from the project defaults.

Important fields:

- `billing_cycle`: `monthly`, `quarterly`, or `yearly`.
- `tier`: `Lite`, `Pro`, or `Max`.
- `targets`: optional account-level list of `{billing_cycle, tier}` targets. If omitted, the top-level
  `billing_cycle` and `tier` are used.
- `user_data_dir`: Playwright persistent profile directory. One account/profile must not be opened by
  multiple workers at the same time.
- `refresh_interval_seconds` and `refresh_jitter_seconds`: account-level low-frequency cadence.
- `active_window_start` and `active_window_end`: optional daily sale window such as `10:00` to
  `10:30`. When omitted, the legacy low-frequency cadence is used.
- `active_timezone`: optional IANA timezone such as `Asia/Shanghai`; empty means local machine time.
- `active_interval_seconds`, `active_jitter_seconds`, and `idle_interval_seconds`: fast cadence inside
  the sale window and low-frequency/sleep-until-window behavior outside it. Very low intervals can
  trigger rate limits or risk controls and reduce the chance of purchase.
- `auto_click_entry`: default `true`; CLI watch can click only the entry button once. Daemon headless
  workers do not click; visible handoff sessions use this as the explicit entry-click choice.
- `on_hit_handoff`: daemon target option, default `true`; when a headless worker sees `available`, it
  stops that account worker and opens a visible handoff session for manual payment.
- `dry_run`: set `true` for safe demonstrations; detect and notify without clicking.

Example sale-window target:

```yaml
targets:
  - billing_cycle: monthly
    tier: Pro
    active_window_start: "10:00"
    active_window_end: "10:30"
    active_timezone: "Asia/Shanghai"
    active_interval_seconds: 3
    active_jitter_seconds: 1
    idle_interval_seconds: 600
```

Login state is stored in `user_data_dir`; account passwords are never stored by this project.

## Running

### CLI: Login

Open a visible browser, log in manually, then close the browser window to save the profile:

```bash
.venv/bin/glm-plan login --config config.yaml
```

Equivalent Make target:

```bash
make login
```

### CLI: One-Off Check

```bash
.venv/bin/glm-plan check --config config.yaml || true
```

The JSON output includes `available`, `button_state`, `button_text`, `reason`, and `checked_at`.
Exit code `0` means available; exit code `1` means unavailable/not found.

Equivalent Make target:

```bash
make check
```

### CLI: Continuous Watch

```bash
.venv/bin/glm-plan watch --config config.yaml
```

Machine-readable `WatchEvent` JSON lines go to stdout. Human logs go to stderr and `logs/app.log`.
Stop gracefully with `Ctrl+C` or `SIGTERM`.

For a safe demo, set `dry_run: true` in a copy of the config before running watch:

```bash
cp config.yaml config.dry-run.yaml
python3 - <<'PY'
from pathlib import Path
path = Path("config.dry-run.yaml")
text = path.read_text()
text = text.replace("dry_run: false", "dry_run: true")
path.write_text(text)
PY
.venv/bin/glm-plan watch --config config.dry-run.yaml
```

Equivalent Make target:

```bash
make watch CONFIG=config.dry-run.yaml
```

### CLI: Debug Selectors

```bash
.venv/bin/glm-plan debug-selectors --config config.yaml --headful
```

This opens the page, saves an HTML snapshot, and prints the current tab/card/button texts and attrs.

Equivalent Make target:

```bash
make debug
```

### Daemon: REST + WebSocket

Start the local daemon on a random localhost port:

```bash
.venv/bin/glm-plan serve \
  --host 127.0.0.1 \
  --port 0 \
  --db daemon.sqlite3 \
  --handshake daemon.handshake.json
```

`--port 0` asks the OS for a free port. If `--token` is omitted, the daemon generates a per-launch
token. The handshake file contains:

```json
{"host": "127.0.0.1", "port": 49152, "token": "..."}
```

Only `/health` is unauthenticated. Every other REST route requires
`Authorization: Bearer <token>`. `/ws/events` requires `?token=<token>`.

In another terminal:

```bash
export no_proxy=127.0.0.1,localhost
export HANDSHAKE=daemon.handshake.json
export HOST=$(.venv/bin/python -c 'import json,os; print(json.load(open(os.environ["HANDSHAKE"]))["host"])')
export PORT=$(.venv/bin/python -c 'import json,os; print(json.load(open(os.environ["HANDSHAKE"]))["port"])')
export TOKEN=$(.venv/bin/python -c 'import json,os; print(json.load(open(os.environ["HANDSHAKE"]))["token"])')
export BASE_URL="http://$HOST:$PORT"
```

Create an account and a dry-run target:

```bash
export PROFILE="$PWD/user_data/daemon-demo"

export ACCOUNT_ID=$(
  curl -sS -X POST "$BASE_URL/accounts" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"display_name\":\"daemon-demo\",\"user_data_dir\":\"$PROFILE\"}" \
  | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["id"])'
)

export TARGET_ID=$(
  curl -sS -X POST "$BASE_URL/accounts/$ACCOUNT_ID/targets" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"billing_cycle":"monthly","tier":"Pro","enabled":true,"interval":90,"jitter":30,"dry_run":true,"auto_click_entry":false,"on_hit_handoff":true,"active_window_start":"10:00","active_window_end":"10:30","active_timezone":"Asia/Shanghai","active_interval_seconds":3,"active_jitter_seconds":1,"idle_interval_seconds":600}' \
  | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["id"])'
)
```

Start, inspect, and stop the headless worker:

```bash
curl -sS -X POST "$BASE_URL/accounts/$ACCOUNT_ID/worker/start" \
  -H "Authorization: Bearer $TOKEN"

curl -sS "$BASE_URL/events?account_id=$ACCOUNT_ID&limit=20" \
  -H "Authorization: Bearer $TOKEN"

curl -sS -X POST "$BASE_URL/accounts/$ACCOUNT_ID/worker/stop" \
  -H "Authorization: Bearer $TOKEN"
```

Open a visible login or handoff session:

```bash
curl -sS -X POST "$BASE_URL/accounts/$ACCOUNT_ID/login" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"restore_worker":false}'

curl -sS -X POST "$BASE_URL/accounts/$ACCOUNT_ID/handoff" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"target_id\":$TARGET_ID,\"click_entry\":false,\"restore_worker\":false}"
```

Subscribe to live events:

```bash
.venv/bin/python - <<'PY'
import asyncio
import os
import websockets

async def main():
    uri = f"ws://{os.environ['HOST']}:{os.environ['PORT']}/ws/events?token={os.environ['TOKEN']}"
    async with websockets.connect(uri) as ws:
        async for message in ws:
            print(message)

asyncio.run(main())
PY
```

Equivalent Make target for starting the daemon:

```bash
make serve
```

### Tauri GUI Development

Install GUI prerequisites first: Rust/Cargo and Node/npm.

```bash
export GLM_WATCHER_DAEMON_BIN="$PWD/.venv/bin/glm-plan"
npm install
npm run tauri:dev
```

Equivalent Make target:

```bash
make gui
```

The Tauri shell starts the daemon with `--port 0`, a per-launch token, and an app-data handshake
file. Daemon binary lookup order:

1. `GLM_WATCHER_DAEMON_BIN`
2. bundled sidecar in Tauri resource/current-exe directories
3. `glm-plan` on `PATH`

In the GUI, add an account using an absolute `user_data_dir`, add targets, set dry-run/active-window
cadence/auto-handoff per target, then use Start/Login/Handoff. Daemon workers are headless. Login and
handoff stop that account worker first and open a visible browser using the same profile. One account
profile cannot be used concurrently.

### Build A macOS `.app`

Install packaging dependencies, build the Python sidecar, copy it to the Tauri target-triple name,
then build:

```bash
make app
```

Manual equivalent for Apple Silicon:

```bash
.venv/bin/pip install -e ".[server,packaging]"
.venv/bin/pyinstaller packaging/glm-plan-daemon.spec --noconfirm --distpath sidecar/bin
cp sidecar/bin/glm-plan-daemon sidecar/bin/glm-plan-daemon-aarch64-apple-darwin
chmod +x sidecar/bin/glm-plan-daemon-aarch64-apple-darwin
npm install
npm run tauri:build
```

Manual equivalent for Intel Mac: copy to `sidecar/bin/glm-plan-daemon-x86_64-apple-darwin` instead.

The repository includes a small dev placeholder shim at
`sidecar/bin/glm-plan-daemon-aarch64-apple-darwin` that delegates to `glm-plan` on `PATH`. It is not
a production daemon binary. Replace it with the PyInstaller-built sidecar before shipping a `.app`.

## Make Targets

```bash
make doctor   # print Python/cargo/node/npm versions and install hints
make setup    # create .venv, install Python deps, install Chromium, init config.yaml if absent
make check    # run one CLI check; unavailable exit code is treated as a normal result
make login    # open visible manual login session
make watch    # run continuous watch
make serve    # start daemon with --port 0 and daemon.handshake.json
make debug    # run debug-selectors --headful
make test     # pytest
make lint     # ruff
make gui      # npm install + tauri dev, using .venv/bin/glm-plan as daemon
make app      # build PyInstaller sidecar + tauri app
make clean    # remove build/cache/runtime files, but keep user_data/
```

Common overrides:

```bash
make watch CONFIG=config.dry-run.yaml
make serve DB=/tmp/glm-watcher.sqlite3 HANDSHAKE=/tmp/glm-watcher.handshake.json
make gui GLM_WATCHER_DAEMON_BIN="$PWD/.venv/bin/glm-plan"
```

## Lockfile Policy

This is an application-style repository, so lockfiles should be committed for reproducible GUI and
packaging builds:

- `package-lock.json` should be committed.
- `src-tauri/Cargo.lock` should remain committed.

Generated outputs remain ignored: `node_modules/`, `src-tauri/target/`, daemon SQLite files,
handshake/token files, PyInstaller build/dist output, and real sidecar binaries.

## Troubleshooting

### Local daemon curl returns 502 with a local HTTP proxy

If your shell has a proxy such as `http_proxy=http://127.0.0.1:7897`, `curl` may send local daemon
requests through the proxy and get a 502.

Use:

```bash
export no_proxy=127.0.0.1,localhost
```

or clear proxy variables for this shell:

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
```

### Playwright cannot launch Chromium

Install Chromium for Playwright:

```bash
.venv/bin/playwright install chromium
```

The first install can be slow.

### Python version is too old

Python 3.11+ is required. Prefer Homebrew Python 3.12:

```bash
brew install python@3.12
make setup
```

If `python3` points to an older version, run:

```bash
PYTHON=/opt/homebrew/bin/python3.12 make setup
```

### Unsigned `.app` is blocked by Gatekeeper

For a local unsigned build, macOS may show "cannot be opened because it is from an unidentified
developer".

Options:

```bash
xattr -dr com.apple.quarantine "src-tauri/target/release/bundle/macos/GLM Plan Watcher.app"
```

or right-click the app and choose Open.

For local testing you can also ad-hoc sign:

```bash
codesign --force --deep --sign - "src-tauri/target/release/bundle/macos/GLM Plan Watcher.app"
```

Production distribution should use proper Apple Developer signing and notarization.

### Sidecar placeholder accidentally shipped

If the packaged app only works on machines that already have `glm-plan` on `PATH`, you likely shipped
the dev shim instead of the PyInstaller sidecar. Rebuild with:

```bash
make app
```

and verify the target-triple sidecar is a real binary, not:

```sh
#!/usr/bin/env sh
exec glm-plan "$@"
```

### Already subscribed account shows sold out

Real page state depends on login state. An already subscribed account can show `sold_out`. A fresh
not-logged-in profile may show `available` with button text like `特惠订阅`; use `dry_run: true` for
safe demos.

### Fast refresh is too aggressive

Use active windows only for the real sale period, for example `10:00` to `10:30`. The scheduler clamps
the fast interval to a non-zero hard minimum and keeps jitter, but choosing very low values can still
trigger rate limits or risk controls.

## Development Checks

```bash
.venv/bin/python -m compileall src
.venv/bin/pytest
.venv/bin/ruff check .
cargo check --manifest-path src-tauri/Cargo.toml
```

## DOM Detection Notes

Current GLM DOM selectors:

- Billing tabs: `#switchTabBox .switch-tab-item`
- Plan cards: `.package-list .package-card-box`
- Card title: `.package-card-title span.font-prompt`
- Entry button: `button.buy-btn`

Use `debug-selectors` when the site changes.
