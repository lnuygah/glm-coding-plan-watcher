const daemonUrlInput = document.querySelector("#daemon-url");
const daemonTokenInput = document.querySelector("#daemon-token");
const accountsEl = document.querySelector("#accounts");
const eventsEl = document.querySelector("#events");
const refreshButton = document.querySelector("#refresh");
const accountForm = document.querySelector("#account-form");

let ws = null;

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
    throw new Error(await response.text());
  }
  if (response.status === 204) {
    return undefined;
  }
  return await response.json();
}

async function loadAccounts() {
  const accounts = await api("/accounts");
  accountsEl.innerHTML = "";
  for (const account of accounts) {
    const targets = await api(`/accounts/${account.id}/targets`);
    accountsEl.append(renderAccount(account, targets));
  }
}

function renderAccount(account, targets) {
  const row = document.createElement("article");
  row.className = "account";
  row.innerHTML = `
    <div class="account-title">
      <strong>${escapeHtml(account.display_name)}</strong>
      <span>${escapeHtml(account.status || "stopped")}</span>
    </div>
    <div class="profile">${escapeHtml(account.user_data_dir)}</div>
    <div class="targets">${targets.map(renderTargetLabel).join("") || "<em>No targets</em>"}</div>
    <form class="target-form">
      <select name="billing_cycle">
        <option value="monthly">monthly</option>
        <option value="quarterly">quarterly</option>
        <option value="yearly">yearly</option>
      </select>
      <select name="tier">
        <option value="Lite">Lite</option>
        <option value="Pro">Pro</option>
        <option value="Max">Max</option>
      </select>
      <button type="submit">Add target</button>
    </form>
    <div class="actions">
      <button data-action="start">Start</button>
      <button data-action="stop">Stop</button>
      <button data-action="login">Login</button>
      <button data-action="handoff">Handoff</button>
    </div>
  `;

  row.querySelector(".target-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await api(`/accounts/${account.id}/targets`, {
      method: "POST",
      body: JSON.stringify({
        billing_cycle: form.get("billing_cycle"),
        tier: form.get("tier"),
      }),
    });
    await loadAccounts();
  });

  row.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", async () => {
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
  return row;
}

function renderTargetLabel(target) {
  return `<span class="target">${escapeHtml(target.billing_cycle)} / ${escapeHtml(target.tier)}</span>`;
}

function connectEvents() {
  ws?.close();
  const url = daemonUrl().replace(/^http/, "ws");
  const token = daemonToken();
  const query = token ? `?token=${encodeURIComponent(token)}` : "";
  ws = new WebSocket(`${url}/ws/events${query}`);
  ws.onmessage = (message) => {
    const payload = JSON.parse(message.data);
    prependEvent(payload);
    if (payload.event?.button_state === "auth_required") {
      void loadAccounts();
    }
  };
}

async function loadHandshake() {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke) {
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
}

function sleep(ms) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function prependEvent(payload) {
  const item = document.createElement("pre");
  item.textContent = JSON.stringify(payload, null, 2);
  eventsEl.prepend(item);
  while (eventsEl.children.length > 80) {
    eventsEl.lastElementChild?.remove();
  }
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

accountForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(accountForm);
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

refreshButton.addEventListener("click", () => void loadAccounts());
daemonUrlInput.addEventListener("change", () => {
  connectEvents();
  void loadAccounts();
});
daemonTokenInput.addEventListener("change", () => {
  connectEvents();
  void loadAccounts();
});

await loadHandshake();
connectEvents();
void loadAccounts();
