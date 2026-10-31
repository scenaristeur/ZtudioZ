from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from api.deps import get_bible, get_project_root

router = APIRouter()


@router.get("/{slug}/entities")
def list_entities(slug: str, etype: str | None = None):
    bible = get_bible(slug)
    entities = bible.list_entities(etype)
    return {
        "entities": [
            {
                "name": e.name,
                "slug": e.slug,
                "type": e.type,
                "active_version": e.active_version,
                "versions": list(e.versions.keys()),
            }
            for e in entities
        ]
    }


@router.get("/{slug}/entity/{entity_slug}")
def get_entity(slug: str, entity_slug: str):
    bible = get_bible(slug)
    entity = bible.get_entity(entity_slug)
    if not entity:
        raise HTTPException(404, f"Entity {entity_slug} not found")

    v = entity.get_active()
    return {
        "name": entity.name,
        "slug": entity.slug,
        "type": entity.type,
        "active_version": entity.active_version,
        "versions": {
            vid: {
                "seed": ev.seed,
                "attributs": ev.attributs,
                "personnalite": ev.personnalite,
                "palette": ev.palette,
                "prompt_image": ev.prompt_image,
            }
            for vid, ev in entity.versions.items()
        },
    }


@router.put("/{slug}/entity/{entity_slug}/version/{version}")
def switch_version(slug: str, entity_slug: str, version: str):
    bible = get_bible(slug)
    entity = bible.get_entity(entity_slug)
    if not entity:
        raise HTTPException(404, f"Entity {entity_slug} not found")
    if version not in entity.versions:
        raise HTTPException(400, f"Version {version} not found")

    entity.switch_version(version)
    bible.save_entity(entity)
    return {"status": "ok", "active_version": version}


@router.get("/{slug}/sheet/{entity_slug}")
def serve_sheet(slug: str, entity_slug: str):
    root = get_project_root(slug)
    bible_dir = root / "bible"
    for subdir in ("personnage", "lieu", "objet"):
        entity_dir = bible_dir / subdir / entity_slug
        if entity_dir.exists():
            # find active version or latest sheet
            active = entity_dir / ".active_version"
            if active.exists():
                ver = active.read_text().strip()
                sheet = entity_dir / f"sheet_{ver}.png"
                if sheet.exists():
                    return FileResponse(sheet, media_type="image/png")
            # fallback: find any sheet_*.png
            sheets = sorted(entity_dir.glob("sheet_*.png"))
            if sheets:
                return FileResponse(sheets[-1], media_type="image/png")
    raise HTTPException(404, "Sheet not found")
