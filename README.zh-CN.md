[English](README.md) | 中文

<!-- 修改上手文档时，请同步维护 README.md 与 README.zh-CN.md。 -->

# glm-coding-plan-watcher

用于 [GLM Coding Plan](https://www.bigmodel.cn/glm-coding) 套餐的个人可用性监控工具。

合规边界：本项目只做监控和提醒。登录与付款都由用户人工完成。它不会保存密码、处理 CAPTCHA/风控、绕过限制，也不会自动完成支付。启用相关选项时，它只会点击一次购买/订阅入口，然后等待用户接手。

## 可运行内容

- CLI：单次 `check`、手动 `login`、持续 `watch`、selector 调试。
- 本地 daemon：FastAPI + SQLite + 账号级 headless worker，通过 REST/WS 控制。
- Tauri GUI 开发壳：启动 daemon，并管理账号、targets 和 workers 的菜单栏应用。
- macOS `.app`：带 PyInstaller Python sidecar 的 Tauri 应用包。

## macOS 前置条件

CLI 和 daemon 只需要 Python 3.11+ 与 Playwright Chromium。GUI 开发和 `.app` 打包还需要 Rust/Cargo 与 Node/npm。

如有需要，先安装 Homebrew：

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

安装通用工具链：

```bash
brew install python@3.12
```

只有计划使用 Tauri 时，才需要安装 GUI/打包工具：

```bash
brew install rust node
```

检查本机环境：

```bash
make doctor
```

## 快速开始：4 条命令跑起 CLI

```bash
git clone https://github.com/lnuygah/glm-coding-plan-watcher.git
cd glm-coding-plan-watcher
make setup
.venv/bin/glm-plan check --config config.yaml || true
```

`make setup` 会创建 `.venv`、安装 Python 依赖、安装 Playwright Chromium，并在 `config.yaml` 不存在时写出默认配置。最后的 `|| true` 是有意保留的：当所选套餐不可用时，`check` 会以退出码 `1` 结束。

## 配置

默认配置文件是 `config.yaml`，由项目默认值生成。

关键字段：

- `billing_cycle`: `monthly`, `quarterly`, or `yearly`.
- `tier`: `Lite`, `Pro`, or `Max`.
- `targets`: 可选的账号级 `{billing_cycle, tier}` targets 列表。省略时使用顶层
  `billing_cycle` 与 `tier`。
- `user_data_dir`: Playwright persistent profile 目录。同一个账号/profile 不能被多个 worker 同时打开。
- `refresh_interval_seconds` and `refresh_jitter_seconds`: 账号级低频刷新节奏。
- `active_window_start` 与 `active_window_end`: 可选的每日开售窗口，例如 `10:00` 到
  `10:30`。省略时使用旧的低频刷新节奏。
- `active_timezone`: 可选 IANA 时区，例如 `Asia/Shanghai`；留空表示本机本地时间。
- `active_interval_seconds`、`active_jitter_seconds` 与 `idle_interval_seconds`: 开售窗口内的快刷节奏，以及窗口外低频检查/睡到窗口开始的行为。过低间隔可能触发限流或风控，反而降低买到的概率。
- `auto_click_entry`: 默认 `true`；CLI watch 可只点击一次入口按钮。daemon headless worker 不点击；可见 handoff 会把它作为显式入口点击选择。
- `on_hit_handoff`: daemon target 选项，默认 `true`；headless worker 检测到 `available` 后，会停止该账号 worker 并打开可见 handoff 会话，交给用户人工付款。
- `dry_run`: 安全演示时设为 `true`；只检测和通知，不点击。

开售窗口 target 示例：

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

登录态保存在 `user_data_dir` 中；本项目不会保存账号密码。

## 运行方式

### CLI：登录

打开可见浏览器，手动登录，然后关闭浏览器窗口以保存 profile：

```bash
.venv/bin/glm-plan login --config config.yaml
```

等价 Make target：

```bash
make login
```

### CLI：单次检查

```bash
.venv/bin/glm-plan check --config config.yaml || true
```

JSON 输出包含 `available`、`button_state`、`button_text`、`reason` 和 `checked_at`。退出码 `0` 表示 available；退出码 `1` 表示 unavailable/not found。

等价 Make target：

```bash
make check
```

### CLI：持续监控

```bash
.venv/bin/glm-plan watch --config config.yaml
```

机器可读的 `WatchEvent` JSON 行写入 stdout。人类日志写入 stderr 和 `logs/app.log`。可用 `Ctrl+C` 或 `SIGTERM` 优雅停止。

安全演示时，先在配置副本中设置 `dry_run: true`，再运行 watch：

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

等价 Make target：

```bash
make watch CONFIG=config.dry-run.yaml
```

### CLI：调试 Selectors

```bash
.venv/bin/glm-plan debug-selectors --config config.yaml --headful
```

这会打开页面、保存 HTML 快照，并打印当前 tab/card/button 的文本与 attrs。

等价 Make target：

```bash
make debug
```

### Daemon：REST + WebSocket

在随机 localhost 端口启动本地 daemon：

```bash
.venv/bin/glm-plan serve \
  --host 127.0.0.1 \
  --port 0 \
  --db daemon.sqlite3 \
  --handshake daemon.handshake.json
```

`--port 0` 会让 OS 分配一个空闲端口。如果省略 `--token`，daemon 会生成一个本次启动专用的 token。handshake 文件包含：

```json
{"host": "127.0.0.1", "port": 49152, "token": "..."}
```

只有 `/health` 不需要鉴权。其他 REST 路由都需要 `Authorization: Bearer <token>`。`/ws/events` 需要 `?token=<token>`。

在另一个终端中：

```bash
export no_proxy=127.0.0.1,localhost
export HANDSHAKE=daemon.handshake.json
export HOST=$(.venv/bin/python -c 'import json,os; print(json.load(open(os.environ["HANDSHAKE"]))["host"])')
export PORT=$(.venv/bin/python -c 'import json,os; print(json.load(open(os.environ["HANDSHAKE"]))["port"])')
export TOKEN=$(.venv/bin/python -c 'import json,os; print(json.load(open(os.environ["HANDSHAKE"]))["token"])')
export BASE_URL="http://$HOST:$PORT"
```

创建一个账号和一个 dry-run 目标：

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

启动、查看并停止 headless worker：

```bash
curl -sS -X POST "$BASE_URL/accounts/$ACCOUNT_ID/worker/start" \
  -H "Authorization: Bearer $TOKEN"

curl -sS "$BASE_URL/events?account_id=$ACCOUNT_ID&limit=20" \
  -H "Authorization: Bearer $TOKEN"

curl -sS -X POST "$BASE_URL/accounts/$ACCOUNT_ID/worker/stop" \
  -H "Authorization: Bearer $TOKEN"
```

打开可见的 login 或 handoff 会话：

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

订阅实时事件：

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

启动 daemon 的等价 Make target：

```bash
make serve
```

### Tauri GUI 开发

先安装 GUI 前置条件：Rust/Cargo 和 Node/npm。

```bash
export GLM_WATCHER_DAEMON_BIN="$PWD/.venv/bin/glm-plan"
npm install
npm run tauri:dev
```

等价 Make target：

```bash
make gui
```

Tauri shell 会以 `--port 0`、本次启动专用 token，以及 app-data handshake 文件启动 daemon。daemon binary 查找顺序：

1. `GLM_WATCHER_DAEMON_BIN`
2. Tauri resource/current-exe 目录中的 bundled sidecar
3. `PATH` 上的 `glm-plan`

在 GUI 中，使用绝对路径形式的 `user_data_dir` 添加账号，添加 targets，并按 target 设置 dry-run、开售窗口、快刷节奏和自动 handoff，然后使用 Start/Login/Handoff。daemon worker 是 headless 的。Login 和 handoff 会先停止该账号 worker，再用同一个 profile 打开可见浏览器。同一个账号 profile 不能并发使用。

### 构建 macOS `.app`

安装打包依赖、构建 Python sidecar、复制到 Tauri target-triple 名称，然后构建：

```bash
make app
```

Apple Silicon 的手动等价步骤：

```bash
.venv/bin/pip install -e ".[server,packaging]"
.venv/bin/pyinstaller packaging/glm-plan-daemon.spec --noconfirm --distpath sidecar/bin
cp sidecar/bin/glm-plan-daemon sidecar/bin/glm-plan-daemon-aarch64-apple-darwin
chmod +x sidecar/bin/glm-plan-daemon-aarch64-apple-darwin
npm install
npm run tauri:build
```

Intel Mac 的手动等价步骤：改为复制到 `sidecar/bin/glm-plan-daemon-x86_64-apple-darwin`。

仓库中包含一个小型开发占位 shim：`sidecar/bin/glm-plan-daemon-aarch64-apple-darwin`，它会把请求转交给 `PATH` 上的 `glm-plan`。它不是生产 daemon 二进制。发布 `.app` 前，必须用 PyInstaller 构建出的 sidecar 替换它。

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

常用覆盖参数：

```bash
make watch CONFIG=config.dry-run.yaml
make serve DB=/tmp/glm-watcher.sqlite3 HANDSHAKE=/tmp/glm-watcher.handshake.json
make gui GLM_WATCHER_DAEMON_BIN="$PWD/.venv/bin/glm-plan"
```

## Lockfile Policy

这是一个应用型仓库，因此应提交 lockfiles，以便 GUI 和打包构建可复现：

- `package-lock.json` 应提交。
- `src-tauri/Cargo.lock` 应继续提交。

生成产物仍然会被忽略：`node_modules/`、`src-tauri/target/`、daemon SQLite 文件、handshake/token 文件、PyInstaller build/dist 产物，以及真实 sidecar 二进制。

## 故障排查

### 本地 HTTP 代理导致 local daemon curl 返回 502

如果 shell 中有代理变量，例如 `http_proxy=http://127.0.0.1:7897`，`curl` 可能会把本地 daemon 请求发给代理，从而得到 502。

使用：

```bash
export no_proxy=127.0.0.1,localhost
```

或者在当前 shell 中清空代理变量：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
```

如果 GUI 显示 `Failed to fetch` 或账号列表加载不出来，也要检查 macOS 系统代理设置，把
`127.0.0.1` / `localhost` 加入绕过代理列表。

### Playwright 无法启动 Chromium

为 Playwright 安装 Chromium：

```bash
.venv/bin/playwright install chromium
```

首次安装可能较慢。

### Python 版本过低

项目要求 Python 3.11+。建议使用 Homebrew Python 3.12：

```bash
brew install python@3.12
make setup
```

如果 `python3` 指向较旧版本，运行：

```bash
PYTHON=/opt/homebrew/bin/python3.12 make setup
```

### 未签名 `.app` 被 Gatekeeper 拦截

对于本地未签名构建，macOS 可能提示应用“无法打开，因为无法验证开发者”。

可选处理方式：

```bash
xattr -dr com.apple.quarantine "src-tauri/target/release/bundle/macos/GLM Plan Watcher.app"
```

或者右键点击应用并选择 Open。

本地测试时也可以做 ad-hoc sign：

```bash
codesign --force --deep --sign - "src-tauri/target/release/bundle/macos/GLM Plan Watcher.app"
```

生产分发应使用正式的 Apple Developer 签名与 notarization。

### 误发布 sidecar 占位 shim

如果打包后的应用只在已经把 `glm-plan` 放到 `PATH` 的机器上可用，通常意味着你发布了开发 shim，而不是真正的 PyInstaller sidecar。重新构建：

```bash
make app
```

并确认 target-triple sidecar 是真实二进制，而不是：

```sh
#!/usr/bin/env sh
exec glm-plan "$@"
```

### 已订阅账号显示 sold out

页面真实状态取决于登录态。已经订阅的账号可能显示 `sold_out`。全新的未登录 profile 可能显示 `available`，按钮文本类似 `特惠订阅`；安全演示请使用 `dry_run: true`。

### 快刷过于激进

只在真实开售时段使用 active window，例如 `10:00` 到 `10:30`。调度器会把快刷间隔钳制到非零硬下限，并保留 jitter；但过低的取值仍可能触发限流或风控。

## 开发检查

```bash
.venv/bin/python -m compileall src
.venv/bin/pytest
.venv/bin/ruff check .
cargo check --manifest-path src-tauri/Cargo.toml
```

## DOM 检测说明

当前 GLM DOM selectors：

- 计费周期 tabs: `#switchTabBox .switch-tab-item`
- 套餐 cards: `.package-list .package-card-box`
- 卡片 title: `.package-card-title span.font-prompt`
- 入口 button: `button.buy-btn`

当站点变化时，使用 `debug-selectors` 重新检查。
