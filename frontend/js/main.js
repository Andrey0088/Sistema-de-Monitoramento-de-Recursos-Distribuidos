import { fetchMachines, fetchAlerts, fetchMetrics } from "./api.js";
import { connectWS } from "./ws.js";
import {
  setWsStatus,
  setDebugConfig,
  upsertMachineStatic,
  updateMachineMetrics,
  renderAlerts,
  getMachineState,
} from "./render.js";
import { seedHistory, pushHistoryPoint, updateCharts } from "./popup.js";

const API_BASE = "http://127.0.0.1:5000";
const WS_HOST = window.location.hostname;
const WS_PORT = 8766;

if (document.getElementById("debugConfig")) {
  setDebugConfig(`API_BASE=${API_BASE}
WS_HOST=${WS_HOST}
WS_PORT=${WS_PORT}
DEBUG_UI=hardcoded`);
}

async function preloadHistories(machineList) {
  const concurrency = 6;
  let i = 0;
  async function worker() {
    while (i < machineList.length) {
      const idx = i++;
      const m = machineList[idx];
      try {
        const rows = await fetchMetrics(m.id_machine, 120);
        seedHistory(m.id_machine, rows);
      } catch (e) {
        console.warn("hist fail", m.id_machine, e);
      }
    }
  }
  await Promise.all(Array.from({ length: concurrency }, worker));
}

async function bootstrap() {
  try {
    const [machines, alerts] = await Promise.all([
      fetchMachines(),
      fetchAlerts(50),
    ]);
    for (const m of machines) {
      upsertMachineStatic({
        ...m,
        id_machine: m.id_machine,
        last_seen: m.last_seen,
      });
    }
    renderAlerts(alerts);

    await preloadHistories(machines);
  } catch (e) {
    console.error("Falha ao carregar estado inicial:", e);
  }

  connectWS({
    host: WS_HOST,
    port: WS_PORT,
    onStatus: setWsStatus,
    onMessage: (msg) => {
      if (msg.type === "metrics") {
        updateMachineMetrics(msg.id_machine, msg.ts, msg.metrics);
        pushHistoryPoint(msg.id_machine, msg.ts, msg.metrics);
        updateCharts(msg.id_machine);
      } else if (msg.type === "alerts") {
        renderAlerts(msg.items || []);
      } else if (msg.type === "hello" && msg.machine) {
        upsertMachineStatic(msg.machine);
      }
    },
  });

  setInterval(async () => {
    try {
      renderAlerts(await fetchAlerts(50));
    } catch {}
  }, 20000);
}

bootstrap();
