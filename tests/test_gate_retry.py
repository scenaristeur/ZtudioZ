"""D8 — retry d'une gate : invalide l'amont, repose la question, ne rejoue pas tout."""

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
  discussion: {type: string, required: true}
nodes:
  - id: brief
    agent: studio:agent/echo
  - id: gate_brief
    type: studio:HumanGate
    needs: [brief]
    question: Le brief est-il bon ?
    options: [continue, retry, stop]
  - id: final
    agent: studio:agent/echo
    needs: [gate_brief]
"""


def make(tmp_path):
    root = catalog(tmp_path, agents={"echo": ECHO_AGENT}, workflows={"gated": GATED}, tools=ECHO_TOOLS)
    provider = MockProvider()
    executor = Executor(
        registry=load_registry(root),
        provider=provider,
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
    )
    return executor, provider


def test_retry_invalidates_upstream_and_reblocks(tmp_path):
    executor, provider = make(tmp_path)

    first = asyncio.run(executor.run("studio:workflow/gated", {"discussion": "d1"}))
    assert first.status == "blocked"
    assert provider.calls == 1

    # l'humain demande une reprise du brief
    second = asyncio.run(
        executor.run("studio:workflow/gated", {"discussion": "d1"}, answers={"gate_brief": "retry"})
    )
    assert second.status == "blocked"
    assert second.pending_gate.id == "gate_brief"
    assert provider.calls == 2  # brief rejoue, final toujours en attente

    events = executor.journal.events()
    invalidated = [e for e in events if e["type"] == "nodes_invalidated"]
    assert len(invalidated) == 1
    assert "brief" in invalidated[0]["nodes"]

    # et cette fois l'humain valide : final roule, brief ne repasse plus
    third = asyncio.run(
        executor.run("studio:workflow/gated", {"discussion": "d1"}, answers={"gate_brief": "continue"})
    )
    assert third.status == "completed"
    assert provider.calls == 3
    assert third.completed >= {"brief", "gate_brief", "final"}


def test_retry_does_not_rerun_grandparent_cached_inputs(tmp_path):
    # l'amont de l'amont (auci ici) : le run est identique, seuls les needs directs
    # sont invalides ; un second retry rejoue exactement d'un cran.
    executor, provider = make(tmp_path)

    asyncio.run(executor.run("studio:workflow/gated", {"discussion": "d1"}, answers={"gate_brief": "retry"}))
    calls_after_retry = provider.calls

    asyncio.run(executor.run("studio:workflow/gated", {"discussion": "d1"}, answers={"gate_brief": "retry"}))

    assert provider.calls == calls_after_retry + 1  # brief seul, encore une fois
