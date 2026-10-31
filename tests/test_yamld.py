"""Garanties du chargement YAML-LD : expansion, unicite, references vivantes."""

import pytest

from studio.kernel.errors import LoadError
from studio.kernel.registry import load_registry
from tests.helpers import ECHO_AGENT, ECHO_TOOLS, catalog


def test_context_expands_ids_and_types(tmp_path):
    root = catalog(tmp_path, agents={"echo": ECHO_AGENT}, tools=ECHO_TOOLS)
    registry = load_registry(root)

    agent = registry.agent("studio:agent/echo")

    assert agent["@id"] == "https://ztudioz.dev/ns/studio#agent/echo"
    assert agent["@type"] == "https://ztudioz.dev/ns/studio#LlmAgent"
    assert agent["version"] == 1
    assert agent["name"] == "Echo"


def test_term_not_declared_in_context_is_rejected(tmp_path):
    root = catalog(tmp_path, agents={"echo": ECHO_AGENT + "foo: bar\n"}, tools=ECHO_TOOLS)

    with pytest.raises(LoadError, match="foo"):
        load_registry(root)


def test_duplicate_id_is_rejected(tmp_path):
    root = catalog(
        tmp_path,
        agents={"echo": ECHO_AGENT, "echo_bis": ECHO_AGENT},
        tools=ECHO_TOOLS,
    )

    with pytest.raises(LoadError, match="duplicate"):
        load_registry(root)


def test_unknown_tool_reference_is_rejected(tmp_path):
    body = ECHO_AGENT + "tools: [nope.nope]\n"
    root = catalog(tmp_path, agents={"echo": body}, tools=ECHO_TOOLS)

    with pytest.raises(LoadError, match="nope.nope"):
        load_registry(root)


def test_unknown_skill_reference_is_rejected(tmp_path):
    body = ECHO_AGENT + "skills: [missing-pack]\n"
    root = catalog(tmp_path, agents={"echo": body}, tools=ECHO_TOOLS)

    with pytest.raises(LoadError, match="missing-pack"):
        load_registry(root)


def test_declared_skill_is_resolved(tmp_path):
    body = ECHO_AGENT + "skills: [scenario-structure]\n"
    root = catalog(
        tmp_path,
        agents={"echo": body},
        tools=ECHO_TOOLS,
        skills={"scenario-structure": "# Structure\nLogline puis synopsis."},
    )

    registry = load_registry(root)

    assert "Logline" in registry.skill("scenario-structure")


def test_shipped_catalog_loads(tmp_path):
    from studio.kernel.registry import STUDIO_ROOT

    registry = load_registry(STUDIO_ROOT)

    assert registry.agent("studio:agent/brief-synthesizer")["version"] >= 1
    assert registry.workflow("studio:workflow/m0-conversation")["@id"].endswith(
        "workflow/m0-conversation"
    )
