"""Bout en bout M1 : 3 shots + 3 images avec refs, verdict vision, gate, cache, lignee."""

import asyncio
import json
import subprocess
import sys

import jsonschema
import pytest

from studio.kernel.artifacts import ArtifactStore, Binary
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import STUDIO_ROOT, load_registry
from studio.tools.images import MockImageProvider
from studio.tools.llm import MockProvider

SHOTS = {
    "shots": [
        {"id": 1, "prompt": "Le phare au crepuscule, vue large."},
        {"id": 2, "prompt": "Mael tourne la lanterne, gros plan."},
        {"id": 3, "prompt": "La mer repond, vague contre la roche."},
    ]
}

VERDICT = {"ok": True, "notes": "Les trois plans tiennent l'intention."}

RESPONSES = {
    "shot-list": SHOTS,
    "continuity-checker": {"verdict": VERDICT},
}

INPUTS = {
    "treatment": "Sequence 1 : le phare. Sequence 2 : la lanterne. Sequence 3 : la mer.",
    "reference": "Phare blanc et rouge, merm grise, crepuscule froid",
}


def make(tmp_path, responses=None):
    provider = MockProvider(responses=responses or RESPONSES)
    image = MockImageProvider()
    executor = Executor(
        registry=load_registry(STUDIO_ROOT),
        provider=provider,
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
        image=image,
    )
    return executor, provider, image


def run_full(executor, **overrides):
    inputs = {**INPUTS, **overrides}
    return asyncio.run(
        executor.run("studio:workflow/m1-scene", inputs, answers={"gate_images": "continue"})
    )


def test_m1_produces_three_shots_three_images_and_a_verdict(tmp_path):
    executor, provider, image = make(tmp_path)

    result = run_full(executor)

    assert result.status == "completed", result.error
    assert len(result.outputs["shots"]) == 3
    assert len(result.outputs["image"]) == 3
    hashes = {entry["artifact"] for entry in result.outputs["image"]}
    assert len(hashes) == 3
    for entry in result.outputs["image"]:
        raw = executor.store.get(entry["artifact"])
        assert isinstance(raw, Binary)
        assert raw.media_type == "image/png"
    assert result.outputs["verdict"] == VERDICT
    assert provider.calls == 2
    assert image.calls == 4  # 1 planche de reference + 3 images


def test_verdict_is_validated_against_its_schema(tmp_path):
    executor, _, _ = make(
        tmp_path, responses={**RESPONSES, "continuity-checker": {"verdict": {"ok": False, "notes": "x"}}}
    )

    result = run_full(executor)

    schema = json.loads((STUDIO_ROOT / "catalog" / "schemas" / "verdict.schema.json").read_text())
    jsonschema.validate(result.outputs["verdict"], schema)


def test_gate_on_images_blocks_without_answer(tmp_path):
    executor, provider, image = make(tmp_path)

    result = asyncio.run(executor.run("studio:workflow/m1-scene", INPUTS))

    assert result.status == "blocked"
    assert result.pending_gate.id == "gate_images"
    assert image.calls == 4  # les images existent deja, c'est elles qu'on valide
    assert provider.calls == 1  # le verdict attend la reponse


def test_second_identical_run_is_fully_cached(tmp_path):
    executor, provider, image = make(tmp_path)

    first = run_full(executor)
    calls = (provider.calls, image.calls)
    second = run_full(executor)

    assert first.status == second.status == "completed"
    assert (provider.calls, image.calls) == calls


def test_reference_change_invalidates_downstream_only(tmp_path):
    executor, provider, image = make(tmp_path)

    run_full(executor)
    assert provider.calls == 2
    assert image.calls == 4

    run_full(executor, reference="Phare vert, mer trope, contre-jour")

    # les shots ne consomment pas la ref : toujours un seul appel
    shot_calls = [r for r in provider.records if r["agent"].endswith("shot-list")]
    assert len(shot_calls) == 1
    # la ref, les 3 images et le verdict sont recalculs
    assert provider.calls == 3
    assert image.calls == 8
    # et la lignee des nouvelles images porte la nouvelle reference
    state = executor.journal.state()
    images_node = [e for e in executor.journal.events() if e.get("node") == "images"][-1]
    parents = set(executor.store.lineage(images_node["artifact"]).parents)
    for unit in ("images#0", "images#1", "images#2"):
        entry = executor.store.get(state.artifacts[unit])
        parents |= set(executor.store.lineage(entry["image"]["artifact"]).parents)
    ref_artifact = state.artifacts["ref_image"]
    ref_binary = executor.store.get(ref_artifact)["reference"]["artifact"]
    assert ref_binary in parents


def test_costs_are_journaled_for_every_produced_node(tmp_path):
    provider = MockProvider(responses=RESPONSES, cost=0.01)
    image = MockImageProvider(cost=0.02)
    executor = Executor(
        registry=load_registry(STUDIO_ROOT),
        provider=provider,
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
        image=image,
    )

    run_full(executor)

    finished = [e for e in executor.journal.events() if e["type"] == "node_finished"]
    assert all("cost" in event for event in finished)
    # 2 llm * 0.01 + 4 images * 0.02 (les evenements agregats font double avec
    # leurs unites 'node#i' : on ne compte que les noeuds sans '#')
    total = sum(e["cost"] for e in finished if "#" not in e["node"])
    assert total == pytest.approx(0.01 * 2 + 0.02 * 4)


def test_cli_runs_m1_with_mock_provider(tmp_path):
    completed = subprocess.run(
        [
            sys.executable, "-m", "studio.cli",
            "--runs", str(tmp_path / "runs"),
            "run", "studio:workflow/m1-scene",
            "--input", json.dumps(INPUTS),
            "--answer", "gate_images=continue",
        ],
        cwd=STUDIO_ROOT.parent,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "verdict" in completed.stdout
