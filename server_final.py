import os
import asyncio
from typing import Set
from datetime import datetime, timedelta

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
        await append_line(line)   # <-- NEW
        await broadcast(line)
    return {"status": "ok"}

@app.post("/ingest-txt")
async def ingest_txt(request: Request):
    if request.headers.get("x-auth-token") != AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    body = (await request.body()).decode("utf-8", errors="replace").rstrip("\r\n")
    if body:
        await append_line(body)   # <-- NEW
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
    from io import StringIO
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

@app.get("/", response_class=HTMLResponse)
async def index():
    return """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Live Metrics (Temp & Pressure + DLs)</title>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<style>
  :root{
    --bg:#0b0f14; --panel:#0d1117; --fg:#e6edf3; --muted:#8b949e; --border:#30363d; --accent:#9ecbff;                                                           --shadow:0 14px 40px rgba(0,0,0,.45);
    --tt-blue:#0e2a47; --tt-yellow:#3b2f0e; --tt-blue-border:#1e4370; --tt-yellow-border:#6b5a1b;                                                               --btn:#1f6feb; --btnText:#fff; --btnHover:#1a5fcc;                                                                                                          --band: rgba(255,214,102,0.18); --band-outline: rgba(255,214,102,0.6);          
    /*
    --bg:#0b0f14; --panel:#0d1117; --fg:#e6edf3; --muted:#8b949e; --border:#30363d; --accent:#9ecbff;
    --shadow:0 10px 30px rgba(0,0,0,.35);
    --btn:#1f6feb; --btnText:#fff; --btnHover:#1a5fcc;
    --band: rgba(255,214,102,0.18); --band-outline: rgba(255,214,102,0.6);
    */
    /* tooltip bg colors (semi-transparent) 
    --tt-blue-bg: rgba(14, 42, 71, 0.85);
    --tt-yellow-bg: rgba(59, 47, 14, 0.85);
    --tt-blue-border:#1e4370; 
    --tt-yellow-border:#6b5a1b;
    */
  }
  html, body { height: 100%; }
  body{margin:0;background:var(--bg);color:var(--fg);font-family:ui-sans-serif,system-ui,Segoe UI,Roboto,Helvetica,Arial}
  #wrap{max-width:1100px;margin:0 auto;padding:16px; overscroll-behavior-y: contain; }

  h1{font-size:18px;margin:0 0 12px;color:var(--accent);display:flex;gap:10px;align-items:center}
  .chip{font-size:12px;color:var(--muted);border:1px solid var(--border);padding:2px 8px;border-radius:999px}
  .card{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:12px;box-shadow:var(--shadow);margin-bottom:16px}

  #toolbar{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;margin:8px 2px 12px 2px}
  .rangeCol{display:flex;flex-direction:column;align-items:flex-start;gap:6px}
  .label{font-size:12px;color:var(--muted)}
  input[type=range]{width:260px}
  .ticks{display:flex;justify-content:space-between;font-size:11px;color:var(--muted);margin-top:2px;width:260px}
  .btn{background:var(--btn);color:var(--btnText);border:1px solid var(--border);padding:8px 12px;border-radius:10px;cursor:pointer}
  .btn:hover{background:var(--btnHover)}

  /* ↑ Taller charts for more Y-axis height */
  .chartWrap{position:relative;height:60vh; overscroll-behavior: contain;}
  .chartWrap.small{height:50vh}
  canvas{border-radius:8px;border:1px solid var(--border);background:#0a0f15; touch-action: none; cursor: grab;}

  .legend{display:flex;gap:12px;align-items:center;color:var(--muted);font-size:12px;margin:6px 2px 0 2px}
  .footer{margin-top:8px;color:var(--muted);font-size:12px}

  #dlTools{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:8px 2px}

  /* Two stacked popups — smaller boxes, same text size, semi-transparent */
  .tt,.tt2,.ttB,.tt2B{
    position:absolute; pointer-events:none; 
    min-width:200px; max-width:340px;          /* smaller box */
    transform:translate(-50%,-110%); 
    color:var(--fg); border-radius:10px;       /* slightly smaller radius */
    padding:8px 10px;                          /* tighter padding (text size unchanged) */
    white-space:nowrap; box-shadow:var(--shadow); 
    font-size:13px; line-height:1.35; letter-spacing:.2px; 
    border:1px solid var(--border);
    backdrop-filter: blur(1.5px);              /* subtle glass effect to see chart through */
  }
  .tt,.ttB{background:var(--tt-blue-bg);border-color:var(--tt-blue-border)}
  .tt2,.tt2B{background:var(--tt-yellow-bg);border-color:var(--tt-yellow-border);transform:translate(-50%,-6%); margin-top:6px; opacity:.97}
  .row{display:flex;gap:10px;justify-content:space-between}
  .k{color:#b6c2cf}
</style>
</head>
<body>
<div id="wrap">
  <h1>Live Metrics <span class="chip">SSE stream</span><span class="chip">History backfill</span></h1>

  <div class="card">
    <div id="toolbar">
      <div class="rangeCol">
        <div class="label" id="rangeLabel">Last 7 days</div>
        <input id="rangeDays" type="range" min="0" max="4" step="1" value="2" />
        <div class="ticks"><span>24h</span><span>3d</span><span>7d</span><span>14d</span><span>30d</span></div>
      </div>
      <button class="btn" id="reloadBtn">Reload</button>
      <button class="btn" id="dlRangeBtn">Download CSV (range)</button>
      <button class="btn" id="dlAllBtn">Download CSV (all)</button>
    </div>
    <div class="footer">CSV: <code>date,time,original_dl,predicted_dl,temperature,pressure</code> • 1 day = 24 hours</div>
  </div>

  <!-- Top chart -->
  <div class="card">
    <div class="chartWrap" id="wrapTop">
      <canvas id="chartTop"></canvas>
      <div id="tt" class="tt" style="display:none"></div>
      <div id="tt2" class="tt2" style="display:none"></div>
    </div>
    <div class="legend"><span>Top: Temperature (0–80 °C, left) vs Pressure (12–14 bar, right). Wheel/pinch to zoom (XY), drag to pan, Shift+drag box-zoom.</span></div>
  </div>

  <!-- Bottom chart -->
  <div class="card">
    <div id="dlTools">
      <button class="btn" id="resetZoomBtn">Reset Zoom (both charts)</button>
      <span class="label">Temp band (°C):</span>
      <label class="label">Min <input id="bandMin" type="number" step="0.1" value="45"></label>
      <label class="label">Max <input id="bandMax" type="number" step="0.1" value="47"></label>
      <button class="btn" id="applyBandBtn">Apply Band</button>
      <span class="label">Tip: hover for values • wheel/pinch to zoom • drag to pan • Shift+drag to box-zoom</span>
    </div>
    <div class="chartWrap small" id="wrapBottom">
      <canvas id="chartBottom"></canvas>
      <div id="ttB" class="ttB" style="display:none"></div>
      <div id="tt2B" class="tt2B" style="display:none"></div>
    </div>
    <div class="legend"><span>Bottom: Original vs Predicted DL (−200…200, left). Temp axis (15–65 °C, right) with adjustable band.</span></div>
  </div>
</div>

<!-- Use UMD build of Chart.js for plugin compatibility -->
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0"></script>
<!-- Correct zoom plugin build -->
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.2.0/dist/chartjs-plugin-zoom.min.js"></script>
<script>
  // Register the zoom plugin BEFORE creating charts
  const zoomPlugin = window['chartjs-plugin-zoom'];
  if (zoomPlugin) {
    Chart.register(zoomPlugin);
    console.log("✅ Zoom plugin registered:", Chart.registry.plugins.get('zoom'));
  } else {
    console.error("❌ Zoom plugin not found – check CDN URL");
  }
</script>

<script>
/* ===== X-axis sync helpers ===== */
let __syncing = false;

function copyXWindow(fromChart, toChart) {
  const s = fromChart.scales?.x;
  if (!s || s.min == null || s.max == null) return;

  __syncing = true;

  // Clear partner's zoom/pan transform so we set a clean window.
  if (typeof toChart.resetZoom === 'function') {
    toChart.resetZoom();
  }

  toChart.options.scales.x.min = s.min;
  toChart.options.scales.x.max = s.max;
  toChart.update('none');

  __syncing = false;
}

/*let __syncing = false;

function copyXWindow(fromChart, toChart) {
  const s = fromChart.scales?.x;
  if (!s || s.min == null || s.max == null) return;
  __syncing = true;
  toChart.options.scales.x.min = s.min;
  toChart.options.scales.x.max = s.max;
  toChart.update('none');
  __syncing = false;
}
*/

/* ---------- Utilities ---------- */
const DAY_CHOICES=[1,3,7,14,30];
const rangeEl=document.getElementById("rangeDays");
const rangeLabelEl=document.getElementById("rangeLabel");
function idxToDays(i){ i=Number(i); return DAY_CHOICES[Math.max(0,Math.min(DAY_CHOICES.length-1,i))]; }
function labelForDays(d){ return d===1 ? "Last 24 hours" : `Last ${d} days`; }
function syncRangeLabel(){ rangeLabelEl.textContent = labelForDays(idxToDays(rangeEl.value)); }
rangeEl.addEventListener("input", syncRangeLabel);

/* CSV parsing */
function parseCSVLine(l){
  if(!l) return null;
  const parts=l.split(",");
  if(parts.length<6) return null;
  const t=new Date((parts[0].trim()+" "+parts[1].trim()).replace(" ","T"));
  const oDL=Number(parts[2]), pDL=Number(parts[3]), temp=Number(parts[4]), pres=Number(parts[5]);
  if(isNaN(+t)||!isFinite(temp)||!isFinite(pres)) return null;
  return { t, temp, pressure: pres, oDL, pDL };
}

/* ---------- Charts ---------- */
const points=[];

/* ===== Top chart ===== */
const tt   = document.getElementById("tt");
const tt2  = document.getElementById("tt2");
const topCtx=document.getElementById("chartTop").getContext("2d");
const chartTop=new Chart(topCtx,{
  type:"line",
  data:{datasets:[
    {label:"Temperature (°C)",yAxisID:"yTemp",data:[],tension:.25,borderWidth:2,pointRadius:0,borderColor:"#58a6ff",backgroundColor:"rgba(88,166,255,0.15)"},
    {label:"Pressure (bar)",  yAxisID:"yPress",data:[],tension:.25,borderWidth:2,pointRadius:0,borderColor:"#ff6b6b",backgroundColor:"rgba(255,107,107,0.15)"}
  ]},
  options:{
    responsive:true,maintainAspectRatio:false,animation:false,
    interaction:{mode:"index",intersect:false},
    scales:{
      x:{type:"time",time:{tooltipFormat:"yyyy-MM-dd HH:mm:ss.SSS"},ticks:{color:"#8b949e"},grid:{color:"rgba(255,255,255,0.06)"}},
      yTemp:{position:"left",min:0,max:80,ticks:{color:"#8b949e"},grid:{color:"rgba(255,255,255,0.06)"}},
      yPress:{position:"right",min:12,max:14,ticks:{color:"#8b949e"},grid:{drawOnChartArea:false}}
    },
    plugins:{
      legend:{labels:{color:"#e6edf3",usePointStyle:true,boxWidth:10}},
      tooltip:{enabled:false,external:externalTooltipTop},
      zoom:{
        limits:{ x:{}, y:{} },
    // ←← PAN with left mouse drag (no modifier). threshold:0 = immediate
    pan: {
      enabled: true,
      mode: 'x',
      threshold: 0,
      onPanStart: ({chart}) => { chart.canvas.style.cursor = 'grabbing'; },
      onPan:       ({chart}) => {},  // (optional) hook
      onPanComplete: ({chart}) => { chart.canvas.style.cursor = ''; }
    },

    // Zoom (wheel/pinch) + box-zoom with Shift
    zoom: {
      wheel: { enabled: true, speed: 0.1 },
      pinch: { enabled: true },
      drag:  { enabled: true, modifierKey: 'shift' }, // Shift+drag = box zoom
      mode: 'xy',
      onZoom:         ({chart}) => { if (!__syncing) copyXWindow(chart, chartBottom); },
      onZoomComplete: ({chart}) => { if (!__syncing) copyXWindow(chart, chartBottom); } 
    } 
      }
    }
  }
});

/* container-relative tooltip positioning */
function positionTooltip(containerEl, chart, tooltip, boxEl, yOffset=0) {
  const containerRect = containerEl.getBoundingClientRect();
  const canvasRect = chart.canvas.getBoundingClientRect();
  const left = (canvasRect.left - containerRect.left) + tooltip.caretX;
  const top  = (canvasRect.top  - containerRect.top)  + tooltip.caretY + yOffset;
  boxEl.style.left = left + "px";
  boxEl.style.top  = top  + "px";
}

function externalTooltipTop(ctx){
  const {chart,tooltip}=ctx;
  if(!tooltip||tooltip.opacity===0||!tooltip.dataPoints?.length){ tt.style.display="none"; tt2.style.display="none"; return; }
  const idx=tooltip.dataPoints[0].dataIndex, p=points[idx];
  if(!p){ tt.style.display="none"; tt2.style.display="none"; return; }
  const timeStr=new Intl.DateTimeFormat(undefined,{year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}).format(p.t);
  tt.innerHTML = `
    <div class="row"><span class="k">Time</span><span>${timeStr}</span></div>
    <div class="row"><span class="k">Temperature</span><span>${p.temp.toFixed(2)} °C</span></div>
    <div class="row"><span class="k">Pressure</span><span>${p.pressure.toFixed(5)}</span></div>`;
  tt2.innerHTML = `
    <div class="row"><span class="k">Original DL</span><span>${isFinite(p.oDL)?p.oDL.toFixed(6):"—"}</span></div>
    <div class="row"><span class="k">Predicted DL</span><span>${isFinite(p.pDL)?p.pDL.toFixed(6):"—"}</span></div>`;
  const container = document.getElementById("wrapTop");
  positionTooltip(container, chart, tooltip, tt, 0);
  positionTooltip(container, chart, tooltip, tt2, 36); /* stacked just below */
  tt.style.display="block"; tt2.style.display="block";
}

/* ===== Bottom chart (DLs + temp band + zoom) ===== */
const ttB  = document.getElementById("ttB");
const tt2B = document.getElementById("tt2B");

const tempBandPlugin={id:"tempBand",beforeDatasetsDraw(c){
  const s=c.scales.yTemp2; if(!s) return;
  const b=c.$band||{min:45,max:47}; if(!(b.max>b.min))return;
  const y1=s.getPixelForValue(b.min), y2=s.getPixelForValue(b.max);
  const {ctx,chartArea:{left,right}}=c; const top=Math.min(y1,y2), h=Math.abs(y2-y1);
  ctx.save();
  ctx.fillStyle=getComputedStyle(document.documentElement).getPropertyValue('--band')||"rgba(255,214,102,.18)";
  ctx.strokeStyle=getComputedStyle(document.documentElement).getPropertyValue('--band-outline')||"rgba(255,214,102,.6)";
  ctx.fillRect(left,top,right-left,h); ctx.strokeRect(left,top,right-left,h);
  ctx.restore();
}};

const bottomCtx=document.getElementById("chartBottom").getContext("2d");
const chartBottom=new Chart(bottomCtx,{
  type:"line",
  data:{datasets:[
    {label:"Original DL", yAxisID:"yDL", data:[], tension:.25, borderWidth:2, pointRadius:0, borderColor:"#3fb950", backgroundColor:"rgba(63,185,80,0.15)"},
    {label:"Predicted DL",yAxisID:"yDL", data:[], tension:.25, borderWidth:2, pointRadius:0, borderColor:"#ffa657", backgroundColor:"rgba(255,166,87,0.15)"}
  ]},
  options:{
    responsive:true,maintainAspectRatio:false,animation:false,
    interaction:{mode:"index",intersect:false},
    scales:{
      x:{type:"time",time:{tooltipFormat:"yyyy-MM-dd HH:mm:ss.SSS"},ticks:{color:"#8b949e"},grid:{color:"rgba(255,255,255,0.06)"}},
      yDL:{position:"left",min:-200,max:200,title:{display:true,text:"DL (−200 … +200)"},ticks:{color:"#8b949e"},grid:{color:"rgba(255,255,255,0.06)"}},
      yTemp2:{position:"right",min:15,max:65,title:{display:true,text:"Temperature (°C)"},ticks:{color:"#8b949e"},grid:{drawOnChartArea:false}}
    },
    plugins:{
      legend:{labels:{color:"#e6edf3",usePointStyle:true,boxWidth:10}},
      tooltip:{enabled:false,external:externalTooltipBottom},
      zoom:{
        limits:{ x:{}, y:{} },
    // ←← PAN with left mouse drag (no modifier). threshold:0 = immediate
    pan: {
      enabled: true,
      mode: 'x',
      threshold: 0,
      onPanStart: ({chart}) => { chart.canvas.style.cursor = 'grabbing'; },
      onPan:       ({chart}) => {},  // (optional) hook
      onPanComplete: ({chart}) => { chart.canvas.style.cursor = ''; }
    },

    // Zoom (wheel/pinch) + box-zoom with Shift
    zoom: {
      wheel: { enabled: true, speed: 0.1 },
      pinch: { enabled: true },
      drag:  { enabled: true, modifierKey: 'shift' }, // Shift+drag = box zoom
      mode: 'xy',

      onZoom:         ({chart}) => { if (!__syncing) copyXWindow(chart, chartTop); },
      onZoomComplete: ({chart}) => { if (!__syncing) copyXWindow(chart, chartTop); }    
    }
      }
    }
  },
  plugins:[tempBandPlugin]
});
chartBottom.$band={min:45,max:47};

function externalTooltipBottom(ctx){
  const {chart,tooltip}=ctx;
  if(!tooltip||tooltip.opacity===0||!tooltip.dataPoints?.length){ ttB.style.display="none"; tt2B.style.display="none"; return; }
  const idx=tooltip.dataPoints[0].dataIndex, p=points[idx];
  if(!p){ ttB.style.display="none"; tt2B.style.display="none"; return; }

  const timeStr=new Intl.DateTimeFormat(undefined,{year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}).format(p.t);
  ttB.innerHTML = `
    <div class="row"><span class="k">Time</span><span>${timeStr}</span></div>
    <div class="row"><span class="k">Band</span><span>${(chartBottom.$band?.min??45).toFixed(1)}–${(chartBottom.$band?.max??47).toFixed(1)} °C</span></div>`;
  tt2B.innerHTML = `
    <div class="row"><span class="k">Original DL</span><span>${isFinite(p.oDL)?p.oDL.toFixed(6):"—"}</span></div>
    <div class="row"><span class="k">Predicted DL</span><span>${isFinite(p.pDL)?p.pDL.toFixed(6):"—"}</span></div>`;

  const container = document.getElementById("wrapBottom");
  positionTooltip(container, chart, tooltip, ttB, 0);
  positionTooltip(container, chart, tooltip, tt2B, 30); /* stacked closer (smaller boxes) */
  ttB.style.display="block"; tt2B.style.display="block";
}

/* Add point to both charts */
function addPoint(p){
  points.push(p);
  chartTop.data.datasets[0].data.push({x:p.t,y:p.temp});
  chartTop.data.datasets[1].data.push({x:p.t,y:p.pressure});
  chartBottom.data.datasets[0].data.push({x:p.t,y:p.oDL});
  chartBottom.data.datasets[1].data.push({x:p.t,y:p.pDL});
}

/* ---------- History backfill then SSE ---------- */
let currentDays=7;

async function loadHistory(days){
  try{
    const res=await fetch(`/history?days=${days}`,{cache:"no-store"});
    if(!res.ok) return;
    const text=await res.text();
    const lines=text.split(/\\r?\\n/).map(s=>s.trim()).filter(Boolean);
    for(const l of lines){ const p=parseCSVLine(l); if(p) addPoint(p); }
    chartTop.update("none"); chartBottom.update("none");
  }catch(e){ console.warn("history load failed:", e); }
}
function startSSE(){
  const es=new EventSource("/stream");
  es.onmessage=(ev)=>{
    const raw=(ev.data||"").trim(); if(!raw) return;
    const lines=raw.includes("\\n")?raw.split(/\\r?\\n/):[raw];
    for(const l of lines){ const p=parseCSVLine(l.trim()); if(p) addPoint(p); }
    chartTop.update("none"); chartBottom.update("none");
  };
  es.onerror=(e)=>console.warn("SSE error/disconnected; browser will retry.", e);
}

/* ---------- Controls ---------- */
const reloadBtn=document.getElementById("reloadBtn");
const dlRangeBtn=document.getElementById("dlRangeBtn");
const dlAllBtn=document.getElementById("dlAllBtn");
const bandMinEl=document.getElementById("bandMin");
const bandMaxEl=document.getElementById("bandMax");
const applyBand=document.getElementById("applyBandBtn");

reloadBtn.addEventListener("click", async ()=>{
  currentDays=idxToDays(rangeEl.value);
  chartTop.data.datasets.forEach(ds=>ds.data.length=0);
  chartBottom.data.datasets.forEach(ds=>ds.data.length=0);
  await loadHistory(currentDays);
});
dlRangeBtn.addEventListener("click", ()=>{ const d=idxToDays(rangeEl.value); window.location.href=`/download-range?days=${d}`; });
dlAllBtn.addEventListener("click", ()=>{ window.location.href="/download"; });

/*
document.getElementById("resetZoomBtn").onclick = () => {
  if (typeof chartTop.resetZoom === "function") chartTop.resetZoom();
  if (typeof chartBottom.resetZoom === "function") chartBottom.resetZoom();

  // Restore Y ranges only for the top chart
  chartTop.options.scales.yTemp.min = 0;
  chartTop.options.scales.yTemp.max = 80;
  chartTop.options.scales.yPress.min = 12;
  chartTop.options.scales.yPress.max = 14;

  // Don’t clamp chartBottom.yDL anymore — let Chart.js decide
  chartBottom.options.scales.yDL.min = -200;
  chartBottom.options.scales.yDL.max = 200;
  chartTop.update("none");
  chartBottom.update("none");
  
  // Keep X windows identical
  copyXWindow(chartTop, chartBottom);

};
*/

document.getElementById("resetZoomBtn").onclick = () => {
  // 1) Clear any zoom/pan transforms from the plugin
  if (typeof chartTop.resetZoom === "function") chartTop.resetZoom();
  if (typeof chartBottom.resetZoom === "function") chartBottom.resetZoom();

  // 2) Let both charts auto-fit X again (full history), then we'll sync X
  chartTop.options.scales.x.min = undefined;
  chartTop.options.scales.x.max = undefined;
  chartBottom.options.scales.x.min = undefined;
  chartBottom.options.scales.x.max = undefined;

  // 3) Restore TOP Y axes to their defaults
  chartTop.options.scales.yTemp.min  = 0;
  chartTop.options.scales.yTemp.max  = 80;
  chartTop.options.scales.yPress.min = 12;
  chartTop.options.scales.yPress.max = 14;

  // 4) Restore BOTTOM DL Y axis to [-200, +200] as requested
  chartBottom.options.scales.yDL.min = -200;
  chartBottom.options.scales.yDL.max =  200;

  // (optional) keep bottom Temp axis pinned
  // chartBottom.options.scales.yTemp2.min = 15;
  // chartBottom.options.scales.yTemp2.max = 65;

  // 5) Update both charts; then sync X windows so they match exactly
  chartTop.update("none");
  chartBottom.update("none");

  // After autoscale, copy X from top -> bottom (keeps them aligned)
  copyXWindow(chartTop, chartBottom);
};


applyBand.addEventListener("click", ()=>{
  let bmin=parseFloat(bandMinEl.value), bmax=parseFloat(bandMaxEl.value);
  if(!isFinite(bmin)||!isFinite(bmax)||bmax<=bmin) return;
  bmin=Math.max(15,Math.min(65,bmin));
  bmax=Math.max(15,Math.min(65,bmax));
  if(bmax<=bmin) return;
  chartBottom.$band={min:bmin,max:bmax};
  chartBottom.update("none");
});

/* ---------- Trap wheel so page doesn't scroll while zooming ---------- */
function trapWheel(el){
  el.addEventListener('wheel', (e)=>{ e.preventDefault(); }, { passive:false });
}
trapWheel(document.getElementById('chartTop'));
trapWheel(document.getElementById('chartBottom'));

/**************************************************************************/

// ---- Manual XY pan shim with X-sync ----
function enableManualPan(chart, canvasEl, partnerChart, yScales, yBounds) {
  // yScales = array of scale IDs to pan vertically together (e.g. ['yTemp','yPress'])
  // yBounds = { yTemp:[min,max], yPress:[min,max], ... } or null to not clamp

  let dragging = false;
  let startX = 0, startY = 0;

  canvasEl.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;            // left button only
    if (e.shiftKey) return;                // let Shift+drag do box-zoom
    dragging = true;
    startX = e.clientX;
    startY = e.clientY;
    canvasEl.style.cursor = 'grabbing';
    e.preventDefault();
  });

  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;

    const sx = chart.scales.x;
    const dx = e.clientX - startX;
    const dy = e.clientY - startY;

    // Decide dominant direction (horizontal vs vertical)
    const horiz = Math.abs(dx) >= Math.abs(dy);

    if (horiz) {
      // --------- X PAN (sync both charts) ---------
      if (sx && sx.min != null && sx.max != null) {
        const deltaX = sx.getValueForPixel(0) - sx.getValueForPixel(dx);
        if (isFinite(deltaX)) {
          chart.options.scales.x.min = sx.min + deltaX;
          chart.options.scales.x.max = sx.max + deltaX;
          chart.update('none');
          if (partnerChart) copyXWindow(chart, partnerChart);   // <<< SYNC X
        }
      }
      startX = e.clientX;
    } else {
      // --------- Y PAN (chart-local only) ---------
      for (const yId of (yScales || [])) {
        const sy = chart.scales[yId];
        if (!sy || sy.min == null || sy.max == null) continue;
        const deltaY = sy.getValueForPixel(0) - sy.getValueForPixel(dy);
        if (!isFinite(deltaY)) continue;

        let nmin = sy.min + deltaY;
        let nmax = sy.max + deltaY;

        // clamp to bounds if provided
        if (yBounds && yBounds[yId]) {
          const [lo, hi] = yBounds[yId];
          const span = sy.max - sy.min;
          // keep window inside [lo,hi]
          if (nmin < lo){ nmin = lo; nmax = lo + span; }
          if (nmax > hi){ nmax = hi; nmin = hi - span; }
        }

        chart.options.scales[yId].min = nmin;
        chart.options.scales[yId].max = nmax;
      }
      chart.update('none');
      startY = e.clientY;
    }
  });

  window.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    canvasEl.style.cursor = 'grab';
  });

  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && dragging) {
      dragging = false;
      canvasEl.style.cursor = 'grab';
    }
  });
}

/* Attach pans:
   - TOP: Y-pans both left & right axes; clamp Temp to 0..80, Press to 12..14
   - BOTTOM: Y-pans DL and Temp2; clamp DL to -200..200, Temp2 to 15..65
*/
enableManualPan(
  chartTop,
  document.getElementById('chartTop'),
  chartBottom,
  ['yTemp','yPress'],
  { yTemp:[0,80], yPress:[12,14] }
);

enableManualPan(
  chartBottom,
  document.getElementById('chartBottom'),
  chartTop,
  ['yDL','yTemp2'],                 // pan both Y axes
  { yTemp2:[15,65] }               // ← no yDL bounds here
);

/*enableManualPan(
  chartBottom,
  document.getElementById('chartBottom'),
  chartTop,
  ['yDL','yTemp2'],
  { yDL:[-200,200], yTemp2:[15,65] }
);
*/

/*********************************************************************/
/* ---------- Boot ---------- */
(async function init(){
  syncRangeLabel();
  await loadHistory(currentDays);
  startSSE();
})();
</script>
</body>
</html>"""

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
