"""CLI du studio : validate, run, journal, serve."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .kernel.artifacts import ArtifactStore
from .kernel.context import short_iri
from .kernel.errors import LoadError, StudioError
from .kernel.executor import Executor
from .kernel.journal import Journal
from .kernel.registry import STUDIO_ROOT, load_registry
from .project import (
    Project,
    ProjectError,
    latest_run,
    missing_inputs,
    next_step,
    pending_gate,
    prepare_inputs,
    run_start,
)
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


def _parse_input(raw: str) -> dict:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StudioError(f"--input JSON invalide: {exc}") from exc
    if not isinstance(value, dict):
        raise StudioError("--input doit etre un objet JSON")
    return value


def _parse_pairs(values: list[str], option: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in values:
        if "=" not in item:
            raise StudioError(f"{option} attend nom=valeur, recu: {item!r}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise StudioError(f"{option} a une cle vide: {item!r}")
        pairs[key] = value
    return pairs


def _load_project_or_fail(args) -> Project:
    project = _load_project(args)
    if project is None:
        raise StudioError("cette commande exige --project <dossier>")
    return project


def _print_next(project: Project, registry) -> dict | None:
    step = next_step(project, registry)
    if step is None:
        current = latest_run(project)
        if current is None:
            print("aucun run dans ce projet")
        else:
            print(f"aucun palier suivant (dernier run: {current['status']})")
        return None

    print(f"palier suivant: {short_iri(step['workflow'])}")
    print(f"nom: {step['label']}")
    print(f"depuis: {short_iri(step['from'])} ({step['run_id']})")
    print(f"herite: {', '.join(step['inherited']) or '-'}")
    if step["missing"]:
        print(f"manquant: {', '.join(step['missing'])}")
        example = json.dumps({name: "<valeur>" for name in step["missing"]}, ensure_ascii=False)
        print(
            "prochaine action: .venv/bin/python -m studio.cli --project "
            f"{project.root} run {step['workflow']} --input '{example}'"
        )
    else:
        print("pret a lancer")
        print(
            "prochaine action: .venv/bin/python -m studio.cli --project "
            f"{project.root} run {step['workflow']}"
        )
    return step


def cmd_next(args) -> int:
    project = _load_project_or_fail(args)
    registry = load_registry(args.root, extra_skill_roots=args.skills_root)
    _print_next(project, registry)
    return 0


def cmd_status(args) -> int:
    project = _load_project_or_fail(args)
    registry = load_registry(args.root, extra_skill_roots=args.skills_root)
    current = latest_run(project)
    if current is None:
        print(f"projet: {project.name}")
        print("aucun run")
        return 0

    state = Journal(project.journal).state(current["run_id"])
    print(f"projet: {project.name}")
    print(f"run: {current['run_id']}")
    print(f"workflow: {short_iri(current['workflow'])}")
    print(f"statut: {current['status']}")
    print(f"cout journalise: {state.cost:.4f}")
    print(f"noeuds termines: {', '.join(sorted(state.completed)) or '-'}")
    if current["status"] == "blocked":
        gate = pending_gate(Journal(project.journal).events(), current["run_id"])
        if gate:
            print(f"gate: {gate['id']}")
            print(f"question: {gate['question']}")
            print(f"options: {', '.join(gate['options'])}")
            executor = _project_executor(project, registry)
            verdict = _verdict_summary(executor, current["run_id"])
            if verdict is not None:
                print(f"verdict: {'OK' if verdict['ok'] else 'a corriger'}")
                if verdict["notes"]:
                    print(f"  {verdict['notes']}")
                for constraint in verdict["violated"]:
                    evidence = constraint.get("evidence") or ""
                    print(f"  x {constraint.get('text', '')} — {evidence}")
            for line in _guidance(project, current["run_id"], gate["id"], verdict):
                print(line)
        else:
            print("gate en attente introuvable dans le journal")
        return 0
    if current["status"] == "failed":
        print(f"erreur: {current.get('error') or 'inconnue'}")
        print(
            "prochaine action: .venv/bin/python -m studio.cli --project "
            f"{project.root} journal --run {current['run_id']}"
        )
        return 0
    if current["status"] == "running":
        print("run en cours : relancer status dans quelques instants")
        return 0
    _print_next(project, registry)
    return 0


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


def _project_executor(project: Project, registry, answers: dict[str, str] | None = None) -> Executor:
    return Executor(
        registry=registry,
        provider=project.build("llm"),
        store=ArtifactStore(project.artifacts),
        journal=Journal(project.journal),
        answers=answers,
        image=project.build("image"),
        video=project.build("video"),
        context=project.context,
    )


def _answer_command(project: Project | None, run_id: str, gate: str, choice: str) -> str | None:
    if project is None:
        return None
    return (
        f".venv/bin/python -m studio.cli --project {project.root} "
        f"answer {run_id} {gate} {choice}"
    )


def _verdict_summary(executor: Executor, run_id: str) -> dict | None:
    state = executor.journal.state(run_id)
    artifact = state.artifacts.get("verdict")
    if not artifact:
        return None
    try:
        value = executor.store.get(artifact)
    except Exception:
        return None
    verdict = value.get("verdict", value) if isinstance(value, dict) else None
    if not isinstance(verdict, dict):
        return None
    notes = verdict.get("notes")
    if isinstance(notes, list):
        notes = " ".join(map(str, notes))
    violated = [
        constraint
        for constraint in (verdict.get("constraints") or [])
        if isinstance(constraint, dict) and not constraint.get("respected")
    ]
    return {"ok": bool(verdict.get("ok")), "notes": notes or "", "violated": violated}


def _suggest_feedback(verdict: dict) -> str:
    parts = []
    if verdict.get("notes"):
        parts.append(str(verdict["notes"]))
    for constraint in verdict.get("violated") or []:
        evidence = constraint.get("evidence") or ""
        parts.append(f"- {constraint.get('text', '')}: {evidence}")
    return " ".join(parts)


def _guidance(
    project: Project | None, run_id: str, gate: str, verdict: dict | None
) -> list[str]:
    if project is None:
        return []
    accept = _answer_command(project, run_id, gate, "continue")
    retry = _answer_command(project, run_id, gate, "retry")
    if verdict is not None and not verdict["ok"]:
        suggestion = _suggest_feedback(verdict)
        return [
            f"correction proposee: {suggestion}",
            f"prochaine action: {retry} \"<correction ci-dessus>\"",
            f"ou accepter:     {accept}",
        ]
    return [
        f"prochaine action: {accept}",
        f"ou corriger:     {retry} \"<correction>\"",
    ]


def _print_result(result, executor: Executor, project: Project | None = None) -> int:
    print(f"run: {result.run_id}")
    print(f"statut: {result.status}")
    print(f"noeuds termines: {', '.join(sorted(result.completed)) or '-'}")
    print(f"cout journalise: {executor.journal.state(result.run_id).cost:.4f}")
    if result.pending_gate is not None:
        gate = result.pending_gate
        print(f"gate: {gate.id}")
        print(f"question: {gate.question}")
        print(f"options: {', '.join(gate.options)}")
        verdict = _verdict_summary(executor, result.run_id)
        if verdict is not None:
            print(f"verdict: {'OK' if verdict['ok'] else 'a corriger'}")
            if verdict["notes"]:
                print(f"  {verdict['notes']}")
            for constraint in verdict["violated"]:
                evidence = constraint.get("evidence") or ""
                print(f"  x {constraint.get('text', '')} — {evidence}")
        for line in _guidance(project, result.run_id, gate.id, verdict):
            print(line)
        return 3
    if result.status == "failed":
        print(f"erreur: {result.error}", file=sys.stderr)
        if project is not None:
            print(
                f"prochaine action: .venv/bin/python -m studio.cli --project {project.root} status",
                file=sys.stderr,
            )
        return 1
    print(json.dumps(result.outputs, ensure_ascii=False, indent=2))
    if project is not None:
        print(
            f"prochaine action: .venv/bin/python -m studio.cli --project {project.root} next"
        )
    return 0


def cmd_run(args) -> int:
    load_env_file(".env")
    registry = load_registry(args.root, extra_skill_roots=args.skills_root)
    explicit = _parse_input(args.input)
    answers = _parse_pairs(args.answer, "--answer")
    feedback = _parse_pairs(args.feedback, "--feedback")
    project = _load_project(args)
    runs = _runs_dir(args, project)
    workflow = registry.workflow(args.workflow)
    graph = registry.graph(args.workflow)

    if project is not None:
        inputs, inherited = prepare_inputs(project, workflow, explicit)
        if inherited:
            print(f"herite du journal: {', '.join(inherited)}")
    else:
        inputs = explicit

    missing = missing_inputs(workflow, graph, inputs)
    if missing:
        print(f"ERREUR: entrees manquantes: {', '.join(missing)}", file=sys.stderr)
        return 1
    if feedback and not any(value == "retry" for value in answers.values()):
        print(
            "avertissement: --feedback ignore sans --answer <gate>=retry",
            file=sys.stderr,
        )

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
        result = asyncio.run(
            executor.run(args.workflow, inputs, feedback=feedback)
        )
    except KeyboardInterrupt:
        # D13 : l'interruption ne casse rien — le journal append-only permet de reprendre
        print("interrompu : relancer la meme commande reprend aux noeuds restants", file=sys.stderr)
        return 130

    return _print_result(result, executor, project)


def cmd_answer(args) -> int:
    load_env_file(".env")
    project = _load_project_or_fail(args)
    registry = load_registry(args.root, extra_skill_roots=args.skills_root)
    events = Journal(project.journal).events()
    workflow_id, inputs = run_start(events, args.run_id)
    if workflow_id is None:
        raise StudioError(f"run inconnu: {args.run_id}")
    gate = pending_gate(events, args.run_id)
    if gate is None or gate["id"] != args.gate:
        raise StudioError(f"aucune gate en attente '{args.gate}' pour ce run")
    if args.choice not in gate["options"]:
        raise StudioError(
            f"reponse invalide: {args.choice!r} (attendu: {', '.join(gate['options'])})"
        )
    if args.feedback and args.choice != "retry":
        print("avertissement: --feedback ignore sans choix retry", file=sys.stderr)

    feedback = {args.gate: args.feedback} if args.choice == "retry" else {}
    executor = _project_executor(project, registry, answers={args.gate: args.choice})
    try:
        result = asyncio.run(executor.run(workflow_id, inputs, feedback=feedback))
    except KeyboardInterrupt:
        print("interrompu : relancer la meme commande reprend aux noeuds restants", file=sys.stderr)
        return 130
    return _print_result(result, executor, project)


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
        if args.run and event.get("run") != args.run:
            continue
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
    run.add_argument("--feedback", action="append", default=[],
                     help="feedback de correction pour une gate retry (gate=texte)")
    run.add_argument("--provider", default=None, choices=["mock", "openai"])
    run.add_argument("--image-provider", default=None, choices=["mock", "openai", "pollinations"])
    run.add_argument("--video-provider", default=None, choices=["mock", "pollinations"])
    run.set_defaults(func=cmd_run)

    answer = sub.add_parser("answer", help="repond a une gate d'un run bloque")
    answer.add_argument("run_id")
    answer.add_argument("gate")
    answer.add_argument("choice")
    answer.add_argument("--feedback", default="", help="correction si choice=retry")
    answer.set_defaults(func=cmd_answer)

    journal = sub.add_parser("journal", help="affiche le journal")
    journal.add_argument("--run", default=None, help="filtre sur un run_id")
    journal.set_defaults(func=cmd_journal)

    next_cmd = sub.add_parser("next", help="affiche le palier suivant du projet")
    next_cmd.set_defaults(func=cmd_next)

    status = sub.add_parser("status", help="etat du dernier run et palier suivant")
    status.set_defaults(func=cmd_status)

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
    try:
        return args.func(args)
    except BrokenPipeError:
        return 0
    except (StudioError, ValueError, OSError) as exc:
        print(f"ERREUR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
