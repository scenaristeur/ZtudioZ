"""CLI du studio : validate, run, journal, serve."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .kernel.artifacts import ArtifactStore
from .kernel.context import short_iri
from .kernel.errors import LoadError
from .kernel.executor import Executor
from .kernel.journal import Journal
from .kernel.registry import STUDIO_ROOT, load_registry
from .project import Project, ProjectError, project_memory
from .tools.images import (
    MockImageProvider,
    OpenAICompatImageProvider,
    PollinationsImageProvider,
)
from .tools.llm import MockProvider, OpenAICompatProvider
from .tools.video import MockVideoProvider, PollinationsVideoProvider


def _build_provider(name: str):
    import os

    if name == "mock":
        return MockProvider(
            delay=float(os.environ.get("STUDIO_MOCK_DELAY", "0")),
            cost=float(os.environ.get("STUDIO_COST", "0")),
        )
    if name == "openai":
        return OpenAICompatProvider(
            model=os.environ.get("STUDIO_MODEL", "openai/gpt-oss-120b"),
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            cost=float(os.environ.get("STUDIO_COST", "0")),
        )
    raise SystemExit(f"provider inconnu: {name}")


def _build_image_provider(name: str):
    import os

    if name == "mock":
        return MockImageProvider(cost=float(os.environ.get("STUDIO_IMAGE_COST", "0")))
    if name == "pollinations":
        return PollinationsImageProvider(
            model=os.environ.get("STUDIO_IMAGE_MODEL", "flux"),
            width=int(os.environ.get("STUDIO_IMAGE_WIDTH", "512")),
            height=int(os.environ.get("STUDIO_IMAGE_HEIGHT", "512")),
            cost=float(os.environ.get("STUDIO_IMAGE_COST", "0")),
        )
    if name == "openai":
        return OpenAICompatImageProvider(
            model=os.environ.get("STUDIO_IMAGE_MODEL", "gpt-image-1"),
            base_url=os.environ.get("STUDIO_IMAGE_URL", "https://api.openai.com/v1"),
            cost=float(os.environ.get("STUDIO_IMAGE_COST", "0")),
        )
    raise SystemExit(f"provider d'images inconnu: {name}")


def _build_video_provider(name: str):
    import os

    if name == "mock":
        return MockVideoProvider(cost_per_second=float(os.environ.get("STUDIO_VIDEO_COST", "0")))
    if name == "pollinations":
        return PollinationsVideoProvider(
            model=os.environ.get("STUDIO_VIDEO_MODEL", "wan-fast"),
            width=int(os.environ.get("STUDIO_VIDEO_WIDTH", "512")),
            height=int(os.environ.get("STUDIO_VIDEO_HEIGHT", "512")),
            cost_per_second=float(os.environ.get("STUDIO_VIDEO_COST", "0.01")),
        )
    raise SystemExit(f"provider de videos inconnu: {name}")


def _load_project(args) -> Project | None:
    if not args.project:
        return None
    try:
        return Project.load(args.project)
    except ProjectError as exc:
        print(f"PROJET INVALIDE: {exc}", file=sys.stderr)
        raise SystemExit(2)


def _runs_dir(args, project: Project | None) -> Path:
    return project.root if project is not None else Path(args.runs)


def cmd_validate(args) -> int:
    try:
        registry = load_registry(args.root, extra_skill_roots=args.skills_root)
        for workflow_id in sorted(registry.workflows):
            registry.graph(workflow_id)
    except Exception as exc:
        print(f"INVALIDE: {exc}", file=sys.stderr)
        return 1

    print(f"agents: {len(registry.agents)}")
    for agent_id in sorted(registry.agents):
        print(f"  {short_iri(agent_id)}")
    print(f"workflows: {len(registry.workflows)}")
    for workflow_id in sorted(registry.workflows):
        print(f"  {short_iri(workflow_id)}")
    print(f"skills: {len(registry.skills)}")
    for name in sorted(registry.skills):
        print(f"  {name}")
    return 0


def cmd_run(args) -> int:
    registry = load_registry(args.root, extra_skill_roots=args.skills_root)
    inputs = json.loads(args.input)
    answers = dict(item.split("=", 1) for item in args.answer)
    project = _load_project(args)
    runs = _runs_dir(args, project)

    explicit = dict(inputs)
    if project is not None:
        workflow = registry.workflow(args.workflow)
        wanted = set(workflow.get("inputs") or {})
        raw_context = workflow.get("context") or {}
        if isinstance(raw_context, dict):
            wanted |= set(raw_context)
        else:
            wanted |= {str(name) for name in raw_context}
        memory = {
            name: value for name, value in project_memory(project).items() if name in wanted
        }
        inputs = {**memory, **project.context, **explicit}
        inherited = sorted(set(inputs) - set(explicit) - set(project.context))
        if inherited:
            print(f"herite du journal: {', '.join(inherited)}")

    if project is not None:
        provider = _build_provider(args.provider) if args.provider else project.build("llm")
        image = (
            _build_image_provider(args.image_provider)
            if args.image_provider
            else project.build("image")
        )
        video = (
            _build_video_provider(args.video_provider)
            if args.video_provider
            else project.build("video")
        )
    else:
        provider = _build_provider(args.provider or "mock")
        image = _build_image_provider(args.image_provider or args.provider or "mock")
        video = _build_video_provider(args.video_provider or "mock")

    executor = Executor(
        registry=registry,
        provider=provider,
        store=ArtifactStore(runs / "artifacts"),
        journal=Journal(runs / "journal.jsonl"),
        answers=answers,
        image=image,
        video=video,
        context=project.context if project is not None else None,
    )
    try:
        result = asyncio.run(executor.run(args.workflow, inputs))
    except KeyboardInterrupt:
        # D13 : l'interruption ne casse rien — le journal append-only permet de reprendre
        print("interrompu : relancer la meme commande reprend aux noeuds restants", file=sys.stderr)
        return 130
    except LoadError as exc:
        print(f"ERREUR: {exc}", file=sys.stderr)
        return 1

    print(f"run: {result.run_id}")
    print(f"statut: {result.status}")
    print(f"noeuds termines: {', '.join(sorted(result.completed)) or '-'}")
    print(f"cout journalise: {executor.journal.state(result.run_id).cost:.4f}")
    if result.pending_gate is not None:
        gate = result.pending_gate
        print(f"gate: {gate.id}")
        print(f"question: {gate.question}")
        print(f"options: {', '.join(gate.options)}")
        print("relancer avec --answer " + gate.id + "=continue")
        return 3
    if result.status == "failed":
        print(f"erreur: {result.error}", file=sys.stderr)
        return 1
    print(json.dumps(result.outputs, ensure_ascii=False, indent=2))
    return 0


def cmd_invalidate(args) -> int:
    """D17 : purge active par lignee — l'amont est preserve, l'aval se rejouera."""
    project = _load_project(args)
    runs = _runs_dir(args, project)
    store = ArtifactStore(runs / "artifacts")
    try:
        purged = store.invalidate(args.hash)
    except Exception as exc:
        print(f"INVALIDE: {exc}", file=sys.stderr)
        return 1
    print(f"cache purge : {len(purged)} artefacts devalues (dont {args.hash[:8]})")
    for hash_ in sorted(purged):
        print(f"  {hash_}")
    return 0


def cmd_journal(args) -> int:
    project = _load_project(args)
    runs = _runs_dir(args, project)
    for event in Journal(runs / "journal.jsonl").events():
        print(json.dumps(event, ensure_ascii=False))
    return 0


def load_env_file(path: str | Path) -> None:
    """Charge un `.env` local sans ecraser l'environnement deja pose.

    Les secrets restent hors du depot : le serveur local peut demarrer sans
    export manuel, mais une variable explicitement posee par l'operateur gagne.
    """
    import os

    candidate = Path(path)
    if not candidate.is_absolute() and not candidate.exists():
        candidate = STUDIO_ROOT.parent / candidate
    if not candidate.exists():
        return
    for raw in candidate.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] in ("'", '"'):
            quote = value[0]
            end = value.find(quote, 1)
            value = value[1:end] if end != -1 else value[1:]
        else:
            value = value.split(" #", 1)[0].strip()
        os.environ.setdefault(key, value)


def cmd_serve(args) -> int:
    import uvicorn

    from .api.app import create_app

    load_env_file(args.env_file)
    uvicorn.run(create_app(args.projects), host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="studio", description="Runtime d'agents declaratif")
    parser.add_argument("--root", default=str(STUDIO_ROOT))
    parser.add_argument("--runs", default="runs")
    parser.add_argument("--project", default=None,
                        help="dossier du projet (studio.yaml) : artefacts, journal et providers")
    parser.add_argument("--skills-root", action="append", default=[],
                        help="racine externe de packs de connaissance (repetable)")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="valide tout le catalogue")
    validate.set_defaults(func=cmd_validate)

    run = sub.add_parser("run", help="execute un workflow")
    run.add_argument("workflow")
    run.add_argument("--input", default="{}")
    run.add_argument("--answer", action="append", default=[])
    run.add_argument("--provider", default=None, choices=["mock", "openai"])
    run.add_argument("--image-provider", default=None, choices=["mock", "openai", "pollinations"])
    run.add_argument("--video-provider", default=None, choices=["mock", "pollinations"])
    run.set_defaults(func=cmd_run)

    journal = sub.add_parser("journal", help="affiche le journal")
    journal.set_defaults(func=cmd_journal)

    serve = sub.add_parser("serve", help="sert l'API HTTP (lecture + reponse aux gates)")
    serve.add_argument("--projects", default="runs", help="racine des projets")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--env-file", default=".env", help="fichier de variables locales")
    serve.set_defaults(func=cmd_serve)

    invalidate = sub.add_parser("invalidate", help="purge le cache d'un artefact et de son aval")
    invalidate.add_argument("hash")
    invalidate.set_defaults(func=cmd_invalidate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
