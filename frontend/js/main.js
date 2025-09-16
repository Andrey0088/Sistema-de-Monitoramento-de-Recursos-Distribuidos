import {
  fetchMachines,
  fetchMachine,
  fetchAlerts,
  fetchMetrics,
} from "./api.js";
import { connectWS } from "./ws.js";
import {
  setWsStatus,
  setDebugConfig,
  upsertMachineStatic,
  updateMachineMetrics,
  renderAlerts,
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

async function refreshMachines() {
  try {
    const machines = await fetchMachines();
    for (const m of machines) {
      upsertMachineStatic({
        ...m,
        id_machine: m.id_machine,
        last_seen: m.last_seen,
      });
    }
  } catch (e) {
    console.warn("refresh /maquinas falhou", e);
  }
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

  // WS: somente métricas/alertas em tempo real; estáticos vêm por REST
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
      }
      // msg.type === "hello" ignorado — a UI não usa WS para estáticos
    },
  });

  // Poll suave para reidratar estáticos a partir do banco
  setInterval(refreshMachines, 20000);

  // (Opcional) tentativa de completar campos faltantes via detalhe:
  setInterval(async () => {
    // Se alguma máquina estiver sem cpu_freq_max/physical, tenta detalhe uma vez
    try {
      const list = await fetchMachines();
      for (const m of list) {
        if (m.cpu_freq_max == null || m.cpu_count_physical == null) {
          try {
            const full = await fetchMachine(m.id_machine);
            if (full && full.id_machine) upsertMachineStatic(full);
          } catch {}
        }
      }
    } catch {}
  }, 30000);
}

bootstrap();
