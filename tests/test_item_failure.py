"""D4 — on_item_failure: continue. La panne d'un item ne fait pas echouer le run."""

import asyncio

from studio.kernel.artifacts import ArtifactStore
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import load_registry
from studio.tools.llm import MockProvider
from tests.helpers import ECHO_AGENT, ECHO_TOOLS, catalog

WF = """
"@id": "studio:workflow/fan"
type: studio:Workflow
inputs:
  items: {type: list, required: true}
nodes:
  - id: work
    agent: studio:agent/echo
    foreach: "${items}"
    parallel: 2
    on_item_failure: continue
"""

WF_STRICT = """
"@id": "studio:workflow/fan"
type: studio:Workflow
inputs:
  items: {type: list, required: true}
nodes:
  - id: work
    agent: studio:agent/echo
    foreach: "${items}"
"""


def boom(prompt: str) -> dict:
    """Le mock explose si l'item 2 passe dans le prompt."""
    if "item-2" in prompt:
        raise RuntimeError("item maudit")
    return {"text": "ok"}


ECHO_ITEM = ECHO_AGENT + """
inputs:
  item:
    type: string
    required: true
outputs:
  text:
    type: string
"""


def make(tmp_path, workflow):
    root = catalog(tmp_path, agents={"echo": ECHO_ITEM}, workflows={"fan": workflow}, tools=ECHO_TOOLS)
    provider = MockProvider(default=boom)
    executor = Executor(
        registry=load_registry(root),
        provider=provider,
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
    )
    return executor, provider


def test_failed_item_does_not_stop_the_run(tmp_path):
    executor, provider = make(tmp_path, WF)

    result = asyncio.run(executor.run("studio:workflow/fan", {"items": ["item-0", "item-1", "item-2"]}))

    assert result.status == "completed"
    assert provider.calls == 3
    assert result.outputs["text"] == ["ok", "ok"]  # l'aval recoit les items sains
    outcome = executor.store.get(executor.journal.state().artifacts["work"])
    assert outcome == [{"text": "ok"}, {"text": "ok"}]  # l'item en panne est retire


def test_item_failure_is_journaled(tmp_path):
    executor, _ = make(tmp_path, WF)

    asyncio.run(executor.run("studio:workflow/fan", {"items": ["item-0", "item-2"]}))

    events = [e for e in executor.journal.events() if e["type"] == "item_failed"]
    assert len(events) == 1
    assert events[0]["node"] == "work#1"
    assert "item maudit" in events[0]["error"]


def test_without_the_flag_a_failed_item_still_fails_the_node(tmp_path):
    executor, _ = make(tmp_path, WF_STRICT)

    result = asyncio.run(executor.run("studio:workflow/fan", {"items": ["item-0", "item-2"]}))

    assert result.status == "failed"
    assert "item maudit" in (result.error or "")
