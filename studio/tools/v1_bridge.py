"""Outils declaratifs du pont v1 : lecture seule, aucune ecriture dans `projets/`."""

from __future__ import annotations

from pathlib import Path

from ..kernel.errors import LoadError
from .. import v1_bridge


def _root(params: dict) -> Path:
    value = params.get("projects_root") or None
    if value is None:
        raise LoadError(
            "v1: racine absente; passer 'projects_root' ou definir STUDIO_V1_PROJECTS"
        )
    return Path(value)


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except v1_bridge.V1Error as exc:
        raise LoadError(str(exc)) from exc


def list_projects(params: dict, ctx) -> dict:
    """`projects_root` -> liste des projets v1 (scenes, entites)."""
    return {"projects": _call(v1_bridge.list_projects, _root(params))}


def project_status(params: dict, ctx) -> dict:
    """`projects_root` + `slug` -> checkpoint, etapes faites, entites."""
    slug = params.get("slug")
    if not slug:
        raise LoadError("v1.project_status: 'slug' requis")
    return {
        "status": _call(v1_bridge.project_status, _root(params), str(slug))
    }


def project_files(params: dict, ctx) -> dict:
    """`projects_root` + `slug` + `pattern` -> fichiers et tailles."""
    slug = params.get("slug")
    if not slug:
        raise LoadError("v1.project_files: 'slug' requis")
    return {
        "files": _call(
            v1_bridge.project_files,
            _root(params),
            str(slug),
            str(params.get("pattern") or "**/*"),
        )
    }


__all__ = ["list_projects", "project_status", "project_files"]
