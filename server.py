# server.py
import os
import asyncio
from typing import Set
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, PlainTextResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

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
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5001)
