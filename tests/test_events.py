"""D9 — node_started et budget_refused sont emis ; D18 — foreach alias ses items."""

import asyncio

from studio.kernel.artifacts import ArtifactStore
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import load_registry
from studio.tools.llm import MockProvider
from tests.helpers import ECHO_AGENT, ECHO_TOOLS, catalog

WF = """
"@id": "studio:workflow/w"
type: studio:Workflow
nodes:
  - id: solo
    agent: studio:agent/echo
"""

ALIAS_WF = """
"@id": "studio:workflow/aliased"
type: studio:Workflow
inputs:
  items: {type: list, required: true}
nodes:
  - id: shots
    agent: studio:agent/namer
    foreach: "${items}"
    as: shot
"""

NAMER = """
"@id": "studio:agent/namer"
type: studio:LlmAgent
name: Nommeur
version: 1
model: mock/echo
prompt: "Nomme le plan."
inputs:
  shot:
    type: string
    required: true
outputs:
  name:
    type: string
"""


def make(tmp_path, workflow, name, agents=None):
    root = catalog(tmp_path, agents=agents or {"echo": ECHO_AGENT}, workflows={name: workflow}, tools=ECHO_TOOLS)
    provider = MockProvider()
    executor = Executor(
        registry=load_registry(root),
        provider=provider,
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
    )
    return executor, provider


def test_node_started_is_emitted(tmp_path):
    executor, _ = make(tmp_path, WF, "w")

    asyncio.run(executor.run("studio:workflow/w"))

    kinds = [e["type"] for e in executor.journal.events()]
    assert "node_started" in kinds
    started = [e for e in executor.journal.events() if e["type"] == "node_started"]
    assert started[0]["node"] == "solo"


def test_budget_refused_is_journaled(tmp_path):
    agents = {"echo": ECHO_AGENT + "budget: 0.01\n"}
    root = catalog(tmp_path, agents=agents, workflows={"w": WF}, tools=ECHO_TOOLS)
    provider = MockProvider(cost=0.05)
    executor = Executor(
        registry=load_registry(root),
        provider=provider,
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
    )

    result = asyncio.run(executor.run("studio:workflow/w"))

    assert result.status == "failed"
    assert provider.calls == 0
    events = [e["type"] for e in executor.journal.events()]
    assert "budget_refused" in events


def test_foreach_alias_names_the_item(tmp_path):
    executor, provider = make(tmp_path, ALIAS_WF, "aliased", agents={"namer": NAMER})

    result = asyncio.run(executor.run("studio:workflow/aliased", {"items": ["phare", "mer"]}))

    assert result.status == "completed"
    assert provider.calls == 2
    # l'entree resolue porte le nom declare, pas 'item'
    assert '"shot"' in provider.records[0]["prompt"]
