from __future__ import annotations

from pathlib import Path

import yaml


class PromptBuilder:
    """Construit des prompts paramétrisables à partir de templates YAML.

    Les templates combinent ambiance générale + fiche entité + paramètres spécifiques.
    """

    def __init__(self, bible_root: str | Path):
        self.bible_root = Path(bible_root)
        self._templates: dict | None = None
        self._ambiance: dict | None = None

    @property
    def templates(self) -> dict:
        if self._templates is None:
            tpl_path = self.bible_root / "ambiance" / "prompt_templates.yaml"
            if tpl_path.exists():
                with open(tpl_path) as f:
                    self._templates = yaml.safe_load(f).get("templates", {})
            else:
                self._templates = {}
        return self._templates

    @property
    def ambiance(self) -> dict:
        if self._ambiance is None:
            style_path = self.bible_root / "ambiance" / "style-guide.yaml"
            if style_path.exists():
                with open(style_path) as f:
                    data = yaml.safe_load(f)
                    ver = data.get("versions", {}).get(data.get("active_version", "v1"), {})
                    self._ambiance = {
                        "ambiance": ver.get("prompt_image", ""),
                        "format": data.get("format", "Cinemascope 2.35:1"),
                        "palette": ", ".join(ver.get("palette", [])),
                        "grain": "35mm film grain",
                    }
            else:
                self._ambiance = {"ambiance": "", "format": "", "palette": ""}
        return self._ambiance

    def build(self, template_id: str, **kwargs) -> str:
        """Construit un prompt à partir d'un template et de variables.

        Args:
            template_id: identifiant du template (character_sheet, location_sheet, etc.)
            **kwargs: variables à injecter dans le template

        Returns:
            Prompt généré
        """
        tpl_data = self.templates.get(template_id)
        if not tpl_data:
            return kwargs.get("fallback", "")

        prompt_template = tpl_data["prompt"]
        seed_offset = tpl_data.get("seed_offset", 0)

        # Fusionner ambiance
        variables = {**self.ambiance, **kwargs}

        # Formater les listes en texte
        for key in ("attributs", "accessoires", "palette"):
            val = variables.get(key)
            if isinstance(val, list):
                variables[key] = ", ".join(val) if val else ""
        # Formater les chaînes multilignes
        for key in ("personnalite",):
            val = variables.get(key)
            if isinstance(val, str) and "\n" in val:
                variables[key] = val.replace("\n", ", ")

        # Remplacer les variables en une seule passe (évite le double-remplacement)
        import re
        def _replace(match):
            key = match.group(1)
            val = variables.get(key, "")
            return str(val) if val else ""
        result = re.sub(r"\{(\w+)\}", _replace, prompt_template)

        # Nettoyer les espaces multiples
        result = " ".join(result.split())

        return result

    def get_seed(self, template_id: str, base_seed: int = 42) -> int:
        tpl_data = self.templates.get(template_id, {})
        return base_seed + tpl_data.get("seed_offset", 0)

    def build_storyboard_board(self, scene_title: str, shots: list[dict],
                                 intention: str = "",
                                 project_slug: str = "",
                                 version: str = "v1",
                                 base_seed: int = 42) -> tuple[str, int, tuple[int, int, int]]:
        """Construit un prompt de planche storyboard depuis une liste de plans.

        Calcule automatiquement la grille optimale et le nombre de pages.

        Args:
            scene_title: Titre de la scène
            shots: Liste des plans (dict avec number, type, description, camera)
            intention: Intention dramatique de la scène
            project_slug: Slug du projet (pour l'en-tête)
            version: Version du storyboard
            base_seed: Seed de base

        Returns:
            (prompt, seed, (width, height, max_panels))
        """
        # Déterminer le nombre de plans par page et la grille
        n_shots = len(shots)
        if n_shots <= 4:
            cols, rows, max_per_page = 2, 2, 4
        elif n_shots <= 6:
            cols, rows, max_per_page = 3, 2, 6
        elif n_shots <= 8:
            cols, rows, max_per_page = 4, 2, 8
        elif n_shots <= 12:
            cols, rows, max_per_page = 4, 3, 12
        else:
            cols, rows, max_per_page = 4, 3, 12  # Page 1, page 2 pour la suite

        # Calculer les dimensions
        panel_w, panel_h = 256, 192
        header_h = 64
        width = max(768, cols * panel_w)
        height = header_h + rows * panel_h

        # Page number
        page = 1

        # Construire la description des plans (pour la première page)
        page_shots = shots[:max_per_page]
        parts = []
        for s in page_shots:
            n = s.get("number", 0)
            d = s.get("description", "").rstrip(". ")
            parts.append(f"panel {n}: {d[:120]}")

        shots_desc = ". ".join(parts)
        remaining = n_shots - max_per_page

        # En-tête avec infos projet
        header = f"PROJECT: {project_slug}" if project_slug else ""
        header += f" | SCENE: {scene_title} | VERSION: {version}"
        if page > 1 or remaining > 0:
            header += f" | PAGE {page}"
        if remaining > 0:
            header += f" ({remaining} more panels on next page)"

        return self.build(
            "storyboard_board",
            scene_title=header,
            shots_description=shots_desc,
            entier_page=1 if remaining <= 0 else 0,
            has_next_page=1 if remaining > 0 else 0,
            intention=intention or "",
            fallback="",
        ), self.get_seed("storyboard_board", base_seed), (width, height, max_per_page)
