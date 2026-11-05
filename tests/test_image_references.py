"""L'executeur transmet toutes les images de reference, pas seulement la premiere."""

import asyncio

from studio.kernel.artifacts import ArtifactStore
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import load_registry
from studio.tools.images import MockImageProvider
from studio.tools.llm import MockProvider
from tests.helpers import catalog

AGENT_A = """
"@id": "studio:agent/pixel-a"
type: studio:ImageAgent
name: Pixel A
version: 1
model: mock-image/png
prompt: "A."
inputs:
  a_text:
    type: string
    required: true
outputs:
  first:
    type: object
"""

AGENT_B = """
"@id": "studio:agent/pixel-b"
type: studio:ImageAgent
name: Pixel B
version: 1
model: mock-image/png
prompt: "B."
inputs:
  b_text:
    type: string
    required: true
outputs:
  second:
    type: object
"""

CONSUMER = """
"@id": "studio:agent/consumer"
type: studio:ImageAgent
name: Consumer
version: 1
model: mock-image/png
prompt: "Utilise les deux references."
inputs:
  first:
    type: object
    required: true
  second:
    type: object
    required: true
outputs:
  image:
    type: object
"""

WORKFLOW = """
"@id": "studio:workflow/two-refs"
type: studio:Workflow
name: Deux references
version: 1
inputs:
  a_text:
    type: string
    required: true
  b_text:
    type: string
    required: true
nodes:
  - id: a
    agent: studio:agent/pixel-a
  - id: b
    agent: studio:agent/pixel-b
  - id: use
    agent: studio:agent/consumer
    needs: [a, b]
"""


def test_les_deux_images_arrivent_au_provider(tmp_path):
    root = catalog(
        tmp_path,
        agents={"a": AGENT_A, "b": AGENT_B, "consumer": CONSUMER},
        workflows={"two-refs": WORKFLOW},
    )
    image = MockImageProvider()
    executor = Executor(
        registry=load_registry(root),
        provider=MockProvider(),
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
        image=image,
    )

    result = asyncio.run(executor.run("studio:workflow/two-refs", {"a_text": "phare", "b_text": "lanterne"}))

    assert result.status == "completed", result.error
    consumer = [record for record in image.records if record["agent"].endswith("consumer")]
    assert len(consumer) == 1
    assert len(consumer[0]["references"]) == 2
    assert len(set(consumer[0]["references"])) == 2
