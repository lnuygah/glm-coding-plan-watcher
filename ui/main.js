const daemonUrlInput = document.querySelector("#daemon-url");
const daemonTokenInput = document.querySelector("#daemon-token");
const accountsEl = document.querySelector("#accounts");
const eventsEl = document.querySelector("#events");
const refreshButton = document.querySelector("#refresh");
const accountForm = document.querySelector("#account-form");
const statusEl = document.querySelector("#status");

const billingLabels = {
  monthly: "连续包月",
  quarterly: "连续包季",
  yearly: "连续包年",
};

let ws = null;
const accountTargets = new Map();

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

function errorText(error) {
  if (error instanceof TypeError && error.message === "Failed to fetch") {
    return "Failed to fetch. Check daemon URL/token, CORS, and local proxy bypass for 127.0.0.1/localhost.";
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
  row.innerHTML = `
    <div class="account-title">
      <div>
        <strong>${escapeHtml(account.display_name)}</strong>
        <div class="profile">${escapeHtml(account.user_data_dir)}</div>
      </div>
      <span>${escapeHtml(account.status || "stopped")}</span>
    </div>
    <div class="actions">
      <button data-action="start">Start</button>
      <button data-action="stop">Stop</button>
      <button data-action="login">Login</button>
      <button data-action="handoff">Handoff</button>
    </div>
    <div class="targets"></div>
    <details class="add-target" open>
      <summary>Add monitor task</summary>
      <form class="target-form">
        ${targetFormFields()}
        <button type="submit">Add target</button>
      </form>
    </details>
  `;

  const targetsContainer = row.querySelector(".targets");
  if (targets.length === 0) {
    targetsContainer.innerHTML = "<em>No targets</em>";
  } else {
    for (const target of targets) {
      targetsContainer.append(renderTarget(account, target));
    }
  }

  row.querySelector(".target-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    await withUiError("Add target failed", async () => {
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
      await withUiError(`${button.dataset.action} failed`, async () => {
        const action = button.dataset.action;
        if (action === "start") await api(`/accounts/${account.id}/worker/start`, { method: "POST" });
        if (action === "stop") await api(`/accounts/${account.id}/worker/stop`, { method: "POST" });
        if (action === "login") await api(`/accounts/${account.id}/login`, { method: "POST", body: "{}" });
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

function renderTarget(account, target) {
  const item = document.createElement("article");
  item.className = "target-card";
  item.innerHTML = `
    <div class="target-title">
      <strong>${escapeHtml(target.billing_cycle)} / ${escapeHtml(target.tier)}</strong>
      <span>${target.enabled ? "enabled" : "disabled"}</span>
    </div>
    <form class="target-edit-form">
      ${targetFormFields(target)}
      <div class="target-actions">
        <button type="submit">Save</button>
        <button type="button" data-target-action="handoff">Handoff</button>
        <button type="button" data-target-action="delete">Delete</button>
      </div>
    </form>
  `;
  setSelectValue(item, "billing_cycle", target.billing_cycle);
  setSelectValue(item, "tier", target.tier);

  item.querySelector(".target-edit-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    await withUiError("Save target failed", async () => {
      await api(`/targets/${target.id}`, {
        method: "PATCH",
        body: JSON.stringify(targetPayloadFromForm(event.currentTarget)),
      });
      await loadAccounts();
    });
  });

  item.querySelector('[data-target-action="handoff"]').addEventListener("click", async () => {
    await withUiError("Handoff failed", async () => {
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
    await withUiError("Delete target failed", async () => {
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
        Billing
        <select name="billing_cycle">
          <option value="monthly">monthly</option>
          <option value="quarterly">quarterly</option>
          <option value="yearly">yearly</option>
        </select>
      </label>
      <label>
        Tier
        <select name="tier">
          <option value="Lite">Lite</option>
          <option value="Pro" selected>Pro</option>
          <option value="Max">Max</option>
        </select>
      </label>
      <label>
        Base interval
        <input name="interval" type="number" min="1" step="1" value="${numberValue(target.interval, 90)}" />
      </label>
      <label>
        Base jitter
        <input name="jitter" type="number" min="0" step="1" value="${numberValue(target.jitter, 30)}" />
      </label>
      <label>
        Window start
        <input name="active_window_start" placeholder="10:00" value="${escapeAttr(target.active_window_start || "")}" />
      </label>
      <label>
        Window end
        <input name="active_window_end" placeholder="10:30" value="${escapeAttr(target.active_window_end || "")}" />
      </label>
      <label>
        Timezone
        <input name="active_timezone" placeholder="local or Asia/Shanghai" value="${escapeAttr(target.active_timezone || "")}" />
      </label>
      <label>
        Active interval
        <input name="active_interval_seconds" type="number" min="1" step="0.5" value="${numberValue(target.active_interval_seconds, 3)}" />
      </label>
      <label>
        Active jitter
        <input name="active_jitter_seconds" type="number" min="0" step="0.5" value="${numberValue(target.active_jitter_seconds, 1)}" />
      </label>
      <label>
        Idle interval
        <input name="idle_interval_seconds" type="number" min="1" step="1" value="${numberValue(target.idle_interval_seconds, 600)}" />
      </label>
    </div>
    <div class="checkbox-row">
      ${checkbox("enabled", target.enabled ?? true, "Enabled")}
      ${checkbox("dry_run", target.dry_run ?? false, "Dry run")}
      ${checkbox("auto_click_entry", target.auto_click_entry ?? true, "Click entry in handoff")}
      ${checkbox("on_hit_handoff", target.on_hit_handoff ?? true, "Open handoff on hit")}
    </div>
  `;
}

function checkbox(name, checked, label) {
  return `
    <label>
      <input name="${name}" type="checkbox" ${checked ? "checked" : ""} />
      ${label}
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
    showStatus(`Connect events failed: ${errorText(error)}`);
    return;
  }
  ws.onmessage = (message) => {
    const payload = JSON.parse(message.data);
    prependEvent(payload);
    if (payload.event?.button_state === "auth_required" || payload.event?.type === "hit") {
      void withUiError("Reload accounts failed", loadAccounts);
    }
  };
  ws.onerror = () => {
    showStatus("WebSocket connection failed. Check daemon URL/token and local proxy bypass.");
  };
  ws.onclose = (event) => {
    if (event.code !== 1000) {
      showStatus(`WebSocket closed: code=${event.code}`, "info");
    }
  };
}

async function loadHandshake() {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke) {
    showStatus("Tauri bridge is unavailable; fill Daemon and Token manually.", "info");
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
  showStatus("Daemon handshake was not found. Check that the Tauri sidecar daemon started.", "error");
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
    actions.append(eventButton("Login", () => api(`/accounts/${payload.account_id}/login`, {
      method: "POST",
      body: "{}",
    })));
  }
  if (event.type === "hit" && event.available) {
    actions.append(eventButton("Handoff", () => handoffForEvent(payload)));
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
    await withUiError(`${label} failed`, async () => {
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
  return `${billingLabels[target.billing_cycle] || target.billing_cycle} / ${target.tier}`;
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
  await withUiError("Add account failed", async () => {
    await api("/accounts", {
      method: "POST",
      body: JSON.stringify({
        display_name: form.get("display_name"),
        user_data_dir: form.get("user_data_dir"),
      }),
    });
    accountForm.reset();
    await loadAccounts();
  });
});

refreshButton.addEventListener("click", () => void withUiError("Refresh failed", loadAccounts));
daemonUrlInput.addEventListener("change", () => {
  connectEvents();
  void withUiError("Load accounts failed", loadAccounts);
});
daemonTokenInput.addEventListener("change", () => {
  connectEvents();
  void withUiError("Load accounts failed", loadAccounts);
});

await loadHandshake();
connectEvents();
void withUiError("Load accounts failed", loadAccounts);
