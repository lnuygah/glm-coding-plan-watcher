# Architecture

## Target Shape

The long-term app has three layers:

```text
Tauri menu-bar app
  <-> localhost HTTP/WebSocket
Python daemon + SQLite
  <-> account-level worker processes
Playwright persistent browser session
```

P0 keeps everything inside the current Python package. The important contract change is that a worker can now represent one account and sequentially scan multiple targets in one browser session.

## Current P0 Worker Contract

- One account maps to one `user_data_dir` and one Playwright persistent context.
- `targets` is optional in config. If absent, the worker falls back to the legacy top-level `billing_cycle` + `tier`.
- The worker checks targets sequentially, never concurrently, and applies one account-level interval plus jitter after each scan round.
- Each target still emits a `WatchEvent` JSON line.
- Each scan round emits a `heartbeat` JSON line for future watchdogs.
- `auth_required` is a detector state for login-wall or session-expired pages.

This preserves the old single-target CLI behavior while making the loop reusable by a future daemon.

## GUI And Daemon Split

Tauri should own desktop integration: menu-bar UI, windows, notifications, app startup, and sidecar lifecycle. Python should own Playwright, detection, scheduling, persistence, and worker supervision.

Recommended P1/P2 shape:

- Tauri starts a Python sidecar daemon.
- The daemon exposes localhost HTTP for CRUD and WebSocket for live status/events/logs.
- The daemon stores state in SQLite.
- The daemon starts one worker process per account.
- Workers communicate upward using stdout JSON lines and SIGTERM/SIGINT for shutdown.

Use a random localhost port plus a per-launch token. Do not expose the daemon on public interfaces.

## Data Model

SQLite is preferred over loose files once GUI task management exists.

Suggested tables:

- `accounts`: display name, `user_data_dir`, status, last login time.
- `targets`: account id, billing cycle, tier, enabled flag, interval, jitter, dry-run flag, auto-click flag.
- `events`: account id, target id, event type, button state, button text, action, timestamp, raw JSON.
- `artifacts`: event id, screenshot path, HTML path, trace path.
- `workers`: account id, pid, status, started time, last heartbeat time.

Runtime artifacts remain files on disk; SQLite stores indexes and metadata.

## Headless And Visible Moments

Normal monitoring should run headless. Two moments must be visible:

- First login: open a headful browser using that account's `user_data_dir`; the user logs in manually.
- Purchase/payment handoff: when a target becomes available, notify the user, bring up a headful session, optionally click only the purchase/subscription entry, then pause for manual payment.

A headless browser should not try to complete login, CAPTCHA, risk checks, or payment. If the session expires, workers should emit `auth_required`; the GUI should show "login required" and offer a button to open the login window.

## Hit Handling

On availability:

1. Save screenshot and HTML snapshot.
2. Emit a `hit` event.
3. Send configured notifications.
4. If enabled and not dry-run, click only the purchase/subscription entry.
5. Keep the browser open for manual completion.

The app must not click payment confirmation buttons, solve verification, or bypass risk controls.

## Scheduling

Workers should remain low-frequency and jittered per account. Multi-target checks must be sequential inside one account session.

Restock text such as "06月18日 10:00 补货" can be parsed as a scheduling hint:

- Far from restock time: reduce checks.
- Near restock time: temporarily tighten checks within safe minimum limits.
- If parsing fails or text changes: fall back to the normal interval plus jitter.

Restock parsing must never become aggressive polling.

## Packaging

Recommended packaging path:

- Build Python daemon/worker as a PyInstaller sidecar.
- Bundle the sidecar with Tauri.
- Tauri starts/stops the sidecar and connects via localhost HTTP/WebSocket.
- Python daemon owns child worker process lifecycles.
- Upgrades should migrate SQLite schema and preserve `user_data_dir` directories.

Avoid stdio between Tauri and the daemon except for boot diagnostics; localhost HTTP/WebSocket is easier for multi-window GUI state and live logs.

## Extension Points

- `DetectorStrategy`: DOM detector now, compliant official API detector later if available.
- `SiteAdapter`: future abstraction for URL, selectors, auth detection, target dimensions, and entry-click behavior.
- `Notifier`: console, desktop, webhook now; email or chat integrations later.
- `SchedulerPolicy`: fixed interval now; restock-time hints later.

Do not over-generalize before a second site/product exists. Keep GLM-specific selectors in the GLM adapter path.

## Compliance And Product Constraints

GUI features must keep the same safety boundary:

- No CAPTCHA solving, slider cracking, risk bypass, or hidden automation.
- No automatic payment or payment confirmation.
- No account/password storage.
- No concurrent checks against one account profile.
- No aggressive polling; enforce safe minimum intervals.
- No UI language that encourages multi-account抢购 or bulk abuse.

The product should present itself as availability monitoring and handoff, not automated purchasing.

## Roadmap

- P0: account-level multi-target monitor, `auth_required`, heartbeat, architecture blueprint.
- P1: local daemon with SQLite, task/account CRUD, worker supervision, restart/backoff, WebSocket event stream.
- P2: Tauri menu-bar shell, sidecar packaging, login/payment visible handoff.
- P3: restock-time scheduler hints, richer notifications, optional site adapter abstraction.
