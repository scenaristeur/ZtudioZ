"""Garanties du graphe : ordre, niveaux paralleles, cycles, references mortes."""

import pytest

from studio.kernel.errors import GraphError, LoadError
from studio.kernel.registry import load_registry
from tests.helpers import ECHO_AGENT, ECHO_TOOLS, catalog

DIAMOND = """
"@id": "studio:workflow/diamond"
type: studio:Workflow
inputs:
  n: {type: int}
nodes:
  - id: a
    agent: studio:agent/echo
  - id: b
    agent: studio:agent/echo
    needs: [a]
  - id: c
    agent: studio:agent/echo
    needs: [a]
  - id: d
    agent: studio:agent/echo
    needs: [b, c]
"""


def build(tmp_path, workflow: str = DIAMOND):
    root = catalog(tmp_path, agents={"echo": ECHO_AGENT}, workflows={"diamond": workflow}, tools=ECHO_TOOLS)
    return load_registry(root)


def test_levels_group_independent_nodes(tmp_path):
    graph = build(tmp_path).graph("studio:workflow/diamond")

    assert graph.levels == [["a"], ["b", "c"], ["d"]]


def test_un_noeud_ne_peut_pas_declarer_inputs_ou_outputs(tmp_path):
    for term in ("inputs", "outputs"):
        workflow = DIAMOND.replace(
            "  - id: a\n    agent: studio:agent/echo",
            f"  - id: a\n    agent: studio:agent/echo\n    {term}:\n      brief: {{type: object}}",
        )
        with pytest.raises(LoadError, match=term):
            build(tmp_path, workflow).graph("studio:workflow/diamond")


def test_cycle_is_rejected(tmp_path):
    workflow = DIAMOND.replace("needs: [b, c]", "needs: [b, c, d]")

    with pytest.raises(GraphError, match="cycle"):
        build(tmp_path, workflow).graph("studio:workflow/diamond")


def test_unknown_dependency_is_rejected(tmp_path):
    workflow = DIAMOND.replace("needs: [a]", "needs: [ghost]", 1)

    with pytest.raises(GraphError, match="ghost"):
        build(tmp_path, workflow).graph("studio:workflow/diamond")


def test_unknown_agent_is_rejected(tmp_path):
    workflow = DIAMOND.replace("agent: studio:agent/echo", "agent: studio:agent/ghost", 1)

    with pytest.raises(GraphError, match="ghost"):
        build(tmp_path, workflow).graph("studio:workflow/diamond")


def test_duplicate_node_id_is_rejected(tmp_path):
    workflow = DIAMOND.replace("id: d", "id: a")

    with pytest.raises(GraphError, match="duplicate"):
        build(tmp_path, workflow).graph("studio:workflow/diamond")


def test_node_attributes_are_parsed(tmp_path):
    workflow = """
"@id": "studio:workflow/fan"
type: studio:Workflow
inputs:
  items: {type: list}
nodes:
  - id: shots
    agent: studio:agent/echo
    foreach: "${items}"
    parallel: 2
    on_item_failure: continue
  - id: review
    type: studio:HumanGate
    needs: [shots]
    question: "Les plans sont-ils bons ?"
    options: [continue, retry, stop]
"""
    graph = build(tmp_path, workflow).graph("studio:workflow/fan")

    shots = graph.node("shots")
    assert shots.foreach == "${items}"
    assert shots.parallel == 2
    assert shots.on_item_failure == "continue"

    review = graph.node("review")
    assert review.is_gate is True
    assert review.question == "Les plans sont-ils bons ?"
    assert review.options == ("continue", "retry", "stop")
