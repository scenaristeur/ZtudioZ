"""D26 — les contraintes freeform du brief deviennent verifiables.

Le controleur voit les images (D23). Il doit rendre une entree par contrainte
`freeform`, avec `respected` et une preuve visuelle. Le schema interdit de
declarer `ok=true` si une contrainte est marquee non respectee.
"""

import asyncio
import json

import jsonschema
import pytest

from studio.kernel.artifacts import ArtifactStore
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import STUDIO_ROOT, load_registry
from studio.tools.images import MockImageProvider
from studio.tools.llm import MockProvider

SHOTS = {
    "shots": [
        {"id": 1, "prompt": "Plan large de la lande, lanterne contre feu follet."},
        {"id": 2, "prompt": "Plan rapproche, la lumiere se fond dans la brume."},
    ]
}
FREEFORM = ["pas de dialogue", "fin ambigue"]
BRIEF = {"constraints": {"max_shots": 2, "freeform": FREEFORM}}
VERDICT = {
    "ok": False,
    "notes": "La fin est trop explicite sur la seconde image.",
    "constraints": [
        {"text": "pas de dialogue", "respected": True, "evidence": "aucun texte visible"},
        {
            "text": "fin ambigue",
            "respected": False,
            "evidence": "l'image 2 montre un visage souriant et net",
        },
    ],
}
INPUTS = {
    "treatment": "La lande, la lanterne, la brume.",
    "reference": "lande bretonne, nuit, lumiere bleue",
    "brief": BRIEF,
}


def schema():
    return json.loads(
        (STUDIO_ROOT / "catalog" / "schemas" / "verdict.schema.json").read_text()
    )


def test_le_prompt_exige_une_reponse_par_contrainte_freeform():
    agent = load_registry(STUDIO_ROOT).agent("studio:agent/continuity-checker")

    assert "freeform" in agent["prompt"]
    assert "respected" in agent["prompt"]
    assert "evidence" in agent["prompt"]
    assert "ok=false" in agent["prompt"].lower()


def test_le_schema_accepte_une_contrainte_structuree():
    jsonschema.validate(VERDICT, schema())


def test_le_schema_refuse_ok_true_avec_une_contrainte_violee():
    incoherent = {
        "ok": True,
        "notes": "tout va bien",
        "constraints": [
            {"text": "pas de dialogue", "respected": False, "evidence": "du texte"}
        ],
    }

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(incoherent, schema())


def test_le_verdict_structure_est_produit_et_valide(tmp_path):
    provider = MockProvider(
        responses={"shot-list": SHOTS, "continuity-checker": {"verdict": VERDICT}}
    )
    executor = Executor(
        registry=load_registry(STUDIO_ROOT),
        provider=provider,
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
        image=MockImageProvider(),
    )

    result = asyncio.run(
        executor.run("studio:workflow/m1-scene", INPUTS, answers={"gate_images": "continue"})
    )

    assert result.status == "completed", result.error
    jsonschema.validate(result.outputs["verdict"], schema())
    assert [c["text"] for c in result.outputs["verdict"]["constraints"]] == FREEFORM
    assert result.outputs["verdict"]["ok"] is False
