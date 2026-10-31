"""Bout en bout M2 : 3 clips -> montage -> gate montage -> probe ffprobe, cache inclus."""

import asyncio
import json
import subprocess
import sys

import pytest

from studio.kernel.artifacts import ArtifactStore, Binary
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import STUDIO_ROOT, load_registry
from studio.tools.llm import MockProvider

CLIPS = [
    {"color": "red", "duration": 0.3},
    {"color": "green", "duration": 0.3},
    {"color": "blue", "duration": 0.3},
]

INPUTS = {"clips": CLIPS}


def make(tmp_path):
    executor = Executor(
        registry=load_registry(STUDIO_ROOT),
        provider=MockProvider(),
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
    )
    return executor


def test_m2_produces_a_probed_montage_behind_a_gate(tmp_path):
    executor = make(tmp_path)

    blocked = asyncio.run(executor.run("studio:workflow/m2-montage", INPUTS))
    assert blocked.status == "blocked"
    assert blocked.pending_gate.id == "gate_montage"

    result = asyncio.run(
        executor.run("studio:workflow/m2-montage", INPUTS, answers={"gate_montage": "continue"})
    )

    assert result.status == "completed", result.error
    probe = result.outputs["probe"]
    assert probe["codec"] == "h264"
    assert 0.8 <= probe["duration"] <= 1.1

    # les 3 clips + le montage vivent en mp4 sur disque
    store = executor.store
    binaries = list(store.root.glob("*.mp4"))
    assert len(binaries) == 4
    montage_ref = result.outputs["montage"]
    assert montage_ref["media_type"] == "video/mp4"
    assert store.path_for(montage_ref["artifact"]).exists()


def test_m2_second_run_produces_nothing(tmp_path):
    executor = make(tmp_path)

    asyncio.run(executor.run("studio:workflow/m2-montage", INPUTS, answers={"gate_montage": "continue"}))
    produced = len([e for e in executor.journal.events() if e["type"] == "node_finished"])

    asyncio.run(executor.run("studio:workflow/m2-montage", INPUTS, answers={"gate_montage": "continue"}))
    after = len([e for e in executor.journal.events() if e["type"] == "node_finished"])

    assert after == produced


def test_m2_item_failure_does_not_stop_the_run(tmp_path):
    executor = make(tmp_path)

    result = asyncio.run(
        executor.run(
            "studio:workflow/m2-montage",
            {"clips": CLIPS + [{"color": "couleur-inexistante", "duration": 0.3}]},
            answers={"gate_montage": "continue"},
        )
    )

    assert result.status == "completed", result.error
    assert result.outputs["probe"]["codec"] == "h264"
    failed = [e for e in executor.journal.events() if e["type"] == "item_failed"]
    assert len(failed) == 1


def test_cli_validate_accepts_m2_catalog():
    completed = subprocess.run(
        [sys.executable, "-m", "studio.cli", "validate"],
        cwd=STUDIO_ROOT.parent,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "m2-montage" in completed.stdout
    assert "agent/clip-maker" in completed.stdout
