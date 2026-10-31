#!/usr/bin/env python3
"""Agent Character Designer — utilise les services partagés du pipeline.

Délègue aux services pipeline/ pour la persistance (BibleDB), la génération
d'image (ImageService + PromptBuilder), et l'analyse vision (VisionAnalysisService).

Usage:
    python -m agents.character_designer --mode stdio
    python -m agents.character_designer --mode sse --port 8001
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import List

from camel.agents import ChatAgent
from camel.toolkits import BaseToolkit, FunctionTool
from camel.utils.mcp import MCPServer

from pipeline import slugify
from pipeline.models.bible import BibleDB, Entity, EntityVersion
from pipeline.services.image import ImageService
from pipeline.services.prompt_builder import PromptBuilder
from pipeline.services.vision import VisionAnalysisService
from pipeline.utils import upload_to_tmpfiles

PROJECTS_DIR = Path(__file__).resolve().parent.parent / "projets"
logger = logging.getLogger(__name__)


@MCPServer()
class CharacterDesignerAgent(BaseToolkit):
    """Agent spécialisé dans la création de personnages pour le cinéma.

    Délègue aux services partagés du pipeline pour la cohérence.
    """

    def __init__(self):
        super().__init__()
        self._agent = None

    @property
    def agent(self) -> ChatAgent:
        if self._agent is None:
            self._agent = ChatAgent(
                system_message="""Tu es le Character Designer d'un studio de cinéma AI.
Tu aides à créer et valider des personnages cohérents pour un film.

Tes outils :
- create_character_fiche : créer la fiche YAML d'un personnage
- generate_character_sheet : générer l'image de référence
- describe_sheet : vérifier la cohérence entre fiche et image

Règles :
- Un personnage = une fiche canonique (pas de v1/v2/v7)
- 10 attributs visuels
- Palette de 5 couleurs
- Prompt_image en anglais, style wilderien sépia
- Attributs concrets, pas de métaphores""",
                tools=self.get_tools(),
            )
        return self._agent

    def get_tools(self) -> List[FunctionTool]:
        return [
            FunctionTool(self.create_character_fiche),
            FunctionTool(self.generate_character_sheet),
            FunctionTool(self.describe_sheet),
        ]

    def _bible(self, project_slug: str) -> BibleDB:
        return BibleDB(PROJECTS_DIR / project_slug / "bible")

    def create_character_fiche(self, project_slug: str, name: str, etype: str = "personnage",
                                attributs: list = None, personnalite: str = "",
                                palette: list = None, accessoires: list = None,
                                prompt_image: str = "") -> str:
        """Créer une fiche personnage/lieu/objet dans la bible.

        Délègue à BibleDB.save_entity() pour la persistance.

        Args:
            project_slug: Slug du projet (ex: 50eme_rose)
            name: Nom de l'entité
            etype: Type (personnage, lieu, objet)
            attributs: Liste de 10 attributs visuels
            personnalite: Description de la personnalité/ambiance
            palette: Liste de 5 couleurs
            accessoires: Liste d'accessoires
            prompt_image: Prompt pour générateur d'image

        Returns:
            Chemin de la fiche créée
        """
        slug = slugify(name)
        bible = self._bible(project_slug)

        entity = Entity(
            name=name,
            slug=slug,
            type=etype,
            active_version="v1",
            versions={
                "v1": EntityVersion(
                    seed=hash(slug) % 10000,
                    mix_alias=etype,
                    attributs=(attributs or [])[:10],
                    personnalite=personnalite or "",
                    palette=(palette or [])[:5],
                    accessoires=accessoires or [],
                    prompt_image=prompt_image or f"Portrait de {name}, style cinématographique wilderien",
                )
            },
        )
        bible.save_entity(entity)
        return f"Fiche créée : {bible.root / etype / slug / 'fiche.yaml'}"

    def generate_character_sheet(self, project_slug: str, slug: str) -> str:
        """Générer l'image de référence (sheet) d'une entité depuis sa fiche.

        Délégue à ImageService + PromptBuilder.

        Args:
            project_slug: Slug du projet
            slug: Slug de l'entité

        Returns:
            Chemin de l'image générée
        """
        bible = self._bible(project_slug)
        entity = bible.get_entity(slug)
        if not entity:
            return f"Fiche introuvable pour '{slug}'"

        version = entity.get_active()
        prompt_builder = PromptBuilder(bible.root)

        template_map = {
            "personnage": "character_sheet",
            "lieu": "location_sheet",
            "objet": "object_sheet",
        }
        template_id = template_map.get(entity.type, "character_sheet")

        prompt = prompt_builder.build(
            template_id,
            name=entity.name,
            type=entity.type,
            attributs=version.attributs,
            palette=version.palette,
            accessoires=version.accessoires,
            personnalite=version.personnalite or "",
            fallback=version.prompt_image,
        )
        sheet_seed = prompt_builder.get_seed(template_id, version.seed)

        out_dir = bible.root / entity.type / slug
        image_svc = ImageService()
        path = image_svc.generate(
            prompt=prompt,
            seed=sheet_seed,
            output_dir=str(out_dir),
            filename=f"sheet_{entity.active_version}.png",
        )
        return f"Sheet générée : {path}"

    def describe_sheet(self, image_path: str) -> str:
        """Analyser une sheet avec vision LLM pour vérifier la cohérence.

        Délègue à VisionAnalysisService.

        Args:
            image_path: Chemin local vers l'image

        Returns:
            Description détaillée de l'image
        """
        url = upload_to_tmpfiles(image_path)
        svc = VisionAnalysisService()
        result = svc.analyze_character_sheet(
            character_name=Path(image_path).parent.name,
            images=[url],
        )
        return result or "Analyse indisponible"


def main():
    parser = argparse.ArgumentParser(description="Character Designer MCP Server")
    parser.add_argument("--mode", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    agent = CharacterDesignerAgent()
    print(f"Character Designer MCP — mode {args.mode}", file=sys.stderr)
    agent.run_mcp_server(args.mode)


if __name__ == "__main__":
    main()
