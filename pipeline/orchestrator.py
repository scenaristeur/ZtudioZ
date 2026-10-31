from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

import yaml

from pipeline import slugify
from pipeline.config import get_config, get_production_config
from pipeline.consumption import get_tracker, reset_tracker
from pipeline.models.bible import BibleDB, Entity, EntityVersion
from pipeline.reporter import step_end, step_item, step_start, step_warn
from pipeline.state import SceneState
from pipeline.steps.audio_step import AudioStep
from pipeline.steps.board_step import BoardStep
from pipeline.steps.fiche_step import FicheStep
from pipeline.steps.keyframes_step import KeyframesStep
from pipeline.steps.parse import ParseStep
from pipeline.steps.produce import ProduceStep
from pipeline.steps.sheets import SheetsStep
from pipeline.steps.storyboard import StoryboardStep
from pipeline.steps.visualize import VisualizeStep

logger = logging.getLogger(__name__)

_PAUSE_REQUESTED = False

VALIDATION_MODES = ("interactive", "manual", "off")


def _signal_handler(signum, frame):
    global _PAUSE_REQUESTED
    print("\n\n  ⏸️  PAUSE demandée (Ctrl+C). Fin de l'étape en cours...", file=sys.stderr)
    _PAUSE_REQUESTED = True


signal.signal(signal.SIGINT, _signal_handler)


class ProjectInit:
    """Crée la structure de projet automatiquement."""

    STRUCTURE = {
        "script": {"parsed": {}},
        # `ambiance` porte style-guide.yaml et prompt_templates.yaml, dont dépend toute
        # génération d'image ; `scene` accueille les contraintes déclaratives de scène.
        # Les deux sont créés d'emblée : une structure incomplète se découvre au milieu
        # d'une production, quand elle coûte déjà cher.
        "bible": {"personnage": {}, "lieu": {}, "objet": {}, "ambiance": {}, "scene": {}},
        "preprod": {"storyboard": {}, "moodboards": {}},
        "production": {"video": {}, "audio": {}},
        "postprod": {"montage": {}, "output": {}},
    }

    @staticmethod
    def verify_structure(slug: str) -> list[str]:
        """Répertoire les répertoires attendus mais absents. Lecture seule.

        Ne crée rien : un projet existant peut être un projet clip, qui n'a pas de bible
        de personnages, ou un projet antérieur à une étape. Le diagnostic informe, il
        ne répare pas — réparer un projet en production n'est jamais automatique.
        """
        root = Path.cwd() / "projets" / slug
        if not root.is_dir():
            return [f"projet inexistant : {root}"]
        missing: list[str] = []
        for name, sub in ProjectInit.STRUCTURE.items():
            if not (root / name).is_dir():
                missing.append(name)
            for child in sub:
                if not (root / name / child).is_dir():
                    missing.append(f"{name}/{child}")
        return missing

    @staticmethod
    def create_project(slug: str, script_path: str) -> Path:
        root = Path.cwd() / "projets" / slug
        if root.exists():
            logger.info(f"Project {slug} already exists at {root}")
            return root

        root.mkdir(parents=True, exist_ok=True)

        def create_structure(base: Path, struct: dict):
            for name, sub in struct.items():
                d = base / name
                d.mkdir(exist_ok=True)
                if sub:
                    create_structure(d, sub)

        create_structure(root, ProjectInit.STRUCTURE)

        if script_path and Path(script_path).exists():
            dest = root / "script" / Path(script_path).name
            shutil.copy2(script_path, dest)

        logger.info(f"Created project {slug} at {root}")
        return root


class Orchestrator:
    """Orchestrateur principal du pipeline de production."""

    AVAILABLE_STEPS = ["init", "parse", "fiches", "sheets", "storyboard", "board", "visualize", "produce", "keyframes", "audio"]

    # Recut narratif (P2) : les étapes réelles regroupées en 7 stages de haut niveau.
    # Un stage peut être passé à `run_pipeline(steps=[...])` et est expandé en étapes
    # existantes. Les identifiants d'étapes et les checkpoints ne changent pas.
    STAGES = {
        "prepare": ["init", "parse"],      # écrire le scénario
        "identify": ["fiches", "sheets"],  # bible, références visuelles
        "cut": ["storyboard", "board"],  # découpage en plans + planche de validation
        "frame": ["visualize"],            # plans en images
        "move": ["produce", "keyframes"],  # vidéo : deux branches concurrentes
        "sound": ["audio"],                # voix off + ambiances
        "assemble": [],                    # montage = phase postprod (service, pas étape)
    }

    @staticmethod
    def expand_stages(steps: list[str]) -> list[str]:
        """Expande les noms de stages en étapes réelles, sans toucher aux étapes.

        `["frame", "produce"]` → `["visualize", "produce"]`. Un nom inconnu est
        laissé tel quel : `run_pipeline` lève une erreur claire plus bas, plutôt que
        de casser un checkpoint existant.
        """
        out: list[str] = []
        for s in steps:
            if s in Orchestrator.STAGES:
                out.extend(Orchestrator.STAGES[s])
            else:
                out.append(s)
        # Préserve l'ordre et évite les doublons (ex: un stage qui contient déjà une étape).
        seen: set[str] = set()
        ordered = []
        for s in out:
            if s not in seen:
                seen.add(s)
                ordered.append(s)
        return ordered

    def __init__(self, project_slug: str, verbose: bool = False):
        self.slug = project_slug
        self.verbose = verbose
        self.config = get_config()
        self.prod_cfg = get_production_config()
        self.root = Path.cwd() / "projets" / project_slug
        self.project_data: dict = {}
        self.checkpoint_path = self.root / ".checkpoint.json"

    # ── Project Lifecycle ──────────────────────────────────────

    def init_project(self, script_path: str) -> Path:
        root = ProjectInit.create_project(self.slug, script_path)
        self.root = root
        return root

    def init_bible_from_script(self) -> None:
        """Crée les entrées de bible à partir du parsing du script."""
        logger.info("Initializing bible from script...")
        parsed = self._load_parsed_scenes()
        all_characters = set()
        all_locations = set()
        for scene in parsed:
            for c in scene.get("characters", []):
                all_characters.add(c)
            loc = scene.get("location", "")
            if loc:
                all_locations.add(loc)

        bible = BibleDB(self.root / "bible")
        existing = {e.slug for e in bible.list_entities()}

        for char in all_characters:
            slug = slugify(char)
            if slug not in existing:
                entity = Entity(
                    name=char,
                    slug=slug,
                    type="personnage",
                    active_version="v1",
                    versions={
                        "v1": EntityVersion(
                            seed=hash(slug) % 10000,
                            mix_alias="personnage principal",
                            attributs=[f"Personnage: {char}"],
                            prompt_image=f"Portrait de {char}, style cinématographique",
                        )
                    },
                    created=datetime.now().isoformat(),
                )
                bible.save_entity(entity)
                logger.info(f"Created character entry: {char}")

        for loc in all_locations:
            slug = slugify(loc)
            if slug not in existing:
                entity = Entity(
                    name=loc,
                    slug=slug,
                    type="lieu",
                    active_version="v1",
                    versions={
                        "v1": EntityVersion(
                            seed=hash(slug) % 10000,
                            mix_alias="lieu",
                            attributs=[f"Lieu: {loc}"],
                            prompt_image=f"Photographie de {loc}, style cinématographique",
                        )
                    },
                    created=datetime.now().isoformat(),
                )
                bible.save_entity(entity)
                logger.info(f"Created location entry: {loc}")

    # ── Parsing ────────────────────────────────────────────────

    def parse_script(self, script_path: str | None = None) -> list[dict]:
        if script_path is None:
            script_files = list((self.root / "script").glob("*.md"))
            if not script_files:
                raise FileNotFoundError(f"No script found in {self.root / 'script'}")
            script_path = str(script_files[0])

        step = ParseStep(verbose=self.verbose)
        scenes = step.run(script_path, self.root / "script" / "parsed")
        self._save_checkpoint({"parse": {"status": "done", "scenes": len(scenes)}})
        return scenes

    # ── Fiches ─────────────────────────────────────────────────

    def generate_fiches(self, scenes: list[dict]) -> dict:
        step = FicheStep(verbose=self.verbose)
        result = step.run(scenes, self.root / "bible")
        self._save_checkpoint({"fiches": {"status": "done", "entities": len(result)}})
        return result

    # ── Sheets ─────────────────────────────────────────────────

    def generate_sheets(self, entities: dict) -> dict:
        step = SheetsStep(verbose=self.verbose)
        result = step.run(entities, self.root / "bible")
        self._save_checkpoint({"sheets": {"status": "done"}})
        return result

    # ── Storyboard ─────────────────────────────────────────────

    def generate_storyboard(self, scenes: list[dict]) -> dict:
        step = StoryboardStep(verbose=self.verbose)
        # `project` est indispensable : sans lui le storyboard ignore la bible
        # (style-guide, fiche de lieu, tenues, refs) et n'applique pas le garde-fou
        # visuel, qui a précisément pour rôle de contrer la dérive de registre.
        result = step.run(scenes, self.root / "preprod" / "storyboard", project=self.slug)
        self._save_checkpoint({"storyboard": {"status": "done"}})
        return result

    # ── Planche storyboard (board) ────────────────────────────

    def generate_board(self, scenes: list[dict]) -> dict:
        step = BoardStep(verbose=self.verbose)
        result = step.run(self.slug, scenes, self.root / "preprod" / "storyboard")
        self._save_checkpoint({"board": {"status": "done"}})
        return result

    # ── Visualisation storyboard ──────────────────────────────

    def generate_visualize(self, scenes: list[dict]) -> dict:
        step = VisualizeStep(verbose=self.verbose)
        result = step.run(self.slug, scenes, self.root / "preprod" / "storyboard")
        self._save_checkpoint({"visualize": {"status": "done"}})
        return result

    # ── Production ─────────────────────────────────────────────

    def produce_shots(self, scenes: list[dict], storyboards: dict | None = None) -> dict:
        step = ProduceStep(verbose=self.verbose)
        result = step.run(self.slug, scenes, self.root / "preprod" / "storyboard", self.root / "production" / "video")
        self._save_checkpoint({"produce": {"status": "done"}})
        return result

    # ── Production keyframes ────────────────────────────────────

    def produce_keyframes(self, scenes: list[dict]) -> dict:
        """Générer les vidéos par keyframes (beats LLM + chaînage agnes) au lieu de l'i2v."""
        step = KeyframesStep(verbose=self.verbose)
        result = step.run(self.slug, scenes, self.root / "preprod" / "storyboard", self.root / "production" / "video")
        self._save_checkpoint({"keyframes": {"status": "done"}})
        return result

    # ── Audio ──────────────────────────────────────────────────

    def generate_audio(self, scenes: list[dict]) -> dict:
        step = AudioStep(verbose=self.verbose)
        result = step.run(self.slug, scenes, self.root / "preprod" / "storyboard", self.root / "production" / "audio")
        self._save_checkpoint({"audio": {"status": "done"}})
        return result

    # ── Pipeline complet ──────────────────────────────────────

    def _get_completed_steps(self) -> set[str]:
        cp = self.load_checkpoint()
        done = set()
        if cp:
            done.update(
                step for step in self.AVAILABLE_STEPS
                if cp.get(step, {}).get("status") == "done"
            )

        # Détection implicite : si les artefacts existent, l'étape est considérée faite
        if self.root.exists():
            done.add("init")
        parsed_scenes = list((self.root / "script" / "parsed").glob("scene_*.yaml"))
        if parsed_scenes:
            done.add("parse")

        # Les étapes coûteuses sont confirmées par la présence de leurs artefacts. Sans
        # ça, un checkpoint marqué « done » alors que les fichiers manquent fait sauter
        # la génération, et un checkpoint valide mais à moitié materialized la refait
        # en entier. Un seuil bas : au moins un fichier non vide.
        for step, folder, pattern in (
            ("board", self.root / "preprod" / "storyboard", "storyboard_board*.png"),
            ("visualize", self.root / "preprod" / "storyboard", "shot_*.png"),
            ("produce", self.root / "production" / "video", "*.mp4"),
            ("keyframes", self.root / "production" / "video", "*.mp4"),
            ("audio", self.root / "production" / "audio", "*.mp3"),
        ):
            if step in done and not self._has_artifact(folder, pattern):
                step_warn(
                    f"Étape '{step}' cochée mais sans artefact",
                    f"aucun {pattern} exploitable dans {folder} — la reprise la relancera.",
                )
                done.discard(step)

        return done

    def _has_artifact(self, folder: Path, pattern: str) -> bool:
        """Au moins un fichier non vide, pour confirmer l'étape par ses artefacts."""
        if not folder.exists():
            return False
        for f in folder.rglob(pattern):
            if f.is_file() and f.suffix != ".json" and f.stat().st_size > 1024:
                return True
        return False

    def _setup_file_logging(self) -> Path:
        """Configure un fichier de log dans le dossier du projet."""
        log_dir = self.root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        sid = get_tracker().get_session_id() or datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = log_dir / f"ztudioz_{sid}.log"

        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler = RotatingFileHandler(log_path, maxBytes=5_242_880, backupCount=3, encoding="utf-8")
        handler.setFormatter(fmt)
        handler.setLevel(logging.DEBUG)
        logging.getLogger().addHandler(handler)

        # Log un marker de début
        logging.getLogger().info(f"═══ SESSION {sid} — {self.slug} ═══")
        return log_path

    def run_pipeline(
        self,
        script_path: str | None = None,
        steps: list[str] | None = None,
        force: bool = False,
    ) -> dict:
        """Run the full pipeline or specific steps, with checkpoint-aware resume."""
        global _PAUSE_REQUESTED

        # Session de consommation
        get_tracker().start_session(label=f"{self.slug} pipeline")
        get_tracker().set_project_dir(self.root)

        # Fichier de log projet
        log_path = self._setup_file_logging()
        print(f"  📝 Log : {log_path}\n", file=sys.stderr)

        if steps is None:
            steps = self.AVAILABLE_STEPS

        # Recut narratif : un nom de stage (`move`, `frame`, ...) s'expand en étapes.
        steps = self.expand_stages(steps)

        # `assemble` seul n'a pas d'étape : monter passe par le service montage.
        if steps == ["assemble"]:
            print("  ℹ️  `assemble` (montage) n'est pas une étape du pipeline :", file=sys.stderr)
            print("  ℹ️  utilisez pipeline.services.montage (assemble_scene / assemble_film).", file=sys.stderr)
            return {}

        completed = set() if force else self._get_completed_steps()
        if completed and not force:
            print(f"\n  ℹ️  Étapes déjà complétées : {', '.join(sorted(completed))}", file=sys.stderr)
            print(f"  ℹ️  Pour tout relancer, ajoutez --force", file=sys.stderr)
            remaining = [s for s in steps if s not in completed]
            if not remaining:
                print(f"  ✅ Toutes les étapes demandées sont déjà faites.", file=sys.stderr)
                return {}
            print(f"  ℹ️  Étapes restantes : {', '.join(remaining)}\n", file=sys.stderr)
            steps = remaining

        context = {}

        # Pré-vol de coût : savoir avant de générer, pas après. Refuse de démarrer une
        # étape coûteuse au-delà du plafond déclaré, sauf --force explicite.
        if not self._cost_preflight(steps, force):
            return {}

        for step in steps:
            if _PAUSE_REQUESTED:
                print(f"\n  ⏸️  Pipeline mis en pause avant l'étape '{step}'.", file=sys.stderr)
                print(f"  ℹ️  Reprendre avec : python main.py resume {self.slug}\n", file=sys.stderr)
                break

            if step == "init":
                step_start("Initialisation du projet")
                self.init_project(script_path or "")
                scenes = self.parse_script(script_path)
                context["scenes"] = scenes
                self.init_bible_from_script()
                step_end("Init", f"Projet créé, {len(scenes)} scènes parsées, bible initialisée")

            elif step == "parse":
                step_start("Parsing du scénario")
                scenes = self.parse_script(script_path)
                context["scenes"] = scenes
                step_end("Parse", f"{len(scenes)} scènes parsées et sauvegardées")

            elif step == "fiches":
                step_start("Génération des fiches personnages/lieux/objets")
                scenes = context.get("scenes") or self._load_parsed_scenes()
                context["fiches"] = self.generate_fiches(scenes)
                n = len(context["fiches"])
                step_end("Fiches", f"{n} entités créées ou enrichies dans la bible")

            elif step == "sheets":
                step_start("Génération des character sheets (images de référence)")
                entities = context.get("fiches") or self._load_bible_entities()
                context["sheets"] = self.generate_sheets(entities)
                ok = sum(1 for v in context["sheets"].values() if "error" not in v)
                err = sum(1 for v in context["sheets"].values() if "error" in v)
                step_end("Sheets", f"{ok} sheets générées" + (f", {err} échecs" if err else ""))

            elif step == "storyboard":
                step_start("Storyboard — découpage en plans")
                scenes = context.get("scenes") or self._load_parsed_scenes()
                context["storyboards"] = self.generate_storyboard(scenes)
                total_shots = sum(
                    sb.get("count", len(sb.get("shots", [])))
                    for sb in context["storyboards"].values()
                )
                step_end("Storyboard", f"{len(context['storyboards'])} scènes storyboardées, {total_shots} plans")

            elif step == "board":
                step_start("Planche storyboard — croquis wilderien de validation")
                scenes = context.get("scenes") or self._load_parsed_scenes()
                context["boards"] = self.generate_board(scenes)
                total = len(context["boards"])
                step_end("Board", f"{total} planches générées")

            elif step == "visualize":
                step_start("Visualisation storyboard — génération des dessins")
                scenes = context.get("scenes") or self._load_parsed_scenes()
                context["visuals"] = self.generate_visualize(scenes)
                total = sum(v.get("count", 0) for v in context["visuals"].values())
                step_end("Visualize", f"{total} dessins storyboard générés")

            elif step == "produce":
                step_start("Production vidéo — génération des séquences")
                scenes = context.get("scenes") or self._load_parsed_scenes()
                storyboards = context.get("storyboards") or self._load_storyboards()
                context["videos"] = self.produce_shots(scenes, storyboards)
                total_videos = sum(v.get("count", 0) for v in context["videos"].values())
                step_end("Production", f"{total_videos} vidéos générées")

            elif step == "keyframes":
                step_start("Production vidéo keyframes — beats LLM → images intermédiaires → chaînage agnes")
                scenes = context.get("scenes") or self._load_parsed_scenes()
                context["keyframes"] = self.produce_keyframes(scenes)
                total_videos = sum(v.get("count", 0) for v in context["keyframes"].values())
                step_end("Keyframes", f"{total_videos} vidéos keyframes générées")

            elif step == "audio":
                step_start("Génération audio — voix off + ambiances")
                scenes = context.get("scenes") or self._load_parsed_scenes()
                context["audio"] = self.generate_audio(scenes)
                total_audio = sum(len(v) for v in context["audio"].values())
                step_end("Audio", f"{total_audio} fichiers audio générés")

            else:
                step_warn(f"Étape inconnue", f"'{step}' ignorée")
                continue

            if _PAUSE_REQUESTED:
                print(f"\n  ⏸️  Pipeline mis en pause après l'étape '{step}'.", file=sys.stderr)
                print(f"  ℹ️  Reprendre avec : python main.py resume {self.slug}\n", file=sys.stderr)
                break

            self._record_scene_state(step, context)

            if self._validation_mode() != "off":
                self._wait_for_validation(step, context)
                if _PAUSE_REQUESTED:
                    break

        # Sauvegarde et affichage conso
        try:
            conso_path = get_tracker().save()
            get_tracker().print_summary()
            print(f"  Fichier de conso : {conso_path}\n", file=sys.stderr)
        except Exception as e:
            logger.warning(f"Échec sauvegarde conso: {e}")

        return context

    def resume_pipeline(self, steps: list[str] | None = None) -> dict:
        """Resume pipeline from the last incomplete step."""
        if steps is None:
            steps = self.AVAILABLE_STEPS
        completed = self._get_completed_steps()
        remaining = [s for s in steps if s not in completed]
        if not remaining:
            print(f"  ✅ Toutes les étapes sont déjà complétées.", file=sys.stderr)
            return {}
        print(f"\n  ▶️  Reprise du pipeline — {len(remaining)} étapes restantes : {', '.join(remaining)}\n", file=sys.stderr)
        return self.run_pipeline(steps=remaining)

    # ── Checkpoint ─────────────────────────────────────────────

    def _save_checkpoint(self, data: dict) -> None:
        if not self.prod_cfg.get("checkpoint", True):
            return
        checkpoint = {}
        if self.checkpoint_path.exists():
            checkpoint = json.loads(self.checkpoint_path.read_text())
        for key in data:
            # Une clé qui n'est pas un identifiant d'étape n'est jamais relue par
            # _get_completed_steps : le run la refait à chaque fois, silencieusement.
            if key not in self.AVAILABLE_STEPS:
                step_warn(
                    "Clé de checkpoint hors registre",
                    f"'{key}' n'est pas dans AVAILABLE_STEPS {self.AVAILABLE_STEPS} — "
                    f"cette étape ne sera jamais reconnue comme faite.",
                )
        checkpoint.update(data)
        checkpoint["_updated"] = datetime.now().isoformat()
        checkpoint["_project"] = self.slug
        self.checkpoint_path.write_text(json.dumps(checkpoint, indent=2, ensure_ascii=False))

    def load_checkpoint(self) -> dict | None:
        if not self.checkpoint_path.exists():
            return None
        data = json.loads(self.checkpoint_path.read_text())
        # Historique : produce_shots écrivait "production" au lieu de "produce", donc
        # l'étape n'était jamais reconnue. Migration en mémoire vers la bonne clé ; le
        # fichier n'est réécrit qu'au prochain _save_checkpoint.
        if "production" in data:
            data.setdefault("produce", data.pop("production"))
            logger.info("Checkpoint migré : clé legacy 'production' -> 'produce'")
        return data
        return None

    def clear_checkpoint(self) -> None:
        if self.checkpoint_path.exists():
            self.checkpoint_path.unlink()

    # ── Helpers ────────────────────────────────────────────────

    def _load_parsed_scenes(self) -> list[dict]:
        parsed_dir = self.root / "script" / "parsed"
        scenes = []
        for f in sorted(parsed_dir.glob("scene_*.yaml")):
            with open(f) as fh:
                scenes.append(yaml.safe_load(fh))
        return scenes

    def _load_storyboards(self) -> dict:
        sb_dir = self.root / "preprod" / "storyboard"
        storyboards = {}
        for d in sorted(sb_dir.glob("scene_*")):
            json_path = d / "storyboard.json"
            if json_path.exists():
                with open(json_path) as f:
                    storyboards[d.name] = {"shots": json.load(f)}
        return storyboards

    def _load_bible_entities(self) -> dict:
        bible = BibleDB(self.root / "bible")
        entities = {}
        for e in bible.list_entities():
            entities[e.slug] = {
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
        return entities

    def _validation_mode(self) -> str:
        """Mode du portail de validation, normalisé.

        `validation_mode` prime. À défaut, l'ancien booléen `human_validation` est
        honoré pour ne pas casser les configs existantes.
        """
        mode = self.prod_cfg.get("validation_mode")
        if mode is None:
            return "interactive" if self.prod_cfg.get("human_validation", True) else "off"
        mode = str(mode).strip().lower()
        if mode not in VALIDATION_MODES:
            step_warn(
                "validation_mode inconnu",
                f"'{mode}' — attendu l'un de {VALIDATION_MODES}. Traité comme 'interactive'.",
            )
            return "interactive"
        return mode

    def _cost_preflight(self, steps: list[str], force: bool) -> bool:
        """Estime le coût des étapes coûteuses avant de les lancer.

        Renvoie False si le plafond est dépassé et que --force n'a pas été passé, auquel
        cas rien n'est généré. Un plafond à 0 désactive le contrôle.
        """
        from pipeline.cost_preflight import COSTLY_STEPS, check_budget, estimate_run, format_report

        costly = [s for s in steps if s in COSTLY_STEPS]
        if not costly:
            return True

        try:
            scenes = [int(s.get("number", i + 1)) for i, s in enumerate(self._load_parsed_scenes())]
        except Exception:
            scenes = []
        if not scenes:
            return True  # rien à estimer : les scènes ne sont pas encore parsées

        try:
            entities = len(self._load_bible_entities())
        except Exception:
            entities = 0

        estimate = estimate_run(costly, self.root, scenes, get_config(), entities=entities)
        budget = float(self.prod_cfg.get("budget_per_scene", 0) or 0)
        check = check_budget(estimate, budget)
        if not any(estimate["per_scene"].values()):
            return True  # rien à générer (toutes les feuilles existent déjà)

        print("\n" + format_report(estimate, check) + "\n", file=sys.stderr)
        if check["refuse"] and not force:
            print("  🛑 Pré-vol refusé : plafond par scène dépassé.", file=sys.stderr)
            print("  ℹ️  Pour passer outre, relancez avec --force.", file=sys.stderr)
            return False
        return True

    def _record_scene_state(self, step: str, context: dict) -> None:
        """Enregistre, par scène, l'artefact qu'une étape vient de produire.

        Volontairement tolérant aux pannes : l'observabilité ne doit jamais faire tomber
        une production. Un échec ici est journalisé, pas propagé.
        """
        source_key = {
            "visualize": "visuals",
            "produce": "videos",
            "keyframes": "keyframes",
            "audio": "audio",
        }.get(step)
        if not source_key:
            return
        result = context.get(source_key)
        if not isinstance(result, dict) or not result:
            return
        try:
            state = SceneState(self.root)
            for scene, payload in result.items():
                artifacts = {}
                if isinstance(payload, dict):
                    if isinstance(payload.get("count"), int):
                        artifacts["count"] = payload["count"]
                    for field in ("shots", "files", "paths"):
                        if isinstance(payload.get(field), list):
                            artifacts[field] = len(payload[field])
                elif isinstance(payload, (list, tuple)):
                    artifacts["count"] = len(payload)
                state.mark_generated(scene, step, artifacts=artifacts or None)
        except Exception as exc:  # pragma: no cover - l'observabilité ne doit rien casser
            logger.warning("SceneState: enregistrement impossible pour '%s' (%s)", step, exc)

    def _wait_for_validation(self, step: str, context: dict) -> None:
        mode = self._validation_mode()

        if mode == "manual":
            print(
                f"\n{'─' * 60}\n"
                f"  ⏭️  validation_mode: manual — étape '{step}' écrite, pas de portail.\n"
                f"      Artefacts : {list(context.keys())}\n"
                f"{'─' * 60}",
                file=sys.stderr,
            )
            return

        # mode == "interactive" : un vrai terminal est requis. input() sur un stdin
        # fermé (appel agent/MCP) lève EOFError, et sur un pipe inerte bloque sans fin.
        if not (sys.stdin and sys.stdin.isatty()):
            raise RuntimeError(
                f"Portail de validation impossible après l'étape '{step}' : stdin n'est pas "
                f"un terminal. Un agent ne peut pas répondre à un prompt, et tenter de le "
                f"faire lèverait EOFError (stdin fermé) ou bloquerait le run (pipe inerte). "
                f"Choisis production.validation_mode dans pipeline/config.yaml : "
                f"'manual' (l'agent valide entre deux appels d'étapes) ou 'off' "
                f"(batch non supervisé). L'étape '{step}' est déjà écrite sur disque."
            )

        print(f"\n{'─' * 60}", file=sys.stderr)
        print(f"  👤 Validation humaine — Étape '{step}' terminée", file=sys.stderr)
        print(f"  Context keys: {list(context.keys())}", file=sys.stderr)
        print(f"{'─' * 60}", file=sys.stderr)
        try:
            response = input("  Actions : (c)ontinuer, (r)etry, (s)kip, (p)ause, (q)uit : ").strip().lower()
        except EOFError:
            raise RuntimeError(
                f"Portail de validation interrompu (stdin fermé) après l'étape '{step}'. "
                f"Utilise validation_mode: 'manual' pour un pilotage par agent."
            ) from None

        global _PAUSE_REQUESTED
        if response == "q":
            print(f"\n  🛑 Arrêt demandé. Reprendre avec : python main.py resume {self.slug}\n", file=sys.stderr)
            _PAUSE_REQUESTED = True
            raise KeyboardInterrupt("Stopped by user")
        elif response == "p":
            print(f"\n  ⏸️  Pause demandée. Reprendre avec : python main.py resume {self.slug}\n", file=sys.stderr)
            _PAUSE_REQUESTED = True
        elif response == "r":
            raise RuntimeError(f"User requested retry of step '{step}'")
        elif response == "s":
            pass
