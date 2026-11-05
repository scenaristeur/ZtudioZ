"""Application Starlette du studio : lecture du journal et reponse aux gates."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from ..kernel.artifacts import ArtifactStore
from ..kernel.executor import Executor
from ..kernel.journal import Journal
from ..kernel.registry import load_registry
from . import service

logger = logging.getLogger(__name__)


def build_executor(project, registry, answers: dict[str, str] | None = None) -> Executor:
    """Meme montage que le CLI : providers et contexte du projet."""
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


def create_app(projects_root: str | Path, registry=None) -> Starlette:
    root = Path(projects_root)
    registry = registry or load_registry()
    running: dict[str, str] = {}
    tasks: set[asyncio.Task] = set()

    async def health(request):
        return JSONResponse({"status": "ok"})

    async def list_projects(request):
        projects = []
        if root.is_dir():
            for path in sorted(root.iterdir()):
                if not path.is_dir() or not (path / "studio.yaml").exists():
                    continue
                try:
                    project = service.resolve_project(root, path.name)
                except HTTPException as exc:
                    projects.append({"slug": path.name, "error": exc.detail})
                    continue
                projects.append(service.project_summary(project))
        return JSONResponse({"projects": projects})

    async def project_detail(request):
        project = service.resolve_project(root, request.path_params["slug"])
        return JSONResponse(
            {
                "slug": project.root.name,
                "name": project.name,
                "providers": {
                    slot: config.kind for slot, config in project.providers.items()
                },
                "context": sorted(project.context),
                "runs": service.summarize(service.events(project)),
            }
        )

    async def journal(request):
        project = service.resolve_project(root, request.path_params["slug"])
        events = service.events(project)
        run_id = request.query_params.get("run")
        if run_id:
            events = [event for event in events if event.get("run") == run_id]
        return JSONResponse({"events": events})

    async def run_detail(request):
        project = service.resolve_project(root, request.path_params["slug"])
        return JSONResponse(service.run_detail(project, request.path_params["run_id"]))

    async def artifact(request):
        project = service.resolve_project(root, request.path_params["slug"])
        path, media_type = service.artifact_path(
            project, request.path_params["artifact"]
        )
        return FileResponse(path, media_type=media_type)

    async def answer_gate(request):
        project = service.resolve_project(root, request.path_params["slug"])
        run_id = request.path_params["run_id"]
        gate_id = request.path_params["gate_id"]

        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "corps JSON invalide") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "corps JSON invalide")
        answer = body.get("answer")

        events = service.events(project)
        gate = service.pending_gate(events, run_id)
        if gate is None or gate["id"] != gate_id:
            raise HTTPException(409, "aucune gate en attente pour ce run")
        if answer not in gate["options"]:
            raise HTTPException(
                400, f"reponse invalide: {answer!r} (attendu: {gate['options']})"
            )

        slug = project.root.name
        if slug in running:
            raise HTTPException(409, "un run est deja en cours sur ce projet")
        workflow, inputs = service.run_start(events, run_id)
        if workflow is None:
            raise HTTPException(404, "run inconnu")

        executor = build_executor(project, registry, answers={gate_id: answer})
        running[slug] = run_id

        async def resume():
            try:
                await executor.run(workflow, inputs)
            except Exception:
                logger.exception("run %s: reprise de gate en echec", run_id)
            finally:
                running.pop(slug, None)

        task = asyncio.create_task(resume())
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return JSONResponse(
            {"run_id": run_id, "gate": gate_id, "answer": answer, "status": "started"},
            status_code=202,
        )

    return Starlette(
        routes=[
            Route("/api/health", health),
            Route("/api/projects", list_projects),
            Route("/api/projects/{slug}", project_detail),
            Route("/api/projects/{slug}/journal", journal),
            Route("/api/projects/{slug}/runs/{run_id}", run_detail),
            Route("/api/projects/{slug}/artifacts/{artifact}", artifact),
            Route(
                "/api/projects/{slug}/runs/{run_id}/gates/{gate_id}",
                answer_gate,
                methods=["POST"],
            ),
        ]
    )
