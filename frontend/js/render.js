import { renderPopup } from "./popup.js";

const grid = document.getElementById("machinesGrid");
const alertsList = document.getElementById("alertsList");
const wsStatusEl = document.getElementById("wsStatus");
const debugLastMsg = document.getElementById("debugLastMsg");
const debugConfig = document.getElementById("debugConfig");

const DEBUG = true;

const machines = new Map();
const expandedIds = new Set();

function fmtPct(v) {
  return v == null ? "—" : `${Number(v).toFixed(1)}%`;
}
function fmtBps(v) {
  if (v == null) return "—";
  const units = ["B/s", "KB/s", "MB/s", "GB/s"];
  let i = 0,
    x = Number(v);
  while (x >= 1024 && i < units.length - 1) {
    x /= 1024;
    i++;
  }
  return `${x.toFixed(1)} ${units[i]}`;
}
function appendDebug(line) {
  if (!DEBUG || !debugLastMsg) return;
  const div = document.createElement("div");
  div.textContent = line;
  debugLastMsg.prepend(div);
}

function normalizeMetrics(m) {
  const out = { ...m };
  if (out.disk_space_percent == null && out.disk_percent != null) {
    out.disk_space_percent = out.disk_percent;
  }
  return out;
}

export function setWsStatus(s) {
  const map = {
    connected: "WS: conectado",
    disconnected: "WS: desconectado",
    error: "WS: erro",
  };
  wsStatusEl.textContent = map[s] || `WS: ${s}`;
  wsStatusEl.style.color =
    s === "connected" ? "#1ad598" : s === "error" ? "#ff5a6b" : "#8aa0b2";
}

export function setDebugConfig(text) {
  if (debugConfig) debugConfig.innerText = text;
}

export function upsertMachineStatic(staticInfo) {
  const id = staticInfo.id_machine;
  const item = machines.get(id) ?? {};
  item.static = staticInfo;
  machines.set(id, item);
  if (DEBUG)
    appendDebug(
      `[${id.slice(0, 8)}] static keys: ${Object.keys(staticInfo).join(", ")}`
    );
  render();
}

export function updateMachineMetrics(id, ts, metrics) {
  const item = machines.get(id) ?? {};
  const norm = normalizeMetrics(metrics);
  item.last = { ts, metrics: norm };
  machines.set(id, item);
  render();
}

function tsToLocal(ts) {
  if (!ts) return "—";
  if (/[a-z]{3},/i.test(ts) || /GMT/i.test(ts)) {
    const d = new Date(ts);
    return isNaN(d) ? ts : d.toLocaleString("pt-BR");
  }
  if (
    typeof ts === "string" &&
    ts.includes("T") &&
    !/[zZ]|[+\-]\d{2}:?\d{2}$/.test(ts)
  ) {
    const d = new Date(ts + "Z");
    return isNaN(d) ? ts : d.toLocaleString("pt-BR");
  }
  const d = new Date(ts);
  return isNaN(d) ? ts : d.toLocaleString("pt-BR");
}

export function renderAlerts(rows) {
  alertsList.innerHTML = "";
  if (!rows || !rows.length) {
    alertsList.innerHTML = "<li class='small'>Nenhum alerta recente.</li>";
    return;
  }
  for (const a of rows) {
    const li = document.createElement("li");
    const when = tsToLocal(a.ts);
    const idShort = (a.id_machine || "").slice(0, 8) || "———";
    li.innerHTML = `<strong>${when}</strong> · <span class="mono">${idShort}</span> · ${a.tipo} – ${a.detalhe}`;
    alertsList.appendChild(li);
  }
}

export function getMachineState(id) {
  return machines.get(id) || null;
}

function statusDot(lastSeen) {
  if (!lastSeen) return "<span class='statusDot status-offline'></span>";
  const last = new Date(lastSeen + "Z").getTime();
  const online = Date.now() - last < 20_000;
  return `<span class='statusDot ${
    online ? "status-online" : "status-offline"
  }'></span>`;
}

function renderCard(id, data) {
  const s = data.static || {};
  const m = data.last?.metrics || {};
  const lastSeen = s.last_seen || data.last?.ts;
  const isOpen = expandedIds.has(id);

  return `
  <div class="card" id="m-${id}" data-id="${id}">
    <h3 style="display:flex;align-items:center;justify-content:space-between;gap:8px;">
      <span>${statusDot(lastSeen)} <span class="mono">${id.slice(
    0,
    8
  )}</span> · ${s.hostname || ""}</span>
      <button class="badge" data-action="toggle-details" title="Mostrar/ocultar detalhes">${
        isOpen ? "Fechar" : "Detalhes"
      }</button>
    </h3>

    <div class="row small kv">IP: <span class="mono">${
      m.local_ip || s.local_ip || "—"
    }</span></div>
    <div class="row small kv">SO: ${[s.os_system, s.os_release]
      .filter(Boolean)
      .join(" ")}</div>

    <div class="row">
      <div style="flex:1">
        <div class="small kv">CPU</div>
        <progress value="${m.cpu_percent ?? 0}" max="100"></progress>
      </div>
      <div class="mono">${fmtPct(m.cpu_percent)}</div>
    </div>

    <div class="row">
      <div style="flex:1">
        <div class="small kv">RAM</div>
        <progress value="${m.ram_percent ?? 0}" max="100"></progress>
      </div>
      <div class="mono">${fmtPct(m.ram_percent)}</div>
    </div>

    <div class="row">
      <div style="flex:1">
        <div class="small kv">Disco (esp)</div>
        <progress value="${m.disk_space_percent ?? 0}" max="100"></progress>
      </div>
      <div class="mono">${fmtPct(m.disk_space_percent)}</div>
    </div>

    <div class="row small kv">Disco (atividade): <span class="mono">${
      m.disk_activity_percent != null ? fmtPct(m.disk_activity_percent) : "—"
    }</span></div>
    <div class="row small kv">Leitura/Gravação: <span class="mono">${fmtBps(
      m.disk_read_bps
    )} / ${fmtBps(m.disk_write_bps)}</span></div>

    <div class="row small kv">Temp CPU: <span class="mono">${
      m.temperature_c ?? "—"
    }°C</span></div>

    <!-- container do popup/expansível -->
    <div class="details" style="display:${
      isOpen ? "block" : "none"
    }; margin-top:12px;"></div>
  </div>`;
}

let listenersBound = false;

function render() {
  const entries = Array.from(machines.entries());
  entries.sort((a, b) =>
    (b[1].static?.last_seen || b[1].last?.ts || "").localeCompare(
      a[1].static?.last_seen || a[1].last?.ts || ""
    )
  );
  grid.innerHTML = entries.length
    ? entries.map(([id, data]) => renderCard(id, data)).join("")
    : "<div class='small'>Nenhuma máquina ainda.</div>";

  for (const [id, data] of entries) {
    if (!expandedIds.has(id)) continue;
    const card = document.querySelector(`.card[data-id="${id}"]`);
    const details = card?.querySelector(".details");
    if (card && details) {
      details.style.display = "block";
      renderPopup(card, id, data.static || {}, data.last || null);
    }
  }

  if (!listenersBound) {
    listenersBound = true;

    grid.addEventListener("click", (ev) => {
      const btn = ev.target.closest('button[data-action="toggle-details"]');
      if (!btn) return;
      ev.stopPropagation();

      const card = btn.closest(".card");
      if (!card) return;

      const id = card.dataset.id;
      const isOpen = expandedIds.has(id);

      if (isOpen) {
        expandedIds.delete(id);
      } else {
        expandedIds.add(id);
      }

      render();
    });
  }
}
