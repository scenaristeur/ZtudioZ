"""D13/D17 — SIGINT = interruption relanceable ; studio invalidate <hash> expose la purge avale."""

import json
import os
import signal
import subprocess
import sys
import time

from studio.kernel.registry import STUDIO_ROOT

DISCUSSION = '{"discussion": "un phare, une voix, un choix"}'


def cli(runs_dir, *args, env=None):
    return subprocess.run(
        [sys.executable, "-m", "studio.cli", "--runs", str(runs_dir), *args],
        cwd=STUDIO_ROOT.parent,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )


def test_sigint_interrupts_then_resume_completes(tmp_path):
    runs = tmp_path / "runs"
    first = subprocess.Popen(
        [
            sys.executable, "-m", "studio.cli", "--runs", str(runs), "run",
            "studio:workflow/m0-conversation", "--input", DISCUSSION,
        ],
        cwd=STUDIO_ROOT.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "STUDIO_MOCK_DELAY": "3"},
    )
    time.sleep(1.5)
    first.send_signal(signal.SIGINT)
    out, err = first.communicate(timeout=15)

    assert first.returncode == 130, out + err
    assert "interr" in (out + err).lower()
    events = [json.loads(line) for line in (runs / "journal.jsonl").read_text().splitlines()]
    assert any(e["type"] == "run_started" for e in events)

    resumed = cli(
        runs, "run", "studio:workflow/m0-conversation",
        "--input", DISCUSSION, "--answer", "gate_brief=continue",
        "--answer", "gate_treatment=continue",
    )
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr


def test_cli_invalidate_purges_downstream_cache(tmp_path):
    runs = tmp_path / "runs"
    completed = cli(
        runs, "run", "studio:workflow/m0-conversation",
        "--input", DISCUSSION, "--answer", "gate_brief=continue",
        "--answer", "gate_treatment=continue",
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

    events = [json.loads(line) for line in (runs / "journal.jsonl").read_text().splitlines()]
    brief_hash = [e for e in events if e.get("node") == "brief" and e["type"] == "node_finished"][-1]["artifact"]

    gone = cli(runs, "invalidate", brief_hash)

    assert gone.returncode == 0, gone.stdout + gone.stderr
    assert brief_hash[:8] in gone.stdout or "purge" in gone.stdout.lower()
