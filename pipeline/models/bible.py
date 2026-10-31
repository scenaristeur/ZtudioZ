from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class EntityVersion:
    seed: int
    mix_alias: str
    attributs: list[str]
    personnalite: str = ""
    palette: str = ""
    accessoires: list[str] = field(default_factory=list)
    prompt_image: str = ""
    tenues: dict[str, str] = field(default_factory=dict)
    eclairage: str = ""
    atmosphere: str = ""


@dataclass
class Entity:
    name: str
    slug: str
    type: str
    active_version: str
    versions: dict[str, EntityVersion]
    created: str = ""

    def get_active(self) -> EntityVersion:
        return self.versions.get(self.active_version, list(self.versions.values())[0])

    def switch_version(self, version: str) -> None:
        if version in self.versions:
            self.active_version = version

    def get_prompt(self, **overrides) -> str:
        v = self.get_active()
        prompt = v.prompt_image
        for k, val in overrides.items():
            if val:
                prompt += f", {k}: {val}"
        return prompt

    def get_seed(self) -> int:
        return self.get_active().seed


class BibleDB:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def get_entity(self, slug: str) -> Optional[Entity]:
        for subdir in ("personnage", "lieu", "objet"):
            fiche_path = self.root / subdir / slug / "fiche.yaml"
            if fiche_path.exists():
                return self._load_entity(fiche_path)
        return None

    def get_entity_with_type(self, slug: str, etype: str) -> Optional[Entity]:
        fiche_path = self.root / etype / slug / "fiche.yaml"
        if fiche_path.exists():
            return self._load_entity(fiche_path)
        return None

    def _load_entity(self, path: Path) -> Entity:
        with open(path) as f:
            data = yaml.safe_load(f)
        versions = {}
        for vid, vdata in data.get("versions", {}).items():
            versions[vid] = EntityVersion(
                seed=vdata.get("seed", 0),
                mix_alias=vdata.get("mix_alias", ""),
                attributs=vdata.get("attributs", []),
                personnalite=vdata.get("personnalite", ""),
                palette=vdata.get("palette", ""),
                accessoires=vdata.get("accessoires", []),
                prompt_image=vdata.get("prompt_image", ""),
                tenues=vdata.get("tenues", {}),
                eclairage=vdata.get("eclairage", ""),
                atmosphere=vdata.get("atmosphere", ""),
            )
        return Entity(
            name=data.get("name", ""),
            slug=data.get("slug", ""),
            type=data.get("type", ""),
            active_version=data.get("active_version", "v1"),
            versions=versions,
            created=data.get("created", ""),
        )

    def save_entity(self, entity: Entity) -> None:
        subdir = entity.type
        path = self.root / subdir / entity.slug / "fiche.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "name": entity.name,
            "slug": entity.slug,
            "type": entity.type,
            "active_version": entity.active_version,
            "versions": {},
        }
        for vid, v in entity.versions.items():
            data["versions"][vid] = {
                "seed": v.seed,
                "mix_alias": v.mix_alias,
                "attributs": v.attributs,
                "personnalite": v.personnalite,
                "palette": v.palette,
                "accessoires": v.accessoires,
                "prompt_image": v.prompt_image,
                "tenues": v.tenues,
            }
            if v.eclairage:
                data["versions"][vid]["eclairage"] = v.eclairage
            if v.atmosphere:
                data["versions"][vid]["atmosphere"] = v.atmosphere
        with open(path, "w") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    def list_entities(self, etype: str | None = None) -> list[Entity]:
        entities = []
        subdirs = [etype] if etype else ["personnage", "lieu", "objet"]
        for subdir in subdirs:
            d = self.root / subdir
            if not d.exists():
                continue
            for slug_dir in sorted(d.iterdir()):
                if slug_dir.is_dir():
                    fiche = slug_dir / "fiche.yaml"
                    if fiche.exists():
                        entities.append(self._load_entity(fiche))
        return entities

    def get_sheet_path(self, slug: str, etype: str, version: str = "") -> Path:
        subdir = etype
        v = f"_{version}" if version else ""
        return self.root / subdir / slug / f"sheet{v}.png"

    def set_active_version(self, slug: str, version: str) -> None:
        entity = self.get_entity(slug)
        if entity and version in entity.versions:
            entity.active_version = version
            self.save_entity(entity)

    def get_active_version(self, slug: str) -> str | None:
        entity = self.get_entity(slug)
        return entity.active_version if entity else None
