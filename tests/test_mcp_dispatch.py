"""D19 — dispatch MCP : le kind mcp du registre est cable, contre un serveur jouet."""

import asyncio
import sys

import pytest

from studio.kernel.artifacts import ArtifactStore
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import load_registry
from studio.tools.llm import MockProvider
from tests.helpers import catalog

SERVER = """
import json

from mcp.server.mcpserver import MCPServer

server = MCPServer("toy")

@server.tool()
def echo(text: str) -> str:
    return f"echo: {text}"

@server.tool()
def report(text: str) -> str:
    return json.dumps({"ok": True, "notes": text})

if __name__ == "__main__":
    server.run(transport="stdio")
"""

TOOLS = """
tools:
  toy.echo:
    kind: mcp
    server: toy
    tool: echo
    arguments:
      text: "${message}"
  toy.report:
    kind: mcp
    server: toy
    tool: report
    arguments:
      text: "${message}"
"""

TOY_AGENT_TEXT = """
"@id": "studio:agent/toy"
type: studio:ToolAgent
name: Toy
version: 1
tools: [toy.echo]
inputs:
  message:
    type: string
    required: true
outputs:
  say:
    type: object
"""

TOY_AGENT_JSON = """
"@id": "studio:agent/toy"
type: studio:ToolAgent
name: Toy
version: 1
tools: [toy.report]
inputs:
  message:
    type: string
    required: true
outputs:
  report:
    type: object
"""

WF = """
"@id": "studio:workflow/toy"
type: studio:Workflow
inputs:
  message: {type: string, required: true}
nodes:
  - id: line
    agent: studio:agent/toy
"""


@pytest.fixture(scope="module")
def server_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("toy") / "toy_server.py"
    path.write_text(SERVER.strip(), encoding="utf-8")
    return path


def make(tmp_path, server_path, agent, servers_body: str):
    root = catalog(
        tmp_path,
        agents={"toy": agent},
        workflows={"toy": WF},
        tools=TOOLS + servers_body,
    )
    executor = Executor(
        registry=load_registry(root),
        provider=MockProvider(),
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
    )
    return executor


def good_servers(server_path):
    return f"""
servers:
  toy:
    command: "{sys.executable}"
    args: ["{server_path}"]
"""


def test_mcp_tool_text_result(tmp_path, server_path):
    executor = make(tmp_path, server_path, TOY_AGENT_TEXT, good_servers(server_path))

    result = asyncio.run(executor.run("studio:workflow/toy", {"message": "salut"}))

    assert result.status == "completed", result.error
    assert result.outputs["say"]["text"] == "echo: salut"


def test_mcp_tool_json_result_is_parsed(tmp_path, server_path):
    executor = make(tmp_path, server_path, TOY_AGENT_JSON, good_servers(server_path))

    result = asyncio.run(executor.run("studio:workflow/toy", {"message": "x"}))

    assert result.status == "completed", result.error
    assert result.outputs["report"] == {"ok": True, "notes": "x"}


def test_mcp_cache_hit_on_second_run(tmp_path, server_path):
    executor = make(tmp_path, server_path, TOY_AGENT_TEXT, good_servers(server_path))

    asyncio.run(executor.run("studio:workflow/toy", {"message": "salut"}))
    produced = len([e for e in executor.journal.events() if e["type"] == "node_finished"])
    asyncio.run(executor.run("studio:workflow/toy", {"message": "salut"}))
    after = len([e for e in executor.journal.events() if e["type"] == "node_finished"])

    assert after == produced  # le serveur jouet n'a pas ete rerappelle


def test_mcp_unknown_server_is_a_clear_failure(tmp_path, server_path):
    executor = make(
        tmp_path, server_path, TOY_AGENT_TEXT,
        """
servers:
  autre:
    command: "echo"
    args: []
""",
    )

    result = asyncio.run(executor.run("studio:workflow/toy", {"message": "salut"}))

    assert result.status == "failed"
    assert "inconnu" in (result.error or "").lower()
