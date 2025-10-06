import os
import asyncio
from typing import Set
from datetime import datetime, timedelta
from io import StringIO

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, PlainTextResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# ---- Persistent storage config ----
DATA_DIR = os.getenv("DATA_DIR", "./data")
DATA_FILE = os.path.join(DATA_DIR, "metrics.csv")
os.makedirs(DATA_DIR, exist_ok=True)
if not os.path.exists(DATA_FILE):
    open(DATA_FILE, "a", encoding="utf-8").close()

data_lock = asyncio.Lock()

app = FastAPI()
AUTH_TOKEN = os.getenv("AUTH_TOKEN", "bbbdatamonitor")

clients: Set[asyncio.Queue[str]] = set()
broadcast_lock = asyncio.Lock()

# Enable CORS for access from diagnostic app at localhost:8000
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ====================================================================
# 1. HTML CONTENT DEFINITION (Full content for the / route)
# ====================================================================

METRICS_HTML_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Live Metrics Dashboard</title>
    
    <!-- Using Tailwind CSS for basic styling -->
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        :root {
            --band: rgba(255,214,102,.18);
            --band-outline: rgba(255,214,102,.6);
        }
        body { font-family: 'Inter', sans-serif; background-color: #0d1117; color: #c9d1d9; padding: 10px; }
        .card { background-color: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 16px; margin-bottom: 16px; }
        .chip { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; margin-left: 8px; font-weight: 500; }
        .chip:nth-child(1) { background-color: #238636; }
        .chip:nth-child(2) { background-color: #8957e5; }
        h1 { font-size: 1.5rem; font-weight: 600; margin-bottom: 1rem; }
        #toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
        .btn { background-color: #21262d; color: #c9d1d9; border: 1px solid #30363d; padding: 6px 12px; border-radius: 6px; cursor: pointer; transition: background-color 0.2s; }
        .btn:hover { background-color: #30363d; }
        input[type="number"], input[type="range"] { background-color: #0d1117; border: 1px solid #30363d; color: #c9d1d9; padding: 4px 8px; border-radius: 4px; }
        .rangeCol { display: flex; flex-direction: column; align-items: flex-start; }
        .rangeCol .label { margin-bottom: 4px; font-size: 0.875rem; color: #8b949e; }
        .rangeCol input[type="range"] { width: 150px; }
        .rangeCol .ticks { display: flex; justify-content: space-between; width: 150px; font-size: 0.65rem; color: #8b949e; margin-top: 2px; }
        .footer { margin-top: 10px; font-size: 0.8rem; color: #8b949e; }
        canvas { background-color: #161b22; }
    </style>

    <!-- Chart Library and Plugins -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.0.1/dist/chartjs-plugin-zoom.umd.min.js"></script>

    <!-- Plugin Registration Script -->
    <script>
        const zoomPlugin = window['chartjs-plugin-zoom'];
        if (zoomPlugin) {
            Chart.register(zoomPlugin);
        }
    </script>
</head>
<body>
    <div class="max-w-7xl mx-auto">
        <h1 class="text-white">Live Metrics Dashboard 
            <span class="chip">SSE stream</span>
            <span class="chip">History backfill</span>
        </h1>
        
        <!-- Controls Card -->
        <div class="card">
            <div id="toolbar">
                <!-- Time Range Control -->
                <div class="rangeCol">
                    <div class="label" id="rangeLabel">Last 7 days</div>
                    <input id="rangeDays" type="range" min="0" max="4" step="1" value="2" />
                    <div class="ticks"><span>24h</span><span>3d</span><span>7d</span><span>14d</span><span>30d</span></div>
                </div>

                <button class="btn" id="reloadBtn">Reload Data</button>
                <button class="btn" id="dlRangeBtn">Download CSV (range)</button>
                <button class="btn" id="dlAllBtn">Download CSV (all)</button>
            </div>
            
            <div id="advancedControls" class="flex flex-wrap gap-3 mt-4">
                <!-- DL Range Control -->
                <div class="flex items-center gap-2">
                    <label class="text-sm">DL Min/Max:</label>
                    <input id="dlMin" type="number" value="-200" class="w-20 text-xs">
                    <input id="dlMax" type="number" value="200" class="w-20 text-xs">
                    <button class="btn text-xs" id="applyRangeBtn">Apply Range</button>
                </div>

                <button class="btn text-xs" id="resetZoomBtn">Reset Zoom/Range</button>

                <!-- Temp Band Control -->
                <div class="flex items-center gap-2">
                    <label class="text-sm">Temp Band Min/Max:</label>
                    <input id="bandMin" type="number" value="45" class="w-20 text-xs">
                    <input id="bandMax" type="number" value="47" class="w-20 text-xs">
                    <button class="btn text-xs" id="applyBandBtn">Apply Band</button>
                </div>
            </div>

            <div class="footer">CSV format: <code>date,time,original_dl,predicted_dl,temperature,pressure</code></div>
        </div>
        
        <!-- Chart Containers -->
        <div class="card h-[45vh] min-h-[300px]">
            <canvas id="chartTop" class="w-full h-full"></canvas>
        </div>
        <div class="card h-[45vh] min-h-[300px]">
            <canvas id="chartBottom" class="w-full h-full"></canvas>
        </div>
    </div>

    <!-- 2. MAIN FUNCTIONALITY AND CHART DEFINITION SCRIPT -->
    <script>
        // --------------------------------------------------------------------------------
        // MAIN FUNCTIONALITY AND CHART DEFINITION (RESTORED)
        // This script handles all data, charting, and control logic for the iframe.
        // --------------------------------------------------------------------------------
        
        /* ---------- Utilities ---------- */
        const DAY_CHOICES = [1, 3, 7, 14, 30];
        const rangeEl = document.getElementById("rangeDays");
        const rangeLabelEl = document.getElementById("rangeLabel");
        let currentDays = 7; 

        // Ensure rangeEl and rangeLabelEl exist before proceeding (safety check)
        if (rangeEl && rangeLabelEl) {
            // Set initial value based on the default range slider value (2 maps to 7 days)
            currentDays = DAY_CHOICES[Number(rangeEl.value) || 2]; 

            function idxToDays(i) { i = Number(i); return DAY_CHOICES[Math.max(0, Math.min(DAY_CHOICES.length - 1, i))]; }
            function labelForDays(d) { return d === 1 ? "Last 24 hours" : `Last ${d} days`; }
            window.syncRangeLabel = function() { 
                currentDays = idxToDays(rangeEl.value);
                rangeLabelEl.textContent = labelForDays(currentDays); 
            } 
            rangeEl.addEventListener("input", window.syncRangeLabel);
        }

        /* Robust CSV parsing */
        function parseCSVLine(l) {
            if (!l) return null;
            const parts = l.split(",");
            if (parts.length < 6) return null;
            // The time string from the server is 'YYYY-MM-DD HH:MM:SS', convert to ISO format
            const ts = (parts[0].trim() + "T" + parts[1].trim()); 
            const t = new Date(ts);
            const oDL = Number(parts[2]); const pDL = Number(parts[3]);
            const temp = Number(parts[4]); const pres = Number(parts[5]);
            if (!(t instanceof Date) || isNaN(+t) || !isFinite(temp) || !isFinite(pres)) return null;
            return { t, temp, pressure: pres, oDL, pDL };
        }

        /* ---------- Charts Setup ---------- */
        const points = [];

        const topCtx = document.getElementById("chartTop") ? document.getElementById("chartTop").getContext("2d") : null;
        const bottomCtx = document.getElementById("chartBottom") ? document.getElementById("chartBottom").getContext("2d") : null;

        if (topCtx) {
            window.chartTop = new Chart(topCtx, {
                type: "line",
                data: {
                    datasets: [{
                        label: "Temperature (°C)",
                        yAxisID: "yTemp",
                        data: [],
                        tension: .25,
                        borderWidth: 2,
                        pointRadius: 0,
                        borderColor: "#58a6ff",
                        backgroundColor: "rgba(88,166,255,0.25)"
                    }, {
                        label: "Pressure (bar)",
                        yAxisID: "yPress",
                        data: [],
                        tension: .25,
                        borderWidth: 2,
                        pointRadius: 0,
                        borderColor: "#ff6b6b",
                        backgroundColor: "rgba(255,107,107,0.25)"
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    interaction: { mode: "index", intersect: false },
                    scales: {
                        x: { type: "time", time: { tooltipFormat: "yyyy-MM-dd HH:mm:ss" }, ticks: { color: "#8b949e" }, grid: { color: "rgba(255,255,255,0.06)" } },
                        yTemp: { position: "left", min: 0, max: 80, ticks: { color: "#8b949e" }, grid: { color: "rgba(255,255,255,0.06)" } },
                        yPress: { position: "right", min: 12, max: 14, ticks: { color: "#8b949e" }, grid: { drawOnChartArea: false } }
                    },
                    plugins: { 
                        legend: { labels: { color: "#e6edf3", usePointStyle: true, boxWidth: 10 } },
                        // Add zoom/pan for chartTop as well for full functionality
                        zoom: {
                            pan: { enabled: true, mode: "xy" },
                            zoom: { wheel: { enabled: true }, pinch: { enabled: true }, drag: { enabled: true, modifierKey: "shift" }, mode: "xy" }
                        }
                    }
                }
            });
        }

        const tempBandPlugin = {
            id: "tempBand",
            beforeDatasetsDraw(c) {
                const s = c.scales.yTemp2;
                if (!s) return;
                const b = c.$band || { min: 45, max: 47 };
                if (!(b.max > b.min)) return;
                const y1 = s.getPixelForValue(b.min),
                    y2 = s.getPixelForValue(b.max);
                const { ctx, chartArea: { left, right } } = c;
                const top = Math.min(y1, y2),
                    h = Math.abs(y2 - y1);
                ctx.save();
                // Use CSS variables defined in the style block
                ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--band') || "rgba(255,214,102,.18)";
                ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--band-outline') || "rgba(255,214,102,.6)";
                ctx.fillRect(left, top, right - left, h);
                ctx.strokeRect(left, top, right - left, h);
                ctx.restore();
            }
        };

        if (bottomCtx) {
            window.chartBottom = new Chart(bottomCtx, {
                type: "line",
                data: {
                    datasets: [{
                        label: "Original DL",
                        yAxisID: "yDL",
                        data: [],
                        tension: .25,
                        borderWidth: 2,
                        pointRadius: 0,
                        borderColor: "#3fb950",
                        backgroundColor: "rgba(63,185,80,0.25)"
                    }, {
                        label: "Predicted DL",
                        yAxisID: "yDL",
                        data: [],
                        tension: .25,
                        borderWidth: 2,
                        pointRadius: 0,
                        borderColor: "#ffa657",
                        backgroundColor: "rgba(255,166,87,0.25)"
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    interaction: { mode: "index", intersect: false },
                    scales: {
                        x: { type: "time", time: { tooltipFormat: "yyyy-MM-dd HH:mm:ss" }, ticks: { color: "#8b949e" }, grid: { color: "rgba(255,255,255,0.06)" } },
                        yDL: { position: "left", min: -200, max: 200, title: { display: true, text: "DL (Range)" }, ticks: { color: "#8b949e" }, grid: { color: "rgba(255,255,255,0.06)" } },
                        // This scale is only used for the temp band visualization in the bottom chart
                        yTemp2: { position: "right", min: 15, max: 65, title: { display: true, text: "Temperature (°C)" }, ticks: { color: "#8b949e" }, grid: { drawOnChartArea: false } }
                    },
                    plugins: {
                        legend: { labels: { color: "#e6edf3", usePointStyle: true, boxWidth: 10 } },
                        zoom: {
                            limits: { y: { min: -200, max: 200 } },
                            pan: { enabled: true, mode: "xy" }, // Enable X-axis pan for better navigation
                            zoom: { wheel: { enabled: true }, pinch: { enabled: true }, drag: { enabled: true, modifierKey: "shift" }, mode: "xy" }
                        }
                    }
                },
                plugins: [tempBandPlugin]
            });
            window.chartBottom.$band = { min: 45, max: 47 };
        }


        /* Add point to both charts */
        function addPoint(p) {
            points.push(p);
            if (window.chartTop) {
                window.chartTop.data.datasets[0].data.push({ x: p.t, y: p.temp });
                window.chartTop.data.datasets[1].data.push({ x: p.t, y: p.pressure });
            }
            if (window.chartBottom) {
                window.chartBottom.data.datasets[0].data.push({ x: p.t, y: p.oDL });
                window.chartBottom.data.datasets[1].data.push({ x: p.t, y: p.pDL });
            }
        }

        /* ---------- History backfill then SSE ---------- */
        async function loadHistory(days) {
            try {
                // Fetch is relative to the iframe's URL (0.0.0.0:5001)
                const res = await fetch(`/history?days=${days}`, { cache: "no-store" });
                if (!res.ok) {
                    console.error("Failed to load history:", res.status, res.statusText);
                    return;
                }
                const text = await res.text();
                // Clear existing data before adding new history
                if (window.chartTop) window.chartTop.data.datasets.forEach(ds => ds.data.length = 0);
                if (window.chartBottom) window.chartBottom.data.datasets.forEach(ds => ds.data.length = 0);
                points.length = 0;

                const lines = text.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
                for (const l of lines) {
                    const p = parseCSVLine(l);
                    if (p) addPoint(p);
                }
                if (window.chartTop) window.chartTop.update("none");
                if (window.chartBottom) window.chartBottom.update("none");
                console.log(`Loaded ${lines.length} historical records.`);
            } catch (e) {
                console.warn("History load failed:", e);
            }
        }

        function startSSE() {
            if (window.eventSource) {
                window.eventSource.close();
            }
            window.eventSource = new EventSource("/stream");

            window.eventSource.onmessage = (ev) => {
                const raw = (ev.data || "").trim();
                if (!raw) return;
                
                // The server broadcasts full CSV lines, potentially multiple if buffered
                const lines = raw.includes("\n") ? raw.split(/\r?\n/) : [raw]; 
                
                for (const l of lines) {
                    const p = parseCSVLine(l.trim());
                    if (p) {
                        addPoint(p);
                    }
                }
                if (window.chartTop) window.chartTop.update("none");
                if (window.chartBottom) window.chartBottom.update("none");
            };
            
            window.eventSource.onerror = (e) => {
                console.warn("SSE error/disconnected; browser will retry.", e);
                // Clean up the object on error
                window.eventSource.close(); 
                delete window.eventSource;
            };
            console.log("SSE stream started.");
        }

        /* ---------- Controls Event Listeners (Restored) ---------- */
        const reloadBtn = document.getElementById("reloadBtn");
        const dlRangeBtn = document.getElementById("dlRangeBtn");
        const dlAllBtn = document.getElementById("dlAllBtn");
        const dlMinEl = document.getElementById("dlMin");
        const dlMaxEl = document.getElementById("dlMax");
        const bandMinEl = document.getElementById("bandMin");
        const bandMaxEl = document.getElementById("bandMax");
        const applyRange = document.getElementById("applyRangeBtn");
        const resetZoom = document.getElementById("resetZoomBtn");
        const applyBand = document.getElementById("applyBandBtn");

        if (reloadBtn) reloadBtn.addEventListener("click", async () => {
            currentDays = idxToDays(rangeEl.value);
            await loadHistory(currentDays);
        });

        if (dlRangeBtn) dlRangeBtn.addEventListener("click", () => {
            const d = idxToDays(rangeEl.value);
            window.location.href = `/download-range?days=${d}`;
        });
        if (dlAllBtn) dlAllBtn.addEventListener("click", () => {
            window.location.href = `/download`;
        });

        if (applyRange && dlMinEl && dlMaxEl && window.chartBottom) applyRange.addEventListener("click", () => {
            let mn = parseFloat(dlMinEl.value),
                mx = parseFloat(dlMaxEl.value);
            if (!isFinite(mn) || !isFinite(mx) || mn >= mx) return;
            mn = Math.max(-200, mn);
            mx = Math.min(200, mx);
            window.chartBottom.options.scales.yDL.min = mn;
            window.chartBottom.options.scales.yDL.max = mx;
            window.chartBottom.update("none");
        });

        if (resetZoom && dlMinEl && dlMaxEl && window.chartBottom) resetZoom.addEventListener("click", () => {
            if (window.chartBottom && typeof window.chartBottom.resetZoom === "function") {
                window.chartBottom.resetZoom();
            }
            // Reset input fields to default values
            if (dlMinEl) dlMinEl.value = "-200";
            if (dlMaxEl) dlMaxEl.value = "200";
            
            // Explicitly set scale back to default after zoom reset
            if (window.chartBottom) {
                window.chartBottom.options.scales.yDL.min = -200;
                window.chartBottom.options.scales.yDL.max = 200;
                window.chartBottom.update("none");
            }
        });

        if (applyBand && bandMinEl && bandMaxEl && window.chartBottom) applyBand.addEventListener("click", () => {
            let bmin = parseFloat(bandMinEl.value),
                bmax = parseFloat(bandMaxEl.value);
            if (!isFinite(bmin) || !isFinite(bmax) || bmax <= bmin) return;
            bmin = Math.max(15, Math.min(65, bmin));
            bmax = Math.max(15, Math.min(65, bmax));
            if (bmax <= bmin) return;
            window.chartBottom.$band = { min: bmin, max: bmax };
            window.chartBottom.update("none");
        });
        
        // --------------------------------------------------------------------------------
        // BOOTSTRAP (RUNS AFTER ALL SETUP IS COMPLETE)
        // --------------------------------------------------------------------------------
        document.addEventListener('DOMContentLoaded', () => {
            (async function init() {
                if (typeof syncRangeLabel === 'function') {
                    syncRangeLabel();
                }

                if (typeof loadHistory === 'function') {
                    await loadHistory(currentDays); 
                }
                if (typeof startSSE === 'function') {
                    startSSE();
                }
            })();
        });
    </script>
</body>
</html>
"""

# ====================================================================
# 2. FASTAPI ROUTES AND FUNCTIONS (Continuation of Python)
# ====================================================================

async def broadcast(line: str):
    async with broadcast_lock:
        dead = []
        for q in clients:
            try:
                q.put_nowait(line)
            except Exception:
                dead.append(q)
        for q in dead:
            clients.discard(q)


async def append_line(line: str):
    """Append a single CSV line to DATA_FILE safely."""
    safe = line.rstrip("\r\n")
    if not safe:
        return
    async with data_lock:
        with open(DATA_FILE, "a", encoding="utf-8") as f:
            f.write(safe + "\n")

# New route to serve the METRICS_HTML_CONTENT
@app.get("/", response_class=HTMLResponse)
async def metrics_page():
    """Serves the complete HTML content for the live metrics dashboard (the iframe content)."""
    return HTMLResponse(content=METRICS_HTML_CONTENT)

@app.post("/ingest")
async def ingest(request: Request):
    if request.headers.get("x-auth-token") != AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    line = data.get("line")
    if not isinstance(line, str):
        raise HTTPException(status_code=400, detail="Expected JSON {'line': <string>}")
    line = line.rstrip("\r\n")
    if line:
        await append_line(line)    # <-- NEW
        await broadcast(line)
    return {"status": "ok"}

@app.post("/ingest-txt")
async def ingest_txt(request: Request):
    if request.headers.get("x-auth-token") != AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    body = (await request.body()).decode("utf-8", errors="replace").rstrip("\r\n")
    if body:
        await append_line(body)    # <-- NEW
        await broadcast(body)
    return {"status": "ok"}

@app.get("/history")
async def history(days: int = 7):
    # Return lines whose timestamp (first two CSV columns) is within last N days.
    cutoff = datetime.utcnow() - timedelta(days=max(1, days))
    out = []

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                parts = line.split(",")
                if len(parts) < 6:
                    continue
                # Combine Date + Time -> ISO-ish; tolerate parse errors by including the line
                dt_str = parts[0].strip() + " " + parts[1].strip()
                try:
                    dt = datetime.fromisoformat(dt_str.replace(" ", "T"))
                    if dt >= cutoff:
                        out.append(line)
                except Exception:
                    # If timestamp is malformed, include it (fail-open)
                    out.append(line)
    except FileNotFoundError:
        pass

    return PlainTextResponse("\n".join(out), media_type="text/plain")

# Full-file download
@app.get("/download")
def download_all():
    # serves the raw metrics.csv as attachment
    return FileResponse(DATA_FILE, media_type="text/csv", filename="metrics.csv")

# Range-filtered download (same filter as /history)
@app.get("/download-range")
def download_range(days: int = 7):
    buf = StringIO()
    cutoff = datetime.utcnow() - timedelta(days=max(1, days))
    # Optional header row for convenience
    buf.write("date,time,original_dl,predicted_dl,temperature,pressure\n")
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                parts = line.split(",")
                if len(parts) < 6:
                    continue
                dt_str = parts[0].strip() + " " + parts[1].strip()
                try:
                    dt = datetime.fromisoformat(dt_str.replace(" ", "T"))
                    if dt >= cutoff:
                        buf.write(line + "\n")
                except Exception:
                    # if parse fails, include the line
                    buf.write(line + "\n")
    except FileNotFoundError:
        pass

    csv_bytes = buf.getvalue().encode("utf-8")
    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="metrics_range.csv"'},
    )


@app.get("/stream")
async def stream():
    q: asyncio.Queue[str] = asyncio.Queue()
    clients.add(q)
    async def event_gen():
        try:
            while True:
                try:
                    line = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {line}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            clients.discard(q)
    return StreamingResponse(event_gen(), media_type="text/event-stream")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5001)
