"""Services de l'API : lecture du journal, resume d'un run, gates.

Aucune logique de production ici : le service lit ce que le noyau a ecrit et
relance un run bloque avec la reponse humaine. Le CLI et l'API partagent ainsi
exactement le meme journal et les memes artefacts.
"""

from __future__ import annotations

import re
from pathlib import Path

from starlette.exceptions import HTTPException

from ..kernel.artifacts import ArtifactStore, digest
from ..kernel.journal import Journal
from ..project import (
    Project, ProjectError, missing_inputs, next_step, pending_gate, prepare_inputs, run_start,
)

SLUG = re.compile(r"^[A-Za-z0-9._-]+$")
HASH = re.compile(r"^[0-9a-f]{16}$")


def resolve_project(root: Path, slug: str) -> Project:
    """Resout un slug sans permettre de sortir de la racine des projets."""
    if not SLUG.fullmatch(slug) or slug in (".", ".."):
        raise HTTPException(404, "projet inconnu")
    base = Path(root).resolve()
    path = (base / slug).resolve()
    if path.parent != base or not path.is_dir():
        raise HTTPException(404, "projet inconnu")
    try:
        return Project.load(path)
    except ProjectError as exc:
        raise HTTPException(400, str(exc)) from exc


def events(project: Project) -> list[dict]:
    return Journal(project.journal).events()


def summarize(events: list[dict]) -> list[dict]:
    """Un resume par run, du plus recent au plus ancien."""
    order: list[str] = []
    runs: dict[str, dict] = {}
    for event in events:
        run_id = event.get("run")
        if not run_id:
            continue
        if run_id not in runs:
            runs[run_id] = {
                "run_id": run_id,
                "workflow": None,
                "status": "running",
                "cost": 0.0,
                "gate": None,
                "error": None,
                "started": None,
                "finished": None,
                "nodes": [],
            }
            order.append(run_id)
        run = runs[run_id]
        kind = event.get("type")
        if kind == "run_started":
            if run["workflow"] is None:
                run["workflow"] = event.get("workflow")
            run["started"] = run["started"] or event.get("ts")
            run["error"] = None
            if run["status"] == "blocked":
                run["status"] = "running"
        elif kind == "node_finished":
            node = event.get("node", "")
            if "#" not in node:
                run["nodes"].append(node)
                run["cost"] += float(event.get("cost", 0.0) or 0.0)
        elif kind == "node_failed":
            run["error"] = event.get("error")
        elif kind == "gate_asked":
            run["gate"] = {
                "id": event.get("node"),
                "question": event.get("question"),
                "options": list(event.get("options") or []),
            }
        elif kind == "gate_answered":
            if run["gate"] and run["gate"]["id"] == event.get("node"):
                run["gate"] = None
        elif kind == "run_finished":
            run["status"] = event.get("status")
            run["finished"] = event.get("ts")
            if run["status"] != "blocked":
                run["gate"] = None
    return [runs[run_id] for run_id in reversed(order)]


def project_summary(project: Project) -> dict:
    runs = summarize(events(project))
    last = runs[0] if runs else None
    return {
        "slug": project.root.name,
        "name": project.name,
        "status": last["status"] if last else "new",
        "run_id": last["run_id"] if last else None,
        "workflow": last["workflow"] if last else None,
        "gate": last["gate"]["id"] if last and last["gate"] else None,
    }


def start_run(project: Project, registry, workflow_id: str, explicit: dict) -> dict:
    """Prepare un nouveau run ; l'execution est lancee par l'appelant."""
    doc = registry.workflow(workflow_id)
    graph = registry.graph(workflow_id)
    inputs, inherited = prepare_inputs(project, doc, explicit)
    missing = missing_inputs(doc, graph, inputs)
    if missing:
        raise HTTPException(400, f"entrees manquantes: {', '.join(missing)}")
    run_id = digest([doc["@id"], inputs])
    return {
        "workflow": doc["@id"],
        "inputs": inputs,
        "inherited": inherited,
        "run_id": run_id,
    }


def _embedded_hashes(value) -> list[str]:
    """Hashs des references `{"artifact": ...}` portees par une valeur JSON."""
    found: list[str] = []
    if isinstance(value, dict):
        artifact = value.get("artifact")
        if isinstance(artifact, str):
            found.append(artifact)
        for nested in value.values():
            found.extend(_embedded_hashes(nested))
    elif isinstance(value, list):
        for nested in value:
            found.extend(_embedded_hashes(nested))
    return found


def _media_refs(store: ArtifactStore, slug: str, artifact: str) -> list[dict]:
    """Images et videos contenues dans un artefact (directement ou imbriquees)."""
    value = store.get(artifact)
    hashes = _embedded_hashes(value) if isinstance(value, (dict, list)) else [artifact]
    media = []
    seen = set()
    for hash_ in hashes:
        if hash_ in seen:
            continue
        seen.add(hash_)
        media_type = store.media_type(hash_)
        if media_type.startswith(("image/", "video/")):
            media.append(
                {
                    "hash": hash_,
                    "media_type": media_type,
                    "url": f"/api/projects/{slug}/artifacts/{hash_}",
                }
            )
    return media


def run_graph(project: Project, run_id: str, registry) -> dict:
    """Graphe du workflow d'un run, fusionne avec son etat journalise."""
    from ..kernel.context import short_iri

    journal_events = events(project)
    workflow, _ = run_start(journal_events, run_id)
    if workflow is None:
        raise HTTPException(404, "run inconnu")
    graph = registry.graph(workflow)
    state = Journal(project.journal).state(run_id)
    store = ArtifactStore(project.artifacts)
    gate = pending_gate(journal_events, run_id)
    if gate:
        gate_node = graph.node(gate["id"])
        gate["retry"] = gate_node.needs[-1] if gate_node.needs else None

    nodes = []
    for node_id in graph.order:
        node = graph.node(node_id)
        artifact = state.artifacts.get(node_id)
        nodes.append(
            {
                "id": node.id,
                "agent": short_iri(node.agent_id) if node.agent_id else None,
                "workflow": short_iri(node.workflow_id) if node.workflow_id else None,
                "needs": list(node.needs),
                "foreach": node.foreach,
                "limit": node.limit,
                "is_gate": node.is_gate,
                "question": node.question,
                "options": list(node.options),
                "completed": node_id in state.completed,
                "artifact": (
                    {
                        "hash": artifact,
                        "media_type": store.media_type(artifact),
                        "url": f"/api/projects/{project.root.name}/artifacts/{artifact}",
                    }
                    if artifact
                    else None
                ),
                "media": (
                    _media_refs(store, project.root.name, artifact) if artifact else []
                ),
            }
        )

    runs = {run["run_id"]: run for run in summarize(journal_events)}
    return {
        "run": runs.get(run_id),
        "workflow": workflow,
        "levels": graph.levels,
        "nodes": nodes,
        "gate": gate,
    }


def run_detail(project: Project, run_id: str) -> dict:
    runs = {run["run_id"]: run for run in summarize(events(project))}
    run = runs.get(run_id)
    if run is None:
        raise HTTPException(404, "run inconnu")

    state = Journal(project.journal).state(run_id)
    store = ArtifactStore(project.artifacts)
    artifacts = [
        {
            "node": node,
            "hash": artifact,
            "media_type": store.media_type(artifact),
            "url": f"/api/projects/{project.root.name}/artifacts/{artifact}",
        }
        for node, artifact in sorted(state.artifacts.items())
    ]
    return {"run": run, "artifacts": artifacts}


def artifact_path(project: Project, artifact: str) -> tuple[Path, str]:
    if not HASH.fullmatch(artifact):
        raise HTTPException(404, "artefact inconnu")
    store = ArtifactStore(project.artifacts)
    try:
        path = store.path_for(artifact)
    except KeyError as exc:
        raise HTTPException(404, "artefact inconnu") from exc
    return path, store.media_type(artifact)
