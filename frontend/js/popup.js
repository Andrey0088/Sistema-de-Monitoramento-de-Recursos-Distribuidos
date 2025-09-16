const DEBUG = true;
const histories = new Map();

function fmtBytesToGB(v) {
  if (v == null) return "—";
  const gb = Number(v) / 1024 ** 3;
  return gb.toFixed(1) + " GB";
}

export function seedHistory(id, rows) {
  histories.set(
    id,
    rows.map((r) => ({
      ts: r.ts,
      cpu_percent: Number(r.cpu_percent ?? 0),
      ram_percent: Number(r.ram_percent ?? 0),
    }))
  );
}

export function pushHistoryPoint(id, ts, metrics) {
  const arr = histories.get(id) ?? [];
  arr.unshift({
    ts,
    cpu_percent: Number(metrics.cpu_percent ?? 0),
    ram_percent: Number(metrics.ram_percent ?? 0),
  });
  if (arr.length > 240) arr.length = 240;
  histories.set(id, arr);
}

export function renderPopup(cardEl, id, staticInfo, lastPoint) {
  const details = cardEl.querySelector(".details");
  if (!details) return;

  const hist = histories.get(id) || [];
  const host = staticInfo.hostname || "—";
  const ip = lastPoint?.metrics?.local_ip || staticInfo.local_ip || "—";
  const so =
    [staticInfo.os_system, staticInfo.os_release].filter(Boolean).join(" ") ||
    "—";

  details.innerHTML = `
    <div class="card" style="background:#0f141d;border:1px solid #1d2633;">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
        <div><div class="small kv">Hostname</div><div class="mono">${host}</div></div>
        <div><div class="small kv">IP</div><div class="mono">${ip}</div></div>
        <div><div class="small kv">SO</div><div class="mono">${so}</div></div>
        <div><div class="small kv">CPU (lógicos/físicos)</div><div class="mono">${
          staticInfo.cpu_count_logical ?? "?"
        } / ${staticInfo.cpu_count_physical ?? "?"}</div></div>
        <div><div class="small kv">CPU máx (MHz)</div><div class="mono">${
          staticInfo.cpu_freq_max ?? "?"
        }</div></div>
        <div><div class="small kv">RAM total</div><div class="mono">${fmtBytesToGB(
          staticInfo.ram_total
        )}</div></div>
      </div>

      <div class="charts-col" style="margin-top:12px;display:flex;flex-direction:column;gap:12px;">
        <div>
          <div class="small kv">CPU (%)</div>
          <div class="chart-wrap" style="width:100%;"><canvas id="chart-cpu-${id}" class="chartCanvas" height="160"></canvas></div>
        </div>
        <div>
          <div class="small kv">RAM (%)</div>
          <div class="chart-wrap" style="width:100%;"><canvas id="chart-ram-${id}" class="chartCanvas" height="160"></canvas></div>
        </div>
      </div>
    </div>
  `;

  fitAndDrawCharts(id, hist, details);

  const ro = new ResizeObserver(() =>
    fitAndDrawCharts(id, histories.get(id) || [], details)
  );
  const wrap = details.querySelector(".charts-col");
  if (wrap) ro.observe(wrap);

  details._ro && details._ro.disconnect();
  details._ro = ro;
}

export function updateCharts(id) {
  const card = document.querySelector(`.card[data-id="${id}"]`);
  if (!card) return;
  const details = card.querySelector(".details");
  if (!details || details.style.display !== "block") return;
  const hist = histories.get(id) || [];
  fitAndDrawCharts(id, hist, details);
}

function fitAndDrawCharts(id, hist, detailsRoot) {
  const cpuCanvas = detailsRoot.querySelector(`#chart-cpu-${id}`);
  const ramCanvas = detailsRoot.querySelector(`#chart-ram-${id}`);
  if (!cpuCanvas || !ramCanvas) return;

  for (const c of [cpuCanvas, ramCanvas]) {
    const parent = c.parentElement;
    const targetW = Math.max(200, Math.floor(parent.clientWidth));
    if (c.width !== targetW) c.width = targetW;
  }

  drawLineChart(
    cpuCanvas,
    hist.map((p) => p.cpu_percent),
    { max: 100, labelStep: 25 }
  );
  drawLineChart(
    ramCanvas,
    hist.map((p) => p.ram_percent),
    { max: 100, labelStep: 25 }
  );
}

function drawLineChart(canvas, values, { max = 100, labelStep = 25 } = {}) {
  const ctx = canvas.getContext("2d");
  const W = canvas.width,
    H = canvas.height;
  ctx.clearRect(0, 0, W, H);

  ctx.fillStyle = "#0b0e13";
  ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = "#283549";
  ctx.lineWidth = 1;

  const L = 36,
    R = 10,
    T = 10,
    B = 20;
  const w = W - L - R;
  const h = H - T - B;

  ctx.font = "12px ui-monospace, Menlo, Consolas, monospace";
  ctx.fillStyle = "#8aa0b2";
  for (let v = 0; v <= max; v += labelStep) {
    const y = T + h - (v / max) * h;
    ctx.beginPath();
    ctx.moveTo(L, y);
    ctx.lineTo(W - R, y);
    ctx.stroke();
    ctx.fillText(String(v), 4, y + 4);
  }

  if (!values || !values.length) return;

  const N = Math.min(values.length, 120);
  const data = values.slice(0, N).reverse();
  const stepX = w / Math.max(1, data.length - 1);

  ctx.strokeStyle = "#6aa3ff";
  ctx.lineWidth = 2;
  ctx.beginPath();
  data.forEach((v, i) => {
    const x = L + i * stepX;
    const y = T + h - (Math.max(0, Math.min(max, v)) / max) * h;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}
