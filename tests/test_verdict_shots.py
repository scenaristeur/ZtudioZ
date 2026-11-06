"""M4 — le verdict compare chaque plan a son image et aux planches de la bible.

Le controleur recoit deja les pixels des plans et les planches (D23). Il rend
maintenant un jugement par plan : `index` (0-based, ordre des plans), `ok`,
`notes`, `issues`. Un plan fautif interdit `ok=true` au niveau global, et le
CLI peut proposer une correction ciblee `--feedback-item gate#index=...`.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

from studio import cli
from studio.kernel.artifacts import ArtifactStore
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import STUDIO_ROOT, load_registry
from studio.tools.images import MockImageProvider
from studio.tools.llm import MockProvider

SHOTS = {
    "shots": [
        {"id": 1, "prompt": "Plan large de la lande, lanterne contre feu follet."},
        {"id": 2, "prompt": "Plan rapproche, le masque beige du tourbier."},
    ]
}
VERDICT = {
    "ok": False,
    "notes": "Le deuxieme plan derive de la planche.",
    "shots": [
        {
            "index": 0,
            "ok": True,
            "notes": "conforme au plan et a la planche",
            "issues": [],
        },
        {
            "index": 1,
            "ok": False,
            "notes": "le masque beige manque",
            "issues": ["masque beige absent", "palette trop bleue"],
        },
    ],
}
INPUTS = {
    "treatment": "La lande, la lanterne, le masque.",
    "reference": "lande bretonne, nuit, lumiere bleue",
}


def schema():
    return json.loads(
        (STUDIO_ROOT / "catalog" / "schemas" / "verdict.schema.json").read_text()
    )


def test_le_prompt_exige_un_jugement_par_plan():
    agent = load_registry(STUDIO_ROOT).agent("studio:agent/continuity-checker")
    prompt = agent["prompt"]

    assert "shots" in prompt
    assert "index" in prompt
    assert "planche" in prompt
    assert "ok=false" in prompt.lower()
    assert int(agent["version"]) >= 10


def test_le_schema_accepte_un_verdict_par_plan():
    jsonschema.validate(VERDICT, schema())


def test_un_plan_fautif_interdit_ok_true():
    incoherent = {
        "ok": True,
        "notes": "tout va bien",
        "shots": [
            {"index": 0, "ok": False, "notes": "derive", "issues": ["masque absent"]}
        ],
    }

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(incoherent, schema())


def test_le_schema_refuse_un_index_negatif_ou_un_champ_inconnu():
    bad_index = {
        "ok": False,
        "notes": "x",
        "shots": [{"index": -1, "ok": False, "notes": "x"}],
    }
    bad_field = {
        "ok": False,
        "notes": "x",
        "shots": [{"index": 0, "ok": True, "notes": "x", "inconnu": 1}],
    }

    for value in (bad_index, bad_field):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(value, schema())


def run_m1(tmp_path, verdict=VERDICT):
    provider = MockProvider(
        responses={"shot-list": SHOTS, "continuity-checker": {"verdict": verdict}}
    )
    executor = Executor(
        registry=load_registry(STUDIO_ROOT),
        provider=provider,
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
        image=MockImageProvider(),
    )
    result = asyncio.run(
        executor.run(
            "studio:workflow/m1-scene",
            INPUTS,
            answers={"gate_images": "continue", "gate_verdict": "continue"},
        )
    )
    return result, executor, provider


def test_le_verdict_par_plan_est_produit_et_resume(tmp_path):
    result, executor, provider = run_m1(tmp_path)

    assert result.status == "completed", result.error
    jsonschema.validate(result.outputs["verdict"], schema())
    summary = cli._verdict_summary(executor, result.run_id)
    assert summary["shots"][1]["index"] == 1
    assert summary["shots"][1]["ok"] is False
    assert "masque beige absent" in summary["shots"][1]["issues"]

    sent = json.loads(
        next(
            r["prompt"]
            for r in provider.records
            if r["agent"].endswith("continuity-checker")
        )
    )
    assert sent["shots"] == SHOTS["shots"]


def test_la_guidance_globale_utilise_le_flag_feedback(tmp_path):
    project = SimpleNamespace(root=tmp_path / "demo")

    lines = cli._guidance(
        project,
        "run1",
        "gate_verdict",
        {"ok": False, "notes": "L'image 1 manque de convoi.", "violated": [], "shots": []},
    )
    joined = "\n".join(lines)

    assert "retry --feedback" in joined
    assert "retry \"<correction" not in joined


def test_la_guidance_propose_une_correction_ciblee_par_plan(tmp_path):
    project = SimpleNamespace(root=tmp_path / "demo")
    verdict = {
        "ok": False,
        "notes": "Le deuxieme plan derive.",
        "violated": [],
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

    lines = cli._guidance(project, "run1", "gate_verdict", verdict)
    joined = "\n".join(lines)

    assert "--feedback-item" in joined
    assert "gate_verdict#1=" in joined
    assert "gate_verdict#0=" not in joined
    assert "masque beige absent" in joined
    assert "continue" in joined
