"""CLI du studio : validate, run, journal."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .kernel.artifacts import ArtifactStore
from .kernel.context import short_iri
from .kernel.executor import Executor
from .kernel.journal import Journal
from .kernel.registry import STUDIO_ROOT, load_registry
from .tools.images import (
    MockImageProvider,
    OpenAICompatImageProvider,
    PollinationsImageProvider,
)
from .tools.llm import MockProvider, OpenAICompatProvider


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

    executor = Executor(
        registry=registry,
        provider=_build_provider(args.provider),
        store=ArtifactStore(Path(args.runs) / "artifacts"),
        journal=Journal(Path(args.runs) / "journal.jsonl"),
        answers=answers,
        image=_build_image_provider(args.image_provider or args.provider),
    )
    try:
        result = asyncio.run(executor.run(args.workflow, inputs))
    except KeyboardInterrupt:
        # D13 : l'interruption ne casse rien — le journal append-only permet de reprendre
        print("interrompu : relancer la meme commande reprend aux noeuds restants", file=sys.stderr)
        return 130

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
    store = ArtifactStore(Path(args.runs) / "artifacts")
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
    for event in Journal(Path(args.runs) / "journal.jsonl").events():
        print(json.dumps(event, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="studio", description="Runtime d'agents declaratif")
    parser.add_argument("--root", default=str(STUDIO_ROOT))
    parser.add_argument("--runs", default="runs")
    parser.add_argument("--skills-root", action="append", default=[],
                        help="racine externe de packs de connaissance (repetable)")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="valide tout le catalogue")
    validate.set_defaults(func=cmd_validate)

    run = sub.add_parser("run", help="execute un workflow")
    run.add_argument("workflow")
    run.add_argument("--input", default="{}")
    run.add_argument("--answer", action="append", default=[])
    run.add_argument("--provider", default="mock", choices=["mock", "openai"])
    run.add_argument("--image-provider", default=None, choices=["mock", "openai", "pollinations"])
    run.set_defaults(func=cmd_run)

    journal = sub.add_parser("journal", help="affiche le journal")
    journal.set_defaults(func=cmd_journal)

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
