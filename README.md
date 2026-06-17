# glm-coding-plan-watcher

浏览器自动化 worker，用于监控 [GLM Coding Plan](https://www.bigmodel.cn/glm-coding)
指定套餐（计费周期 × Lite/Pro/Max）是否可购买。命中时会记录日志、截图、保存 HTML 快照、
发送通知，并默认点击「购买/订阅入口」后暂停，等待人工完成后续支付。

本项目仅用于个人可用性监控与提醒。使用者需自行遵守目标网站服务条款。

## 安装

本项目要求 Python 3.11+。当前开发环境建议使用 Homebrew Python 3.12：

```bash
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/playwright install chromium
```

常用验证：

```bash
.venv/bin/python -m compileall src
.venv/bin/pytest
.venv/bin/ruff check .
```

## 首次登录

先生成配置，再打开可视化浏览器手动登录。登录态保存在 `user_data_dir`，项目不保存账号密码。

```bash
.venv/bin/glm-plan init-config --output config.yaml
.venv/bin/glm-plan login --config config.yaml
```

登录完成后回到终端按 Enter，后续 `check` / `watch` 会复用同一 `user_data_dir`。

## 配置

参考 [config.example.yaml](config.example.yaml)。一个配置文件只对应一个目标和一个进程；多目标请复制多份配置并使用不同的 `user_data_dir`。

关键字段：

- `billing_cycle`: `monthly` / `quarterly` / `yearly`，对应页面「连续包月 / 连续包季 / 连续包年」。
- `tier`: `Lite` / `Pro` / `Max`。
- `refresh_interval_seconds`: 基础刷新间隔，默认 `90` 秒。
- `refresh_jitter_seconds`: 随机抖动，默认 `30` 秒，避免固定频率刷新。
- `auto_click_entry`: 默认 `true`。命中时只点击购买/订阅入口，然后暂停等待人工。
- `dry_run`: 默认 `false`。设为 `true` 时只检测和通知，不点击入口。
- `notify.webhook_url`: 建议用环境变量 `GLM_WATCHER__NOTIFY__WEBHOOK_URL` 设置。

配置优先级：环境变量和 `.env` 高于 YAML，高于默认值。

## 命令

单次检测，退出码 `0` 表示可购买，`1` 表示不可购买或未定位到：

```bash
.venv/bin/glm-plan check --config config.yaml
```

循环监控。`watch` 每轮向 stdout 输出一行 `WatchEvent` JSON，供后续 FastAPI 父进程消费；人类日志写入 stderr 和 `logs/app.log`。

```bash
.venv/bin/glm-plan watch --config config.yaml
```

调试 selector，保存当前 HTML 并输出周期 tab、套餐卡片、按钮文本和属性：

```bash
.venv/bin/glm-plan debug-selectors --config config.yaml --headful
```

## 检测策略

当前真实 DOM 校准结果：

- 周期切换器：`#switchTabBox .switch-tab-item`，激活态 class 包含 `active`。
- 套餐卡片：`.package-list .package-card-box`。
- 卡片标题：`.package-card-title span.font-prompt`，文本精确匹配 `Lite` / `Pro` / `Max`。
- 按钮：`button.buy-btn`。

真实售罄按钮样本：

```html
<button disabled="disabled" class="el-button el-tooltip buy-btn el-button--primary is-disabled disabled" name="暂时售罄 ｜06月18日 10:00 补货">
  <span> 暂时售罄 ｜06月18日 10:00 补货 </span>
</button>
```

`tests/fixtures/sold_out.html` 使用该真实售罄标记。`available.html` 和 `unavailable.html` 是占位假设样本；当前没有真实可购买 DOM。页面补货后应运行 `debug-selectors`，用真实按钮文案和属性校准 `selectors.py` 与 fixture。

## 安全与合规边界

本项目不会实现，也不应添加：

- 验证码识别、滑块破解、风控绕过。
- 隐藏自动化痕迹或规避检测。
- 并发刷接口、下单接口轮询、抢购支付自动化。
- 保存账号密码。
- 自动确认付款或完成支付。

允许行为只限：正常页面打开、低频带 jitter 刷新、读取公开 DOM、通知用户、命中时点击一次购买/订阅入口并等待人工。

## GitHub 初始化参考

```bash
git init
git add .
git commit -m "chore: project scaffold"
git remote add origin <repo-url>
git push -u origin main
```

## 开发备注

结构化事件模型在 `WatchEvent` 中定义。未来编排层可以把每个 watcher 当作子进程 worker，通过 stdout JSON 行读取状态，通过 SIGTERM/SIGINT 优雅停止。
