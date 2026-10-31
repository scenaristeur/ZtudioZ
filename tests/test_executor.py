"""Garanties du moteur : concurrence reelle, fan-out borne, budget refuse avant l'appel."""

import asyncio

from studio.kernel.artifacts import ArtifactStore
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import load_registry
from studio.tools.llm import MockProvider
from tests.helpers import ECHO_AGENT, ECHO_TOOLS, catalog

WIDE = """
"@id": "studio:workflow/wide"
type: studio:Workflow
nodes:
  - id: a
    agent: studio:agent/echo
  - id: b
    agent: studio:agent/echo
  - id: c
    agent: studio:agent/echo
  - id: d
    agent: studio:agent/echo
    needs: [a, b, c]
"""

FAN = """
"@id": "studio:workflow/fan"
type: studio:Workflow
inputs:
  items: {type: list}
nodes:
  - id: shots
    agent: studio:agent/echo
    foreach: "${items}"
    parallel: 2
"""


def make(tmp_path, workflow: str, name: str, provider=None, agents=None):
    root = catalog(
        tmp_path,
        agents=agents or {"echo": ECHO_AGENT},
        workflows={name: workflow},
        tools=ECHO_TOOLS,
    )
    registry = load_registry(root)
    provider = provider or MockProvider()
    executor = Executor(
        registry=registry,
        provider=provider,
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
    )
    return executor, provider


def test_independent_nodes_run_concurrently(tmp_path):
    executor, provider = make(tmp_path, WIDE, "wide", MockProvider(delay=0.03))

    result = asyncio.run(executor.run("studio:workflow/wide"))

    assert result.status == "completed"
    assert provider.max_concurrency == 3


def test_foreach_respects_parallel_bound(tmp_path):
    executor, provider = make(tmp_path, FAN, "fan", MockProvider(delay=0.02))

    result = asyncio.run(executor.run("studio:workflow/fan", {"items": [1, 2, 3, 4]}))

    assert result.status == "completed"
    assert provider.calls == 4
    assert provider.max_concurrency == 2


def test_budget_is_refused_before_any_call(tmp_path):
    agents = {"echo": ECHO_AGENT + "budget: 0.01\n"}
    executor, provider = make(tmp_path, WIDE, "wide", MockProvider(cost=0.02), agents=agents)

    result = asyncio.run(executor.run("studio:workflow/wide"))

    assert result.status == "failed"
    assert "budget" in (result.error or "").lower()
    assert provider.calls == 0


def test_missing_required_input_fails_before_any_call(tmp_path):
    agents = {
        "echo": ECHO_AGENT
        + """
inputs:
  discussion: {type: string, required: true}
"""
    }
    workflow = WIDE.replace("  - id: a\n    agent: studio:agent/echo\n", "  - id: a\n    agent: studio:agent/echo\n", 1)
    executor, provider = make(tmp_path, workflow, "wide", agents=agents)

    result = asyncio.run(executor.run("studio:workflow/wide"))

    assert result.status == "failed"
    assert "discussion" in (result.error or "")
    assert provider.calls == 0
