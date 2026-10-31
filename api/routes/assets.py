from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from api.deps import get_project_root

router = APIRouter()

MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
}


@router.get("/{slug}/file/{path:path}")
def serve_file(slug: str, path: str):
    root = get_project_root(slug)
    file = root / path
    if not file.exists() or not file.is_file():
        raise HTTPException(404, "File not found")
    ext = file.suffix.lower()
    media = MEDIA_TYPES.get(ext, "application/octet-stream")
    return FileResponse(file, media_type=media)


def _resolve_shot_refs(slug: str, shot: dict) -> list[dict]:
    """Read refs from shot['refs'] and return their URLs."""
    root = get_project_root(slug)
    bible = root / "bible"
    ref_slugs = shot.get("refs", [])
    refs = []
    for s in ref_slugs[:4]:
        for etype in ("personnage", "lieu", "objet"):
            sheet = bible / etype / s / "sheet_v1.png"
            if sheet.exists():
                refs.append({
                    "slug": s,
                    "type": etype,
                    "url": f"/api/assets/{slug}/file/bible/{etype}/{s}/sheet_v1.png",
                })
                break
    return refs


@router.get("/{slug}/storyboard/{scene_number}")
def get_storyboard(slug: str, scene_number: int):
    root = get_project_root(slug)
    sb_dir = root / "preprod" / "storyboard" / f"scene_{scene_number:02d}"
    sb_path = sb_dir / "storyboard.json"
    if not sb_path.exists():
        raise HTTPException(404, "Storyboard not found")

    shots = json.loads(sb_path.read_text())
    board_path = sb_dir / "storyboard_board.png"

    result = {
        "scene_number": scene_number,
        "board_url": f"/api/assets/{slug}/file/preprod/storyboard/scene_{scene_number:02d}/storyboard_board.png" if board_path.exists() else None,
        "shots": [],
    }
    for i, shot in enumerate(shots, 1):
        shot_file = sb_dir / f"shot_{i:02d}.png"
        shot_url = f"/api/assets/{slug}/file/preprod/storyboard/scene_{scene_number:02d}/shot_{i:02d}.png" if shot_file.exists() else None
        refs = _resolve_shot_refs(slug, shot)
        result["shots"].append({
            **shot,
            "index": i,
            "image_url": shot_url,
            "refs": refs,
        })
    return result


@router.get("/{slug}/videos/{scene_number}")
def get_scene_videos(slug: str, scene_number: int):
    root = get_project_root(slug)
    vid_dir = root / "production" / "video" / f"scene_{scene_number:02d}"
    if not vid_dir.exists():
        return {"shots": []}

    shots = []
    for f in sorted(vid_dir.glob("shot_*.mp4")):
        m = re.match(r"shot_(\d+)\.mp4", f.name)
        if m:
            shots.append({
                "index": int(m.group(1)),
                "url": f"/api/assets/{slug}/file/production/video/scene_{scene_number:02d}/{f.name}",
                "size": f.stat().st_size,
            })
    return {"scene_number": scene_number, "shots": shots}


@router.get("/{slug}/audio/{scene_number}")
def get_scene_audio(slug: str, scene_number: int):
    root = get_project_root(slug)
    audio_dir = root / "production" / "audio" / f"scene_{scene_number:02d}"
    if not audio_dir.exists():
        return {"files": []}

    files = []
    for f in sorted(audio_dir.glob("*.mp3")):
        files.append({
            "name": f.stem,
            "url": f"/api/assets/{slug}/file/production/audio/scene_{scene_number:02d}/{f.name}",
            "size": f.stat().st_size,
        })
    return {"scene_number": scene_number, "files": files}


@router.get("/{slug}/montage/{scene_number}")
def get_scene_montage(slug: str, scene_number: int):
    root = get_project_root(slug)
    scene_key = f"scene_{scene_number:02d}"
    montage = root / "postprod" / "montage" / f"{scene_key}.mp4"
    if not montage.exists():
        return None
    return {
        "url": f"/api/assets/{slug}/file/postprod/montage/{scene_key}.mp4",
        "size": montage.stat().st_size,
    }


@router.get("/{slug}/sources")
def list_sources(slug: str):
    root = get_project_root(slug)
    sources_dir = root / "sources"
    if not sources_dir.exists():
        return {"sources": []}

    sources = []
    for f in sorted(sources_dir.iterdir()):
        if f.name.startswith("."):
            continue
        if f.is_file():
            sources.append({
                "name": f.name,
                "type": "file",
                "size": f.stat().st_size,
                "url": f"/api/assets/{slug}/file/sources/{f.name}",
            })
        elif f.is_dir():
            contents = list(f.iterdir()) if f.exists() else []
            sources.append({
                "name": f.name,
                "type": "directory",
                "count": len(contents),
            })
    return {"sources": sources}


@router.get("/{slug}/sources/{path:path}")
def list_sources_sub(slug: str, path: str):
    root = get_project_root(slug)
    target = root / "sources" / path
    if not target.exists():
        raise HTTPException(404, "Not found")
    if target.is_file():
        ext = target.suffix.lower()
        media = MEDIA_TYPES.get(ext, "application/octet-stream")
        return FileResponse(target, media_type=media)
    items = []
    for f in sorted(target.iterdir()):
        if f.name.startswith("."):
            continue
        if f.is_file():
            items.append({"name": f.name, "type": "file", "size": f.stat().st_size})
        elif f.is_dir():
            items.append({"name": f.name, "type": "directory", "count": len(list(f.iterdir()))})
    return {"path": path, "items": items}


@router.put("/{slug}/storyboard/{scene_number}/shot/{shot_number}")
def update_shot(slug: str, scene_number: int, shot_number: int, shot_data: dict):
    """Update a single shot's prompt/description/etc in storyboard.json."""
    root = get_project_root(slug)
    sb_path = root / "preprod" / "storyboard" / f"scene_{scene_number:02d}" / "storyboard.json"
    if not sb_path.exists():
        raise HTTPException(404, "Storyboard not found")

    shots = json.loads(sb_path.read_text())
    for i, shot in enumerate(shots):
        if shot.get("number") == shot_number:
            # Update editable fields
            for key in ("prompt_image", "description", "dialogue", "type", "camera", "duration_seconds", "refs"):
                if key in shot_data:
                    shots[i][key] = shot_data[key]
            break
    else:
        raise HTTPException(404, f"Shot {shot_number} not found")

    with open(sb_path, "w") as f:
        json.dump(shots, f, ensure_ascii=False, indent=2)
    return {"status": "ok", "shot": shot_number}
