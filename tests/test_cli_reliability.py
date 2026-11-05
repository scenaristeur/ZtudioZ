"""CLI : erreurs propres, preflight des entrees, commandes status/next."""

import json
import os
import subprocess
import sys

from studio.kernel.registry import STUDIO_ROOT
from tests.helpers import write

DISCUSSION = json.dumps({"discussion": "un phare, une voix"})


def project(tmp_path):
    root = tmp_path / "projet"
    write(
        root / "studio.yaml",
        """
        name: cli
        providers:
          llm: {kind: mock}
          image: {kind: mock}
          video: {kind: mock}
        """,
    )
    return root


def cli(*args, env=None):
    return subprocess.run(
        [sys.executable, "-m", "studio.cli", *args],
        cwd=STUDIO_ROOT.parent,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )


def test_input_json_invalide_est_une_erreur_propre(tmp_path):
    completed = cli("--runs", str(tmp_path / "runs"), "run",
                    "studio:workflow/m0-conversation", "--input", "{")

    assert completed.returncode == 1
    assert "JSON invalide" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_answer_mal_formee_est_une_erreur_propre(tmp_path):
    completed = cli("--runs", str(tmp_path / "runs"), "run",
                    "studio:workflow/m0-conversation", "--input", DISCUSSION,
                    "--answer", "continue")

    assert completed.returncode == 1
    assert "nom=valeur" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_entree_requise_manquante_est_refusee_avant_run(tmp_path):
    runs = tmp_path / "runs"
    completed = cli("--runs", str(runs), "run",
                    "studio:workflow/m1-scene", "--input", "{}")

    assert completed.returncode == 1
    assert "entrees manquantes" in completed.stderr
    assert "reference" in completed.stderr
    assert not (runs / "journal.jsonl").exists()


def test_workflow_inconnu_est_une_erreur_propre(tmp_path):
    completed = cli("--runs", str(tmp_path / "runs"), "run",
                    "studio:workflow/inexistant", "--input", "{}")

    assert completed.returncode == 1
    assert "workflow inconnu" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_reponse_de_gate_invalide_est_une_erreur_propre(tmp_path):
    root = project(tmp_path)
    blocked = cli("--project", str(root), "run",
                  "studio:workflow/m0-conversation", "--input", DISCUSSION)
    assert blocked.returncode == 3

    completed = cli("--project", str(root), "run",
                    "studio:workflow/m0-conversation", "--input", DISCUSSION,
                    "--answer", "gate_brief=peut-etre")

    assert completed.returncode == 1
    assert "reponse invalide" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_next_propose_le_palier_suivant_et_l_entree_manquante(tmp_path):
    root = project(tmp_path)
    completed = cli(
        "--project", str(root), "run",
        "studio:workflow/m0-conversation", "--input", DISCUSSION,
        "--answer", "gate_brief=continue",
        "--answer", "gate_treatment=continue",
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

    nxt = cli("--project", str(root), "next")

    assert nxt.returncode == 0, nxt.stdout + nxt.stderr
    assert "m1-scene" in nxt.stdout
    assert "manquant: reference" in nxt.stdout


def test_status_montre_le_run_et_la_gate(tmp_path):
    root = project(tmp_path)
    blocked = cli("--project", str(root), "run",
                  "studio:workflow/m0-conversation", "--input", DISCUSSION)
    assert blocked.returncode == 3

    status = cli("--project", str(root), "status")

    assert status.returncode == 0, status.stdout + status.stderr
    assert "statut: blocked" in status.stdout
    assert "gate: gate_brief" in status.stdout


def test_answer_repond_a_une_gate_par_run_id(tmp_path):
    root = project(tmp_path)
    blocked = cli("--project", str(root), "run",
                  "studio:workflow/m0-conversation", "--input", DISCUSSION)
    assert blocked.returncode == 3
    run_id = [line for line in blocked.stdout.splitlines() if line.startswith("run: ")][0].split(": ", 1)[1]

    treatment = cli("--project", str(root), "answer", run_id, "gate_brief", "continue")
    assert treatment.returncode == 3, treatment.stdout + treatment.stderr
    assert "gate: gate_treatment" in treatment.stdout

    completed = cli("--project", str(root), "answer", run_id, "gate_treatment", "continue")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "statut: completed" in completed.stdout


def test_answer_refuse_une_gate_qui_n_est_pas_en_attente(tmp_path):
    root = project(tmp_path)
    blocked = cli("--project", str(root), "run",
                  "studio:workflow/m0-conversation", "--input", DISCUSSION)
    run_id = [line for line in blocked.stdout.splitlines() if line.startswith("run: ")][0].split(": ", 1)[1]

    completed = cli("--project", str(root), "answer", run_id, "gate_treatment", "continue")

    assert completed.returncode == 1
    assert "aucune gate en attente" in completed.stderr


def test_journal_peut_filtrer_un_run(tmp_path):
    root = project(tmp_path)
    completed = cli(
        "--project", str(root), "run",
        "studio:workflow/m0-conversation", "--input", DISCUSSION,
        "--answer", "gate_brief=continue",
        "--answer", "gate_treatment=continue",
    )
    assert completed.returncode == 0
    run_id = [line for line in completed.stdout.splitlines() if line.startswith("run: ")][0].split(": ", 1)[1]

    journal = cli("--project", str(root), "journal", "--run", run_id)
    events = [json.loads(line) for line in journal.stdout.splitlines() if line.strip()]

    assert events
    assert {event["run"] for event in events} == {run_id}
