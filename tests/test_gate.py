"""Garanties de la gate humaine : elle bloque, et ne se saute jamais silencieusement."""

import asyncio

from studio.kernel.artifacts import ArtifactStore
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import load_registry
from studio.tools.llm import MockProvider
from tests.helpers import ECHO_AGENT, ECHO_TOOLS, catalog

GATED = """
"@id": "studio:workflow/gated"
type: studio:Workflow
inputs:
  discussion: {type: string}
nodes:
  - id: brief
    agent: studio:agent/echo
  - id: gate_brief
    type: studio:HumanGate
    needs: [brief]
    question: "Le brief est-il bon ?"
    options: [continue, retry, stop]
  - id: final
    agent: studio:agent/echo
    needs: [gate_brief]
"""


def make(tmp_path, provider=None):
    root = catalog(
        tmp_path,
        agents={"echo": ECHO_AGENT},
        workflows={"gated": GATED},
        tools=ECHO_TOOLS,
    )
    provider = provider or MockProvider()
    executor = Executor(
        registry=load_registry(root),
        provider=provider,
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
    )
    return executor, provider


def test_gate_blocks_and_reports_question(tmp_path):
    executor, provider = make(tmp_path)

    result = asyncio.run(executor.run("studio:workflow/gated", {"discussion": "hello"}))

    assert result.status == "blocked"
    assert result.pending_gate is not None
    assert result.pending_gate.id == "gate_brief"
    assert result.pending_gate.question == "Le brief est-il bon ?"
    assert result.pending_gate.options == ("continue", "retry", "stop")
    assert provider.calls == 1


def test_gate_continue_runs_downstream(tmp_path):
    executor, provider = make(tmp_path)

    result = asyncio.run(
        executor.run(
            "studio:workflow/gated",
            {"discussion": "hello"},
            answers={"gate_brief": "continue"},
        )
    )

    assert result.status == "completed"
    assert provider.calls == 2
    assert "gate_brief" in result.completed


def test_gate_stop_halts_downstream(tmp_path):
    executor, provider = make(tmp_path)

    result = asyncio.run(
        executor.run(
            "studio:workflow/gated",
            {"discussion": "hello"},
            answers={"gate_brief": "stop"},
        )
    )

    assert result.status == "stopped"
    assert provider.calls == 1
    assert "final" not in result.completed
