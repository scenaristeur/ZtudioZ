"""Correction humaine a une gate : retry + feedback = nouvelle version."""

import asyncio
import json

from studio.kernel.artifacts import ArtifactStore
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import load_registry
from studio.tools.llm import MockProvider
from tests.helpers import catalog

AGENT = """
"@id": "studio:agent/writer"
type: studio:LlmAgent
name: Writer
version: 1
model: mock/echo
prompt: "Ecris."
inputs:
  feedback:
    type: string
outputs:
  draft:
    type: object
"""

WORKFLOW = """
"@id": "studio:workflow/feedback"
type: studio:Workflow
name: Feedback
version: 1
inputs:
  topic:
    type: string
    required: true
nodes:
  - id: draft
    agent: studio:agent/writer
  - id: gate_draft
    type: studio:HumanGate
    needs: [draft]
    question: Le texte est-il bon ?
    options: [continue, retry, stop]
"""


def writer(prompt):
    data = json.loads(prompt)
    return {"draft": {"text": data.get("feedback") or "v1"}}


def make(tmp_path):
    root = catalog(
        tmp_path,
        agents={"writer": AGENT},
        workflows={"feedback": WORKFLOW},
    )
    provider = MockProvider(responses={"writer": writer})
    executor = Executor(
        registry=load_registry(root),
        provider=provider,
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
    )
    return executor, provider


def test_un_retry_avec_feedback_produit_une_nouvelle_version(tmp_path):
    executor, provider = make(tmp_path)
    inputs = {"topic": "la lande"}

    first = asyncio.run(executor.run("studio:workflow/feedback", inputs))
    assert first.status == "blocked"
    assert first.pending_gate.id == "gate_draft"
    assert provider.calls == 1

    second = asyncio.run(
        executor.run(
            "studio:workflow/feedback",
            inputs,
            answers={"gate_draft": "retry"},
            feedback={"gate_draft": "plus sombre, moins de plans"},
        )
    )

    assert second.status == "blocked"
    assert second.pending_gate.id == "gate_draft"
    assert provider.calls == 2
    assert "plus sombre, moins de plans" in provider.records[-1]["prompt"]
    retry = [e for e in executor.journal.events() if e["type"] == "gate_retry"]
    assert retry[-1]["feedback"] == "plus sombre, moins de plans"

    third = asyncio.run(
        executor.run(
            "studio:workflow/feedback",
            inputs,
            answers={"gate_draft": "continue"},
        )
    )

    assert third.status == "completed"
    assert third.outputs["draft"]["text"] == "plus sombre, moins de plans"


def test_un_retry_sans_feedback_rejoue_sans_inventer_de_correction(tmp_path):
    executor, provider = make(tmp_path)
    inputs = {"topic": "la lande"}

    asyncio.run(executor.run("studio:workflow/feedback", inputs))
    second = asyncio.run(
        executor.run(
            "studio:workflow/feedback",
            inputs,
            answers={"gate_draft": "retry"},
        )
    )

    assert second.status == "blocked"
    assert provider.calls == 2
    assert "feedback" not in json.loads(provider.records[-1]["prompt"])
