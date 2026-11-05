"""Bible de production : entites, versions actives et planches de reference.

Compatible avec l'arborescence v1 (`bible/<type>/<slug>/fiche.yaml` et
`sheet_<version>.png`). Le noyau v3 ne fait que lire : la creation/edition des
fiches reste une decision humaine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ENTITY_TYPES = ("personnage", "lieu", "objet")
MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


@dataclass(frozen=True)
class Entity:
    name: str
    slug: str
    type: str
    active_version: str
    path: Path
    versions: dict[str, Any] = field(default_factory=dict)

    @property
    def sheet(self) -> Path | None:
        return _sheet_for(self.path, self.active_version)


def _sheet_for(entity_dir: Path, version: str) -> Path | None:
    for suffix in (".png", ".jpg", ".jpeg", ".webp"):
        candidate = entity_dir / f"sheet_{version}{suffix}"
        if candidate.exists():
            return candidate
    return None


class Bible:
    """Lecture d'une bible de projet."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def list_entities(self, etype: str | None = None) -> list[Entity]:
        entities: list[Entity] = []
        for kind in ([etype] if etype else ENTITY_TYPES):
            directory = self.root / kind
            if not directory.is_dir():
                continue
            for path in sorted(directory.iterdir()):
                fiche = path / "fiche.yaml"
                if path.is_dir() and fiche.exists():
                    entities.append(self._load(fiche, kind))
        return entities

    def get(self, slug: str, etype: str | None = None) -> Entity | None:
        for entity in self.list_entities(etype):
            if entity.slug == slug:
                return entity
        return None

    def _load(self, path: Path, kind: str) -> Entity:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return Entity(
            name=str(data.get("name") or path.parent.name),
            slug=str(data.get("slug") or path.parent.name),
            type=str(data.get("type") or kind),
            active_version=str(data.get("active_version") or "v1"),
            path=path.parent,
            versions=dict(data.get("versions") or {}),
        )

    def resolve(self, references: list[Any] | None) -> list[Path]:
        """Resout des references `slug`, `type/slug` ou `{type,slug}` en planches."""
        paths: list[Path] = []
        for reference in references or []:
            etype = None
            slug = reference
            if isinstance(reference, dict):
                etype = reference.get("type")
                slug = reference.get("slug") or reference.get("id")
            elif isinstance(reference, str) and "/" in reference:
                etype, slug = reference.split("/", 1)
            if not slug:
                continue
            entity = self.get(str(slug), str(etype) if etype else None)
            if entity is None:
                continue
            sheet = entity.sheet
            if sheet is not None and sheet not in paths:
                paths.append(sheet)
        return paths


def media_type_for(path: Path) -> str:
    return MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
