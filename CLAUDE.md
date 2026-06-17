# CLAUDE.md

## 架构

`glm-coding-plan-watcher` 当前实现的是未来编排系统中的 worker 单元：

```text
config.yaml -> glm-plan watch -> Playwright persistent context
                         |
                         +-> DomDetector -> CheckResult
                         +-> WatchEvent JSON lines on stdout
                         +-> logs / screenshots / HTML snapshots / optional trace
                         +-> notifier console / desktop / webhook
```

一个配置文件对应一个目标、一个浏览器 profile、一个进程。多目标监控由上层启动多个进程完成。

## 模块职责

- `models.py`: 枚举和数据契约，包含 `WatchEvent` JSON 行 schema。
- `config.py`: YAML + 环境变量配置加载；枚举从 `models.py` 导入，避免循环依赖。
- `selectors.py`: 所有页面 CSS selector、周期文案、可用/不可用同义词表。
- `detector.py`: `DetectorStrategy` 抽象、`DomDetector` 三段定位、`ApiDetector` 占位、`classify_button` 纯函数。
- `browser.py`: Playwright persistent context、页面打开、trace、截图/HTML 捕获入口。
- `storage.py`: 运行产物目录、时间戳文件名、截图和 HTML 落盘。
- `logging_setup.py`: Rich 人类日志和 `logs/app.log` rotating file。
- `notifier.py`: console / desktop / webhook 通知，失败降级为 warning。
- `watcher.py`: 循环、jitter、状态变化、stdout JSON、SIGTERM/SIGINT、命中后动作。
- `cli.py`: `init-config` / `login` / `check` / `watch` / `debug-selectors`。

## 检测策略

1. 在 `#switchTabBox` 内按中文周期标签定位 `.switch-tab-item`，必要时点击切换。
2. 在 `.package-list` 内遍历 `.package-card-box`，按 `.package-card-title span.font-prompt` 精确匹配档位。
3. 在卡片作用域内读取 `button.buy-btn`，用 `classify_button(text, attrs)` 判定状态。

售罄真实 DOM 已校准；可购买态样本仍是假设。补货后优先使用 `glm-plan debug-selectors --config config.yaml --headful` 获取真实按钮文案和属性，再更新 `selectors.py`、fixture 和测试。

## 后续路线图

1. FastAPI 编排层：以子进程方式启动多个 watcher，读取 stdout JSON 行，提供 WebSocket 状态/日志推送，发送 SIGTERM 优雅停止。
2. React 任务界面：管理多份 config，展示当前状态、最近事件、日志、截图和 HTML 快照入口。
3. Tauri macOS `.app`：菜单栏入口、桌面通知、后台 worker 管理。

当前 watcher 的 stdout JSON 行协议就是未来父子进程契约，不要把人类日志写入 stdout。

## 合规边界

不得实现验证码识别、滑块破解、风控绕过、隐藏自动化、并发刷接口、自动下单支付或账号密码保存。当前允许的自动化仅限页面检测、低频刷新、通知，以及命中时点击一次购买/订阅入口后等待人工。
