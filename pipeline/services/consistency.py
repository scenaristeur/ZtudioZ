from __future__ import annotations

import logging
from pathlib import Path

from pipeline.services.llm import LLMService

logger = logging.getLogger(__name__)


class ConsistencyService:
    def __init__(self, llm_service: LLMService | None = None):
        self.llm = llm_service or LLMService()

    def check_character(self, character_name: str, images: list[str]) -> str:
        if len(images) < 2:
            return "Pas assez d'images pour comparer."
        prompt = (
            f"Tu vérifies la cohérence visuelle du personnage '{character_name}'.\n"
            f"Les images ci-dessous montrent ce personnage dans différentes scènes.\n\n"
            f"1. A-t-il la MÊME apparence partout ? (vêtements, coiffure, morphologie)\n"
            f"2. Y a-t-il des INCOHÉRENCES ?\n"
            f"3. Note globale : bonne / moyenne / mauvaise\n\n"
            f"Sois précis, cite les différences visibles."
        )
        return self.llm.vision(images, prompt)

    def check_location(self, location_name: str, images: list[str]) -> str:
        if len(images) < 2:
            return "Pas assez d'images pour comparer."
        prompt = (
            f"Tu vérifies la cohérence du lieu '{location_name}'.\n"
            f"Les images montrent-elles le même endroit ?\n"
            f"1. Mobilier, couleurs, éclairage cohérents ?\n"
            f"2. Incohérences visibles ?\n"
            f"3. Note globale : bonne / moyenne / mauvaise"
        )
        return self.llm.vision(images, prompt)

    def check_scene(self, scene_num: int, reference_images: dict[str, str], shot_images: list[str]) -> str:
        if not shot_images:
            return "Aucun plan à vérifier."
        refs = list(reference_images.values())
        n_refs = min(len(refs), 6)
        n_shots = min(len(shot_images), 10 - n_refs)
        all_images = refs[:n_refs] + shot_images[:n_shots]
        prompt = (
            f"Vérifie la cohérence visuelle de la SCÈNE {scene_num}.\n"
            f"Compare les plans générés avec les images de référence.\n"
            f"1. Les personnages ressemblent-ils aux références ?\n"
            f"2. Le lieu est-il cohérent ?\n"
            f"3. Les objets sont-ils reconnaissables ?\n"
            f"4. Note globale sur 10.\n"
            f"Réponds en français, sois constructif."
        )
        return self.llm.vision(all_images, prompt)
