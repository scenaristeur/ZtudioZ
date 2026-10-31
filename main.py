#!/usr/bin/env python3
"""ZtudioZ — Pipeline de production audiovisuelle par IA.

Usage:
    python main.py init <project_slug> <script_path>
    python main.py run <project_slug> [--steps ...] [--force]
    python main.py resume <project_slug>
    python main.py status <project_slug>
    python main.py switch-version <project_slug> <entity_slug> <version>
"""

import json
import logging
import sys
from pathlib import Path

import yaml

from pipeline.orchestrator import Orchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ztudioz")


def cmd_init(args):
    orch = Orchestrator(args.project)
    root = orch.init_project(args.script)
    scenes = orch.parse_script(args.script)
    logger.info(f"Parsed {len(scenes)} scenes")
    orch.init_bible_from_script()
    logger.info(f"Project initialized at {root}")
    print(f"\nProjet '{args.project}' créé. {len(scenes)} scènes parsées.")
    print(f"Bible initialisée. Lance : python main.py run {args.project}")


def cmd_run(args):
    orch = Orchestrator(args.project, verbose=args.verbose)
    steps = args.steps.split(",") if args.steps else None
    try:
        context = orch.run_pipeline(steps=steps, force=args.force)
        if context:
            print(f"\nPipeline terminé. Étapes: {list(context.keys())}")
    except KeyboardInterrupt:
        print("\nPipeline interrompu.")
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        raise


def cmd_resume(args):
    orch = Orchestrator(args.project, verbose=args.verbose)
    steps = args.steps.split(",") if args.steps else None
    try:
        context = orch.resume_pipeline(steps=steps)
        if context:
            print(f"\nPipeline repris et terminé. Étapes: {list(context.keys())}")
    except KeyboardInterrupt:
        print("\nPipeline interrompu.")
    except Exception as e:
        logger.error(f"Resume failed: {e}")
        raise


def cmd_status(args):
    orch = Orchestrator(args.project)
    checkpoint = orch.load_checkpoint()
    if checkpoint:
        print(f"\nCheckpoint pour '{args.project}':")
        print(json.dumps(checkpoint, indent=2, ensure_ascii=False))
    else:
        print(f"Aucun checkpoint trouvé pour '{args.project}'")

    bible_path = orch.root / "bible"
    if bible_path.exists():
        from pipeline.models.bible import BibleDB
        bible = BibleDB(bible_path)
        entities = bible.list_entities()
        print(f"\nBible: {len(entities)} entités")
        for e in entities:
            print(f"  [{e.type}] {e.name} ({e.slug}) — active: {e.active_version}")


def cmd_switch_version(args):
    orch = Orchestrator(args.project)
    from pipeline.models.bible import BibleDB
    bible = BibleDB(orch.root / "bible")
    bible.set_active_version(args.entity, args.version)
    entity = bible.get_entity(args.entity)
    if entity:
        print(f"Version basculée: {entity.name} → {entity.active_version}")
    else:
        print(f"Entité '{args.entity}' introuvable")


def cmd_clip(args):
    from pipeline.clip.steps import ClipStep
    step = ClipStep(verbose=args.verbose)
    secs = args.sections.split(",") if args.sections else None
    try:
        result = step.run(
            project=args.project,
            variant_id=args.variant,
            audio_path=args.audio or None,
            mode=args.mode,
            sections=secs,
            force=args.force,
            max_plans=args.max_plans,
        )
        if result.get("final"):
            print(f"\nClip final : {result['final']}")
        else:
            print(f"\nProduit {len(result.get('images', {}))} images, "
                  f"{len(result.get('videos', {}))} vidéos (mode {args.mode})")
    except Exception as e:
        logger.error(f"Clip failed: {e}")
        raise


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ZtudioZ — Production Pipeline")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose mode")
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="Initialiser un nouveau projet")
    p_init.add_argument("project", help="Nom du projet (slug)")
    p_init.add_argument("script", help="Chemin vers le script source")

    p_run = sub.add_parser("run", help="Lancer le pipeline")
    p_run.add_argument("project", help="Nom du projet")
    p_run.add_argument("--steps", "-s",
                       help="Étapes à exécuter (séparées par des virgules): "
                            "init,parse,fiches,sheets,storyboard,visualize,produce,keyframes,audio")
    p_run.add_argument("--force", "-f", action="store_true",
                       help="Forcer la re-exécution des étapes déjà complétées")

    p_resume = sub.add_parser("resume", help="Reprendre le pipeline après une pause")
    p_resume.add_argument("project", help="Nom du projet")
    p_resume.add_argument("--steps", "-s",
                          help="Étapes à exécuter (séparées par des virgules)")

    p_status = sub.add_parser("status", help="Voir l'état du projet")
    p_status.add_argument("project", help="Nom du projet")

    p_switch = sub.add_parser("switch-version", help="Basculer la version d'une entité")
    p_switch.add_argument("project", help="Nom du projet")
    p_switch.add_argument("entity", help="Slug de l'entité")
    p_switch.add_argument("version", help="Version (v1, v2, ...)")

    p_clip = sub.add_parser("clip", help="Produire un clip musical (variante v1/v2)")
    p_clip.add_argument("project", help="Nom du projet")
    p_clip.add_argument("--variant", default="v2", help="Variante: v1 (bulles) | v2 (récit humain)")
    p_clip.add_argument("--mode", default="images",
                        help="images (héros par section) | video | full")
    p_clip.add_argument("--sections", "-s",
                        help="Sections à traiter, séparées par des virgules")
    p_clip.add_argument("--audio", help="Chemin du fichier audio (défaut: sources/*.mp3)")
    p_clip.add_argument("--force", "-f", action="store_true",
                        help="Régénérer même si les fichiers existent")
    p_clip.add_argument("--max-plans", type=int, default=None,
                        help="Limiter au N premiers plans (test d'enchaînement)")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "resume":
        cmd_resume(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "switch-version":
        cmd_switch_version(args)
    elif args.command == "clip":
        cmd_clip(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
