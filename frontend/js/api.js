const API_BASE = "http://127.0.0.1:5000";
const DEBUG = true;

export async function fetchMachines() {
  const url = `${API_BASE}/maquinas`;
  if (DEBUG) console.log("[REST] GET", url);
  const r = await fetch(url);
  if (!r.ok) throw new Error("Falha /maquinas");
  const data = await r.json();
  if (DEBUG) console.log("[REST] /maquinas ->", data);
  return data;
}

export async function fetchAlerts(n = 50) {
  const url = `${API_BASE}/alertas/recentes/${n}`;
  if (DEBUG) console.log("[REST] GET", url);
  const r = await fetch(url);
  if (!r.ok) throw new Error("Falha /alertas/recentes");
  const data = await r.json();
  if (DEBUG) console.log("[REST] /alertas/recentes ->", data);
  return data;
}

export async function fetchMetrics(id_machine, n = 100) {
  const url = `${API_BASE}/metricas/${encodeURIComponent(
    id_machine
  )}/ultimas/${n}`;
  if (DEBUG) console.log("[REST] GET", url);
  const r = await fetch(url);
  if (!r.ok) throw new Error("Falha /metricas/.../ultimas");
  const data = await r.json();
  if (DEBUG) console.log("[REST] metricas ->", id_machine, data?.length || 0);
  return (data || []).map((row) => ({
    ...row,
    disk_space_percent: row.disk_space_percent ?? row.disk_percent ?? null,
  }));
}
