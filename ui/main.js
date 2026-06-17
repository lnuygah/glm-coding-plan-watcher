const daemonUrlInput = document.querySelector("#daemon-url");
const daemonTokenInput = document.querySelector("#daemon-token");
const accountsEl = document.querySelector("#accounts");
const eventsEl = document.querySelector("#events");
const refreshButton = document.querySelector("#refresh");
const accountForm = document.querySelector("#account-form");
const statusEl = document.querySelector("#status");
const languageSelect = document.querySelector("#language-select");

const LANGUAGE_STORAGE_KEY = "glm-plan-watcher-language";

const translations = {
  "zh-CN": {
    documentTitle: "GLM Plan Watcher",
    appSubtitle: "后台 headless 监控；登录和命中付款衔接才打开可见浏览器。",
    daemonLabel: "Daemon",
    tokenLabel: "Token",
    languageLabel: "语言",
    notice:
      "可为每个 target 配置开售窗口与快刷间隔。过低间隔可能触发限流/风控，反而买不到；Handoff 只打开可见浏览器并可点击一次购买入口，后续支付必须人工确认。",
    accountsTitle: "账号",
    eventsTitle: "事件",
    refreshButton: "刷新",
    accountNamePlaceholder: "账号名称",
    profilePlaceholder: "可选：留空自动管理，或填绝对路径导入已有 profile",
    advancedLabel: "高级/诊断",
    addAccountButton: "添加",
    noTargets: "暂无 targets",
    addMonitorTask: "添加监控任务",
    addTargetButton: "添加 target",
    startButton: "启动",
    stopButton: "停止",
    loginButton: "登录",
    handoffButton: "接管付款",
    handoffButtonTitle: "用可见浏览器接管去付款：打开可见浏览器，最多点击一次购买入口；最终支付必须人工确认。",
    saveButton: "保存",
    deleteButton: "删除",
    enabledStatus: "启用",
    disabledStatus: "停用",
    billingLabel: "计费周期",
    tierLabel: "套餐",
    baseIntervalLabel: "基础间隔",
    baseJitterLabel: "基础抖动",
    windowStartLabel: "开售窗口开始",
    windowEndLabel: "开售窗口结束",
    timezoneLabel: "时区",
    activeIntervalLabel: "窗口内间隔",
    activeJitterLabel: "窗口内抖动",
    idleIntervalLabel: "窗口外间隔",
    timezonePlaceholder: "本机时区或 Asia/Shanghai",
    enabledLabel: "启用",
    dryRunLabel: "Dry run 安全模式",
    clickEntryLabel: "接管时点击购买入口",
    openHandoffLabel: "命中后自动接管付款",
    visibleInWindowLabel: "开售时段用可见浏览器常驻（命中秒点）",
    advancedSettingsLabel: "高级设置",
    firstRunGuide: "① 登录 → ② 加任务 → ③ 开始监控",
    startMonitorButton: "③ 开始监控",
    addMonitorTaskPrimary: "② 添加监控任务",
    needLoginPrompt: "该账号需要登录：请点「① 登录」，在弹出的可见浏览器中手动登录，完成后关闭浏览器。",
    loginStepButton: "① 登录",
    failedToFetch:
      "Failed to fetch. 请检查 daemon URL/token、CORS，以及 127.0.0.1/localhost 是否绕过本机代理。",
    addTargetFailed: "添加 target 失败",
    actionFailed: "{action} 失败",
    saveTargetFailed: "保存 target 失败",
    handoffFailed: "Handoff 失败",
    deleteTargetFailed: "删除 target 失败",
    connectEventsFailed: "连接事件流失败",
    webSocketFailed: "WebSocket 连接失败。请检查 daemon URL/token 和本机代理绕过。",
    webSocketClosed: "WebSocket 已关闭：code={code}",
    reloadAccountsFailed: "重新加载账号失败",
    tauriUnavailable: "Tauri bridge 不可用；请手动填写 Daemon 和 Token。",
    handshakeMissing: "未找到 daemon handshake。请检查 Tauri sidecar daemon 是否启动。",
    eventActionFailed: "{action} 失败",
    addAccountFailed: "添加账号失败",
    refreshFailed: "刷新失败",
    loadAccountsFailed: "加载账号失败",
  },
  en: {
    documentTitle: "GLM Plan Watcher",
    appSubtitle: "Headless background monitoring; visible browser opens only for login and payment handoff.",
    daemonLabel: "Daemon",
    tokenLabel: "Token",
    languageLabel: "Language",
    notice:
      "Configure sale windows and fast refresh per target. Too-low intervals can trigger rate limits or risk checks and reduce your chance of purchase. Handoff only opens a visible browser and may click the purchase entry once; final payment must be manual.",
    accountsTitle: "Accounts",
    eventsTitle: "Events",
    refreshButton: "Refresh",
    accountNamePlaceholder: "Account name",
    profilePlaceholder: "Optional: leave blank to auto-manage, or enter an absolute path to import an existing profile",
    advancedLabel: "Advanced / diagnostics",
    addAccountButton: "Add",
    noTargets: "No targets",
    addMonitorTask: "Add monitor task",
    addTargetButton: "Add target",
    startButton: "Start",
    stopButton: "Stop",
    loginButton: "Login",
    handoffButton: "Take over payment",
    handoffButtonTitle: "Take over payment in a visible browser: opens a visible browser and may click the purchase entry once; final payment is manual.",
    saveButton: "Save",
    deleteButton: "Delete",
    enabledStatus: "enabled",
    disabledStatus: "disabled",
    billingLabel: "Billing",
    tierLabel: "Tier",
    baseIntervalLabel: "Base interval",
    baseJitterLabel: "Base jitter",
    windowStartLabel: "Window start",
    windowEndLabel: "Window end",
    timezoneLabel: "Timezone",
    activeIntervalLabel: "Active interval",
    activeJitterLabel: "Active jitter",
    idleIntervalLabel: "Idle interval",
    timezonePlaceholder: "local or Asia/Shanghai",
    enabledLabel: "Enabled",
    dryRunLabel: "Dry run",
    clickEntryLabel: "Click purchase entry on take-over",
    openHandoffLabel: "Auto take over payment on hit",
    visibleInWindowLabel: "Keep a visible browser during the sale window",
    advancedSettingsLabel: "Advanced settings",
    firstRunGuide: "① Login → ② Add task → ③ Start monitoring",
    startMonitorButton: "③ Start monitoring",
    addMonitorTaskPrimary: "② Add monitor task",
    needLoginPrompt: "This account needs login: click 《① Login》, sign in manually in the visible browser, then close it.",
    loginStepButton: "① Login",
    failedToFetch:
      "Failed to fetch. Check daemon URL/token, CORS, and local proxy bypass for 127.0.0.1/localhost.",
    addTargetFailed: "Add target failed",
    actionFailed: "{action} failed",
    saveTargetFailed: "Save target failed",
    handoffFailed: "Handoff failed",
    deleteTargetFailed: "Delete target failed",
    connectEventsFailed: "Connect events failed",
    webSocketFailed: "WebSocket connection failed. Check daemon URL/token and local proxy bypass.",
    webSocketClosed: "WebSocket closed: code={code}",
    reloadAccountsFailed: "Reload accounts failed",
    tauriUnavailable: "Tauri bridge is unavailable; fill Daemon and Token manually.",
    handshakeMissing: "Daemon handshake was not found. Check that the Tauri sidecar daemon started.",
    eventActionFailed: "{action} failed",
    addAccountFailed: "Add account failed",
    refreshFailed: "Refresh failed",
    loadAccountsFailed: "Load accounts failed",
  },
};

const billingEventLabels = {
  monthly: "连续包月",
  quarterly: "连续包季",
  yearly: "连续包年",
};

const billingDisplayLabels = {
  "zh-CN": billingEventLabels,
  en: {
    monthly: "monthly",
    quarterly: "quarterly",
    yearly: "yearly",
  },
};

const accountStatusLabels = {
  "zh-CN": {
    running: "运行中",
    stopped: "已停止",
    login: "登录中",
    handoff: "付款衔接",
    crashed: "已崩溃",
    exited: "已退出",
  },
  en: {
    running: "running",
    stopped: "stopped",
    login: "login",
    handoff: "handoff",
    crashed: "crashed",
    exited: "exited",
  },
};

const actionKeys = {
  start: "startButton",
  stop: "stopButton",
  login: "loginButton",
  handoff: "handoffButton",
};

let ws = null;
const accountTargets = new Map();
// Login state is not exposed by the API; we only flag accounts that emitted an
// auth_required event so we can surface a prominent "needs login" prompt.
const authRequiredAccounts = new Set();
let currentLanguage = detectLanguage();

function detectLanguage() {
  const stored = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
  if (stored && translations[stored]) {
    return stored;
  }
  return navigator.language?.toLowerCase().startsWith("zh") ? "zh-CN" : "en";
}

function t(key, params = {}) {
  const template = translations[currentLanguage]?.[key] ?? translations.en[key] ?? key;
  return template.replace(/\{(?<name>\w+)\}/g, (_match, name) => params[name] ?? "");
}

function applyI18n() {
  document.documentElement.lang = currentLanguage;
  document.title = t("documentTitle");
  languageSelect.value = currentLanguage;
  document.querySelectorAll("[data-i18n]").forEach((element) => {
    element.textContent = t(element.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((element) => {
    element.placeholder = t(element.dataset.i18nPlaceholder);
  });
}

function actionText(action) {
  return t(actionKeys[action] || action);
}

function daemonUrl() {
  return daemonUrlInput.value.replace(/\/$/, "");
}

function daemonToken() {
  return daemonTokenInput.value.trim();
}

async function api(path, init = {}) {
  const headers = { "Content-Type": "application/json", ...(init.headers ?? {}) };
  if (daemonToken()) {
    headers.Authorization = `Bearer ${daemonToken()}`;
  }
  const response = await fetch(`${daemonUrl()}${path}`, {
    ...init,
    headers,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${response.status} ${response.statusText}${body ? `: ${body}` : ""}`);
  }
  if (response.status === 204) {
    return undefined;
  }
  return await response.json();
}

function showStatus(message, kind = "error") {
  statusEl.textContent = message;
  statusEl.dataset.kind = kind;
  statusEl.hidden = false;
}

function clearStatus() {
  statusEl.textContent = "";
  statusEl.hidden = true;
}

// WKWebView (macOS) surfaces network failures as TypeErrors with messages other
// than Chrome's "Failed to fetch"; map the common ones to the same friendly hint.
const NETWORK_FAILURE_MESSAGES = new Set([
  "Failed to fetch",
  "Load failed",
  "cancelled",
  "The network connection was lost.",
]);

function errorText(error) {
  if (error instanceof TypeError && NETWORK_FAILURE_MESSAGES.has(error.message)) {
    return t("failedToFetch");
  }
  return error?.message || String(error);
}

async function withUiError(label, action) {
  try {
    clearStatus();
    return await action();
  } catch (error) {
    showStatus(`${label}: ${errorText(error)}`);
    return undefined;
  }
}

async function loadAccounts() {
  const accounts = await api("/accounts");
  accountsEl.innerHTML = "";
  accountTargets.clear();
  for (const account of accounts) {
    const targets = await api(`/accounts/${account.id}/targets`);
    accountTargets.set(account.id, targets);
    accountsEl.append(renderAccount(account, targets));
  }
}

function renderAccount(account, targets) {
  const row = document.createElement("article");
  row.className = "account";
  const status = account.status || "stopped";
  const hasTargets = targets.length > 0;
  const needsLogin = authRequiredAccounts.has(account.id);
  row.innerHTML = `
    <div class="account-title">
      <div>
        <strong>${escapeHtml(account.display_name)}</strong>
        <div class="profile">${escapeHtml(account.user_data_dir)}</div>
      </div>
      <span class="status-badge" data-status="${escapeAttr(status)}">${escapeHtml(accountStatusLabel(status))}</span>
    </div>
    ${needsLogin ? `<p class="login-prompt">${escapeHtml(t("needLoginPrompt"))}</p>` : ""}
    <div class="actions">
      ${accountActionsMarkup(status, hasTargets)}
    </div>
    <div class="targets"></div>
    <details class="add-target"${hasTargets ? "" : " open"}>
      <summary>${escapeHtml(t("addMonitorTask"))}</summary>
      <form class="target-form">
        ${targetFormFields()}
        <button type="submit">${escapeHtml(t("addTargetButton"))}</button>
      </form>
    </details>
  `;

  const targetsContainer = row.querySelector(".targets");
  if (targets.length === 0) {
    targetsContainer.innerHTML = `<em>${escapeHtml(t("noTargets"))}</em>`;
  } else {
    for (const target of targets) {
      targetsContainer.append(renderTarget(account, target));
    }
  }

  row.querySelector(".target-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    await withUiError(t("addTargetFailed"), async () => {
      await api(`/accounts/${account.id}/targets`, {
        method: "POST",
        body: JSON.stringify(targetPayloadFromForm(event.currentTarget)),
      });
      event.currentTarget.reset();
      await loadAccounts();
    });
  });

  row.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const action = button.dataset.action;
      // "addtarget" is a pure UI shortcut: reveal the add-target form, no API call.
      if (action === "addtarget") {
        const details = row.querySelector(".add-target");
        if (details) {
          details.open = true;
          details.scrollIntoView({ behavior: "smooth", block: "nearest" });
          details.querySelector('[name="billing_cycle"]')?.focus();
        }
        return;
      }
      await withUiError(t("actionFailed", { action: actionText(action) }), async () => {
        if (action === "start") await api(`/accounts/${account.id}/worker/start`, { method: "POST" });
        if (action === "stop") await api(`/accounts/${account.id}/worker/stop`, { method: "POST" });
        if (action === "login") {
          await api(`/accounts/${account.id}/login`, { method: "POST", body: "{}" });
          authRequiredAccounts.delete(account.id);
        }
        if (action === "handoff") {
          await api(`/accounts/${account.id}/handoff`, {
            method: "POST",
            body: JSON.stringify({ click_entry: false }),
          });
        }
        await loadAccounts();
      });
    });
  });
  return row;
}

// Derive a single prominent primary call-to-action from (status + hasTargets),
// demoting the rest to secondary. Login stays reachable as a labeled step since
// the API does not expose login state.
function accountActionsMarkup(status, hasTargets) {
  const buttons = [];
  const seen = new Set();
  const add = (action, labelKey, { primary = false, titleKey } = {}) => {
    if (seen.has(action)) return;
    seen.add(action);
    const cls = primary ? "primary-action" : "secondary-action";
    const title = titleKey ? ` title="${escapeAttr(t(titleKey))}"` : "";
    buttons.push(
      `<button data-action="${escapeAttr(action)}" class="${cls}"${title}>${escapeHtml(t(labelKey))}</button>`,
    );
  };

  if (status === "running") {
    add("stop", "stopButton", { primary: true });
  } else if (status === "login") {
    add("login", "loginStepButton", { primary: true });
  } else if (status === "handoff") {
    add("handoff", "handoffButton", { primary: true, titleKey: "handoffButtonTitle" });
  } else if (!hasTargets) {
    add("addtarget", "addMonitorTaskPrimary", { primary: true });
  } else {
    add("start", "startMonitorButton", { primary: true });
  }

  // Labeled secondary steps. Login is always reachable.
  add("login", "loginStepButton");
  if (hasTargets && status !== "running") add("start", "startButton");
  if (status === "running") add("stop", "stopButton");
  add("handoff", "handoffButton", { titleKey: "handoffButtonTitle" });

  return buttons.join("");
}

function renderTarget(account, target) {
  const item = document.createElement("article");
  item.className = "target-card";
  item.innerHTML = `
    <div class="target-title">
      <strong>${escapeHtml(billingDisplayLabel(target.billing_cycle))} / ${escapeHtml(target.tier)}</strong>
      <span>${target.enabled ? escapeHtml(t("enabledStatus")) : escapeHtml(t("disabledStatus"))}</span>
    </div>
    <form class="target-edit-form">
      ${targetFormFields(target)}
      <div class="target-actions">
        <button type="submit">${escapeHtml(t("saveButton"))}</button>
        <button type="button" data-target-action="handoff" title="${escapeAttr(t("handoffButtonTitle"))}">${escapeHtml(t("handoffButton"))}</button>
        <button type="button" data-target-action="delete">${escapeHtml(t("deleteButton"))}</button>
      </div>
    </form>
  `;
  setSelectValue(item, "billing_cycle", target.billing_cycle);
  setSelectValue(item, "tier", target.tier);

  item.querySelector(".target-edit-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    await withUiError(t("saveTargetFailed"), async () => {
      await api(`/targets/${target.id}`, {
        method: "PATCH",
        body: JSON.stringify(targetPayloadFromForm(event.currentTarget)),
      });
      await loadAccounts();
    });
  });

  item.querySelector('[data-target-action="handoff"]').addEventListener("click", async () => {
    await withUiError(t("handoffFailed"), async () => {
      await api(`/accounts/${account.id}/handoff`, {
        method: "POST",
        body: JSON.stringify({
          target_id: target.id,
          click_entry: Boolean(target.auto_click_entry),
          restore_worker: false,
        }),
      });
      await loadAccounts();
    });
  });

  item.querySelector('[data-target-action="delete"]').addEventListener("click", async () => {
    await withUiError(t("deleteTargetFailed"), async () => {
      await api(`/targets/${target.id}`, { method: "DELETE" });
      await loadAccounts();
    });
  });

  return item;
}

function targetFormFields(target = {}) {
  return `
    <div class="form-grid">
      <label>
        ${escapeHtml(t("billingLabel"))}
        <select name="billing_cycle">
          <option value="monthly">${escapeHtml(billingDisplayLabel("monthly"))}</option>
          <option value="quarterly">${escapeHtml(billingDisplayLabel("quarterly"))}</option>
          <option value="yearly">${escapeHtml(billingDisplayLabel("yearly"))}</option>
        </select>
      </label>
      <label>
        ${escapeHtml(t("tierLabel"))}
        <select name="tier">
          <option value="Lite">Lite</option>
          <option value="Pro" selected>Pro</option>
          <option value="Max">Max</option>
        </select>
      </label>
      <label>
        ${escapeHtml(t("windowStartLabel"))}
        <input name="active_window_start" placeholder="10:00" value="${escapeAttr(target.active_window_start || "")}" />
      </label>
      <label>
        ${escapeHtml(t("windowEndLabel"))}
        <input name="active_window_end" placeholder="10:30" value="${escapeAttr(target.active_window_end || "")}" />
      </label>
    </div>
    <div class="checkbox-row">
      ${checkbox("enabled", target.enabled ?? true, t("enabledLabel"))}
      ${checkbox("visible_in_window", target.visible_in_window ?? false, t("visibleInWindowLabel"))}
      ${checkbox("on_hit_handoff", target.on_hit_handoff ?? true, t("openHandoffLabel"))}
    </div>
    <details class="advanced-target">
      <summary>${escapeHtml(t("advancedSettingsLabel"))}</summary>
      <div class="form-grid">
        <label>
          ${escapeHtml(t("baseIntervalLabel"))}
          <input name="interval" type="number" min="1" step="1" value="${numberValue(target.interval, 90)}" />
        </label>
        <label>
          ${escapeHtml(t("baseJitterLabel"))}
          <input name="jitter" type="number" min="0" step="1" value="${numberValue(target.jitter, 30)}" />
        </label>
        <label>
          ${escapeHtml(t("timezoneLabel"))}
          <input name="active_timezone" placeholder="${escapeAttr(t("timezonePlaceholder"))}" value="${escapeAttr(target.active_timezone || "")}" />
        </label>
        <label>
          ${escapeHtml(t("activeIntervalLabel"))}
          <input name="active_interval_seconds" type="number" min="1" step="0.5" value="${numberValue(target.active_interval_seconds, 3)}" />
        </label>
        <label>
          ${escapeHtml(t("activeJitterLabel"))}
          <input name="active_jitter_seconds" type="number" min="0" step="0.5" value="${numberValue(target.active_jitter_seconds, 1)}" />
        </label>
        <label>
          ${escapeHtml(t("idleIntervalLabel"))}
          <input name="idle_interval_seconds" type="number" min="1" step="1" value="${numberValue(target.idle_interval_seconds, 600)}" />
        </label>
      </div>
      <div class="checkbox-row">
        ${checkbox("dry_run", target.dry_run ?? false, t("dryRunLabel"))}
        ${checkbox("auto_click_entry", target.auto_click_entry ?? true, t("clickEntryLabel"))}
      </div>
    </details>
  `;
}

function checkbox(name, checked, label) {
  return `
    <label>
      <input name="${name}" type="checkbox" ${checked ? "checked" : ""} />
      ${escapeHtml(label)}
    </label>
  `;
}

function targetPayloadFromForm(form) {
  const data = new FormData(form);
  return {
    billing_cycle: data.get("billing_cycle"),
    tier: data.get("tier"),
    enabled: form.elements.enabled.checked,
    interval: numberFromForm(data, "interval"),
    jitter: numberFromForm(data, "jitter"),
    dry_run: form.elements.dry_run.checked,
    auto_click_entry: form.elements.auto_click_entry.checked,
    active_window_start: String(data.get("active_window_start") || "").trim(),
    active_window_end: String(data.get("active_window_end") || "").trim(),
    active_timezone: String(data.get("active_timezone") || "").trim(),
    active_interval_seconds: numberFromForm(data, "active_interval_seconds"),
    active_jitter_seconds: numberFromForm(data, "active_jitter_seconds"),
    idle_interval_seconds: numberFromForm(data, "idle_interval_seconds"),
    on_hit_handoff: form.elements.on_hit_handoff.checked,
    // Backend support is being added by a parallel task; send it regardless.
    visible_in_window: form.elements.visible_in_window.checked,
  };
}

function numberFromForm(data, key) {
  const value = Number(data.get(key));
  return Number.isFinite(value) ? value : 0;
}

function numberValue(value, fallback) {
  return Number.isFinite(Number(value)) ? Number(value) : fallback;
}

function setSelectValue(root, name, value) {
  const select = root.querySelector(`[name="${name}"]`);
  if (select) select.value = value;
}

function connectEvents() {
  ws?.close();
  const url = daemonUrl().replace(/^http/, "ws");
  const token = daemonToken();
  const query = token ? `?token=${encodeURIComponent(token)}` : "";
  try {
    ws = new WebSocket(`${url}/ws/events${query}`);
  } catch (error) {
    showStatus(`${t("connectEventsFailed")}: ${errorText(error)}`);
    return;
  }
  ws.onmessage = (message) => {
    const payload = JSON.parse(message.data);
    prependEvent(payload);
    if (payload.event?.button_state === "auth_required" && payload.account_id != null) {
      authRequiredAccounts.add(payload.account_id);
    }
    if (payload.event?.button_state === "auth_required" || payload.event?.type === "hit") {
      void withUiError(t("reloadAccountsFailed"), loadAccounts);
    }
  };
  ws.onerror = () => {
    showStatus(t("webSocketFailed"));
  };
  ws.onclose = (event) => {
    if (event.code !== 1000) {
      showStatus(t("webSocketClosed", { code: event.code }), "info");
    }
  };
}

async function loadHandshake() {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke) {
    showStatus(t("tauriUnavailable"), "info");
    return;
  }

  for (let attempt = 0; attempt < 40; attempt += 1) {
    const handshake = await invoke("daemon_handshake").catch(() => null);
    if (handshake?.host && handshake?.port && handshake?.token) {
      daemonUrlInput.value = `http://${handshake.host}:${handshake.port}`;
      daemonTokenInput.value = handshake.token;
      return;
    }
    await sleep(250);
  }
  showStatus(t("handshakeMissing"), "error");
}

function sleep(ms) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function prependEvent(payload) {
  const item = document.createElement("article");
  item.className = "event-card";
  const event = payload.event ?? {};
  const actions = document.createElement("div");
  actions.className = "event-actions";

  if (event.button_state === "auth_required") {
    actions.append(eventButton(t("loginButton"), () => api(`/accounts/${payload.account_id}/login`, {
      method: "POST",
      body: "{}",
    })));
  }
  if (event.type === "hit" && event.available) {
    actions.append(eventButton(t("handoffButton"), () => handoffForEvent(payload)));
  }

  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(payload, null, 2);
  item.append(actions, pre);
  eventsEl.prepend(item);
  while (eventsEl.children.length > 80) {
    eventsEl.lastElementChild?.remove();
  }
}

function eventButton(label, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", async () => {
    await withUiError(t("eventActionFailed", { action: label }), async () => {
      await onClick();
      await loadAccounts();
    });
  });
  return button;
}

async function handoffForEvent(payload) {
  const target = targetForEvent(payload);
  await api(`/accounts/${payload.account_id}/handoff`, {
    method: "POST",
    body: JSON.stringify({
      target_id: target?.id,
      click_entry: Boolean(target?.auto_click_entry),
      restore_worker: false,
    }),
  });
}

function targetForEvent(payload) {
  const targets = accountTargets.get(payload.account_id) ?? [];
  return targets.find((target) => targetLabel(target) === payload.event?.target);
}

function targetLabel(target) {
  return `${billingEventLabels[target.billing_cycle] || target.billing_cycle} / ${target.tier}`;
}

function billingDisplayLabel(billingCycle) {
  return billingDisplayLabels[currentLanguage]?.[billingCycle] || billingCycle;
}

function accountStatusLabel(status) {
  return accountStatusLabels[currentLanguage]?.[status] || status;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => {
    const entities = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return entities[char];
  });
}

function escapeAttr(value) {
  return escapeHtml(value);
}

accountForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(accountForm);
  await withUiError(t("addAccountFailed"), async () => {
    const body = { display_name: form.get("display_name") };
    const userDataDir = String(form.get("user_data_dir") || "").trim();
    // 留空时不传 user_data_dir，由 daemon 自动管理 profile 目录。
    if (userDataDir) {
      body.user_data_dir = userDataDir;
    }
    await api("/accounts", {
      method: "POST",
      body: JSON.stringify(body),
    });
    accountForm.reset();
    await loadAccounts();
  });
});

refreshButton.addEventListener("click", () => void withUiError(t("refreshFailed"), loadAccounts));
daemonUrlInput.addEventListener("change", () => {
  connectEvents();
  void withUiError(t("loadAccountsFailed"), loadAccounts);
});
daemonTokenInput.addEventListener("change", () => {
  connectEvents();
  void withUiError(t("loadAccountsFailed"), loadAccounts);
});
languageSelect.addEventListener("change", () => {
  currentLanguage = languageSelect.value;
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, currentLanguage);
  applyI18n();
  void withUiError(t("loadAccountsFailed"), loadAccounts);
});

applyI18n();
await loadHandshake();
connectEvents();
void withUiError(t("loadAccountsFailed"), loadAccounts);
