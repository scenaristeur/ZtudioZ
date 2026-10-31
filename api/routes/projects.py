from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.deps import get_project_root

router = APIRouter()

PROJECTS_DIR = Path(__file__).resolve().parent.parent.parent / "projets"


class ProjectCreate(BaseModel):
    slug: str
    title: str = ""


@router.get("/")
def list_projects():
    projects = []
    if PROJECTS_DIR.exists():
        for d in sorted(PROJECTS_DIR.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                checkpoint = d / ".checkpoint.json"
                status = "initialized" if checkpoint.exists() else "new"
                projects.append({"slug": d.name, "status": status})
    return {"projects": projects}


@router.get("/{slug}")
def get_project(slug: str):
    root = get_project_root(slug)
    if not root.exists():
        raise HTTPException(404, f"Project {slug} not found")

    checkpoint = root / ".checkpoint.json"
    history_json = root / ".history.json"

    scenes = []
    storyboard_dir = root / "preprod" / "storyboard"
    if storyboard_dir.exists():
        import re
        for d in sorted(storyboard_dir.iterdir()):
            if d.is_dir() and re.match(r"^scene_\d+$", d.name):
                sb = d / "storyboard.json"
                has_sb = sb.exists()
                shots = []
                if has_sb:
                    import json
                    shots = json.loads(sb.read_text())
                scenes.append({
                    "number": int(re.match(r"^scene_(\d+)$", d.name).group(1)),
                    "slug": d.name,
                    "has_storyboard": has_sb,
                    "shot_count": len(shots),
                })

    return {
        "slug": slug,
        "root": str(root),
        "checkpoint": str(checkpoint) if checkpoint.exists() else None,
        "scenes": scenes,
    }
