"""M3 tranche 3 — un projet avance d'un palier a l'autre.

Le journal d'un run termine devient la memoire du projet : le run suivant
recupere les sorties validees (traitement, brief, plans, images) sans qu'on les
recopie a la main. Un run bloque a une gate ne nourrit **pas** la memoire.
"""

import json
import os
import subprocess
import sys
from types import SimpleNamespace

from studio.kernel.artifacts import ArtifactStore
from studio.kernel.journal import Journal
from studio.kernel.registry import STUDIO_ROOT, load_registry
from studio.project import Project, next_step, project_memory
from tests.helpers import write

DISCUSSION = "Un phare, une voix, un choix : rester ou partir."
REFERENCE = "Phare blanc et rouge, mer grise, crepuscule froid"


def projet(tmp_path):
    write(
        tmp_path / "studio.yaml",
        """
        name: chaine
        providers:
          llm: {kind: mock}
          image: {kind: mock}
          video: {kind: mock}
        """,
    )
    return tmp_path


def cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "studio.cli", "--project", str(root), *args],
        cwd=STUDIO_ROOT.parent,
        capture_output=True,
        text=True,
        env={**os.environ},
    )


def events(root):
    return [
        json.loads(line)
        for line in (root / "journal.jsonl").read_text().splitlines()
    ]


def m0(root, *extra):
    return cli(
        root, "run", "studio:workflow/m0-conversation",
        "--input", json.dumps({"discussion": DISCUSSION}),
        "--answer", "gate_treatment=continue", *extra,
    )


def m1(root, inputs):
    return cli(
        root, "run", "studio:workflow/m1-scene",
        "--input", json.dumps(inputs),
        "--answer", "gate_images=continue",
            "--answer", "gate_verdict=continue",
    )


def test_un_projet_enchaine_m0_vers_m1(tmp_path):
    root = projet(tmp_path)

    first = m0(root, "--answer", "gate_brief=continue")
    assert first.returncode == 0, first.stdout + first.stderr

    second = m1(root, {"reference": REFERENCE})

    assert second.returncode == 0, second.stdout + second.stderr
    assert "herite du journal" in second.stdout
    assert "treatment" in second.stdout
    completed = [
        e for e in events(root)
        if e["type"] == "run_finished" and e["status"] == "completed"
    ]
    assert len(completed) >= 2
    started = [
        e for e in events(root)
        if e["type"] == "run_started" and e["workflow"].endswith("m1-scene")
    ]
    assert "treatment" in started[-1]["inputs"]
    assert "brief" in started[-1]["inputs"]
    assert "discussion" not in started[-1]["inputs"]


def test_un_run_bloque_ne_nourrit_pas_la_memoire(tmp_path):
    root = projet(tmp_path)

    blocked = m0(root)
    assert blocked.returncode == 3, blocked.stdout + blocked.stderr

    second = m1(root, {"reference": REFERENCE})

    assert second.returncode != 0
    assert "treatment" in second.stdout + second.stderr


def test_next_federe_les_plans_video_depuis_m1(tmp_path):
    root = projet(tmp_path)
    assert m0(root, "--answer", "gate_brief=continue").returncode == 0
    assert m1(root, {"reference": REFERENCE}).returncode == 0

    project = Project.load(root)
    step = next_step(project, load_registry(STUDIO_ROOT))
    memory = project_memory(project)
    shots = memory["shots"]
    images = memory["image"]

    assert step["workflow"] == "studio:workflow/m2-video"
    assert step["missing"] == []
    assert "plans" in step["inherited"]

    plans = step["inputs"]["plans"]
    assert len(plans) == len(shots) == len(images)
    for index, plan in enumerate(plans):
        assert plan["id"] == shots[index]["id"]
        assert plan["prompt"] == shots[index]["prompt"]
        assert plan["image"] == images[index]
        assert plan["duration"] == 5.0
        assert plan["motion"] == ""


def test_project_memory_lit_les_noeuds_en_cache_et_la_derniere_version(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    old = store.put({"shots": [{"id": 1, "prompt": "vieux"}]})
    new = store.put({"shots": [{"id": 1, "prompt": "nouveau"}]})
    images = store.put(
        [{"image": {"artifact": "a"}}, {"image": {"artifact": "b"}}]
    )

    journal = Journal(tmp_path / "journal.jsonl")
    journal.append("run_started", run="r", workflow="w", inputs={"treatment": "T"})
    journal.append("node_finished", run="r", node="shots", artifact=old, cost=0)
    journal.append("node_finished", run="r", node="shots", artifact=new, cost=0)
    journal.append("node_skipped", run="r", node="shots", artifact=new, reason="cache", cost=0)
    journal.append("node_finished", run="r", node="images", artifact=images, cost=0)
    journal.append("run_finished", run="r", status="completed")

    project = SimpleNamespace(root=tmp_path, journal=tmp_path / "journal.jsonl", artifacts=tmp_path / "artifacts")
    memory = project_memory(project)

    assert memory["shots"] == [{"id": 1, "prompt": "nouveau"}]
    assert memory["image"] == [{"artifact": "a"}, {"artifact": "b"}]


def test_une_entree_explicite_ecrase_la_memoire(tmp_path):
    root = projet(tmp_path)
    assert m0(root, "--answer", "gate_brief=continue").returncode == 0

    second = m1(root, {"reference": REFERENCE, "treatment": "TRAITEMENT EXPLICITE"})
    assert second.returncode == 0, second.stdout + second.stderr

    started = [
        e for e in events(root)
        if e["type"] == "run_started" and e["workflow"].endswith("m1-scene")
    ]
    assert started[-1]["inputs"]["treatment"] == "TRAITEMENT EXPLICITE"
