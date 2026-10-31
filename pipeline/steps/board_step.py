from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml

from pipeline import slugify
from pipeline.services.image import ImageService
from pipeline.steps.visualize import _active_prompt, _location_aliases, _upload_refs

logger = logging.getLogger(__name__)

PROJECTS_DIR = Path(__file__).resolve().parent.parent.parent / "projets"


class BoardStep:
    """Génère la planche croquis wilderien de validation après le storyboard.json.

    La planche (`storyboard_board.png`) montre la salle pleine de public, l'écran avec
    son contenu et les personnages de chaque plan. C'est une porte de validation à
    moindre coût (1 image) entre `storyboard` et `visualize`.

    Le prompt est construit à la main, contraintes critiques d'abord (lieu, salle pleine,
    écran), puis les plans. Le template `build_storyboard_board` ne suffit pas : sans
    description forte du lieu en tête, le modèle invente un décor (constaté : salon
    domestique au lieu de la salle de conférence).
    """

    def __init__(self, verbose: bool = False):
        self.image = ImageService()
        self.verbose = verbose

    def _location_prompt(self, project: str, location: str) -> str:
        loc_slug = _location_aliases(project, location)
        if not loc_slug:
            return ""
        fiche = PROJECTS_DIR / project / "bible" / "lieu" / loc_slug / "fiche.yaml"
        if not fiche.exists():
            return ""
        return _active_prompt(fiche)

    @staticmethod
    def _clean_plan_text(text: str) -> str:
        """Aligne le vocabulaire du plan sur le lieu de la scène.

        Le scénario nomme le lieu « GRAND SALON HIGH-TECH » : le LLM répète
        « le grand salon » dans chaque plan, et le modèle d'image croit voir un
        salon domestique (canapés). On neutralise ce terme ambigu avant l'envoi.
        """
        return (
            text.replace("grand salon", "grande salle de conférence")
            .replace("le salon", "la salle de conférence")
            .replace("du salon", "de la salle de conférence")
        )

    def _board_prompt(
        self,
        location_prompt: str,
        character_names: list[str],
        shots: list[dict],
        scene_title: str,
    ) -> str:
        loc_desc = location_prompt or (
            "a modern French conference hall with rows of seats, a big screen "
            "and a low stage"
        )
        parts = [
            "Professional film storyboard board, black ink line drawing, gray shading, "
            "no colors, Billy Wilder style cinematic composition, wide frames, deep focus, "
            "frontal symmetric staging.",
            "SETTING (MUST appear in every panel): " + loc_desc + ".",
            "The hall is FULL of a varied audience seated in the rows — many small figures "
            "with different postures, some turned to chat, one raising a hand. Never an "
            "empty hall. The big screen on stage shows a glowing sphere of connected points.",
        ]
        if character_names:
            parts.append("Characters: " + ", ".join(character_names) + ".")
        for s in shots:
            n = s.get("number", 0)
            d = s.get("description", "").rstrip(". ")
            d = self._clean_plan_text(d)
            parts.append(f"Panel {n}: {d}")
        parts.append(
            "Sober realistic French public architecture, warm bright atmosphere, "
            "no holograms, no neon, no sci-fi, French captions under each panel."
        )
        return " ".join(p for p in parts if p)

    def run(self, project: str, scenes: list[dict], storyboard_dir: Path) -> dict:
        results = {}
        for scene in scenes:
            snum = scene.get("number", 0)
            skey = f"scene_{snum:02d}"
            sb_path = storyboard_dir / skey / "storyboard.json"
            if not sb_path.exists():
                logger.warning("No storyboard for scene %d", snum)
                continue
            shots = json.loads(sb_path.read_text())
            title = scene.get("title", f"Scène {snum}")

            location = scene.get("location", "")
            location_prompt = self._location_prompt(project, location)
            character_names = list(scene.get("characters", []) or [])

            # Réfs : lieu + personnages présents (sheets existantes).
            slugs = [_location_aliases(project, location)]
            slugs = [s for s in slugs if s]
            slugs += [slugify(c) for c in character_names]
            refs = _upload_refs(project, slugs)

            out_dir = storyboard_dir / skey
            out_dir.mkdir(parents=True, exist_ok=True)

            # Planches par groupes de 5 plans : à cette échelle chaque case garde
            # salle + public + écran + personnage (une planche de 27 tassait tout).
            boards = []
            group_size = 5
            for i in range(0, len(shots), group_size):
                group = shots[i : i + group_size]
                first, last = group[0]["number"], group[-1]["number"]
                prompt = self._board_prompt(
                    location_prompt, character_names, group, title
                )
                filename = f"storyboard_board_{first}-{last}.png"
                out_path = out_dir / filename
                if out_path.exists():
                    v = 1
                    while (out_dir / f"storyboard_board_{first}-{last}_v{v}.png").exists():
                        v += 1
                    out_path.rename(out_dir / f"storyboard_board_{first}-{last}_v{v}.png")
                path = self.image.generate(
                    prompt=prompt, seed=42,
                    width=1024, height=1024,
                    ref_image=refs or None,
                    output_dir=str(out_dir), filename=filename,
                )
                boards.append(path)
                logger.info("Board scène %d (%s) → %s", snum, f"{first}-{last}", Path(path).name)

            results[skey] = {"boards": boards, "count": len(shots), "groups": len(boards)}
        return results