import asyncio
import json
import os
import platform
import socket
import time
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

import psutil
import websockets

# =========================
# Logging
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("coletor")

# =========================
# Config (hardcoded de MVP)
# =========================
WS_URL = os.getenv("WS_URL", "ws://127.0.0.1:8765")
AUTH_TOKEN = os.getenv("AUTH_TOKEN", "")
MACHINE_ID = os.getenv("MACHINE_ID", None)
SEND_INTERVAL_SEC = int(os.getenv("SEND_INTERVAL_SEC", "5"))
HEARTBEAT_INTERVAL_SEC = int(os.getenv("HEARTBEAT_INTERVAL_SEC", "30"))
STATIC_REFRESH_SEC = int(os.getenv("STATIC_REFRESH_SEC", str(24 * 3600)))
BUFFER_MAX = int(os.getenv("BUFFER_MAX", "1000"))

CPU_SAMPLE_PERIOD_SEC = float(os.getenv("CPU_SAMPLE_PERIOD_SEC", "0.2"))  # 200ms
CPU_WINDOW_SEC = float(os.getenv("CPU_WINDOW_SEC", "1.0"))                # média móvel ~1s

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def get_machine_id() -> str:
    if MACHINE_ID:
        return MACHINE_ID
    import uuid
    mac = hex(uuid.getnode()).replace("0x", "").upper()
    return mac

def get_local_ip() -> Optional[str]:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    try:
        addrs = psutil.net_if_addrs()
        for _, addrlist in addrs.items():
            for addr in addrlist:
                if getattr(addr, "family", None) == socket.AF_INET:
                    if addr.address and not addr.address.startswith("127."):
                        return addr.address
    except Exception:
        pass
    return None

def _system_mountpoint() -> str:
    if os.name == "nt":
        return os.getenv("SystemDrive", "C:") + "\\"
    return "/"

def get_static_info() -> Dict[str, Any]:
    uname = platform.uname()
    cpu_freq = psutil.cpu_freq()
    mem = psutil.virtual_memory()
    disks = {}
    for part in psutil.disk_partitions(all=False):
        if part.fstype:
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disks[part.mountpoint] = {"fstype": part.fstype, "total": usage.total}
            except Exception:
                pass
    return {
        "id_machine": get_machine_id(),
        "hostname": socket.gethostname(),
        "local_ip": get_local_ip(),
        "os_system": platform.system(),
        "os_release": platform.release(),
        "os_version": platform.version(),
        "platform": uname.system,
        "platform_node": uname.node,
        "platform_release": uname.release,
        "platform_version": uname.version,
        "platform_machine": uname.machine,
        "platform_processor": uname.processor,
        "cpu_count_logical": psutil.cpu_count(logical=True),
        "cpu_count_physical": psutil.cpu_count(logical=False),
        "cpu_freq_max": cpu_freq.max if cpu_freq else None,
        "ram_total": mem.total,
        "disks": disks,
        "python_version": platform.python_version(),
        "psutil_version": getattr(psutil, "__version__", None),
    }




# =========================
# Coletor de temperatura
# =========================


def get_temperature_c() -> Optional[float]:
    """
    1) Tenta psutil.sensors_temperatures()
    2) Se Windows e psutil falhar, tenta WMI (requer 'pip install wmi'):
       classe MSAcpi_ThermalZoneTemperature retorna décimos de Kelvin.
    """
    # 1) psutil
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            for _, entries in temps.items():
                if entries:
                    v = entries[0].current
                    if v is not None:
                        return float(v)
    except Exception:
        pass

    # 2) WMI (Windows)
    try:
        if platform.system().lower().startswith("win"):
            import wmi  # opcional
            c = wmi.WMI(namespace="root\\WMI")
            for sensor in c.MSAcpi_ThermalZoneTemperature():
                # valor em décimos de Kelvin
                kelvin10 = getattr(sensor, "CurrentTemperature", None)
                if kelvin10:
                    celsius = (kelvin10 / 10.0) - 273.15
                    return float(celsius)
    except Exception:
        pass

    return None

# =========================
# CpuSampler (janela curta ~1s, 200ms tick)
# =========================
class CpuSampler:
    """
    Mede CPU% por deltas de cpu_times() em alta frequência e mantém
    uma média móvel (~1s), aproximando o Task Manager.
    """
    def __init__(self, period_sec: float = 0.2, window_sec: float = 1.0):
        self.period = max(0.05, period_sec)
        self.window = max(self.period, window_sec)
        self.prev_total = None
        self.prev_idle = None
        self.samples = deque()  # guarda (ts, cpu_percent_instant)
        self._task: Optional[asyncio.Task] = None

    @staticmethod
    def _tot_idle(times) -> (float, float):
        total = sum(times)
        idle = getattr(times, "idle", 0.0) or 0.0
        # NÃO somamos iowait ao idle: para aproximar Task Manager (no Windows não existe iowait).
        return total, idle

    def _instant_percent(self) -> Optional[float]:
        times = psutil.cpu_times()
        total, idle = self._tot_idle(times)
        if self.prev_total is None:
            self.prev_total, self.prev_idle = total, idle
            return None
        d_total = total - self.prev_total
        d_idle = idle - self.prev_idle
        self.prev_total, self.prev_idle = total, idle
        if d_total <= 0:
            return None
        busy = max(0.0, d_total - max(0.0, d_idle))
        pct = (busy / d_total) * 100.0
        if pct < 0: pct = 0.0
        if pct > 100: pct = 100.0
        return pct

    async def _loop(self):
        # primeira leitura para baseline
        self._instant_percent()
        while True:
            await asyncio.sleep(self.period)
            pct = self._instant_percent()
            if pct is None:
                continue
            ts = time.time()
            self.samples.append((ts, pct))
            # drop antigos fora da janela
            cutoff = ts - self.window
            while self.samples and self.samples[0][0] < cutoff:
                self.samples.popleft()

    def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    def get(self) -> Optional[float]:
        if not self.samples:
            return None
        # média simples dos valores na janela
        return sum(p for _, p in self.samples) / len(self.samples)

# =========================
# DiskSampler (atividade e taxas)
# =========================
class DiskSampler:
    def __init__(self):
        self.prev_time = None
        self.prev_total = None
        self.prev_perdisk = None

    def sample(self) -> Dict[str, Optional[float]]:
        total = psutil.disk_io_counters(nowrap=True)
        per_disk = psutil.disk_io_counters(perdisk=True, nowrap=True)
        now = time.time()

        if self.prev_time is None:
            self.prev_time = now
            self.prev_total = total
            self.prev_perdisk = per_disk
            return {"disk_activity_percent": None, "disk_read_bps": None, "disk_write_bps": None}

        elapsed = max(0.0001, now - self.prev_time)
        delta_read = max(0, total.read_bytes - self.prev_total.read_bytes)
        delta_write = max(0, total.write_bytes - self.prev_total.write_bytes)
        read_bps = delta_read / elapsed
        write_bps = delta_write / elapsed

        curr_time_ms = 0
        for name, cur in per_disk.items():
            prev = self.prev_perdisk.get(name)
            if not prev:
                continue
            r_now = getattr(cur, "read_time", 0) or 0
            w_now = getattr(cur, "write_time", 0) or 0
            r_old = getattr(prev, "read_time", 0) or 0
            w_old = getattr(prev, "write_time", 0) or 0
            curr_time_ms += max(0, r_now - r_old) + max(0, w_now - w_old)

        activity = max(0.0, min(100.0, (curr_time_ms / (elapsed * 1000.0)) * 100.0))

        self.prev_time = now
        self.prev_total = total
        self.prev_perdisk = per_disk
        return {
            "disk_activity_percent": activity,
            "disk_read_bps": read_bps,
            "disk_write_bps": write_bps,
        }

# =========================
# Métricas dinâmicas (usando CpuSampler + DiskSampler)
# =========================
def get_dynamic_metrics(cpu_sampler: CpuSampler, disk_sampler: DiskSampler) -> Dict[str, Any]:
    cpu_percent = cpu_sampler.get()
    if cpu_percent is None:
        # fallback rápido até encher a janela
        cpu_percent = psutil.cpu_percent(interval=0.2)

    mem = psutil.virtual_memory()

    disk_space_percent = None
    try:
        mount = _system_mountpoint()
        disk_space_percent = psutil.disk_usage(mount).percent
    except Exception:
        try:
            for part in psutil.disk_partitions(all=False):
                if part.fstype:
                    disk_space_percent = psutil.disk_usage(part.mountpoint).percent
                    break
        except Exception:
            pass

    disk_derived = disk_sampler.sample()

    temperature = get_temperature_c()

    return {
        "cpu_percent": cpu_percent,
        "ram_percent": mem.percent,
        # compat com agregador/DB antigo:
        "disk_percent": disk_space_percent,
        # novo nome (preferencial no front e broadcast):
        "disk_space_percent": disk_space_percent,
        "disk_activity_percent": disk_derived.get("disk_activity_percent"),
        "disk_read_bps": disk_derived.get("disk_read_bps"),
        "disk_write_bps": disk_derived.get("disk_write_bps"),
        "temperature_c": temperature,
        "local_ip": get_local_ip(),
    }

# =========================
# Buffer e envio
# =========================
class Buffer:
    def __init__(self, maxlen: int = 1000):
        self.maxlen = maxlen
        self._q: List[Dict[str, Any]] = []

    def push(self, msg: Dict[str, Any]):
        if len(self._q) >= self.maxlen:
            logger.warning("Buffer cheio, descartando mensagem antiga")
            self._q.pop(0)
        self._q.append(msg)

    def pop_all(self):
        items = self._q[:]
        self._q.clear()
        return items

async def send_json(ws, payload: Dict[str, Any]):
    await ws.send(json.dumps(payload, separators=(",", ":")))

# =========================
# Loop principal
# =========================
async def run_client():
    machine_info = get_static_info()
    buffer = Buffer(BUFFER_MAX)
    cpu_sampler = CpuSampler(CPU_SAMPLE_PERIOD_SEC, CPU_WINDOW_SEC)
    disk_sampler = DiskSampler()

    # inicia o sampler de CPU em background (200ms)
    cpu_sampler.start()

    backoff = 1.0
    backoff_max = 30.0
    last_static_sent = 0.0
    last_heartbeat = 0.0

    while True:
        try:
            logger.info(f"Conectando a {WS_URL} ...")
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20, max_queue=64) as ws:
                logger.info("Conexão estabelecida")
                backoff = 1.0

                hello = {
                    "type": "hello",
                    "ts": now_iso(),
                    "token": AUTH_TOKEN or None,
                    "machine": machine_info,
                }
                await send_json(ws, hello)
                logger.info(
                    "HELLO enviado: id=%s host=%s ip=%s so=%s %s (%s)",
                    machine_info["id_machine"],
                    machine_info["hostname"],
                    machine_info["local_ip"],
                    machine_info["os_system"],
                    machine_info["os_release"],
                    machine_info["os_version"],
                )
                last_static_sent = time.time()

                for item in buffer.pop_all():
                    await send_json(ws, item)
                    logger.info("Reenviado pacote em buffer")

                while True:
                    now = time.time()

                    if now - last_static_sent >= STATIC_REFRESH_SEC:
                        machine_info = get_static_info()
                        await send_json(ws, {
                            "type": "hello",
                            "ts": now_iso(),
                            "token": AUTH_TOKEN or None,
                            "machine": machine_info,
                        })
                        logger.info("Reenviado pacote 'hello' (refresh)")
                        last_static_sent = now

                    if now - last_heartbeat >= HEARTBEAT_INTERVAL_SEC:
                        await send_json(ws, {
                            "type": "heartbeat",
                            "ts": now_iso(),
                            "id_machine": machine_info["id_machine"],
                        })
                        logger.debug("Enviado heartbeat")
                        last_heartbeat = now

                    metrics = get_dynamic_metrics(cpu_sampler, disk_sampler)
                    payload = {
                        "type": "metrics",
                        "ts": now_iso(),
                        "id_machine": machine_info["id_machine"],
                        "metrics": metrics,
                    }
                    try:
                        await send_json(ws, payload)
                        logger.info(
                            "Métricas: CPU=%.1f%% TEMP=%.0f%% RAM=%.1f%% DISCO_ESP=%.1f%% DISCO_ATV=%s%% R=%.0fB/s W=%.0fB/s IP=%s",
                            metrics.get("cpu_percent", -1) or 0,
                            metrics.get("temperature_c", -1) or 0,
                            metrics.get("ram_percent", -1) or 0,
                            (metrics.get("disk_space_percent") or 0),
                            ("%.1f" % metrics["disk_activity_percent"]) if metrics.get("disk_activity_percent") is not None else "n/a",
                            metrics.get("disk_read_bps") or 0,
                            metrics.get("disk_write_bps") or 0,
                            metrics.get("local_ip"),
                        )
                    except Exception as e:
                        buffer.push(payload)
                        logger.error(f"Falha ao enviar métricas, armazenado no buffer: {e}")
                        raise

                    await asyncio.sleep(SEND_INTERVAL_SEC)

        except Exception as e:
            sleep_for = min(backoff, backoff_max) + (0.2 * backoff)
            logger.warning(f"Desconectado ({type(e).__name__}: {e}); tentando em {sleep_for:.1f}s")
            await asyncio.sleep(sleep_for)
            backoff = min(backoff * 2, backoff_max)

if __name__ == "__main__":
    asyncio.run(run_client())
