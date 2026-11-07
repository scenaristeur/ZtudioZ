"""CLI du studio : validate, run, journal, serve."""

from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import sys
from pathlib import Path

from .kernel.artifacts import ArtifactStore
from .kernel.context import short_iri
from .kernel.errors import LoadError, StudioError
from .kernel.executor import Executor
from .kernel.journal import Journal
from . import models
from .kernel.registry import STUDIO_ROOT, load_registry
from .project import (
    Project,
    ProjectError,
    answerable_gate,
    latest_run,
    missing_inputs,
    next_step,
    pending_gate,
    prepare_inputs,
    run_start,
)
from .tools.agnes import AgnesImageProvider, AgnesVideoProvider
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
            ref_model=os.environ.get("STUDIO_REF_MODEL", "gptimage"),
            multi_ref_model=os.environ.get("STUDIO_MULTI_REF_MODEL", "gptimage"),
            strict_refs=os.environ.get("STUDIO_STRICT_REFS", "1") not in ("0", "false", ""),
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
    if name == "agnes":
        return AgnesImageProvider(
            base_url=os.environ.get("STUDIO_AGNES_URL", "http://localhost:8765"),
            size=os.environ.get("STUDIO_AGNES_IMAGE_SIZE", "1152x768"),
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
    if name == "agnes":
        return AgnesVideoProvider(
            base_url=os.environ.get("STUDIO_AGNES_URL", "http://localhost:8765"),
            width=int(os.environ.get("STUDIO_VIDEO_WIDTH", "1152")),
            height=int(os.environ.get("STUDIO_VIDEO_HEIGHT", "768")),
            cost_per_second=float(os.environ.get("STUDIO_VIDEO_COST", "0")),
            dev=os.environ.get("STUDIO_AGNES_DEV", "1") not in ("0", "false", ""),
            max_retries=int(os.environ.get("STUDIO_AGNES_VIDEO_RETRIES", "2")),
            retry_delay=float(os.environ.get("STUDIO_AGNES_VIDEO_RETRY_DELAY", "30")),
        )
    raise SystemExit(f"provider de videos inconnu: {name}")


def _build_fallback_image():
    """Repli Pollinations : `flux` sans reference, `gptimage` des qu'une reference existe."""
    import os

    return PollinationsImageProvider(
        model=os.environ.get("STUDIO_FALLBACK_IMAGE_MODEL", "flux"),
        ref_model=os.environ.get("STUDIO_FALLBACK_REF_MODEL", "gptimage"),
        multi_ref_model=os.environ.get("STUDIO_FALLBACK_MULTI_REF_MODEL", "gptimage"),
        strict_refs=True,
        width=int(os.environ.get("STUDIO_IMAGE_WIDTH", "512")),
        height=int(os.environ.get("STUDIO_IMAGE_HEIGHT", "512")),
        cost=float(os.environ.get("STUDIO_FALLBACK_IMAGE_COST", "0")),
    )


def _build_fallback_video():
    """Repli Pollinations video : modele a image de reference, cout bas pour tester."""
    import os

    return PollinationsVideoProvider(
        model=os.environ.get("STUDIO_FALLBACK_VIDEO_MODEL", "wan-fast"),
        width=int(os.environ.get("STUDIO_VIDEO_WIDTH", "512")),
        height=int(os.environ.get("STUDIO_VIDEO_HEIGHT", "512")),
        cost_per_second=float(os.environ.get("STUDIO_FALLBACK_VIDEO_COST", "0")),
    )


def _agnes_health(base_url: str, timeout: float = 2.0) -> tuple[bool, str]:
    """Agnes repond-il ? Ne teste pas la file video, qui n'a pas d'endpoint public."""
    import urllib.error
    import urllib.request

    url = f"{base_url.rstrip('/')}/api/health"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "ztudioz-studio/0.1"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, str(exc)
    if payload.get("ok"):
        return True, "sain"
    return False, json.dumps(payload, ensure_ascii=False)[:200]


def _ask_yes_no(question: str) -> bool:
    try:
        answer = input(question).strip().lower()
    except EOFError:
        return False
    return answer in ("o", "oui", "y", "yes")


def _fallback_allowed(
    policy: str,
    reason: str,
    *,
    ask=None,
    interactive: bool | None = None,
) -> bool:
    """Decision humaine, jamais automatique : ask | pollinations | fail."""
    if policy == "pollinations":
        return True
    if policy == "fail":
        return False
    if interactive is None:
        interactive = sys.stdin.isatty()
    if not interactive:
        return False
    ask = ask or _ask_yes_no
    print(f"AGNES INDISPONIBLE: {reason}", file=sys.stderr)
    return ask(
        "Generer sur Pollinations a la place ? "
        "(images: flux sans reference puis gptimage ; videos: wan-fast avec image) [o/N] "
    )


def _workflow_slots(registry, workflow_id: str) -> set[str]:
    """Quels adaptateurs (image/video) ce workflow utilise reellement."""
    slots: set[str] = set()
    for raw in (registry.workflow(workflow_id).get("nodes") or []):
        ref = raw.get("agent")
        if not ref:
            continue
        try:
            agent = registry.agent(registry.resolve(ref))
        except StudioError:
            continue
        kind = str(agent.get("@type", ""))
        if kind.endswith("ImageAgent") or kind.endswith("KeyframeAgent"):
            slots.add("image")
        if kind.endswith("VideoAgent"):
            slots.add("video")
    return slots


def _project_agnes_url(project: Project | None) -> str:
    import os

    if project is not None:
        config = project.providers.get("image")
        if config is not None and config.kind == "agnes":
            return str(config.options.get("base_url") or "http://localhost:8765")
        config = project.providers.get("video")
        if config is not None and config.kind == "agnes":
            return str(config.options.get("base_url") or "http://localhost:8765")
    return os.environ.get("STUDIO_AGNES_URL", "http://localhost:8765")


class RuntimeProviders:
    """Adaptateurs construits pour un run, et slots reellement bascules."""

    def __init__(self, llm, image, video, names: dict[str, str], agnes_slots: set[str], fallback: bool):
        self.llm = llm
        self.image = image
        self.video = video
        self.names = names
        self.agnes_slots = agnes_slots
        self.fallback = fallback


def _resolve_runtime_providers(
    args,
    project: Project | None,
    slots: set[str],
    *,
    policy: str | None = None,
    ask=None,
    interactive: bool | None = None,
    force_fallback: bool = False,
) -> RuntimeProviders:
    """Construit les adaptateurs et demande l'accord humain si Agnes manque."""
    policy = policy or getattr(args, "fallback", "ask")
    if project is not None:
        names = {
            "llm": getattr(args, "provider", None) or project.providers["llm"].kind,
            "image": getattr(args, "image_provider", None) or project.providers["image"].kind,
            "video": getattr(args, "video_provider", None) or project.providers["video"].kind,
        }
    else:
        names = {
            "llm": getattr(args, "provider", None) or "mock",
            "image": getattr(args, "image_provider", None) or getattr(args, "provider", None) or "mock",
            "video": getattr(args, "video_provider", None) or "mock",
        }

    agnes_slots = {slot for slot in slots if names.get(slot) == "agnes"}
    fallback = False
    if agnes_slots and not force_fallback:
        healthy, detail = _agnes_health(_project_agnes_url(project))
        if not healthy:
            reason = (
                f"le serveur {_project_agnes_url(project)} ne repond pas ({detail}). "
                "Demarrez Agnes, ou acceptez le repli Pollinations."
            )
            if not _fallback_allowed(policy, reason, ask=ask, interactive=interactive):
                raise StudioError(
                    "Agnes est injoignable et le repli Pollinations n'a pas ete accepte. "
                    "Action: demarrer Agnes, ou relancer avec --fallback pollinations, "
                    "ou --image-provider pollinations --video-provider pollinations."
                )
            fallback = True
    if force_fallback:
        fallback = True

    fallback_slots = set(agnes_slots) if fallback else set()
    if fallback:
        print(
            "repli Pollinations accepte: images flux sans reference puis gptimage avec "
            "reference ; videos wan-fast avec image de reference.",
            file=sys.stderr,
        )

    llm = (
        _build_provider(args.provider)
        if getattr(args, "provider", None)
        else (project.build("llm") if project is not None else _build_provider("mock"))
    )

    def build(slot: str):
        if slot in fallback_slots:
            return _build_fallback_image() if slot == "image" else _build_fallback_video()
        override = getattr(args, f"{slot}_provider", None)
        if override:
            builder = _build_image_provider if slot == "image" else _build_video_provider
            return builder(override)
        if project is not None:
            return project.build(slot)
        builder = _build_image_provider if slot == "image" else _build_video_provider
        return builder(names[slot])

    return RuntimeProviders(
        llm=llm,
        image=build("image"),
        video=build("video"),
        names=names,
        agnes_slots=agnes_slots,
        fallback=fallback,
    )


def _agnes_failure(error: str | None) -> bool:
    text = (error or "").lower()
    return "agnes" in text or "video_queue_full" in text


def _executor_for(
    registry,
    runtime: RuntimeProviders,
    project: Project | None,
    runs: Path,
    answers: dict[str, str] | None = None,
) -> Executor:
    return Executor(
        registry=registry,
        provider=runtime.llm,
        store=ArtifactStore(runs / "artifacts"),
        journal=Journal(runs / "journal.jsonl"),
        answers=answers,
        image=runtime.image,
        video=runtime.video,
        context=project.context if project is not None else None,
        bible_root=(project.root / "bible") if project is not None else None,
    )


def _run_with_fallback(
    registry,
    runtime: RuntimeProviders,
    project: Project | None,
    runs: Path,
    workflow_id: str,
    inputs: dict,
    answers: dict[str, str],
    feedback: dict[str, str | dict[int, str]],
    policy: str,
):
    """Execute, puis propose le repli Pollinations si Agnes lache en cours de route."""
    executor = _executor_for(registry, runtime, project, runs, answers=answers)
    result = asyncio.run(executor.run(workflow_id, inputs, feedback=feedback))
    if (
        result.status == "failed"
        and runtime.agnes_slots
        and not runtime.fallback
        and _agnes_failure(result.error)
    ):
        reason = f"Agnes a echoue en cours de run ({result.error})."
        if _fallback_allowed(policy, reason):
            runtime = _resolve_runtime_providers(
                _fallback_args(runtime),
                project,
                _workflow_slots(registry, workflow_id),
                policy=policy,
                force_fallback=True,
            )
            executor = _executor_for(registry, runtime, project, runs, answers=answers)
            result = asyncio.run(executor.run(workflow_id, inputs, feedback=feedback))
    return result, executor


def _fallback_args(runtime: RuntimeProviders):
    """Un objet minimal : les providers ont deja ete choisis, on force Pollinations."""
    import argparse

    return argparse.Namespace(
        provider=None,
        image_provider=runtime.names["image"],
        video_provider=runtime.names["video"],
    )


def _parse_feedback_items(values: list[str]) -> dict[str, dict[int, str]]:
    """`--feedback-item gate#index=texte` -> {gate: {index: texte}}."""
    items: dict[str, dict[int, str]] = {}
    for raw in values:
        if "=" not in raw:
            raise StudioError(
                f"--feedback-item attend gate#index=texte, recu: {raw!r}"
            )
        key, text = raw.split("=", 1)
        if "#" not in key:
            raise StudioError(
                f"--feedback-item attend gate#index=texte, recu: {key!r}"
            )
        gate, raw_index = key.rsplit("#", 1)
        gate = gate.strip()
        try:
            index = int(raw_index)
        except ValueError as exc:
            raise StudioError(
                f"--feedback-item: index invalide dans {key!r}"
            ) from exc
        if not gate or index < 0:
            raise StudioError(f"--feedback-item invalide: {raw!r}")
        items.setdefault(gate, {})[index] = text
    return items


def _merge_feedback(
    global_feedback: dict[str, str],
    item_feedback: dict[str, dict[int, str]],
) -> dict[str, str | dict[int, str]]:
    both = sorted(set(global_feedback) & set(item_feedback))
    if both:
        raise StudioError(
            "feedback global et cible melanges pour la meme gate: "
            f"{', '.join(both)} ; choisir --feedback (tous) ou --feedback-item (cibles)"
        )
    return {**global_feedback, **item_feedback}


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


SLOT_FOR_KIND = {"text": "llm", "image": "image", "video": "video"}


def _current_model(project: Project | None, kind: str) -> str | None:
    slot = SLOT_FOR_KIND.get(kind)
    if project is None or slot is None:
        return None
    provider = project.providers.get(slot)
    return provider.options.get("model") if provider is not None else None


def cmd_models(args) -> int:
    """Liste le catalogue live : sante, prix, options, modele courant."""
    project = _load_project(args)
    needs = list(getattr(args, "need", []) or [])
    for need in needs:
        models.matches_need({}, need)  # valide le besoin avant tout appel reseau
    kinds = ["all"] if args.kind == "all" else [args.kind]
    sections = []
    for kind in kinds:
        try:
            raw = models.fetch_models(kind)
        except Exception as exc:
            print(f"{kind}: catalogue indisponible ({exc})", file=sys.stderr)
            continue
        all_rows = [models.normalize(kind, item) for item in raw]
        categories = models.KIND_CATEGORIES.get(kind)
        if categories:
            all_rows = [row for row in all_rows if row["category"] in categories]
        rows = all_rows
        if needs:
            rows = [row for row in rows if models.matches_needs(row, needs)]
        if args.search:
            needle = args.search.lower()
            rows = [
                row
                for row in rows
                if needle in row["name"].lower()
                or needle in row["title"].lower()
                or any(needle in alias.lower() for alias in row["aliases"])
            ]
        rows = models.ranked(rows, available=args.available, max_price=args.max_price)
        sections.append((kind, all_rows, rows))

    if args.json:
        payload = {
            "models": [
                {"kind": kind, **row}
                for kind, _, rows in sections
                for row in rows
            ]
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    for kind, all_rows, rows in sections:
        print(f"{kind}:")
        if not rows:
            print("  aucun modele avec ces filtres")
        else:
            print(f"  {'modele':<34} {'sante':<9} {'ok%':>5}  {'prix':<18} options")
            for row in rows:
                ok = "-" if row["success_rate"] is None else f"{row['success_rate']:.1f}"
                print(
                    f"  {row['name']:<34} {row['status']:<9} {ok:>5}  "
                    f"{models.format_price(row):<18} {models.format_options(row)}"
                )
        current = _current_model(project, kind)
        if not current:
            continue
        found = next((row for row in all_rows if models.matches(row, current)), None)
        status = found["status"] if found else "inconnu"
        print(f"  modele courant: {current} ({kind}) — {status}")
        if found is not None and found["status"] == "down":
            alternatives = [
                row
                for row in rows
                if row["status"] != "down"
                and row["name"] != found["name"]
                and models.suitable(row, kind)
                and models.matches_needs(row, needs)
            ][:3]
            if alternatives:
                detail = ", ".join(
                    f"{row['name']} ({models.format_price(row)})"
                    for row in alternatives
                )
                print(f"  alternatives disponibles: {detail}")
    return 0


def cmd_bible(args) -> int:
    from .bible import Bible

    project = _load_project_or_fail(args)
    bible = Bible(project.root / "bible")
    entities = bible.list_entities(args.type)
    if not entities:
        print("bible vide")
        return 0
    for entity in entities:
        sheet = entity.sheet
        print(
            f"{entity.type}/{entity.slug} — {entity.name} "
            f"({entity.active_version}) — {sheet if sheet else 'pas de planche'}"
        )
    return 0


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
                _print_shots(verdict)
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
        bible_root=project.root / "bible",
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
    shots = []
    for shot in verdict.get("shots") or []:
        if not isinstance(shot, dict) or not isinstance(shot.get("index"), int):
            continue
        shots.append(
            {
                "index": shot["index"],
                "ok": bool(shot.get("ok")),
                "notes": str(shot.get("notes") or ""),
                "issues": [
                    str(issue)
                    for issue in (shot.get("issues") or [])
                    if str(issue).strip()
                ],
            }
        )
    return {
        "ok": bool(verdict.get("ok")),
        "notes": notes or "",
        "violated": violated,
        "shots": shots,
    }


def _suggest_feedback(verdict: dict) -> str:
    parts = []
    if verdict.get("notes"):
        parts.append(str(verdict["notes"]))
    for constraint in verdict.get("violated") or []:
        evidence = constraint.get("evidence") or ""
        parts.append(f"- {constraint.get('text', '')}: {evidence}")
    return " ".join(parts)


def _targeted_feedback(verdict: dict) -> list[tuple[int, str]]:
    """Plans fautifs -> corrections par index de fan-out (0-based)."""
    items = []
    for shot in verdict.get("shots") or []:
        if shot.get("ok"):
            continue
        parts = [shot.get("notes") or "", *(shot.get("issues") or [])]
        text = " ; ".join(part for part in parts if part)
        if text:
            items.append((shot["index"], text))
    return items


def _targeted_answer_command(
    project: Project | None, run_id: str, gate: str, items: list[tuple[int, str]]
) -> str | None:
    if project is None:
        return None
    command = [
        ".venv/bin/python",
        "-m",
        "studio.cli",
        "--project",
        str(project.root),
        "answer",
        run_id,
        gate,
        "retry",
    ]
    for index, text in items:
        command += ["--feedback-item", f"{gate}#{index}={text}"]
    return " ".join(shlex.quote(part) for part in command)


def _print_shots(verdict: dict) -> None:
    for shot in verdict.get("shots") or []:
        label = f"plan {shot['index'] + 1} (index {shot['index']})"
        if shot["ok"]:
            print(f"  {label}: ok")
            continue
        print(f"  {label}: a corriger — {shot['notes']}")
        for issue in shot["issues"]:
            print(f"    x {issue}")


def _guidance(
    project: Project | None, run_id: str, gate: str, verdict: dict | None
) -> list[str]:
    if project is None:
        return []
    accept = _answer_command(project, run_id, gate, "continue")
    retry = _answer_command(project, run_id, gate, "retry")
    if verdict is not None and not verdict["ok"]:
        targeted = _targeted_feedback(verdict)
        if targeted:
            command = _targeted_answer_command(project, run_id, gate, targeted)
            concernes = ", ".join(f"#{index}" for index, _ in targeted)
            return [
                f"plans a reprendre: {concernes}",
                f"prochaine action (ciblee): {command}",
                f"ou tout reprendre: {retry} \"<correction globale>\"",
                f"ou accepter:       {accept}",
            ]
        suggestion = _suggest_feedback(verdict)
        return [
            f"correction proposee: {suggestion}",
            f"prochaine action: {retry} --feedback \"<correction ci-dessus>\"",
            f"ou accepter:     {accept}",
        ]
    return [
        f"prochaine action: {accept}",
        f"ou corriger:     {retry} --feedback \"<correction>\"",
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
            _print_shots(verdict)
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
    feedback = _merge_feedback(
        _parse_pairs(args.feedback, "--feedback"),
        _parse_feedback_items(args.feedback_item),
    )
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

    runtime = _resolve_runtime_providers(
        args,
        project,
        _workflow_slots(registry, args.workflow),
        policy=args.fallback,
    )
    try:
        result, executor = _run_with_fallback(
            registry,
            runtime,
            project,
            runs,
            args.workflow,
            inputs,
            answers,
            feedback,
            args.fallback,
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
    gate = answerable_gate(events, args.run_id, args.gate)
    if gate is None:
        raise StudioError(f"aucune gate en attente '{args.gate}' pour ce run")
    if args.choice not in gate["options"]:
        raise StudioError(
            f"reponse invalide: {args.choice!r} (attendu: {', '.join(gate['options'])})"
        )
    if args.feedback and args.choice != "retry":
        print("avertissement: --feedback ignore sans choix retry", file=sys.stderr)
    if args.feedback_item and args.choice != "retry":
        print("avertissement: --feedback-item ignore sans choix retry", file=sys.stderr)

    item_feedback = _parse_feedback_items(args.feedback_item)
    wrong_gate = sorted(gate for gate in item_feedback if gate != args.gate)
    if wrong_gate:
        raise StudioError(
            f"--feedback-item vise {', '.join(wrong_gate)} mais la gate est {args.gate}"
        )
    feedback: dict[str, str | dict[int, str]] = {}
    if args.choice == "retry":
        feedback = _merge_feedback(
            {args.gate: args.feedback} if args.feedback else {},
            item_feedback,
        )
    runtime = _resolve_runtime_providers(
        args,
        project,
        _workflow_slots(registry, workflow_id),
        policy=args.fallback,
    )
    try:
        result, executor = _run_with_fallback(
            registry,
            runtime,
            project,
            project.root,
            workflow_id,
            inputs,
            {args.gate: args.choice},
            feedback,
            args.fallback,
        )
    except KeyboardInterrupt:
        print("interrompu : relancer la meme commande reprend aux noeuds restants", file=sys.stderr)
        return 130
    return _print_result(result, executor, project)


def cmd_restore(args) -> int:
    """Repointe un noeud vers une version anterieure, sans reecrire le journal.

    Le prochain replay repart de cet artefact ; utile quand l'humain prefere une
    version precedente d'un clip ou d'une image a la derniere generee.
    """
    project = _load_project_or_fail(args)
    events = Journal(project.journal).events()
    if not any(event.get("run") == args.run_id for event in events):
        raise StudioError(f"run inconnu: {args.run_id}")
    store = ArtifactStore(project.artifacts)
    try:
        store.get(args.artifact)
    except Exception as exc:
        raise StudioError(f"artefact introuvable: {args.artifact}") from exc
    known = any(
        event.get("run") == args.run_id
        and event.get("node") == args.node
        and event.get("type") in ("node_finished", "node_skipped")
        for event in events
    )
    if not known:
        raise StudioError(f"noeud inconnu dans ce run: {args.node}")
    Journal(project.journal).append(
        "node_finished",
        run=args.run_id,
        node=args.node,
        artifact=args.artifact,
        cost=0.0,
        restored=True,
    )
    print(f"restaure: {args.node} -> {args.artifact[:12]}")
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
    run.add_argument(
        "--feedback-item",
        action="append",
        default=[],
        help="correction ciblee d'un item (gate#index=texte), rejoue seulement les items vises",
    )
    run.add_argument("--provider", default=None, choices=["mock", "openai"])
    run.add_argument("--image-provider", default=None, choices=["mock", "openai", "pollinations", "agnes"])
    run.add_argument("--video-provider", default=None, choices=["mock", "pollinations", "agnes"])
    run.add_argument(
        "--fallback",
        default="ask",
        choices=["ask", "pollinations", "fail"],
        help="si Agnes manque : demander, basculer Pollinations, ou echouer",
    )
    run.set_defaults(func=cmd_run)

    answer = sub.add_parser("answer", help="repond a une gate d'un run bloque")
    answer.add_argument("run_id")
    answer.add_argument("gate")
    answer.add_argument("choice")
    answer.add_argument("--feedback", default="", help="correction si choice=retry")
    answer.add_argument(
        "--feedback-item",
        action="append",
        default=[],
        help="correction ciblee d'un item (gate#index=texte), rejoue seulement les items vises",
    )
    answer.add_argument(
        "--fallback",
        default="ask",
        choices=["ask", "pollinations", "fail"],
        help="si Agnes manque : demander, basculer Pollinations, ou echouer",
    )
    answer.set_defaults(func=cmd_answer)

    journal = sub.add_parser("journal", help="affiche le journal")
    journal.add_argument("--run", default=None, help="filtre sur un run_id")
    journal.set_defaults(func=cmd_journal)

    next_cmd = sub.add_parser("next", help="affiche le palier suivant du projet")
    next_cmd.set_defaults(func=cmd_next)

    bible = sub.add_parser("bible", help="liste les entites et planches de la bible")
    bible.add_argument("--type", default=None, choices=["personnage", "lieu", "objet"])
    bible.set_defaults(func=cmd_bible)

    models_cmd = sub.add_parser(
        "models", help="liste les modeles Pollinations (sante, prix, options)"
    )
    models_cmd.add_argument(
        "--kind", default="video", choices=["video", "image", "text", "audio", "all"]
    )
    models_cmd.add_argument(
        "--need",
        action="append",
        default=[],
        help=f"besoin ponctuel, repetable ({models.NEEDS_HELP})",
    )
    models_cmd.add_argument(
        "--available", action="store_true", help="masque les modeles down"
    )
    models_cmd.add_argument(
        "--max-price", type=float, default=None, help="prix max dans l'unite affichee"
    )
    models_cmd.add_argument("--search", default=None, help="filtre nom/alias/titre")
    models_cmd.add_argument("--json", action="store_true", help="sortie JSON")
    models_cmd.set_defaults(func=cmd_models)

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

    restore = sub.add_parser(
        "restore", help="restaure une version anterieure d'un artefact (append-only)"
    )
    restore.add_argument("run_id")
    restore.add_argument("node")
    restore.add_argument("artifact")
    restore.set_defaults(func=cmd_restore)
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
