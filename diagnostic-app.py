import json
import os
import re
import socket
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from flask import (
    Flask, render_template, request, jsonify,
    Response, stream_with_context, session, send_file
)

app = Flask(__name__)

# --- Secrets / Auth (unchanged behavior; password lock is optional) ---
app.secret_key = os.environ.get("BBB_SECRET_KEY", "dev-secret-not-for-production")
ADMIN_PASSWORD = os.environ.get("BBB_ADMIN_PASSWORD", "beagle")  # default password; override via env

# --- Paths / Constants ---
DB_PATH = "ai-addon.db"              # << NEW DB NAME
TABLE_NAME = "ai-table"              # << NEW TABLE NAME
LOG_PATH = "db_ops.log"              # << DB operations log text file

NETWORK_DIR = "/etc/systemd/network"
NETWORK_FILE = f"{NETWORK_DIR}/eth0.network"

# CPU freq/thermal paths
from pathlib import Path
CPUFREQ_BASE = Path("/sys/devices/system/cpu/cpu0/cpufreq")
CPUINFO_CUR = CPUFREQ_BASE / "cpuinfo_cur_freq"
SCALING_CUR = CPUFREQ_BASE / "scaling_cur_freq"
SCALING_GOV = CPUFREQ_BASE / "scaling_governor"
SCALING_MIN = CPUFREQ_BASE / "scaling_min_freq"
SCALING_MAX = CPUFREQ_BASE / "scaling_max_freq"

# --- Small helpers ---

def log_db(op: str, detail: str = ""):
    """Append a line to db_ops.log for every DB read/write."""
    ts = datetime.utcnow().isoformat() + "Z"
    line = f"{ts} | {op} | {detail}\n"
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line)
    except Exception:
        pass  # never break main flow on logging

def is_root() -> bool:
    return os.geteuid() == 0 if hasattr(os, "geteuid") else False

def get_current_ip_eth0() -> str:
    try:
        out = subprocess.check_output(["ip", "-4", "addr", "show", "dev", "eth0"], text=True)
        m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", out)
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""

def current_hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "beaglebone"

def has_internet(timeout=2.0) -> bool:
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=timeout).close()
        return True
    except Exception:
        return False

# --- DB layer (ai-addon.db / ai-table) ---

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db_if_needed():
    """
    On first app start: create ai-addon.db + ai-table and insert default row.
    We keep a single row (id=1) and UPDATE it instead of appending history.
    """
    first_time = not Path(DB_PATH).exists()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(f"""
        CREATE TABLE IF NOT EXISTS "{TABLE_NAME}" (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            hostname TEXT,
            ip TEXT,
            network_mode TEXT,       -- 'dhcp' or 'static'
            ip_static TEXT,
            subnet TEXT,
            gateway TEXT,
            dns TEXT,
            time_source TEXT,        -- 'ntp' or 'manual'
            date TEXT,               -- e.g. '2025-01-01'
            time TEXT,               -- e.g. '00:00:00'
            temperature_unit INTEGER, -- 1=Celsius, 0=Fahrenheit
            pressure_unit INTEGER,    -- code: 0 Psig, 1 Pascal, 2 KPa, 3 MPa, 4 Bar
            mode INTEGER,             -- 0=Inference, 1=Training
            instrument_name TEXT,     -- Instrument Name
            instrument_ip TEXT        -- Instrument IP Address
        )
    """)
    conn.commit()

    if first_time:
        hn = current_hostname()
        ip_now = get_current_ip_eth0()
        # Defaults per your spec
        defaults = {
            "hostname": hn,
            "ip": ip_now,
            "network_mode": "dhcp",
            "ip_static": "",
            "subnet": "",
            "gateway": "",
            "dns": "",
            "time_source": "ntp",
            "date": "2025-01-01",     # Jan 01, 2025
            "time": "00:00:00",
            "temperature_unit": 1,     # Celsius
            "pressure_unit": 0,        # Psig
            "mode": 0,                 # Inference
            "instrument_name": "A28 Leak Detection System",  # default name
            "instrument_ip": ""            # No IP first time
        }
        c.execute(f"""
            INSERT INTO "{TABLE_NAME}" (
                id, hostname, ip, network_mode, ip_static, subnet, gateway, dns,
                time_source, date, time, temperature_unit, pressure_unit, mode, instrument_name, instrument_ip
            ) VALUES (1, :hostname, :ip, :network_mode, :ip_static, :subnet, :gateway, :dns,
                      :time_source, :date, :time, :temperature_unit, :pressure_unit, :mode, :instrument_name, :instrument_ip)
        """, defaults)
        conn.commit()
        log_db("INIT", f"created {DB_PATH} / {TABLE_NAME} with defaults hostname={hn} ip={ip_now}")
    conn.close()

def db_get_row() -> sqlite3.Row:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(f'SELECT * FROM "{TABLE_NAME}" WHERE id=1')
    row = c.fetchone()
    conn.close()
    log_db("READ", "SELECT * FROM ai-table WHERE id=1")
    return row

def db_update(values: dict):
    """
    Update selected fields in the single-row table.
    Keys in 'values' must match column names in ai-table.
    """
    if not values:
        return
    sets = ", ".join([f'{k} = :{k}' for k in values.keys()])
    sql = f'UPDATE "{TABLE_NAME}" SET {sets} WHERE id = 1'
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(sql, values)
    conn.commit()
    conn.close()
    log_db("WRITE", f"UPDATE ai-table SET {list(values.keys())}")

# --- System configuration writers ---

def ensure_network_dir():
    Path(NETWORK_DIR).mkdir(parents=True, exist_ok=True)

def write_network_config(network_mode: str, ip: str, subnet: str, gateway: str, dns: str):
    """Write /etc/systemd/network/eth0.network for DHCP or Static."""
    ensure_network_dir()
    if network_mode == "dhcp":
        text = "[Match]\nName=eth0\nType=ether\n\n[Link]\nRequiredForOnline=yes\n\n[Network]\nDHCP=ipv4\n"
    else:
        dns_lines = ""
        if dns:
            dns_list = [d.strip() for d in re.split(r"[,\s]+", dns) if d.strip()]
            dns_lines = "".join(f"DNS={d}\n" for d in dns_list)
        text = (
            "[Match]\nName=eth0\nType=ether\n\n[Link]\nRequiredForOnline=yes\n\n[Network]\n"
            f"Address={ip}/{subnet}\nGateway={gateway}\n{dns_lines}"
        )
    Path("./temp_eth0.network").write_text(text)
    subprocess.run(["cp", "./temp_eth0.network", NETWORK_FILE], check=True)

def set_hostname(hostname: str):
    subprocess.run(["hostnamectl", "set-hostname", hostname], check=True)

def set_time_manual(date_str: str, time_str: str):
    timestr = f"{date_str} {time_str}"
    subprocess.run(["timedatectl", "set-ntp", "false"], check=True)
    subprocess.run(["date", "-s", timestr], check=True)
    subprocess.run(["hwclock", "-w"], check=True)

def set_time_ntp():
    subprocess.run(["timedatectl", "set-ntp", "true"], check=True)

# --- System info (for UI) ---

def read_cpu_times() -> Tuple[int, int]:
    parts = Path("/proc/stat").read_text().splitlines()[0].split()
    vals = list(map(int, parts[1:8]))
    idle = vals[3] + vals[4]
    nonidle = vals[0] + vals[1] + vals[2] + vals[5] + vals[6]
    total = idle + nonidle
    return total, idle

def _read_int(p: Path) -> Optional[int]:
    try:
        return int(p.read_text().strip())
    except Exception:
        return None

def read_cpu_freq_khz() -> Optional[int]:
    for p in (SCALING_CUR, CPUINFO_CUR):
        if p.exists():
            v = _read_int(p)
            if v and v > 0:
                return v
    return None

def read_cpu_governor() -> Optional[str]:
    if SCALING_GOV.exists():
        try:
            return SCALING_GOV.read_text().strip()
        except Exception:
            return None
    return None

def read_cpu_freq_bounds() -> Tuple[Optional[int], Optional[int]]:
    mn = _read_int(SCALING_MIN) if SCALING_MIN.exists() else None
    mx = _read_int(SCALING_MAX) if SCALING_MAX.exists() else None
    return mn, mx

def read_cpu_temp_c() -> Optional[float]:
    for cand in [Path("/sys/class/thermal/thermal_zone0/temp"),
                 Path("/sys/class/hwmon/hwmon0/temp1_input")]:
        if cand.exists():
            try:
                return int(cand.read_text().strip()) / 1000.0
            except Exception:
                pass
    # Fallback: first hwmon temp
    root = Path("/sys/class/hwmon")
    if root.exists():
        for hw in root.glob("hwmon*"):
            for inp in hw.glob("temp*_input"):
                try:
                    return int(inp.read_text().strip()) / 1000.0
                except Exception:
                    pass
    return None

def read_uptime_seconds() -> float:
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except Exception:
        return 0.0

# --- Auth routes (optional UI lock) ---

@app.route("/auth/state")
def auth_state():
    return jsonify({"unlocked": bool(session.get("unlocked", False))})

@app.route("/auth/unlock", methods=["POST"])
def auth_unlock():
    try:
        pwd = (request.get_json() or {}).get("password", "")
        if pwd == ADMIN_PASSWORD:
            session["unlocked"] = True
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "Invalid password"}), 401
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/auth/lock", methods=["POST"])
def auth_lock():
    session["unlocked"] = False
    return jsonify({"ok": True})

# --- Web routes ---

@app.route("/")
def index():
    # Always lock on (re)start / new visit
    session["unlocked"] = False

    row = db_get_row()

    # Prepare defaults for template
    defaults = {
        "hostname": row["hostname"],
        "network_mode": row["network_mode"],
        "ip": row["ip_static"] if row["network_mode"] == "static" and row["ip_static"] else "",
        "subnet": row["subnet"] or "",
        "gateway": row["gateway"] or "",
        "dns": row["dns"] or "",
        "pressure_code": int(row["pressure_unit"]),             # 0..4 code
        "temperature_unit": int(row["temperature_unit"]),       # 1 or 0
        "mode": int(row["mode"]),                               # 0 or 1
        "time_source": row["time_source"],
        "date": row["date"],
        "time": row["time"],
        "instrument_name": row["instrument_name"],
        "instrument_ip": row["instrument_ip"],
    }

    return render_template(
        "index.html",
        defaults=defaults,
        is_root=is_root(),
        current_ip=get_current_ip_eth0(),
        is_unlocked=False,   # lock by default on every load
    )

@app.route("/api/sysinfo")
def api_sysinfo():
    total1, idle1 = read_cpu_times()
    time.sleep(0.1)
    total2, idle2 = read_cpu_times()
    cpu_pct = 0.0
    if total2 - total1 > 0:
        cpu_pct = (1.0 - (idle2 - idle1) / (total2 - total1)) * 100.0
    load1, load5, load15 = os.getloadavg()
    temp_c = read_cpu_temp_c()
    freq_khz = read_cpu_freq_khz()
    gov = read_cpu_governor()
    fmin, fmax = read_cpu_freq_bounds()
    uptime_s = read_uptime_seconds()
    return jsonify({
        "cpu": round(cpu_pct, 1),
        "load": {"1m": load1, "5m": load5, "15m": load15},
        "temp_c": temp_c,
        "freq_khz": freq_khz,
        "governor": gov,
        "freq_min_khz": fmin,
        "freq_max_khz": fmax,
        "uptime_seconds": uptime_s,
        "server_time": datetime.now().isoformat(),
    })

@app.route("/api/telemetry")
def api_telemetry():
    @stream_with_context
    def event_stream():
        t_prev, idle_prev = read_cpu_times()
        while True:
            time.sleep(0.25)
            t_cur, idle_cur = read_cpu_times()
            dt = t_cur - t_prev
            cpu_pct = 0.0
            if dt > 0:
                cpu_pct = (1.0 - (idle_cur - idle_prev) / dt) * 100.0
            t_prev, idle_prev = t_cur, idle_cur
            payload = {
                "ts": datetime.utcnow().isoformat() + "Z",
                "cpu": round(cpu_pct, 1),
                "freq_khz": read_cpu_freq_khz(),
                "temp_c": read_cpu_temp_c(),
            }
            yield f"data: {json.dumps(payload)}\n\n"
    return Response(event_stream(), headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive"
    })

@app.route("/api/db-log")
def api_db_log():
    """
    Return the DB operations log as text (tail-like, but here we send all; it's small).
    """
    try:
        if not Path(LOG_PATH).exists():
            return Response("No log yet.", mimetype="text/plain")
        return send_file(LOG_PATH, mimetype="text/plain")
    except Exception as e:
        return Response(f"Error reading log: {e}", mimetype="text/plain", status=500)

@app.route("/submit-data", methods=["POST"])
def submit_data():
    # Optional lock: block if not unlocked
    if not session.get("unlocked", False):
        return jsonify({"error": "Editing is locked. Unlock to apply settings."}), 403

    try:
        data = request.get_json() or {}

        # Gather inputs
        hostname = (data.get("hostname") or "").strip() or current_hostname()
        network_mode = data.get("network_mode", "dhcp")
        ip = (data.get("ip") or "").strip()
        subnet = (data.get("subnet") or "").strip()
        gateway = (data.get("gateway") or "").strip()
        dns = (data.get("dns") or "").strip()

        temp_choice = str(data.get("temperature_unit", "1"))
        mode_choice = int(data.get("mode", 0))
        pressure_code = int(data.get("pressure_unit", 0))  # 0..4 as requested

        time_source = data.get("time_source", "ntp")
        date_str = data.get("date", "2025-01-01")
        time_str = data.get("time", "00:00:00")
     
        instrument_name = data.get("instrument_name", "")
        instrument_ip = data.get("instrument_ip", "")
        

        # Validation
        if time_source == "manual" and (not date_str or not time_str):
            return jsonify({"error": "For manual time, both Date and Time are required."}), 400
        if time_source == "ntp" and not has_internet():
            return jsonify({"error": "Internet is not available right now; cannot enable NTP."}), 409

        # --- WRITE to DB (single row) ---
        write_values = {
            "hostname": hostname,
            "network_mode": network_mode,
            "ip": get_current_ip_eth0(),  # live IP snapshot
            "time_source": time_source,
            "date": date_str,
            "time": time_str,
            "temperature_unit": 1 if temp_choice == "1" else 0,
            "pressure_unit": pressure_code,   # store the code (0..4)
            "mode": mode_choice,
            "instrument_name": instrument_name,
            "instrument_ip": instrument_ip,
        }
        if network_mode == "static":
            write_values.update({
                "ip_static": ip,
                "subnet": subnet,
                "gateway": gateway,
                "dns": dns,
            })
        else:
            write_values.update({
                "ip_static": "",
                "subnet": "",
                "gateway": "",
                "dns": "",
            })

        db_update(write_values)

        # ---- System changes (require root) ----
        if not is_root():
            return jsonify({"message": "Configuration saved to DB. Run as root to apply system changes.", "requires_root": True}), 200

        # Hostname
        set_hostname(hostname)

        # Network
        write_network_config(network_mode, ip, subnet, gateway, dns)
        subprocess.run(["systemctl", "restart", "systemd-networkd"], check=True)

        # Time
        if time_source == "manual":
            set_time_manual(date_str, time_str)
        else:
            set_time_ntp()

        return jsonify({"message": "Configuration applied successfully."}), 200

    except subprocess.CalledProcessError as cpe:
        return jsonify({"error": f"System command failed: {cpe}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ping-internet")
def api_ping_internet():
    return jsonify({"online": has_internet()})

# --- Boot ---
if __name__ == "__main__":
    init_db_if_needed()
    app.run(host="0.0.0.0", port=5000, debug=True)
