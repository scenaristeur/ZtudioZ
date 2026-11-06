"""Retry cible : les items non vises gardent leur version, meme apres un feedback global.

Un retry global applique le feedback a tout le fan-out : les items sont alors
memorises avec ce feedback. Un retry cible ulterieur ne doit pas recalculer les
items non vises sans ce feedback (sinon ils sont regeneres et perdent la
correction precedente). Le moteur doit reutiliser leur artefact tel quel.
"""

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
  item:
    type: string
    required: true
  feedback:
    type: string
outputs:
  draft:
    type: object
"""

WORKFLOW = """
"@id": "studio:workflow/targeted"
type: studio:Workflow
name: Targeted
version: 1
inputs:
  items:
    type: list
    required: true
nodes:
  - id: drafts
    agent: studio:agent/writer
    foreach: "${items}"
    as: item
  - id: gate_drafts
    type: studio:HumanGate
    needs: [drafts]
    question: Les textes sont-ils bons ?
    options: [continue, retry, stop]
"""


def writer(prompt):
    data = json.loads(prompt)
    return {"draft": {"text": f"{data['item']}|{data.get('feedback', '')}"}}


def make(tmp_path):
    root = catalog(tmp_path, agents={"writer": AGENT}, workflows={"targeted": WORKFLOW})
    provider = MockProvider(responses={"writer": writer})
    executor = Executor(
        registry=load_registry(root),
        provider=provider,
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
    )
    return executor, provider


def drafts(executor, run_id):
    state = executor.journal.state(run_id)
    return executor.store.get(state.artifacts["drafts"])


def test_un_retry_cible_ne_rejoue_pas_les_items_non_vises(tmp_path):
    executor, provider = make(tmp_path)
    inputs = {"items": ["a", "b"]}

    # 1. un feedback global corrige les deux items
    first = asyncio.run(
        executor.run(
            "studio:workflow/targeted",
            inputs,
            answers={"gate_drafts": "retry"},
            feedback={"gate_drafts": "G"},
        )
    )
    assert first.status == "blocked"
    assert provider.calls == 4  # 2 items, puis 2 items avec le feedback global
    assert [item["draft"]["text"] for item in drafts(executor, first.run_id)] == [
        "a|G",
        "b|G",
    ]

    # 2. un retry cible ne corrige que l'item 0 ; l'item 1 garde "G"
    second = asyncio.run(
        executor.run(
            "studio:workflow/targeted",
            inputs,
            answers={"gate_drafts": "retry"},
            feedback={"gate_drafts": {0: "T"}},
        )
    )
    assert second.status == "blocked"
    assert provider.calls == 5  # seul l'item 0 est rappele
    assert [item["draft"]["text"] for item in drafts(executor, second.run_id)] == [
        "a|T",
        "b|G",
    ]

    skipped = [
        event
        for event in executor.journal.events()
        if event.get("type") == "node_skipped"
        and event.get("node") == "drafts#1"
    ]
    assert skipped and skipped[-1]["reason"] == "retry"


def test_un_retry_cible_survit_a_un_redemarrage(tmp_path):
    executor, provider = make(tmp_path)
    inputs = {"items": ["a", "b"]}

    first = asyncio.run(
        executor.run(
            "studio:workflow/targeted",
            inputs,
            answers={"gate_drafts": "retry"},
            feedback={"gate_drafts": "G"},
        )
    )
    assert first.status == "blocked"
    assert provider.calls == 4

    # Nouveau processus : l'etat vient du journal, pas de la memoire du premier.
    restarted, restarted_provider = make(tmp_path)
    second = asyncio.run(
        restarted.run(
            "studio:workflow/targeted",
            inputs,
            answers={"gate_drafts": "retry"},
            feedback={"gate_drafts": {0: "T"}},
        )
    )

    assert second.status == "blocked"
    assert restarted_provider.calls == 1  # seul l'item 0 est rappele
    assert [item["draft"]["text"] for item in drafts(restarted, second.run_id)] == [
        "a|T",
        "b|G",
    ]
