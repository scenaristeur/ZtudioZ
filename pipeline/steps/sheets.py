from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pipeline.config import get_production_config
from pipeline.models.bible import BibleDB
from pipeline.reporter import step_item, step_warn
from pipeline.services.image import ImageService
from pipeline.services.prompt_builder import PromptBuilder

logger = logging.getLogger(__name__)


class SheetsStep:
    """Génère les images de référence (character sheets) depuis la bible."""

    def __init__(self, verbose: bool = False):
        self.image = ImageService()
        self.verbose = verbose

    def _generate_one(self, slug: str, data: dict, bible: BibleDB,
                      prompt_builder: PromptBuilder, bible_root: Path) -> tuple[str, dict]:
        entity = bible.get_entity(slug)
        if not entity:
            step_warn(f"{slug}", "Introuvable dans la bible")
            return slug, {"error": "Introuvable dans la bible"}

        version = entity.get_active()
        seed = version.seed

        template_map = {
            "personnage": "character_sheet",
            "lieu": "location_sheet",
            "objet": "object_sheet",
            "ambiance": "character_sheet",
        }
        template_id = template_map.get(entity.type, "character_sheet")

        prompt = prompt_builder.build(
            template_id,
            name=entity.name,
            type=entity.type,
            attributs=version.attributs,
            palette=version.palette,
            accessoires=version.accessoires,
            personnalite=version.personnalite or version.atmosphere or "",
            fallback=version.prompt_image,
        )
        sheet_seed = prompt_builder.get_seed(template_id, seed)

        etype = entity.type
        out_dir = bible_root / etype / slug
        out_dir.mkdir(parents=True, exist_ok=True)

        label = f"{entity.name} ({slug})"
        details = f"seed={sheet_seed}, template={template_id}, prompt={prompt[:80]}…"
        try:
            path = self.image.generate(
                prompt=prompt,
                seed=sheet_seed,
                output_dir=str(out_dir),
                filename=f"sheet_{entity.active_version}.png",
            )
            step_item(label, f"{path} — {details}")
            return slug, {"path": path, "seed": sheet_seed, "version": entity.active_version}
        except Exception as e:
            step_warn(label, str(e))
            return slug, {"error": str(e)}

    def run(self, entities: dict, bible_root: Path) -> dict:
        bible = BibleDB(bible_root)
        prompt_builder = PromptBuilder(bible_root)
        results = {}

        parallel = get_production_config().get("parallel_shots", False)
        if parallel and len(entities) > 1:
            with ThreadPoolExecutor(max_workers=min(len(entities), 4)) as pool:
                futures = [pool.submit(self._generate_one, slug, data, bible, prompt_builder, bible_root)
                           for slug, data in entities.items()]
                for future in as_completed(futures):
                    slug, res = future.result()
                    results[slug] = res
        else:
            for slug, data in entities.items():
                slug, res = self._generate_one(slug, data, bible, prompt_builder, bible_root)
                results[slug] = res

        return results
