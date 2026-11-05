"""Bout en bout M0 : conversation -> brief -> gate -> traitement, sans aucun reseau."""

import asyncio
import json
import subprocess
import sys

import jsonschema

from studio.kernel.artifacts import ArtifactStore
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import STUDIO_ROOT, load_registry
from studio.tools.llm import MockProvider

BRIEF = {
    "logline": "Un gardien de phare decouvre que la mer lui repond.",
    "synopsis": "Mael entend une voix dans la tempete et doit choisir de repondre.",
    "characters": ["Mael", "La voix"],
    "locations": ["Le phare", "La cale"],
    "open_questions": ["Que coute une reponse ?"],
}

RESPONSES = {
    "source-reader": {"notes": "Discussion initiale : phare, tempete, voix."},
    "brief-synthesizer": {"brief": BRIEF},
    "script-editor": {"treatment": "Sequence 1 : la lumiere tourne. Sequence 2 : la voix."},
}


def make(tmp_path):
    provider = MockProvider(responses=RESPONSES)
    executor = Executor(
        registry=load_registry(STUDIO_ROOT),
        provider=provider,
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
    )
    return executor, provider


def test_m0_produces_a_valid_brief_without_network(tmp_path):
    """Le workflow m0 produit un brief valide sans appel reseau."""
    executor, provider = make(tmp_path)

    result = asyncio.run(
        executor.run(
            "studio:workflow/m0-conversation",
            {"discussion": "On parle d'un phare et d'une voix dans la tempete."},
            answers={"gate_brief": "continue", "gate_treatment": "continue"},
        )
    )

    assert result.status == "completed"
    assert provider.calls == 3
    assert result.outputs["brief"] == BRIEF

    schema = json.loads((STUDIO_ROOT / "catalog" / "schemas" / "brief.schema.json").read_text())
    jsonschema.validate(result.outputs["brief"], schema)

    state = executor.journal.state()
    assert result.completed >= {
        "sources", "brief", "gate_brief", "treatment", "gate_treatment"
    }


def test_m0_without_gate_answer_stays_blocked(tmp_path):
    executor, provider = make(tmp_path)

    result = asyncio.run(
        executor.run(
            "studio:workflow/m0-conversation",
            {"discussion": "On parle d'un phare."},
        )
    )

    assert result.status == "blocked"
    assert result.pending_gate.id == "gate_brief"
    assert provider.calls == 2

    treatment = asyncio.run(
        executor.run(
            "studio:workflow/m0-conversation",
            {"discussion": "On parle d'un phare."},
            answers={"gate_brief": "continue"},
        )
    )
    assert treatment.status == "blocked"
    assert treatment.pending_gate.id == "gate_treatment"


def test_cli_validate_accepts_shipped_catalog(tmp_path):
    completed = subprocess.run(
        [sys.executable, "-m", "studio.cli", "validate"],
        cwd=STUDIO_ROOT.parent,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "m0-conversation" in completed.stdout
