"""Garanties du journal : append-only, sequence stable, etat reconstructible."""

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
    journal = Journal(tmp_path / "journal.jsonl")
    executor = Executor(
        registry=load_registry(root),
        provider=MockProvider(),
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=journal,
    )
    return executor, journal


def run(executor):
    return asyncio.run(
        executor.run(
            "studio:workflow/gated",
            {"discussion": "hello"},
            answers={"gate_brief": "continue"},
        )
    )


def test_events_are_well_formed_and_ordered(tmp_path):
    executor, journal = make(tmp_path)
    run(executor)

    events = journal.events()

    assert events
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    for event in events:
        assert {"seq", "ts", "type"} <= set(event)


def test_journal_is_append_only(tmp_path):
    executor, journal = make(tmp_path)
    run(executor)
    before = (tmp_path / "journal.jsonl").read_bytes()

    run(executor)
    after = (tmp_path / "journal.jsonl").read_bytes()

    assert after.startswith(before)
    assert len(after) > len(before)


def test_state_is_reconstructed_from_journal(tmp_path):
    executor, journal = make(tmp_path)
    result = run(executor)

    state = journal.state()

    assert state.completed == result.completed
    assert state.status == "completed"
    assert state.gate_answers == {"gate_brief": "continue"}
    assert set(state.artifacts) == result.completed
