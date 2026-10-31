from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routes import bible, assets, history, pipeline, projects
from api.ws import ws_router

logger = logging.getLogger(__name__)

PROJECTS_DIR = Path(__file__).resolve().parent.parent / "projets"

app = FastAPI(
    title="ZtudioZ API",
    description="AI film production pipeline",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
app.include_router(pipeline.router, prefix="/api/pipeline", tags=["pipeline"])
app.include_router(bible.router, prefix="/api/bible", tags=["bible"])
app.include_router(assets.router, prefix="/api/assets", tags=["assets"])
app.include_router(history.router, prefix="/api/history", tags=["history"])
app.include_router(ws_router)


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "2.0.0"}
