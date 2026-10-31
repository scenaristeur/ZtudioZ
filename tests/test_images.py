"""Adaptateur image : l'ImageAgent ecrit des artefacts binaires et journalise son cout."""

import asyncio

from studio.kernel.artifacts import ArtifactStore
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import load_registry
from studio.tools.images import MockImageProvider
from studio.tools.llm import MockProvider
from tests.helpers import ECHO_AGENT, ECHO_TOOLS, catalog

IMAGE_AGENT = """
"@id": "studio:agent/pixel"
type: studio:ImageAgent
name: Pixel
version: 1
model: mock-image/png
prompt: "Genere une image."
outputs:
  image:
    type: object
"""

SINGLE = """
"@id": "studio:workflow/pic"
type: studio:Workflow
nodes:
  - id: img
    agent: studio:agent/pixel
"""

FAN = """
"@id": "studio:workflow/pics"
type: studio:Workflow
inputs:
  items: {type: list}
nodes:
  - id: pics
    agent: studio:agent/pixel
    foreach: "${items}"
    parallel: 2
"""

CHAINED = """
"@id": "studio:workflow/chained"
type: studio:Workflow
nodes:
  - id: ref
    agent: studio:agent/pixel
  - id: use
    agent: studio:agent/echo
    needs: [ref]
"""


_UNSET = object()


def make(tmp_path, workflow, name, agents=None, provider=None, image=_UNSET):
    root = catalog(
        tmp_path,
        agents=agents or {"pixel": IMAGE_AGENT},
        workflows={name: workflow},
        tools=ECHO_TOOLS,
    )
    provider = provider or MockProvider()
    image = MockImageProvider() if image is _UNSET else image
    executor = Executor(
        registry=load_registry(root),
        provider=provider,
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
        image=image,
    )
    return executor, provider, image


def test_image_agent_writes_a_binary_artifact(tmp_path):
    executor, provider, image = make(tmp_path, SINGLE, "pic")

    result = asyncio.run(executor.run("studio:workflow/pic"))

    assert result.status == "completed"
    assert image.calls == 1
    node_artifact = executor.journal.state().artifacts["img"]
    value = executor.store.get(node_artifact)
    binary_key = value["image"]["artifact"]
    assert value["image"]["media_type"] == "image/png"
    raw = executor.store.root / f"{binary_key}.png"
    assert raw.exists()
    assert raw.read_bytes()[:4] == b"\x89PNG"


def test_image_cost_is_journaled_on_node_finished(tmp_path):
    executor, provider, image = make(tmp_path, SINGLE, "pic", image=MockImageProvider(cost=0.05))

    asyncio.run(executor.run("studio:workflow/pic"))

    events = {e["node"]: e for e in executor.journal.events() if e["type"] == "node_finished"}
    assert events["img"]["cost"] == 0.05
    assert executor.journal.state().cost == 0.05


def test_foreach_image_costs_sum_on_the_aggregate_event(tmp_path):
    executor, _, image = make(
        tmp_path, FAN, "pics", image=MockImageProvider(cost=0.03)
    )

    asyncio.run(executor.run("studio:workflow/pics", {"items": ["a", "b", "c"]}))

    events = {e["node"]: e for e in executor.journal.events() if e["type"] == "node_finished"}
    assert events["pics"]["cost"] == 0.09
    assert image.calls == 3


def test_second_run_on_images_is_a_cache_hit(tmp_path):
    executor, _, image = make(tmp_path, SINGLE, "pic")

    asyncio.run(executor.run("studio:workflow/pic"))
    result = asyncio.run(executor.run("studio:workflow/pic"))

    assert result.status == "completed"
    assert image.calls == 1


def test_image_budget_is_refused_before_any_call(tmp_path):
    agents = {"pixel": IMAGE_AGENT + "budget: 0.10\n"}
    executor, _, image = make(
        tmp_path, SINGLE, "pic", agents=agents, image=MockImageProvider(cost=0.50)
    )

    result = asyncio.run(executor.run("studio:workflow/pic"))

    assert result.status == "failed"
    assert "budget" in (result.error or "").lower()
    assert image.calls == 0


def test_image_agent_without_adapter_fails_clearly(tmp_path):
    executor, _, _ = make(tmp_path, SINGLE, "pic", image=None)

    result = asyncio.run(executor.run("studio:workflow/pic"))

    assert result.status == "failed"
    assert "adaptateur" in (result.error or "").lower()


def test_image_reference_flows_downstream_and_is_recorded_in_lineage(tmp_path):
    echo = (
        ECHO_AGENT
        + """
inputs:
  image:
    type: object
    required: true
"""
    )
    executor, provider, image = make(
        tmp_path, CHAINED, "chained", agents={"pixel": IMAGE_AGENT, "echo": echo}
    )

    result = asyncio.run(executor.run("studio:workflow/chained"))

    assert result.status == "completed"
    state = executor.journal.state()
    used = executor.store.lineage(state.artifacts["use"])
    assert state.artifacts["ref"] in used.parents  # le noeud aval reference son amont
    binary_key = executor.store.get(state.artifacts["ref"])["image"]["artifact"]
    assert binary_key in used.parents  # et jusqu'au binaire consomme


def test_image_prompt_is_natural_text_not_json_blob(tmp_path):
    agent = IMAGE_AGENT + """
inputs:
  shot:
    type: string
    required: true
"""
    workflow = """
"@id": "studio:workflow/natural"
type: studio:Workflow
inputs:
  shot: {type: string, required: true}
nodes:
  - id: img
    agent: studio:agent/pixel
"""
    executor, _, image = make(tmp_path, workflow, "natural", agents={"pixel": agent})

    asyncio.run(executor.run("studio:workflow/natural", {"shot": "gros plan sur la lanterne"}))

    assert image.records[-1]["prompt"] == "gros plan sur la lanterne"


def test_image_prompt_prefers_the_shot_prompt_field(tmp_path):
    from studio.kernel.executor import Executor

    assert Executor._text_prompt({"shot": "texte libre"}) == "texte libre"
    assert Executor._text_prompt(
        {"item": {"id": 3, "prompt": "la mer repond"}, "reference": {"artifact": "abc"}}
    ) == "la mer repond"
    import json as _json

    assert _json.loads(Executor._text_prompt({"a": 1, "b": 2})) == {"a": 1, "b": 2}
