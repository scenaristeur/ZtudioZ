"""ToolAgent et dispatch : kind python (ffmpeg), cli, mcp non cable, budget, schema, cache."""

import asyncio
import json
import subprocess

import pytest

from studio.kernel.artifacts import ArtifactStore, Binary
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import load_registry
from studio.tools.llm import MockProvider
from tests.helpers import ECHO_TOOLS, catalog

MEDIA_TOOLS = """
tools:
  media.clip:
    kind: python
    impl: studio.tools.media:clip
    cost: 0.01
  media.montage:
    kind: python
    impl: studio.tools.media:montage
  media.probe:
    kind: python
    impl: studio.tools.media:probe
"""

MCP_TOOLS = """
tools:
  vision.describe:
    kind: mcp
    server: character-designer
    tool: describe_sheet
"""

CLI_TOOLS = """
tools:
  say.echo:
    kind: cli
    command: ["echo", "salut ${message}"]
"""

CLIPPER = """
"@id": "studio:agent/clipper"
type: studio:ToolAgent
name: Fabricant de clips
version: 1
tools: [media.clip]
inputs:
  color:
    type: string
    required: true
  duration:
    type: number
    required: true
outputs:
  clip:
    type: object
"""

ONE_CLIP = """
"@id": "studio:workflow/oneclip"
type: studio:Workflow
inputs:
  color: {type: string, required: true}
  duration: {type: number, required: true}
nodes:
  - id: clip
    agent: studio:agent/clipper
"""

SAYER = """
"@id": "studio:agent/sayer"
type: studio:ToolAgent
name: Diseur
version: 1
tools: [say.echo]
inputs:
  message:
    type: string
    required: true
outputs:
  stdout:
    type: string
"""

SAY_WF = """
"@id": "studio:workflow/say"
type: studio:Workflow
inputs:
  message: {type: string, required: true}
nodes:
  - id: line
    agent: studio:agent/sayer
"""

SEEKER = """
"@id": "studio:agent/seeker"
type: studio:ToolAgent
name: Chercheur
version: 1
tools: [vision.describe]
inputs:
  sheet:
    type: string
    required: true
outputs:
  description:
    type: string
"""


def make(tmp_path, workflow, name, agents, tools, provider=None, schemas=None):
    root = catalog(
        tmp_path, agents=agents, workflows={name: workflow}, tools=tools, schemas=schemas
    )
    provider = provider or MockProvider()
    executor = Executor(
        registry=load_registry(root),
        provider=provider,
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
    )
    return executor, provider


def ffprobe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def test_tool_agent_writes_binary_then_ffprobe_is_conform(tmp_path):
    executor, _ = make(tmp_path, ONE_CLIP, "oneclip", {"clipper": CLIPPER}, MEDIA_TOOLS)

    result = asyncio.run(executor.run("studio:workflow/oneclip", {"color": "red", "duration": 0.4}))

    assert result.status == "completed", result.error
    clip_ref = executor.store.get(executor.journal.state().artifacts["clip"])["clip"]
    assert clip_ref["media_type"] == "video/mp4"
    path = executor.store.path_for(clip_ref["artifact"])
    assert path.read_bytes()[:4] == b"\x00\x00\x00\x18" or path.stat().st_size > 0
    report = ffprobe(path)
    video = [s for s in report["streams"] if s["codec_type"] == "video"][0]
    assert video["codec_name"] == "h264"
    assert 0.3 <= float(report["format"]["duration"]) <= 0.6


def test_tool_montage_then_probe_concatenates(tmp_path):
    mounter = """
"@id": "studio:agent/mounter"
type: studio:ToolAgent
name: Monteur
version: 1
tools: [media.montage]
inputs:
  clips:
    type: array
    required: true
outputs:
  montage:
    type: object
"""
    prober = """
"@id": "studio:agent/prober"
type: studio:ToolAgent
name: Sondeur
version: 1
tools: [media.probe]
inputs:
  montage:
    type: object
    required: true
outputs:
  probe:
    type: object
"""
    workflow = """
"@id": "studio:workflow/montage"
type: studio:Workflow
inputs:
  specs: {type: list, required: true}
nodes:
  - id: clips
    agent: studio:agent/clipper2
    foreach: "${specs}"
    parallel: 2
  - id: board
    agent: studio:agent/mounter
    needs: [clips]
  - id: probe
    agent: studio:agent/prober
    needs: [board]
"""
    clipper2 = CLIPPER.replace("studio:agent/clipper", "studio:agent/clipper2").replace(
        """inputs:
  color:
    type: string
    required: true
  duration:
    type: number
    required: true""",
        """inputs:
  item:
    type: object
    required: true""",
    )
    root = catalog(
        tmp_path,
        agents={"clipper2": clipper2, "mounter": mounter, "prober": prober},
        workflows={"montage": workflow},
        tools=MEDIA_TOOLS,
    )
    executor = Executor(
        registry=load_registry(root),
        provider=MockProvider(),
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
    )

    result = asyncio.run(
        executor.run("studio:workflow/montage", {"specs": [
            {"color": "red", "duration": 0.4},
            {"color": "blue", "duration": 0.4},
        ]})
    )

    assert result.status == "completed", result.error
    assert result.outputs["probe"]["codec"] == "h264"
    assert 0.7 <= result.outputs["probe"]["duration"] <= 1.0
    # deuxieme run : tout en cache, aucun nouvel evenement de production
    produced_before = len([e for e in executor.journal.events() if e["type"] == "node_finished"])
    asyncio.run(executor.run("studio:workflow/montage", {"specs": [
        {"color": "red", "duration": 0.4},
        {"color": "blue", "duration": 0.4},
    ]}))
    produced_after = len([e for e in executor.journal.events() if e["type"] == "node_finished"])
    assert produced_after == produced_before  # montage reproductible : rejoue = 0 production


def test_cli_tool_dispatch_returns_stdout(tmp_path):
    executor, _ = make(tmp_path, SAY_WF, "say", {"sayer": SAYER}, CLI_TOOLS)

    result = asyncio.run(executor.run("studio:workflow/say", {"message": "M2"}))

    assert result.status == "completed", result.error
    assert "salut M2" in result.outputs["stdout"]


SEEKER_WF = """
"@id": "studio:workflow/seek"
type: studio:Workflow
inputs:
  sheet: {type: string, required: true}
nodes:
  - id: look
    agent: studio:agent/seeker
"""


def test_mcp_without_declared_server_fails_cleanly(tmp_path):
    # D19 : le kind mcp est cable, mais un serveur non declare dans `servers:`
    # doit echouer net, pas partir en pendule
    executor, _ = make(tmp_path, SEEKER_WF, "seek", {"seeker": SEEKER}, MCP_TOOLS)

    result = asyncio.run(executor.run("studio:workflow/seek", {"sheet": "planche"}))

    assert result.status == "failed"
    assert "mcp" in (result.error or "").lower()


def test_tool_kind_invalide_is_rejected_at_load(tmp_path):
    tools = """
tools:
  weird:
    kind: magic
    impl: x:y
"""
    agent = SAYER.replace("say.echo", "weird")
    with pytest.raises(Exception, match="kind"):
        make(tmp_path, SAY_WF, "say", {"sayer": agent}, tools)


def test_tool_budget_refused_before_call(tmp_path):
    tools = """
tools:
  media.clip:
    kind: python
    impl: studio.tools.media:clip
    cost: 0.50
"""
    agent = CLIPPER + "budget: 0.10\n"
    executor, _ = make(tmp_path, ONE_CLIP, "oneclip", {"clipper": agent}, tools)

    result = asyncio.run(executor.run("studio:workflow/oneclip", {"color": "red", "duration": 0.4}))

    assert result.status == "failed"
    assert "budget" in (result.error or "").lower()


def test_tool_output_validated_against_schema(tmp_path):
    prober = """
"@id": "studio:agent/prober"
type: studio:ToolAgent
name: Sondeur
version: 1
tools: [media.probe]
inputs:
  montage:
    type: object
    required: true
outputs:
  probe:
    type: object
    schema: catalog/schemas/probe.schema.json
"""
    # un probe attendu conforme : on lui donne un faux montage JSON, la panne doit etre nette
    workflow = """
"@id": "studio:workflow/badprobe"
type: studio:Workflow
nodes:
  - id: fake
    agent: studio:agent/faker
  - id: probe
    agent: studio:agent/prober
    needs: [fake]
"""
    faker = """
"@id": "studio:agent/faker"
type: studio:ToolAgent
name: Faketeur
version: 1
tools: [say.json]
outputs:
  montage:
    type: object
"""
    tools = """
tools:
  say.json:
    kind: cli
    command: ["echo", "{\\"note\\": \\"pas un montage\\"}"]
  media.probe:
    kind: python
    impl: studio.tools.media:probe
"""
    schema = json.dumps(
        {"type": "object", "required": ["duration", "codec"],
         "properties": {"duration": {"type": "number"}, "codec": {"type": "string"}}}
    )
    executor, _ = make(
        tmp_path, workflow, "badprobe", {"prober": prober, "faker": faker}, tools,
        schemas={"probe.schema.json": schema},
    )

    result = asyncio.run(executor.run("studio:workflow/badprobe"))

    assert result.status == "failed"
    assert "montage" in (result.error or "")
