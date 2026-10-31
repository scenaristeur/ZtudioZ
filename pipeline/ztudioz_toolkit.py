from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, List

import requests

from camel.toolkits import BaseToolkit, FunctionTool
from camel.utils.mcp import MCPServer

from pipeline.models.bible import BibleDB
from pipeline.orchestrator import Orchestrator
from pipeline.reporter import step_end, step_item, step_start
from pipeline.services.audio import AudioService
from pipeline.services.image import ImageService
from pipeline.services.llm import LLMService
from pipeline.services.video import VideoService
from pipeline.services.vision import VisionAnalysisService
from pipeline.utils import upload_to_tmpfiles

logger = logging.getLogger(__name__)

_PROJECTS_DIR = Path.cwd() / "projets"


def _get_orch(slug: str) -> Orchestrator:
    return Orchestrator(slug)


def _get_bible(slug: str) -> BibleDB:
    return BibleDB(_PROJECTS_DIR / slug / "bible")


@MCPServer()
class ZtudioZToolkit(BaseToolkit):
    """Toolkit ZtudioZ : pipeline de production audiovisuelle par IA.

    Décoré avec @MCPServer() pour exposer automatiquement tous les outils
    via le Model Context Protocol.
    """

    def get_tools(self) -> List[FunctionTool]:
        return [
            FunctionTool(self.init_project),
            FunctionTool(self.list_projects),
            FunctionTool(self.get_status),
            FunctionTool(self.parse_script),
            FunctionTool(self.generate_fiches),
            FunctionTool(self.generate_sheets),
            FunctionTool(self.storyboard_text),
            FunctionTool(self.storyboard_visualize),
            FunctionTool(self.storyboard_board),
            FunctionTool(self.produce_video),
            FunctionTool(self.generate_audio),
            FunctionTool(self.run_pipeline),
            FunctionTool(self.switch_version),
            FunctionTool(self.generate_image),
            FunctionTool(self.generate_video),
            FunctionTool(self.say_audio),
            FunctionTool(self.llm_chat),
            FunctionTool(self.upload_media),
            FunctionTool(self.get_project_files),
            FunctionTool(self.analyze_images),
            FunctionTool(self.analyze_character_sheet),
            FunctionTool(self.analyze_animatic),
            FunctionTool(self.upload_sheets),
            FunctionTool(self.generate_clip),
            FunctionTool(self.analyze_clip_audio),
        ]

    # ── Gestion des projets ──────────────────────────────────

    def init_project(self, project_slug: str, script_path: str) -> str:
        """Initialiser un nouveau projet : crée l'arborescence et parse le script.

        Args:
            project_slug (str): Identifiant unique du projet (ex: 50emeRose)
            script_path (str): Chemin vers le fichier script .md

        Returns:
            str: Résumé de l'initialisation (nombre de scènes, entités créées)
        """
        orch = _get_orch(project_slug)
        orch.init_project(script_path)
        scenes = orch.parse_script(script_path)
        orch.init_bible_from_script()
        bible = _get_bible(project_slug)
        entities = bible.list_entities()
        return (
            f"Projet '{project_slug}' initialisé. "
            f"{len(scenes)} scènes parsées, {len(entities)} entités bible."
        )

    def list_projects(self) -> str:
        """Lister tous les projets existants.

        Returns:
            str: Liste des projets avec nombre de scènes et d'entités
        """
        if not _PROJECTS_DIR.exists():
            return "Aucun projet."
        lines = []
        for d in sorted(_PROJECTS_DIR.iterdir()):
            if d.is_dir():
                scenes = len(list((d / "script" / "parsed").glob("scene_*.yaml")))
                bible = BibleDB(d / "bible")
                entities = len(bible.list_entities())
                lines.append(f"{d.name}: {scenes} scènes, {entities} entités")
        return "\n".join(lines) if lines else "Aucun projet."

    def get_status(self, project_slug: str) -> str:
        """Obtenir le statut détaillé d'un projet.

        Args:
            project_slug (str): Identifiant du projet

        Returns:
            str: Statut JSON avec checkpoint, entités bible, étapes complétées
        """
        orch = _get_orch(project_slug)
        cp = orch.load_checkpoint()
        bible = _get_bible(project_slug)
        entities = bible.list_entities()
        return json.dumps({
            "checkpoint": cp or {},
            "total_entities": len(entities),
            "entities": [
                {"name": e.name, "slug": e.slug, "type": e.type,
                 "active_version": e.active_version}
                for e in entities
            ],
            "completed_steps": list(orch._get_completed_steps()),
        }, indent=2, ensure_ascii=False)

    # ── Pipeline steps ───────────────────────────────────────

    def parse_script(self, project_slug: str) -> str:
        """Parser le script d'un projet : extrait les scènes via LLM.

        Args:
            project_slug (str): Identifiant du projet

        Returns:
            str: Résumé du parsing (nombre de scènes)
        """
        orch = _get_orch(project_slug)
        scenes = orch.parse_script()
        return f"{len(scenes)} scènes parsées et sauvegardées."

    def generate_fiches(self, project_slug: str) -> str:
        """Générer les fiches personnages/lieux/objets depuis les scènes parsées.

        Args:
            project_slug (str): Identifiant du projet

        Returns:
            str: Résumé des entités créées
        """
        orch = _get_orch(project_slug)
        scenes = orch._load_parsed_scenes()
        result = orch.generate_fiches(scenes)
        return f"{len(result)} entités créées ou enrichies dans la bible."

    def generate_sheets(self, project_slug: str,
                         entity_slugs: str | None = None) -> str:
        """Générer les images de référence (character sheets) pour chaque entité.

        Args:
            project_slug (str): Identifiant du projet
            entity_slugs (str, optional): Slugs des entités, séparés par des virgules.
                Si None, toutes les entités.

        Returns:
            str: Résultat par entité (chemin ou erreur)
        """
        orch = _get_orch(project_slug)
        entities = orch._load_bible_entities()
        if entity_slugs:
            slugs = [s.strip() for s in entity_slugs.split(",")]
            entities = {k: v for k, v in entities.items() if k in slugs}
        result = orch.generate_sheets(entities)
        ok = sum(1 for v in result.values() if "error" not in v)
        err = sum(1 for v in result.values() if "error" in v)
        parts = [f"{ok} sheets générées"]
        if err:
            parts.append(f"{err} échecs")
        return ", ".join(parts)

    def upload_sheets(self, project_slug: str,
                       entity_slugs: str | None = None,
                       expire: int = 172800) -> str:
        """Uploader les sheets de la bible vers tmpfiles.org pour les utiliser comme refs.

        Args:
            project_slug (str): Identifiant du projet
            entity_slugs (str, optional): Slugs des entités, séparés par des virgules.
                Si None, toutes les entités de la bible.
            expire (int): Durée de vie en secondes (défaut 172800 = 48h)

        Returns:
            str: Résumé des uploads avec les URLs
        """
        from pipeline.utils import upload_to_tmpfiles as _up
        bible_root = _PROJECTS_DIR / project_slug / "bible"
        types = {"personnage": "personnage", "objet": "objet", "lieu": "lieu"}
        results = []
        for etype, eslug in types.items():
            e_dir = bible_root / etype
            if not e_dir.exists():
                continue
            for slug_dir in sorted(e_dir.iterdir()):
                if not slug_dir.is_dir():
                    continue
                slug = slug_dir.name
                if entity_slugs and slug not in entity_slugs.split(","):
                    continue
                sheet = slug_dir / "sheet_v1.png"
                if not sheet.exists():
                    continue
                url = _up(str(sheet), expire=expire)
                results.append(f"{slug}: {url}")
        return "\n".join(results)

    def storyboard_text(self, project_slug: str) -> str:
        """Générer le storyboard textuel (découpage en plans) sans images.

        Args:
            project_slug (str): Identifiant du projet

        Returns:
            str: Résumé du storyboard (scènes, plans)
        """
        orch = _get_orch(project_slug)
        scenes = orch._load_parsed_scenes()
        result = orch.generate_storyboard(scenes)
        total = sum(sb.get("count", len(sb.get("shots", [])))
                    for sb in result.values())
        return f"{len(result)} scènes storyboardées, {total} plans."

    def storyboard_visualize(self, project_slug: str,
                              scene_number: int | None = None) -> str:
        """Générer les dessins storyboard (mode croquis) depuis le découpage textuel.

        À la manière de Billy Wilder : une scène à la fois, chaque plan doit servir
        le climax. On ne passe à la scène suivante que lorsque la précédente est validée.

        Pré-upload automatiquement toutes les sheets de la bible avec 48h d'expiration
        pour servir d'images de référence à la génération.

        Génère d'abord la planche board (storyboard_board.png) guidée par l'intention,
        puis les plans individuels (shot_01.png à shot_N.png).

        Args:
            project_slug (str): Identifiant du projet
            scene_number (int, optional): Numéro de scène (ex: 1). Si None, toutes les scènes.

        Returns:
            str: Résumé des visualisations
        """
        import json as _json
        from pipeline.services.image import ImageService as _ImageService
        from pipeline.services.prompt_builder import PromptBuilder as _PromptBuilder

        # 1. Pré-upload des sheets avec 48h d'expiration
        self.upload_sheets(project_slug)

        # 2. Générer la planche board pour chaque scène
        orch = _get_orch(project_slug)
        scenes = orch._load_parsed_scenes()
        if scene_number is not None:
            scenes = [s for s in scenes if s.get("number") == scene_number]
            if not scenes:
                return f"Scène {scene_number} introuvable."

        results = []
        for scene in scenes:
            snum = scene.get("number", 0)
            skey = f"scene_{snum:02d}"
            sb_path = _PROJECTS_DIR / project_slug / "preprod" / "storyboard" / skey / "storyboard.json"
            if not sb_path.exists():
                results.append(f"Scène {snum}: storyboard.json introuvable")
                continue

            with open(sb_path) as f:
                shots = _json.load(f)

            title = scene.get("title", f"Scène {snum}")
            intention = scene.get("intention", "")

            # Board
            pb = _PromptBuilder(_PROJECTS_DIR / project_slug / "bible")
            prompt, seed, (w, h, _) = pb.build_storyboard_board(
                title, shots, intention, project_slug=project_slug
            )
            svc = _ImageService()
            out_dir = _PROJECTS_DIR / project_slug / "preprod" / "storyboard" / skey
            svc.generate(
                prompt=prompt, seed=seed,
                width=max(768, w), height=max(576, h),
                output_dir=str(out_dir), filename="storyboard_board.png",
            )

            # Shots individuels via VisualizeStep
            orch.generate_visualize([scene])

            total = len(shots)
            results.append(f"Scène {snum}: {total} plans + board")

        return " | ".join(results)

    def storyboard_board(self, project_slug: str,
                          scene_number: int = 1) -> str:
        """Générer une planche storyboard complète (tous les plans en grille).

        Combine les plans du storyboard.json en une seule image avec le template
        storyboard_board, en utilisant l'ambiance et les ref sheets.

        Pré-upload automatiquement toutes les sheets de la bible avec 48h d'expiration
        pour servir d'images de référence à la génération.

        Args:
            project_slug (str): Identifiant du projet
            scene_number (int): Numéro de scène (défaut: 1)

        Returns:
            str: Chemin de l'image générée
        """
        import json as _json
        from pipeline.services.prompt_builder import PromptBuilder as _PromptBuilder
        from pipeline.utils import upload_to_tmpfiles as _upload

        scene_key = f"scene_{scene_number:02d}"
        out_dir = _PROJECTS_DIR / project_slug / "preprod" / "storyboard" / scene_key
        sb_path = out_dir / "storyboard.json"
        if not sb_path.exists():
            return f"Storyboard introuvable : {sb_path}"

        with open(sb_path) as f:
            shots = _json.load(f)

        # Lire l'intention depuis le fichier parsé
        scene_path = _PROJECTS_DIR / project_slug / "script" / "parsed" / f"scene_{scene_number:02d}.yaml"
        scene_title = f"Scène {scene_number}"
        intention = ""
        if scene_path.exists():
            import yaml as _yaml
            data = _yaml.safe_load(scene_path.read_text())
            scene_title = data.get("title", scene_title)
            intention = data.get("intention", "")

        # 1. Upload toutes les sheets avec 48h (en parallèle)
        sheet_paths = []
        for etype in ["personnage", "objet", "lieu"]:
            edir = _PROJECTS_DIR / project_slug / "bible" / etype
            if not edir.exists():
                continue
            for d in sorted(edir.iterdir()):
                sheet = d / "sheet_v1.png"
                if sheet.exists():
                    sheet_paths.append(str(sheet))

        from concurrent.futures import ThreadPoolExecutor as _TPE
        with _TPE(max_workers=8) as pool:
            sheet_urls = list(pool.map(lambda p: _upload(p, expire=172800), sheet_paths))

        # 2. Construire le prompt via le template
        pb = _PromptBuilder(_PROJECTS_DIR / project_slug / "bible")
        prompt, seed, (width, height, max_per_page) = pb.build_storyboard_board(
            scene_title, shots, intention,
            project_slug=project_slug, version="v1",
        )

        # 3. Génération via ImageService (respecte le provider actif config)
        from pipeline.services.image import ImageService as _ImgSvc
        svc = _ImgSvc()
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "storyboard_board.png"

        board_width, board_height = max(768, width), max(576, height)
        if sheet_urls:
            ref_images = "|".join(sheet_urls)
        else:
            ref_images = None
        svc.generate(
            prompt=prompt, seed=seed,
            width=board_width, height=board_height,
            ref_image=ref_images,
            output_dir=str(out_dir), filename="storyboard_board.png",
        )

        results = [f"Planche storyboard : {out_path} ({out_path.stat().st_size} bytes)"]

        # 4. Page 2 si plus de 12 plans
        remaining = len(shots) - max_per_page
        if remaining > 0:
            page2_shots = shots[max_per_page:]
            prompt2, seed2, _ = pb.build_storyboard_board(
                scene_title, page2_shots, intention,
                project_slug=project_slug,
            )
            svc.generate(
                prompt=prompt2, seed=seed2 + 1,
                width=board_width, height=board_height,
                ref_image=ref_images,
                output_dir=str(out_dir), filename="storyboard_board_p2.png",
            )
            out_path2 = out_dir / "storyboard_board_p2.png"
            results.append(f"Page 2 : {out_path2} ({out_path2.stat().st_size} bytes)")

        return "\n".join(results)

    def produce_video(self, project_slug: str) -> str:
        """Générer les vidéos des plans à partir du storyboard.

        Args:
            project_slug (str): Identifiant du projet

        Returns:
            str: Résumé des vidéos générées
        """
        orch = _get_orch(project_slug)
        scenes = orch._load_parsed_scenes()
        storyboards = orch._load_storyboards()
        result = orch.produce_shots(scenes, storyboards)
        total = sum(v.get("count", 0) for v in result.values())
        return f"{total} vidéos générées."

    def generate_audio(self, project_slug: str) -> str:
        """Générer l'audio (voix off + ambiances) pour chaque scène.

        Args:
            project_slug (str): Identifiant du projet

        Returns:
            str: Résumé des fichiers audio générés
        """
        orch = _get_orch(project_slug)
        scenes = orch._load_parsed_scenes()
        result = orch.generate_audio(scenes)
        total = sum(len(v) for v in result.values())
        return f"{total} fichiers audio générés."

    def run_pipeline(self, project_slug: str, steps: str = "",
                     force: bool = False) -> str:
        """Exécuter une ou plusieurs étapes du pipeline.

        Args:
            project_slug (str): Identifiant du projet
            steps (str): Étapes séparées par des virgules
                         (ex: fiches,sheets,storyboard)
                         Vide = toutes les étapes
            force (bool): Régénérer même si l'étape est déjà complétée
                          (défaut: False)

        Returns:
            str: Résumé des étapes exécutées
        """
        orch = _get_orch(project_slug)
        steps_list = steps.split(",") if steps else None
        context = orch.run_pipeline(steps=steps_list, force=force)
        return f"Étapes exécutées : {list(context.keys())}"

    # ── A/B testing ──────────────────────────────────────────

    def switch_version(self, project_slug: str, entity_slug: str,
                       version: str) -> str:
        """Basculer la version active d'une entité (A/B testing).

        Args:
            project_slug (str): Identifiant du projet
            entity_slug (str): Slug de l'entité (ex: paul-henri)
            version (str): Version cible (ex: v2)

        Returns:
            str: Confirmation du changement
        """
        bible = _get_bible(project_slug)
        entity = bible.get_entity(entity_slug)
        if not entity:
            return f"Entité '{entity_slug}' introuvable."
        if version not in entity.versions:
            return f"Version '{version}' inexistante. Disponibles: {list(entity.versions.keys())}"
        bible.set_active_version(entity_slug, version)
        return f"{entity.name} → version {version} activée."

    # ── Services individuels ─────────────────────────────────

    def generate_image(self, prompt: str, seed: int = 0,
                       width: int = 256, height: int = 256,
                       model: str = "gptimage",
                       dev: bool = False) -> str:
        """Générer une image via le provider actif (Agnes par défaut).

        Args:
            prompt (str): Description de l'image
            seed (int): Seed pour la reproductibilité (défaut: 0)
            width (int): Largeur (défaut: 256)
            height (int): Hauteur (défaut: 256)
            model (str): Modèle (défaut: gptimage)
            dev (bool): Résolution réduite (défaut: False)

        Returns:
            str: Chemin du fichier image généré
        """
        svc = ImageService()
        cfg = dict(svc.config)
        cfg["dev"] = dev
        svc = ImageService(config=cfg)
        path = svc.generate(
            prompt=prompt,
            seed=seed if seed else None,
            width=width, height=height, model=model,
            output_dir="/tmp/ztudioz_images",
        )
        return f"Image générée : {path}"

    def generate_video(self, prompt: str, seed: int = 0,
                       width: int = 512, height: int = 512,
                       duration: int = 5, model: str = "ltx-2",
                       ref_images: str = "",
                       dev: bool = False) -> str:
        """Générer une vidéo via le provider actif (Agnes par défaut).

        Avec 1 image de référence → mode i2v ; avec 2 images → mode keyframes
        (interpolation start/end, équivalent à `agnes video keyframes`).

        Args:
            prompt (str): Description de la vidéo
            seed (int): Seed pour la reproductibilité
            width (int): Largeur (défaut: 512)
            height (int): Hauteur (défaut: 512)
            duration (int): Durée en secondes (défaut: 5)
            model (str): Modèle (défaut: ltx-2)
            ref_images (str): Images de référence, chemins ou URLs,
                séparées par des virgules ou "|" (ex: a.png|b.png)
            dev (bool): Résolution réduite (défaut: False)

        Returns:
            str: Chemin du fichier vidéo généré
        """
        refs = None
        if ref_images:
            refs = [r.strip() for r in ref_images.replace(",", "|").split("|") if r.strip()]
        svc = VideoService()
        cfg = dict(svc.config)
        cfg["dev"] = dev
        svc = VideoService(config=cfg)
        path = svc.generate(
            prompt=prompt,
            seed=seed if seed else None,
            width=width, height=height,
            duration=duration, model=model,
            ref_images=refs,
            output_dir="/tmp/ztudioz_videos",
        )
        return f"Vidéo générée : {path}"

    def say_audio(self, text: str, voice: str = "onyx") -> str:
        """Générer un audio TTS (text-to-speech).

        Args:
            text (str): Texte à prononcer
            voice (str): Voix (onyx, alloy, echo, fable, nova, shimmer)

        Returns:
            str: Chemin du fichier audio généré
        """
        svc = AudioService()
        path = svc.say(text=text, voice=voice, output_dir="/tmp/ztudioz_audio")
        return f"Audio généré : {path}"

    def llm_chat(self, prompt: str, system: str = "") -> str:
        """Interroger le LLM directement.

        Args:
            prompt (str): Prompt utilisateur
            system (str): Instruction système optionnelle

        Returns:
            str: Réponse du LLM
        """
        svc = LLMService()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return svc.chat(messages)

    def upload_media(self, file_path: str) -> str:
        """Uploader un fichier vers tmpfiles.org pour le partager.

        Retourne l'URL de téléchargement direct (via /api/v1/download/)
        exploitable par les APIs Pollinations, vision, etc.

        Args:
            file_path (str): Chemin local du fichier

        Returns:
            str: URL publique directe du fichier
        """
        try:
            url = upload_to_tmpfiles(file_path)
            return f"Fichier partagé : {url}"
        except Exception as e:
            return f"Erreur upload : {e}"

    # ── Vision analysis ──────────────────────────────────────

    def analyze_images(self, prompt: str, image_paths: str) -> str:
        """Analyser des images via vision LLM (Albert/Mistral).

        Les images sont encodées en base64 et envoyées directement
        à l'API Albert. Pas besoin d'upload.

        Args:
            prompt (str): Question ou description demandée
            image_paths (str): Chemins des images, séparés par des virgules

        Returns:
            str: Analyse textuelle détaillée
        """
        paths = [p.strip() for p in image_paths.split(",") if p.strip()]
        if not paths:
            return "Aucune image spécifiée."
        svc = VisionAnalysisService()
        return svc.analyze(prompt, paths)

    def analyze_character_sheet(self, character_name: str,
                                 image_path: str) -> str:
        """Analyser une fiche personnage : apparence, style, attitude.

        Args:
            character_name (str): Nom du personnage (ex: Adèle)
            image_path (str): Chemin vers l'image de la fiche

        Returns:
            str: Description détaillée du character design
        """
        svc = VisionAnalysisService()
        return svc.analyze_character_sheet(character_name, [image_path])

    def analyze_animatic(self, scene_label: str,
                          image_path: str) -> str:
        """Analyser un storyboard/animatique : composition, cases, style.

        Args:
            scene_label (str): Nom de la scène (ex: Scène 1)
            image_path (str): Chemin vers l'image de l'animatique

        Returns:
            str: Analyse détaillée du storyboard
        """
        svc = VisionAnalysisService()
        return svc.analyze_animatic(scene_label, [image_path])

    def get_project_files(self, project_slug: str,
                          pattern: str = "**/*") -> str:
        """Lister les fichiers d'un projet avec un pattern glob.

        Args:
            project_slug (str): Identifiant du projet
            pattern (str): Pattern glob (défaut: **/*)

        Returns:
            str: Liste des fichiers
        """
        root = _PROJECTS_DIR / project_slug
        if not root.exists():
            return f"Projet '{project_slug}' introuvable."
        files = []
        for f in sorted(root.glob(pattern)):
            if f.is_file():
                rel = f.relative_to(root)
                size = f.stat().st_size
                files.append(f"{rel} ({size} o)")
        return "\n".join(files) if files else "Aucun fichier trouvé."

    # ── Clip musical ──────────────────────────────────────────

    def generate_clip(self, project_slug: str, variant: str = "v2",
                      mode: str = "images",
                      sections: str = "",
                      audio_path: str = "",
                      force: bool = False,
                      max_plans: int | None = None) -> str:
        """Produire un clip musical (une variante) depuis l'audio + le brief visuel.

        Args:
            project_slug (str): Slug du projet (ex: KeepTalkingAndListening)
            variant (str): Variante (v1 bulles | v3 Le Voyageur) — défaut v2
            mode (str): images (héros par section, validation) | video | full
            sections (str): Sections à traiter, séparées par des virgules
            audio_path (str): Chemin du fichier audio (défaut: sources/*.mp3)
            force (bool): Régénérer même si les fichiers existent
            max_plans (int): Limiter au N premiers plans (test d'enchaînement)

        Returns:
            str: Résumé de la production (images/vidéos/chemin final)
        """
        from pipeline.clip.steps import ClipStep
        step = ClipStep(verbose=True)
        secs = [s.strip() for s in sections.split(",") if s.strip()] if sections else None
        result = step.run(
            project=project_slug,
            variant_id=variant,
            audio_path=audio_path or None,
            mode=mode,
            sections=secs,
            force=force,
            max_plans=max_plans,
        )
        lines = [f"Variante: {variant} | mode: {mode}"]
        if "images" in result:
            lines.append(f"Images héro: {len(result['images'])} → {result.get('dir')}")
        if "videos" in result:
            lines.append(f"Vidéos: {len(result['videos'])}")
        if result.get("final"):
            lines.append(f"Clip final: {result['final']}")
        return "\n".join(lines)

    def analyze_clip_audio(self, project_slug: str, audio_path: str = "",
                           mode: str = "transcription") -> str:
        """Transcrire/analyser l'audio d'un clip via le modèle audio d'Albert (whisper).

        Args:
            project_slug (str): Slug du projet
            audio_path (str): Chemin du fichier (mix mp3 OU piste voix seule wav)
            mode (str): transcription (fenêtres) | structure (durée + énergie RMS)

        Returns:
            str: Transcription par segments ou structure énergétique
        """
        from pipeline.clip.audio import ClipAudioService
        root = _PROJECTS_DIR / project_slug
        ap = Path(audio_path) if audio_path else None
        if ap is None or not ap.exists():
            sources = root / "sources"
            ap = next((f for f in sorted(sources.glob("*.wav")) if "8-BT" in f.name), None) \
                 or next(iter(sorted(sources.glob("*.mp3"))), None)
            if ap is None:
                return "Aucun audio trouvé."
        svc = ClipAudioService()
        if mode == "structure":
            st = svc.analyze_structure(ap)
            lines = [f"Durée: {st['duration']}s"]
            for w in st["windows"]:
                lines.append(f"{w['time']}s RMS={w['rms']}")
            return "\n".join(lines)
        segs = svc.transcribe_full(ap)
        lines = [f"Transcription ({ap.name}):"]
        for s in segs:
            mm, ss = divmod(int(s["start"]), 60)
            lines.append(f"{mm:02d}:{ss:02d} {s['text']}")
        return "\n".join(lines)
