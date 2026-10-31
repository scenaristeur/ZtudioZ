"""D5 — sous-workflow : un noeud dont l'agent est un workflow, rejoue en cache partage."""

import asyncio

from studio.kernel.artifacts import ArtifactStore
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import load_registry
from studio.tools.llm import MockProvider
from tests.helpers import ECHO_AGENT, ECHO_TOOLS, catalog

INNER_CLEAN = """
"@id": "studio:workflow/inner"
type: studio:Workflow
inputs:
  text:
    type: string
    required: true
nodes:
  - id: a
    agent: studio:agent/echo
  - id: b
    agent: studio:agent/echo
    needs: [a]
"""

INNER_GATED = """
"@id": "studio:workflow/inner"
type: studio:Workflow
inputs:
  text:
    type: string
    required: true
nodes:
  - id: a
    agent: studio:agent/echo
  - id: gate_x
    type: studio:HumanGate
    needs: [a]
    question: Valider l'interne ?
    options: [continue, stop]
"""

OUTER = """
"@id": "studio:workflow/outer"
type: studio:Workflow
inputs:
  text:
    type: string
    required: true
nodes:
  - id: render
    agent: studio:workflow/inner
"""


def make(tmp_path, inner: str):
    root = catalog(tmp_path, agents={"echo": ECHO_AGENT}, workflows={"inner": inner, "outer": OUTER}, tools=ECHO_TOOLS)
    provider = MockProvider()
    executor = Executor(
        registry=load_registry(root),
        provider=provider,
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
    )
    return executor, provider


def test_node_agent_can_be_a_workflow(tmp_path):
    executor, provider = make(tmp_path, INNER_CLEAN)

    result = asyncio.run(executor.run("studio:workflow/outer", {"text": "hello"}))

    assert result.status == "completed", result.error
    assert provider.calls == 2
    assert "render" in result.completed


def test_second_outer_run_is_a_cache_hit(tmp_path):
    executor, provider = make(tmp_path, INNER_CLEAN)

    asyncio.run(executor.run("studio:workflow/outer", {"text": "hello"}))
    result = asyncio.run(executor.run("studio:workflow/outer", {"text": "hello"}))

    assert result.status == "completed"
    assert provider.calls == 2  # l'interne n'a pas ete re-execute


def test_inner_gate_blocks_the_outer_run_with_a_prefixed_gate(tmp_path):
    executor, provider = make(tmp_path, INNER_GATED)

    first = asyncio.run(executor.run("studio:workflow/outer", {"text": "hello"}))

    assert first.status == "blocked"
    assert first.pending_gate.id == "render/gate_x"
    assert provider.calls == 1

    resumed = asyncio.run(
        executor.run(
            "studio:workflow/outer",
            {"text": "hello"},
            answers={"render/gate_x": "continue"},
        )
    )

    assert resumed.status == "completed", resumed.error
    assert provider.calls == 1  # 'a' etait en cache, la gate ne le repaye pas
