"""Le prompt image porte l'ancre visuelle, le brief et la correction humaine."""

import asyncio

from studio.kernel.artifacts import ArtifactStore
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import STUDIO_ROOT, load_registry
from studio.tools.images import MockImageProvider
from studio.tools.llm import MockProvider

SHOTS = {
    "shots": [
        {"id": 1, "prompt": "Le convoi traverse le desert de sel."},
        {"id": 2, "prompt": "La cour de la forteresse, machines et etincelles."},
    ]
}
BRIEF = {
    "logline": "Une pilote decouvre le secret d'une forteresse.",
    "synopsis": "Le desert, la forteresse, le choix.",
    "characters": ["La pilote"],
    "locations": ["Desert de sel", "Forteresse"],
    "open_questions": [],
    "constraints": {
        "max_shots": 2,
        "freeform": ["pas de dialogue", "lumiere froide"],
    },
}
INPUTS = {
    "treatment": "La pilote traverse le desert puis entre dans la forteresse.",
    "reference": "desert de sel, nuit, brume, lumiere artificielle jaune",
    "brief": BRIEF,
}


def make(tmp_path):
    provider = MockProvider(
        responses={
            "shot-list": SHOTS,
            "continuity-checker": {"verdict": {"ok": True, "notes": "vu"}},
        }
    )
    image = MockImageProvider()
    executor = Executor(
        registry=load_registry(STUDIO_ROOT),
        provider=provider,
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
        image=image,
    )
    return executor, image, provider


def image_prompts(image):
    return [record["prompt"] for record in image.records if record["agent"].endswith("image-creator")]


def test_les_images_recoivent_l_ancre_et_les_contraintes_du_brief(tmp_path):
    executor, image, _ = make(tmp_path)

    result = asyncio.run(executor.run("studio:workflow/m1-scene", INPUTS))

    assert result.status == "blocked"
    assert result.pending_gate.id == "gate_images"
    prompts = image_prompts(image)
    assert len(prompts) == 2
    for prompt in prompts:
        assert "desert de sel, nuit, brume" in prompt
        assert "pas de dialogue" in prompt
        assert "lumiere froide" in prompt


def test_un_retry_de_gate_regenere_les_images_avec_le_feedback(tmp_path):
    executor, image, _ = make(tmp_path)
    asyncio.run(executor.run("studio:workflow/m1-scene", INPUTS))
    before = image.calls

    retried = asyncio.run(
        executor.run(
            "studio:workflow/m1-scene",
            INPUTS,
            answers={"gate_images": "retry"},
            feedback={"gate_images": "rends la brume plus epaisse"},
        )
    )

    assert retried.status == "blocked"
    assert retried.pending_gate.id == "gate_images"
    assert image.calls > before
    assert "rends la brume plus epaisse" in image_prompts(image)[-1]


def test_gate_verdict_retry_rejoue_images_et_verdict(tmp_path):
    executor, image, provider = make(tmp_path)
    asyncio.run(
        executor.run(
            "studio:workflow/m1-scene",
            INPUTS,
            answers={"gate_images": "continue"},
        )
    )
    images_before = image.calls
    verdicts_before = len(
        [r for r in provider.records if r["agent"].endswith("continuity-checker")]
    )

    retried = asyncio.run(
        executor.run(
            "studio:workflow/m1-scene",
            INPUTS,
            answers={"gate_verdict": "retry"},
            feedback={"gate_verdict": "l'image 2 doit montrer les machines de la forteresse"},
        )
    )

    assert retried.status == "blocked"
    assert retried.pending_gate.id == "gate_verdict"
    assert image.calls > images_before
    assert len(
        [r for r in provider.records if r["agent"].endswith("continuity-checker")]
    ) > verdicts_before
    assert "l'image 2 doit montrer les machines" in image_prompts(image)[-1]

    finished = asyncio.run(
        executor.run(
            "studio:workflow/m1-scene",
            INPUTS,
            answers={"gate_verdict": "continue"},
        )
    )
    assert finished.status == "completed"
