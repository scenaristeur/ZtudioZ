"""M3 tranche 4 — API de lecture et reponse aux gates (Starlette).

L'API ne remplace pas le CLI : elle expose le meme journal, les memes artefacts
et les memes gates. Une reponse de gate relance le run bloque avec la reponse,
en arriere-plan, et le client suit le journal.
"""

import asyncio
import json

import pytest

from studio.api.app import create_app
from studio.kernel.artifacts import ArtifactStore
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import STUDIO_ROOT, load_registry
from studio.tools.images import MockImageProvider
from studio.tools.llm import MockProvider
from studio.tools.video import MockVideoProvider
from tests.helpers import write

CLIPS = {"clips": [{"color": "red", "duration": 0.2}]}


def project(tmp_path):
    root = tmp_path / "projets"
    path = root / "demo"
    write(
        path / "studio.yaml",
        """
        name: demo
        providers:
          llm: {kind: mock}
          image: {kind: mock}
          video: {kind: mock}
        """,
    )
    return root, path


def make_executor(path):
    return Executor(
        registry=load_registry(STUDIO_ROOT),
        provider=MockProvider(),
        store=ArtifactStore(path / "artifacts"),
        journal=Journal(path / "journal.jsonl"),
        image=MockImageProvider(),
        video=MockVideoProvider(),
    )


def block_at_gate(path, answers=None):
    return asyncio.run(
        make_executor(path).run("studio:workflow/m2-montage", CLIPS, answers=answers)
    )


async def call(app, method, path, body=None):
    payload = json.dumps(body).encode("utf-8") if body is not None else b""
    messages = []

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json")] if body is not None else [],
        "client": ("test", 1),
        "server": ("test", 80),
    }

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return start["status"], raw


async def get_json(app, path):
    status, raw = await call(app, "GET", path)
    if not raw:
        return status, None
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


def test_api_liste_les_projets(tmp_path):
    root, path = project(tmp_path)
    block_at_gate(path)

    async def scenario():
        app = create_app(root)
        status, body = await get_json(app, "/api/projects")
        assert status == 200
        assert [p["slug"] for p in body["projects"]] == ["demo"]
        assert body["projects"][0]["status"] == "blocked"
        assert body["projects"][0]["gate"] == "gate_clips"

    asyncio.run(scenario())


def test_api_detail_projet_et_journal(tmp_path):
    root, path = project(tmp_path)
    block_at_gate(path)

    async def scenario():
        app = create_app(root)
        status, detail = await get_json(app, "/api/projects/demo")
        assert status == 200
        assert detail["name"] == "demo"
        assert detail["providers"]["llm"] == "mock"
        assert len(detail["runs"]) == 1

        status, journal = await get_json(app, "/api/projects/demo/journal")
        assert status == 200
        assert any(e["type"] == "run_started" for e in journal["events"])

    asyncio.run(scenario())


def test_api_detail_run_et_artefacts(tmp_path):
    root, path = project(tmp_path)
    blocked = block_at_gate(path, answers={"gate_clips": "continue"})
    assert blocked.pending_gate.id == "gate_montage"

    async def scenario():
        app = create_app(root)
        status, run = await get_json(app, f"/api/projects/demo/runs/{blocked.run_id}")
        assert status == 200
        assert run["run"]["status"] == "blocked"
        assert run["run"]["gate"]["id"] == "gate_montage"
        assert run["artifacts"]
        node = run["artifacts"][0]

        status, raw = await call(app, "GET", node["url"])
        assert status == 200
        assert raw

    asyncio.run(scenario())


def test_api_graphe_du_run(tmp_path):
    root, path = project(tmp_path)
    blocked = block_at_gate(path, answers={"gate_clips": "continue"})

    async def scenario():
        app = create_app(root)
        status, graph = await get_json(
            app, f"/api/projects/demo/runs/{blocked.run_id}/graph"
        )
        assert status == 200
        assert graph["gate"]["id"] == "gate_montage"
        ids = [node["id"] for node in graph["nodes"]]
        assert "gate_clips" in ids and "clips" in ids
        gate = next(node for node in graph["nodes"] if node["id"] == "gate_montage")
        assert gate["is_gate"]
        assert gate["options"] == ["continue", "retry", "stop"]
        assert any(node["completed"] for node in graph["nodes"])
        assert graph["levels"]
        clips = next(node for node in graph["nodes"] if node["id"] == "clips")
        assert clips["media"]
        assert clips["media"][0]["media_type"] == "video/mp4"

    asyncio.run(scenario())


def test_api_404_et_slug_invalide(tmp_path):
    root, _ = project(tmp_path)

    async def scenario():
        app = create_app(root)
        assert (await get_json(app, "/api/projects/inconnu"))[0] == 404
        assert (await get_json(app, "/api/projects/..%2Fevil"))[0] == 404

    asyncio.run(scenario())


def test_api_repond_aux_gates_jusqu_au_bout(tmp_path):
    root, path = project(tmp_path)
    blocked = block_at_gate(path)
    assert blocked.pending_gate.id == "gate_clips"

    async def scenario():
        app = create_app(root)
        status, body = await call(
            app,
            "POST",
            f"/api/projects/demo/runs/{blocked.run_id}/gates/gate_clips",
            {"answer": "continue"},
        )
        assert status == 202, body

        gate = None
        for _ in range(200):
            status, run = await get_json(app, f"/api/projects/demo/runs/{blocked.run_id}")
            gate = run["run"].get("gate")
            if gate and gate["id"] == "gate_montage":
                break
            await asyncio.sleep(0.01)
        assert gate and gate["id"] == "gate_montage"

        status, body = await call(
            app,
            "POST",
            f"/api/projects/demo/runs/{blocked.run_id}/gates/gate_montage",
            {"answer": "continue"},
        )
        assert status == 202, body

        for _ in range(200):
            status, run = await get_json(app, f"/api/projects/demo/runs/{blocked.run_id}")
            if run["run"]["status"] == "completed":
                break
            await asyncio.sleep(0.01)
        assert run["run"]["status"] == "completed"
        assert any(a["hash"] for a in run["artifacts"])

    asyncio.run(scenario())


def test_summarize_expose_l_erreur_du_dernier_echec():
    from studio.api.service import summarize

    events = [
        {"run": "r", "type": "run_started", "workflow": "w"},
        {"run": "r", "type": "node_failed", "node": "treatment", "error": "cle absente"},
        {"run": "r", "type": "run_finished", "status": "failed"},
    ]

    run = summarize(events)[0]

    assert run["status"] == "failed"
    assert run["error"] == "cle absente"


def test_api_retry_avec_feedback_relance_l_etape(tmp_path):
    root, path = project(tmp_path)
    blocked = asyncio.run(
        make_executor(path).run(
            "studio:workflow/m0-conversation", {"discussion": "un phare, une voix"}
        )
    )
    assert blocked.pending_gate.id == "gate_brief"

    async def scenario():
        app = create_app(root)
        status, body = await call(
            app,
            "POST",
            f"/api/projects/demo/runs/{blocked.run_id}/gates/gate_brief",
            {"answer": "retry", "feedback": "plus sombre, moins de dialogue"},
        )
        assert status == 202, body

        retry = []
        for _ in range(200):
            status, journal = await get_json(
                app, "/api/projects/demo/journal"
            )
            retry = [
                e
                for e in journal["events"]
                if e["type"] == "gate_retry" and e.get("run") == blocked.run_id
            ]
            if retry:
                break
            await asyncio.sleep(0.01)
        assert retry[-1]["feedback"] == "plus sombre, moins de dialogue"

    asyncio.run(scenario())


def test_api_next_et_lancement_du_palier_suivant(tmp_path):
    root, path = project(tmp_path)
    done = asyncio.run(
        make_executor(path).run(
            "studio:workflow/m0-conversation",
            {"discussion": "un phare, une voix, un choix"},
            answers={"gate_brief": "continue", "gate_treatment": "continue"},
        )
    )
    assert done.status == "completed", done.error

    async def scenario():
        app = create_app(root)
        status, body = await get_json(app, "/api/projects/demo/next")
        assert status == 200
        nxt = body["next"]
        assert nxt["workflow"].endswith("m1-scene")
        assert "treatment" in nxt["inherited"]
        assert nxt["missing"] == ["reference"]

        status, raw = await call(
            app,
            "POST",
            "/api/projects/demo/runs",
            {"workflow": nxt["workflow"], "inputs": {}},
        )
        assert status == 400, raw

        status, raw = await call(
            app,
            "POST",
            "/api/projects/demo/runs",
            {"workflow": nxt["workflow"], "inputs": {"reference": "lande bretonne"}},
        )
        assert status == 202, raw
        body = json.loads(raw)
        run_id = body["run_id"]

        detail = None
        for _ in range(300):
            status, detail = await get_json(
                app, f"/api/projects/demo/runs/{run_id}"
            )
            if status == 200 and detail["run"]["status"] != "running":
                break
            await asyncio.sleep(0.01)
        assert detail["run"]["status"] == "blocked"
        assert detail["run"]["gate"]["id"] == "gate_images"

    asyncio.run(scenario())


def test_api_sert_l_ui_si_dist_existe(tmp_path):
    root, _ = project(tmp_path)
    dist = STUDIO_ROOT.parent / "web" / "dist"
    if not dist.is_dir():
        pytest.skip("web/dist absent (npm run build)")

    async def scenario():
        app = create_app(root)
        status, raw = await call(app, "GET", "/")
        assert status == 200
        assert b'<div id="app">' in raw

    asyncio.run(scenario())


def test_api_refuse_une_reponse_de_gate_invalide(tmp_path):
    root, path = project(tmp_path)
    blocked = block_at_gate(path)

    async def scenario():
        app = create_app(root)
        status, body = await call(
            app,
            "POST",
            f"/api/projects/demo/runs/{blocked.run_id}/gates/gate_clips",
            {"answer": "peut-etre"},
        )
        assert status == 400, body

    asyncio.run(scenario())
