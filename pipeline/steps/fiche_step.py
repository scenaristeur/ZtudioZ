from __future__ import annotations

import logging
from pathlib import Path

from pipeline import slugify
from pipeline.models.bible import BibleDB, Entity, EntityVersion
from pipeline.reporter import step_item, step_warn
from pipeline.services.llm import LLMService, tool

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Tu es un créateur de fiches personnages pour un film d'animation.
À partir du résumé d'une scène, tu détermines quels personnages, lieux et objets
sont présents, et tu génères pour chacun 10 attributs visuels concrets et précis.

Règle importante : ne crée une fiche QUE pour les entités RÉCURRENTES ou déterminantes
pour le récit (personnages principaux, lieux principaux de l'action, objets à fort
enjeu narratif). IGNORE les éléments one-shot ou décoratifs (éléments d'arrière-plan,
objets sans importance, fragments descriptifs) — ils ne méritent pas de fiche.

Pour chaque entité, fournis :
- name: nom
- attributs: liste de 10 attributs visuels (concrets, pas de métaphores)
- personnalite: description courte
- palette: couleurs dominantes
- accessoires: objets associés
- prompt_image: prompt pour générateur d'image

Réponds UNIQUEMENT avec du JSON valide.
Format: {"personnages": [{"name": "...", "attributs": [...]}], "lieux": [...], "objets": [...]}
"""


class FicheStep:
    def __init__(self, verbose: bool = False):
        self.llm = LLMService()
        self.verbose = verbose

    def run(self, scenes: list[dict], output_dir: Path) -> dict:
        bible = BibleDB(output_dir)
        all_entities = {}

        for scene in scenes:
            scene_num = scene.get("number", 0)
            scene_text = scene.get("description", "")

            step_item(f"Scène {scene_num}", f"Génération des fiches pour « {scene.get('title', '')} »")
            result = self.llm.chat_structured(
                system=_SYSTEM_PROMPT,
                user=f"Scène {scene_num}: {scene.get('title', '')}\n\n{scene_text[:3000]}",
                response_schema={"type": "object"},
            )

            if isinstance(result, dict) and "error" not in result:
                saved = self._save_fiches(bible, result, scene_num)
                for s in saved:
                    step_item(s["name"], f"{s['type']} — {s['path']} — seed={s['seed']}, alias={s['mix_alias']}")
            else:
                step_warn(f"Scène {scene_num}", f"Échec LLM: {result.get('error', 'réponse invalide')}")

        for e in bible.list_entities():
            all_entities[e.slug] = {
                "name": e.name,
                "slug": e.slug,
                "type": e.type,
                "active_version": e.active_version,
                "versions": {
                    v: {
                        "seed": ev.seed,
                        "mix_alias": ev.mix_alias,
                        "attributs": ev.attributs,
                        "prompt_image": ev.prompt_image,
                    }
                    for v, ev in e.versions.items()
                },
            }

        return all_entities

    def _save_fiches(self, bible: BibleDB, data: dict, scene_num: int) -> list[dict]:
        saved = []
        for category, entries in data.items():
            if not isinstance(entries, list):
                continue
            etype = {
                "personnages": "personnage",
                "lieux": "lieu",
                "objets": "objet",
            }.get(category, "personnage")

            for entry in entries:
                name = entry.get("name", "Inconnu")
                slug = slugify(name)

                existing = bible.get_entity(slug)
                if existing:
                    version_id = existing.active_version or "v1"
                    v = existing.versions.get(version_id)
                    if v is None:
                        v = EntityVersion(seed=abs(hash(f"{slug}_v1")) % 10000, mix_alias="personnage")
                        existing.versions[version_id] = v
                    v.mix_alias = entry.get("mix_alias", v.mix_alias or "personnage")
                    if entry.get("attributs"):
                        v.attributs = entry["attributs"]
                    if entry.get("personnalite"):
                        v.personnalite = entry["personnalite"]
                    if entry.get("palette"):
                        v.palette = entry["palette"]
                    if entry.get("accessoires"):
                        v.accessoires = entry["accessoires"]
                    if entry.get("prompt_image"):
                        v.prompt_image = entry["prompt_image"]
                    bible.save_entity(existing)
                    saved.append({
                        "name": name, "type": etype,
                        "path": str(bible.root / etype / slug / "fiche.yaml"),
                        "seed": v.seed,
                        "mix_alias": v.mix_alias,
                        "version": version_id,
                        "attributs": len(v.attributs),
                    })
                else:
                    entity = Entity(
                        name=name,
                        slug=slug,
                        type=etype,
                        active_version="v1",
                        versions={
                            "v1": EntityVersion(
                                seed=abs(hash(slug)) % 10000,
                                mix_alias=entry.get("mix_alias", "personnage"),
                                attributs=entry.get("attributs", []),
                                personnalite=entry.get("personnalite", ""),
                                palette=entry.get("palette", ""),
                                accessoires=entry.get("accessoires", []),
                                prompt_image=entry.get("prompt_image", f"Portrait de {name}"),
                            )
                        },
                    )
                    bible.save_entity(entity)
                    v = entity.versions["v1"]
                    saved.append({
                        "name": name, "type": etype,
                        "path": str(bible.root / etype / slug / "fiche.yaml"),
                        "seed": v.seed,
                        "mix_alias": v.mix_alias,
                        "version": "v1",
                        "attributs": len(v.attributs),
                    })
        return saved
