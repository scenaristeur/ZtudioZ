"""Keyframes : deux images par plan, validees avant la video (Agnes debut/fin)."""

import asyncio

from studio.kernel.artifacts import ArtifactStore
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import STUDIO_ROOT, load_registry
from studio.tools.images import MockImageProvider
from studio.tools.llm import MockProvider
from studio.tools.video import MockVideoProvider

DURATION = 5.0


def plan(tmp_path, name: str) -> dict:
    store = ArtifactStore(tmp_path / "artifacts")
    image = asyncio.run(
        MockImageProvider().generate(
            agent_id="m1", model="mock", system="", prompt=f"plan {name}"
        )
    )
    ref = store.put(image)
    return {
        "image": store.get(ref).ref(),
        "prompt": f"Plan {name} : le convoi traverse la tempete.",
        "motion": "travelling lateral, la poussiere monte",
        "end_state": "le convoi a franchi la dune, la poussiere retombe",
        "duration": DURATION,
    }


def make_executor(tmp_path, image, video):
    return Executor(
        registry=load_registry(STUDIO_ROOT),
        provider=MockProvider(),
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
        image=image,
        video=video,
    )


def run(tmp_path, plans, image, video, answers):
    executor = make_executor(tmp_path, image, video)
    result = asyncio.run(
        executor.run("studio:workflow/m2-keyframes", {"plans": plans}, answers=answers)
    )
    return result, executor


def test_le_catalogue_declare_les_keyframes_avant_la_video():
    registry = load_registry(STUDIO_ROOT)
    agent = registry.agent("studio:agent/keyframe-maker")
    workflow = registry.workflow("studio:workflow/m2-keyframes")
    nodes = {node["id"]: node for node in workflow["nodes"]}

    assert str(agent["@type"]).endswith("KeyframeAgent")
    assert set(agent["outputs"]) >= {"start_image", "end_image"}
    assert nodes["clips"]["foreach"] == "${keyframes}"
    assert "gate_keyframes" in nodes["clips"]["needs"]


def test_aucune_video_avant_la_gate_des_keyframes(tmp_path):
    image, video = MockImageProvider(), MockVideoProvider()
    result, _ = run(
        tmp_path,
        [plan(tmp_path, "a")],
        image,
        video,
        answers={"gate_storyboard": "continue"},
    )

    assert result.status == "blocked"
    assert result.pending_gate.id == "gate_keyframes"
    assert image.calls == 2, "deux images cles par plan"
    assert video.calls == 0, "la video a ete payee avant la validation des keyframes"


def test_les_keyframes_produisent_un_plan_debut_fin_puis_deux_refs_video(tmp_path):
    image, video = MockImageProvider(), MockVideoProvider()
    result, executor = run(
        tmp_path,
        [plan(tmp_path, "a"), plan(tmp_path, "b")],
        image,
        video,
        answers={
            "gate_storyboard": "continue",
            "gate_keyframes": "continue",
            "gate_videos": "continue",
            "gate_montage": "continue",
        },
    )

    assert result.status == "completed", result.error
    assert image.calls == 4
    assert video.calls == 2
    for record in video.records:
        assert len(record["references"]) == 2, "debut et fin doivent partir ensemble"
        assert record["references"][0] != record["references"][1]
    assert result.outputs["probe"]["codec"] == "h264"

    events = executor.journal.events()
    assert not [e for e in events if e["type"] == "item_failed"]


def test_la_fin_du_plan_reprend_l_etat_demande(tmp_path):
    image, video = MockImageProvider(), MockVideoProvider()
    run(
        tmp_path,
        [plan(tmp_path, "a")],
        image,
        video,
        answers={"gate_storyboard": "continue", "gate_keyframes": "continue"},
    )

    prompts = [record["prompt"] for record in image.records]
    assert len(prompts) == 2
    assert "Fin du plan" in prompts[1] or "Derniere image" in prompts[1]
    assert "poussiere retombe" in prompts[1]
