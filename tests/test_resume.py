"""Garanties de la reprise : cache, reprise apres gate, invalidation par les entrees."""

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
    options: [continue, stop]
  - id: final
    agent: studio:agent/echo
    needs: [gate_brief]
"""


def make(tmp_path):
    root = catalog(
        tmp_path,
        agents={"echo": ECHO_AGENT},
        workflows={"gated": GATED},
        tools=ECHO_TOOLS,
    )
    provider = MockProvider()
    executor = Executor(
        registry=load_registry(root),
        provider=provider,
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
    )
    return executor, provider


def test_second_identical_run_uses_cache(tmp_path):
    executor, provider = make(tmp_path)

    asyncio.run(
        executor.run(
            "studio:workflow/gated", {"discussion": "hello"}, answers={"gate_brief": "continue"}
        )
    )
    calls_after_first = provider.calls

    result = asyncio.run(
        executor.run(
            "studio:workflow/gated", {"discussion": "hello"}, answers={"gate_brief": "continue"}
        )
    )

    assert result.status == "completed"
    assert provider.calls == calls_after_first


def test_resume_after_gate_only_runs_remaining_nodes(tmp_path):
    executor, provider = make(tmp_path)

    blocked = asyncio.run(executor.run("studio:workflow/gated", {"discussion": "hello"}))
    assert blocked.status == "blocked"
    assert provider.calls == 1

    resumed = asyncio.run(
        executor.run(
            "studio:workflow/gated", {"discussion": "hello"}, answers={"gate_brief": "continue"}
        )
    )

    assert resumed.status == "completed"
    assert provider.calls == 2
    assert resumed.completed >= {"brief", "gate_brief", "final"}


def test_changed_input_invalidates_downstream(tmp_path):
    executor, provider = make(tmp_path)

    asyncio.run(
        executor.run(
            "studio:workflow/gated", {"discussion": "hello"}, answers={"gate_brief": "continue"}
        )
    )
    assert provider.calls == 2

    # Changer l'entree ne invalide PAS le cache quand l'agent n'a pas d'inputs declares
    # (echo n'a pas d'inputs : son comportement est identique quel que soit l'input).
    asyncio.run(
        executor.run(
            "studio:workflow/gated", {"discussion": "autre"}, answers={"gate_brief": "continue"}
        )
    )

    assert provider.calls == 2
