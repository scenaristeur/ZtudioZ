"""Pont lecture seule vers les projets v1 (`projets/`).

Le toolkit v1 complet ecrit dans `projets/` ; le studio v3 ne doit jamais y
toucher. Ce module se contente de lire les checkpoints, les fiches de bible et
les fichiers de production pour les exposer au CLI et aux outils declaratifs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

from .bible import ENTITY_TYPES, Bible
from .kernel.errors import StudioError

# Meme registre que `Orchestrator.AVAILABLE_STEPS` cote v1.
V1_STEPS = (
    "init",
    "parse",
    "fiches",
    "sheets",
    "storyboard",
    "board",
    "visualize",
    "produce",
    "keyframes",
    "audio",
)


class V1Error(StudioError):
    """Racine, slug ou pattern v1 invalide."""


def _root(root: str | Path | None) -> Path:
    value = root if root is not None else os.environ.get("STUDIO_V1_PROJECTS")
    if not value:
        raise V1Error(
            "racine v1 absente: passer projects_root ou definir STUDIO_V1_PROJECTS"
        )
    return Path(value).expanduser()


def _project_dir(root: Path, slug: str) -> Path:
    slug = str(slug or "")
    if (
        not slug
        or slug in (".", "..")
        or slug.startswith(".")
        or Path(slug).name != slug
        or "/" in slug
        or "\\" in slug
    ):
        raise V1Error(f"slug invalide: {slug!r}")
    project = root / slug
    if not project.is_dir():
        raise V1Error(f"projet v1 introuvable: {slug}")
    return project


def _scenes(project: Path) -> int:
    return len(list((project / "script" / "parsed").glob("scene_*.yaml")))


def _read_entities(project: Path) -> tuple[list, list[dict[str, str]]]:
    """Lit les fiches valides ; une fiche cassee n'empeche pas d'inspecter le reste."""
    bible = Bible(project / "bible")
    entities: list = []
    errors: list[dict[str, str]] = []
    for kind in ENTITY_TYPES:
        directory = project / "bible" / kind
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            fiche = path / "fiche.yaml"
            if not (path.is_dir() and fiche.exists()):
                continue
            try:
                entities.append(bible._load(fiche, kind))
            except (OSError, yaml.YAMLError) as exc:
                message = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
                errors.append(
                    {"type": kind, "slug": path.name, "error": message}
                )
    return entities, errors


def _checkpoint(project: Path) -> dict[str, Any]:
    path = project / ".checkpoint.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise V1Error(f"checkpoint illisible: {path} ({exc})") from exc
    if not isinstance(data, dict):
        raise V1Error(f"checkpoint invalide: {path}")
    # v1 a longtemps ecrit 'production' : migration en memoire uniquement.
    if "production" in data:
        data.setdefault("produce", data.pop("production"))
    return data


def _has_artifact(folder: Path, pattern: str) -> bool:
    """Meme seuil que la v1 : un fichier non vide, non JSON, > 1024 octets."""
    if not folder.exists():
        return False
    for path in folder.rglob(pattern):
        if (
            path.is_file()
            and path.suffix != ".json"
            and path.stat().st_size > 1024
        ):
            return True
    return False


def _completed_steps(project: Path, checkpoint: dict[str, Any]) -> list[str]:
    done = {
        step
        for step in V1_STEPS
        if (checkpoint.get(step) or {}).get("status") == "done"
    }
    if project.exists():
        done.add("init")
    if list((project / "script" / "parsed").glob("scene_*.yaml")):
        done.add("parse")
    for step, folder, pattern in (
        ("board", project / "preprod" / "storyboard", "storyboard_board*.png"),
        ("visualize", project / "preprod" / "storyboard", "shot_*.png"),
        ("produce", project / "production" / "video", "*.mp4"),
        ("keyframes", project / "production" / "video", "*.mp4"),
        ("audio", project / "production" / "audio", "*.mp3"),
    ):
        if step in done and not _has_artifact(folder, pattern):
            done.discard(step)
    return sorted(done)


def list_projects(root: str | Path | None = None) -> list[dict[str, Any]]:
    """Liste les projets v1 : slug, nombre de scenes parsees, entites bible."""
    base = _root(root)
    if not base.is_dir():
        return []
    projects: list[dict[str, Any]] = []
    for path in sorted(base.iterdir()):
        if not path.is_dir() or path.name.startswith("."):
            continue
        entities, errors = _read_entities(path)
        projects.append(
            {
                "slug": path.name,
                "scenes": _scenes(path),
                "entities": len(entities),
                "errors": len(errors),
            }
        )
    return projects


def project_status(
    root: str | Path | None, slug: str
) -> dict[str, Any]:
    """Statut d'un projet v1, reconstruit sans ecrire dans le projet."""
    base = _root(root)
    project = _project_dir(base, slug)
    checkpoint = _checkpoint(project)
    entities, errors = _read_entities(project)
    return {
        "slug": project.name,
        "checkpoint": checkpoint,
        "completed_steps": _completed_steps(project, checkpoint),
        "entities": [
            {
                "name": entity.name,
                "slug": entity.slug,
                "type": entity.type,
                "active_version": entity.active_version,
                "sheet": entity.sheet.name if entity.sheet else None,
            }
            for entity in entities
        ],
        "invalid_entities": errors,
        "counts": {"scenes": _scenes(project), "entities": len(entities)},
    }


def project_files(
    root: str | Path | None, slug: str, pattern: str = "**/*"
) -> list[dict[str, Any]]:
    """Fichiers d'un projet v1, filtres par glob, sans sortir de sa racine."""
    base = _root(root)
    project = _project_dir(base, slug)
    pattern = str(pattern or "**/*")
    parts = Path(pattern).parts
    if Path(pattern).is_absolute() or ".." in parts:
        raise V1Error(f"pattern invalide (hors projet): {pattern!r}")
    resolved_project = project.resolve()
    files: list[dict[str, Any]] = []
    for path in sorted(project.glob(pattern)):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(resolved_project):
            raise V1Error(f"fichier hors du projet: {path}")
        files.append(
            {
                "path": str(path.relative_to(project)),
                "size": path.stat().st_size,
            }
        )
    return files


__all__ = [
    "V1_STEPS",
    "V1Error",
    "list_projects",
    "project_files",
    "project_status",
]
