"""
Agregador de Telemetria (Flask + WebSockets)
- WS (8765): recebe "hello", "metrics", "heartbeat" dos coletores
- WS (8766): broadcast em tempo real para o dashboard (somente leitura)
- API Flask (5000): /health, /maquinas, /metricas/<id>/ultimas/<n>, /alertas/recentes/<n>
- MySQL: tabelas criadas via init.sql (não há db_init aqui)

Dependências:
  pip install websockets flask flask-cors mysql-connector-python
"""

import asyncio
import json
import os
import threading
from datetime import datetime, timezone
from typing import Dict, Any

from flask import Flask, jsonify
from flask_cors import CORS

import websockets
import mysql.connector

# =========================
# Config
# =========================
WS_HOST = os.getenv("WS_HOST", "0.0.0.0")
WS_PORT_COLLECTORS = int(os.getenv("WS_PORT", "8765"))
WS_PORT_DASHBOARD = int(os.getenv("WS_PORT_DASH", "8766"))
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "5000"))
AUTH_TOKEN = os.getenv("AUTH_TOKEN", "")  # opcional

DB_CFG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", "root"),
    "database": os.getenv("DB_NAME", "telemetria"),
    "autocommit": True,
}

# =========================
# DB helpers
# =========================
def db_connect():
    return mysql.connector.connect(**DB_CFG)

def upsert_machine(machine: Dict[str, Any]):
    conn = db_connect()
    cur = conn.cursor()
    q = """
    INSERT INTO maquinas
    (id_machine, hostname, local_ip, os_system, os_release, os_version, platform, platform_node,
     platform_release, platform_version, platform_machine, platform_processor, cpu_count_logical,
     cpu_count_physical, cpu_freq_max, ram_total, disks, python_version, psutil_version,
     first_seen, last_seen)
    VALUES (%(id_machine)s, %(hostname)s, %(local_ip)s, %(os_system)s, %(os_release)s, %(os_version)s,
            %(platform)s, %(platform_node)s, %(platform_release)s, %(platform_version)s, %(platform_machine)s,
            %(platform_processor)s, %(cpu_count_logical)s, %(cpu_count_physical)s, %(cpu_freq_max)s,
            %(ram_total)s, %(disks)s, %(python_version)s, %(psutil_version)s, NOW(), NOW())
    ON DUPLICATE KEY UPDATE
        hostname=VALUES(hostname),
        local_ip=VALUES(local_ip),
        os_system=VALUES(os_system),
        os_release=VALUES(os_release),
        os_version=VALUES(os_version),
        platform=VALUES(platform),
        platform_node=VALUES(platform_node),
        platform_release=VALUES(platform_release),
        platform_version=VALUES(platform_version),
        platform_machine=VALUES(platform_machine),
        platform_processor=VALUES(platform_processor),
        cpu_count_logical=VALUES(cpu_count_logical),
        cpu_count_physical=VALUES(cpu_count_physical),
        cpu_freq_max=VALUES(cpu_freq_max),
        ram_total=VALUES(ram_total),
        disks=VALUES(disks),
        python_version=VALUES(python_version),
        psutil_version=VALUES(psutil_version),
        last_seen=NOW();
    """
    machine2 = dict(machine)
    if isinstance(machine2.get("disks"), (dict, list)):
        machine2["disks"] = json.dumps(machine2["disks"], separators=(",", ":"))
    cur.execute(q, machine2)
    cur.close()
    conn.close()

def _parse_ts(ts_iso: str):
    return datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)

def insert_metrics(id_machine: str, metrics: Dict[str, Any], ts_iso: str):
    conn = db_connect()
    cur = conn.cursor()
    q = """
    INSERT INTO metricas
    (id_machine, ts, cpu_percent, ram_percent, disk_percent, temperature_c, local_ip)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    ts = _parse_ts(ts_iso)
    cur.execute(q, (
        id_machine,
        ts,
        metrics.get("cpu_percent"),
        metrics.get("ram_percent"),
        # No DB guardamos ocupação (compat), não atividade:
        metrics.get("disk_space_percent", metrics.get("disk_percent")),
        metrics.get("temperature_c"),
        metrics.get("local_ip"),
    ))
    conn.commit()
    cur.close()
    conn.close()

async def maybe_alert(id_machine: str, metrics: Dict[str, Any], ts_iso: str):
    alerts = []
    if metrics.get("cpu_percent", 0) >= 90:
        alerts.append(("CPU_ALTA", f"CPU {metrics['cpu_percent']}%"))
    if metrics.get("ram_percent", 0) >= 90:
        alerts.append(("RAM_ALTA", f"RAM {metrics['ram_percent']}%"))
    # Disco: alerta por espaço cheio (compat com DB)
    disk_occ = metrics.get("disk_space_percent", metrics.get("disk_percent") or 0)
    if disk_occ >= 95:
        alerts.append(("DISCO_QUASE_CHEIO", f"DISCO {disk_occ}%"))
    if not alerts:
        return

    conn = db_connect()
    cur = conn.cursor()
    ts = _parse_ts(ts_iso)
    cur.executemany(
        "INSERT INTO alertas (id_machine, ts, tipo, detalhe) VALUES (%s, %s, %s, %s)",
        [(id_machine, ts, a, d) for a, d in alerts]
    )
    conn.commit()
    cur.close()
    conn.close()

# =========================
# Broadcast p/ dashboard (WS 8766)
# =========================
DASHBOARD_CLIENTS = set()

async def dashboard_ws_handler(websocket):
    DASHBOARD_CLIENTS.add(websocket)
    try:
        async for _ in websocket:
            pass
    finally:
        DASHBOARD_CLIENTS.discard(websocket)

async def dashboard_broadcast(payload: dict):
    if not DASHBOARD_CLIENTS:
        return
    msg = json.dumps(payload, separators=(",", ":"))
    dead = []
    for ws in DASHBOARD_CLIENTS:
        try:
            await ws.send(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        DASHBOARD_CLIENTS.discard(ws)

# =========================
# WS de ingestão (coletores)
# =========================
async def process_message(msg: Dict[str, Any], websocket):
    mtype = msg.get("type")

    if mtype == "hello":
        machine = msg.get("machine") or {}
        if "id_machine" not in machine:
            return
        upsert_machine(machine)
        await dashboard_broadcast({"type": "hello", "machine": machine})
        await websocket.send(json.dumps({"type": "ack_hello", "id_machine": machine["id_machine"]}))
        return

    if mtype == "heartbeat":
        idm = msg.get("id_machine")
        if not idm:
            return
        conn = db_connect()
        cur = conn.cursor()
        cur.execute("UPDATE maquinas SET last_seen = NOW() WHERE id_machine = %s", (idm,))
        conn.commit()
        cur.close()
        conn.close()
        return

    if mtype == "metrics":
        idm = msg.get("id_machine")
        metrics = msg.get("metrics") or {}
        ts = msg.get("ts")
        if not (idm and ts and metrics):
            return
        insert_metrics(idm, metrics, ts)
        await maybe_alert(idm, metrics, ts)  # <- importante: await

        # broadcast tempo real p/ dashboard
        await dashboard_broadcast({
            "type": "metrics",
            "id_machine": idm,
            "ts": ts,
            "metrics": metrics
        })

        await websocket.send(json.dumps({"type": "ack", "ts": ts, "id_machine": idm}))
        return

async def handle_ws_collectors(websocket):
    # auth simples por token: primeiro frame
    if AUTH_TOKEN:
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=10)
            msg = json.loads(raw)
            token = msg.get("token") or msg.get("machine", {}).get("token")
            if token != AUTH_TOKEN:
                await websocket.close(code=4001, reason="unauthorized")
                return
            await process_message(msg, websocket)
        except Exception:
            await websocket.close(code=4002, reason="bad auth/init")
            return

    try:
        async for raw in websocket:
            try:
                msg = json.loads(raw)
                await process_message(msg, websocket)
            except Exception:
                continue
    except Exception:
        pass

def start_ws_server():
    async def ws_main():
        ws_collectors = websockets.serve(
            lambda ws, path=None: handle_ws_collectors(ws),
            WS_HOST, WS_PORT_COLLECTORS, ping_interval=20, ping_timeout=20, max_queue=256
        )
        ws_dashboards = websockets.serve(
            lambda ws, path=None: dashboard_ws_handler(ws),
            WS_HOST, WS_PORT_DASHBOARD, ping_interval=20, ping_timeout=20, max_queue=256
        )
        async with ws_collectors, ws_dashboards:
            print(f"[ws] Colectors WS em ws://{WS_HOST}:{WS_PORT_COLLECTORS}")
            print(f"[ws] Dashboard WS em ws://{WS_HOST}:{WS_PORT_DASHBOARD}")
            await asyncio.Future()  # run forever
    asyncio.run(ws_main())

# =========================
# API Flask (leitura)
# =========================
app = Flask(__name__)
CORS(app)

@app.route("/health")
def health():
    return jsonify({"ok": True, "time": datetime.utcnow().isoformat()})

@app.route("/maquinas")
def maquinas():
    conn = db_connect()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
    SELECT id_machine, hostname, local_ip, os_system, os_release, platform_machine,
           cpu_count_logical, ram_total, first_seen, last_seen
    FROM maquinas
    ORDER BY last_seen DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(rows)

@app.route("/metricas/<id_machine>/ultimas/<int:limite>")
def metricas_ultimas(id_machine, limite):
    limite = max(1, min(limite, 2000))
    conn = db_connect()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
    SELECT ts, cpu_percent, ram_percent, disk_percent, temperature_c, local_ip
    FROM metricas
    WHERE id_machine=%s
    ORDER BY ts DESC
    LIMIT %s
    """, (id_machine, limite))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(rows)

@app.route("/alertas/recentes/<int:limite>")
def alertas_recentes(limite):
    limite = max(1, min(limite, 1000))
    conn = db_connect()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
    SELECT id_machine, ts, tipo, detalhe
    FROM alertas
    ORDER BY ts DESC
    LIMIT %s
    """, (limite,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(rows)

def start_api():
    print(f"[api] Flask em http://{API_HOST}:{API_PORT}")
    app.run(host=API_HOST, port=API_PORT, debug=False, use_reloader=False)

def main():
    t_ws = threading.Thread(target=start_ws_server, daemon=True)
    t_ws.start()
    start_api()

if __name__ == "__main__":
    main()
