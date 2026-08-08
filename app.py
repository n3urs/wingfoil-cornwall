"""Local server for the Cornwall wing foiling dashboard.

    ./run.sh          then open http://localhost:8787
"""
from __future__ import annotations

import traceback
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

from wingfoil.engine import build_dashboard, parse_demo  # noqa: E402  (needs .env loaded first)

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Cornwall Wing Foiling")


@app.get("/api/dashboard")
def dashboard(days: int = 7, demo: str | None = None):
    try:
        return JSONResponse(build_dashboard(days=days, demo=parse_demo(demo)))
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
