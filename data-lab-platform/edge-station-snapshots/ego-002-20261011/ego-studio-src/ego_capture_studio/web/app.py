from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

HERE = Path(__file__).resolve().parent

app = FastAPI(title="Ego Studio", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
templates = Jinja2Templates(directory=str(HERE / "templates"))


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"year": datetime.now(timezone.utc).year},
    )


@app.get("/api/health")
def health():
    return JSONResponse({"status": "ok", "service": "ego-capture-studio"})


@app.get("/api/stats")
def stats_placeholder():
    """Replace with DB / object-store metrics in production."""
    return JSONResponse(
        {
            "hours_recorded": 0,
            "episodes": 0,
            "tasks": 0,
            "scenes": 0,
            "annotators": 0,
        }
    )
