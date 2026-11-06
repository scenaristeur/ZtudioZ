"""Reponse de gate ciblee par item via l'API (memes garanties que le CLI)."""

import asyncio
import json

import pytest

from studio.api.app import create_app, feedback_from_body
from studio.kernel.artifacts import ArtifactStore
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import STUDIO_ROOT, load_registry
from studio.tools.images import MockImageProvider
from studio.tools.llm import MockProvider
from tests.helpers import write

SHOTS = {
    "shots": [
        {"id": 1, "prompt": "Plan large de la lande."},
        {"id": 2, "prompt": "Plan rapproche du masque beige."},
    ]
}
VERDICT = {
    "ok": False,
    "notes": "Le plan 2 derive.",
    "shots": [
        {"index": 0, "ok": True, "notes": "conforme", "issues": []},
        {
            "index": 1,
            "ok": False,
            "notes": "masque absent",
            "issues": ["masque beige absent"],
        },
    ],
}
INPUTS = {
    "treatment": "La lande, la lanterne, le masque.",
    "reference": "lande bretonne, nuit, lumiere bleue",
}


def make_project(tmp_path):
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


def make_executor(path, provider):
    return Executor(
        registry=load_registry(STUDIO_ROOT),
        provider=provider,
        store=ArtifactStore(path / "artifacts"),
        journal=Journal(path / "journal.jsonl"),
        image=MockImageProvider(),
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
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(
        m.get("body", b"") for m in messages if m["type"] == "http.response.body"
    )
    if not body:
        return status, None
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body.decode()


def test_feedback_from_body_global_et_cible():
    assert feedback_from_body({}) == ""
    assert feedback_from_body({"feedback": "plus sombre"}) == "plus sombre"
    assert feedback_from_body({"feedback_items": {"1": "masque beige"}}) == {
        1: "masque beige"
    }


def test_feedback_from_body_refuse_le_menage_et_les_index_invalides():
    with pytest.raises(ValueError, match="melanges"):
        feedback_from_body(
            {"feedback": "global", "feedback_items": {"1": "cible"}}
        )
    for body in (
        {"feedback_items": {"x": "texte"}},
        {"feedback_items": {"-1": "texte"}},
        {"feedback_items": {"1": "  "}},
        {"feedback_items": ["texte"]},
    ):
        with pytest.raises(ValueError):
            feedback_from_body(body)


def test_api_retry_cible_rejoue_les_plans_vises(tmp_path):
    root, path = make_project(tmp_path)
    provider = MockProvider(
        responses={"shot-list": SHOTS, "continuity-checker": {"verdict": VERDICT}}
    )
    blocked = asyncio.run(
        make_executor(path, provider).run(
            "studio:workflow/m1-scene", INPUTS, answers={"gate_images": "continue"}
        )
    )
    assert blocked.pending_gate.id == "gate_verdict"

    async def scenario():
        app = create_app(root)
        status, body = await call(
            app,
            "POST",
            f"/api/projects/demo/runs/{blocked.run_id}/gates/gate_verdict",
            {"answer": "retry", "feedback_items": {"1": "masque beige absent"}},
        )
        assert status == 202, body

        retry = []
        for _ in range(200):
            events = Journal(path / "journal.jsonl").events()
            retry = [
                event
                for event in events
                if event["type"] == "gate_retry"
                and event.get("run") == blocked.run_id
            ]
            if retry:
                break
            await asyncio.sleep(0.01)
        assert retry[-1]["feedback"] == {"1": "masque beige absent"}

    asyncio.run(scenario())


def test_api_refuse_le_feedback_cible_invalide(tmp_path):
    root, path = make_project(tmp_path)
    provider = MockProvider(
        responses={"shot-list": SHOTS, "continuity-checker": {"verdict": VERDICT}}
    )
    blocked = asyncio.run(
        make_executor(path, provider).run(
            "studio:workflow/m1-scene", INPUTS, answers={"gate_images": "continue"}
        )
    )

    async def scenario():
        app = create_app(root)
        status, body = await call(
            app,
            "POST",
            f"/api/projects/demo/runs/{blocked.run_id}/gates/gate_verdict",
            {"answer": "retry", "feedback_items": {"x": "texte"}},
        )
        assert status == 400, body
        assert "index invalide" in str(body)

    asyncio.run(scenario())
