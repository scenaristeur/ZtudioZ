from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from camel.tasks import Task, TaskManager

from pipeline.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

_PROJECTS_DIR = Path.cwd() / "projets"


def _run_step(project_slug: str, step_name: str) -> dict:
    """Exécute une étape du pipeline et retourne son contexte."""
    orch = Orchestrator(project_slug)
    steps_map = {
        "parse": lambda: orch._load_parsed_scenes()
        or orch.parse_script(),
        "fiches": lambda: orch.generate_fiches(orch._load_parsed_scenes()),
        "sheets": lambda: orch.generate_sheets(orch._load_bible_entities()),
        "storyboard": lambda: orch.generate_storyboard(orch._load_parsed_scenes()),
        "visualize": lambda: orch.generate_visualize(orch._load_parsed_scenes()),
        "produce": lambda: orch.produce_shots(
            orch._load_parsed_scenes(), orch._load_storyboards()
        ),
        "audio": lambda: orch.generate_audio(orch._load_parsed_scenes()),
    }
    fn = steps_map.get(step_name)
    if fn is None:
        raise ValueError(f"Étape inconnue : {step_name}")
    result = fn()
    return {"step": step_name, "result": str(result)[:500]}


# ── Tâches individuelles ────────────────────────────────────

TASK_PARSE = Task(
    content="Parser le script du projet et extraire les scènes via LLM",
    id="parse",
    type="llm",
)

TASK_FICHES = Task(
    content="Générer les fiches personnages/lieux/objets depuis les scènes",
    id="fiches",
    type="llm",
    dependencies=[TASK_PARSE],
)

TASK_SHEETS = Task(
    content="Générer les images de référence (character sheets) via Pollinations",
    id="sheets",
    type="image",
    dependencies=[TASK_FICHES],
)

TASK_STORYBOARD = Task(
    content="Générer le storyboard textuel (découpage en plans) via LLM",
    id="storyboard",
    type="llm",
    dependencies=[TASK_PARSE],
)

TASK_VISUALIZE = Task(
    content="Générer les dessins storyboard (mode croquis) via Pollinations",
    id="visualize",
    type="image",
    dependencies=[TASK_STORYBOARD],
)

TASK_PRODUCE = Task(
    content="Générer les vidéos des plans via Pollinations video",
    id="produce",
    type="video",
    dependencies=[TASK_VISUALIZE],
)

TASK_AUDIO = Task(
    content="Générer l'audio (voix off + ambiances) pour chaque scène",
    id="audio",
    type="audio",
    dependencies=[TASK_PARSE],
)


def build_film_pipeline() -> Task:
    """Construire le workflow complet de production d'un film.

    Retourne la tâche racine avec toutes les dépendances.
    Le graphe d'exécution est :

        parse ─┬─ fiches ── sheets
               ├─ storyboard ── visualize ── produce
               └─ audio
    """
    root = Task(
        content="Pipeline complet de production audiovisuelle",
        id="film_pipeline",
        type="workflow",
        subtasks=[
            TASK_PARSE,
            TASK_FICHES,
            TASK_SHEETS,
            TASK_STORYBOARD,
            TASK_VISUALIZE,
            TASK_PRODUCE,
            TASK_AUDIO,
        ],
    )
    return root


def run_workflow(project_slug: str,
                 task: Task | None = None,
                 parallel: bool = False) -> Task:
    """Exécuter un workflow Camel avec TaskManager.

    Args:
        project_slug: Slug du projet
        task: Tâche racine (si None, utilise build_film_pipeline())
        parallel: True pour exécution parallèle quand possible

    Retourne la tâche racine avec les résultats.
    """
    if task is None:
        task = build_film_pipeline()

    manager = TaskManager(task)
    _execute(manager, manager.root_task, project_slug, parallel)
    return task


def _execute(manager: TaskManager, task: Task,
             project_slug: str, parallel: bool) -> None:
    """Exécuter une tâche et ses dépendances récursivement."""
    logger.info("🔄 %s (%s) — %s", task.id, task.type, task.content[:80])

    # Exécuter les dépendances
    if task.subtasks:
        if parallel:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor() as pool:
                futures = [
                    pool.submit(_execute, manager, st, project_slug, parallel)
                    for st in task.subtasks
                ]
                for f in futures:
                    f.result()
        else:
            # Ordre topologique
            sorted_tasks = TaskManager.topological_sort(task.subtasks)
            for st in sorted_tasks:
                _execute(manager, st, project_slug, parallel)

    # Exécuter la tâche elle-même si c'est une feuille
    if not task.subtasks and task.id != "film_pipeline":
        task.state = "RUNNING"
        try:
            result = _run_step(project_slug, task.id)
            task.result = json.dumps(result, ensure_ascii=False)
            task.state = "DONE"
            logger.info("✅ %s — %s", task.id, result["result"][:100])
        except Exception as e:
            task.result = f"Erreur: {e}"
            task.state = "FAILED"
            logger.error("❌ %s — %s", task.id, e)


def print_workflow(task: Task, indent: int = 0) -> None:
    """Afficher l'arbre du workflow."""
    prefix = "  " * indent
    state_icon = {"DONE": "✅", "RUNNING": "🔄", "FAILED": "❌", "OPEN": "⬜"}
    icon = state_icon.get(task.state, "⬜")
    deps = f" (deps: {[d.id for d in task.subtasks]})" if task.subtasks else ""
    print(f"{prefix}{icon} [{task.id}] {task.content[:70]}{deps}")
    if task.result:
        print(f"{prefix}   → {str(task.result)[:120]}")
    for st in task.subtasks:
        print_workflow(st, indent + 1)
