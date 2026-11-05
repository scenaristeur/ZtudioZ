"""M3 tranche 2 — le contexte projet est herite par les noeuds, pas redeclare.

D25 : la contrainte du brief ne doit pas dependre d'une redeclaration dans
chaque workflow. Le projet porte un contexte ; un workflow declare ce qu'il
consomme une seule fois (`context:`), et les agents le recoivent par leur
contrat d'entrees habituel.
"""

import asyncio
import json
import os
import subprocess
import sys

import pytest

from studio.kernel.artifacts import ArtifactStore
from studio.kernel.errors import LoadError
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import STUDIO_ROOT, load_registry
from studio.tools.llm import MockProvider
from tests.helpers import catalog, write

AGENT = """
"@id": "studio:agent/echo"
type: studio:LlmAgent
name: Echo
version: 1
model: mock/echo
prompt: "Decoupe."
inputs:
  brief:
    type: object
"""

WORKFLOW = """
"@id": "studio:workflow/ctx"
type: studio:Workflow
name: Contexte
version: 1
context: [brief]
inputs:
  treatment:
    type: string
    required: true
nodes:
  - id: shots
    agent: studio:agent/echo
"""

OPTIONAL = """
"@id": "studio:workflow/optional"
type: studio:Workflow
name: Contexte optionnel
version: 1
context:
  brief:
    required: false
inputs:
  items:
    type: list
    required: true
nodes:
  - id: shots
    agent: studio:agent/echo
    foreach: "${items}"
    limit: "${brief.max_shots}"
"""


def make(tmp_path, workflow=WORKFLOW, name="ctx", context=None):
    root = catalog(
        tmp_path,
        agents={"echo": AGENT},
        workflows={name: workflow},
    )
    provider = MockProvider()
    executor = Executor(
        registry=load_registry(root),
        provider=provider,
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
        context=context,
    )
    return executor, provider


def test_le_contexte_projet_atteint_l_agent_sans_entree_de_workflow(tmp_path):
    executor, provider = make(tmp_path, context={"brief": {"constraints": {"max_shots": 1}}})

    result = asyncio.run(
        executor.run("studio:workflow/ctx", {"treatment": "un phare, une voix"})
    )

    assert result.status == "completed", result.error
    assert provider.calls == 1
    assert '"max_shots": 1' in provider.records[0]["prompt"]


def test_un_contexte_requis_absent_echoue_avant_tout_appel(tmp_path):
    executor, provider = make(tmp_path)

    with pytest.raises(LoadError, match="brief"):
        asyncio.run(executor.run("studio:workflow/ctx", {"treatment": "un phare"}))

    assert provider.calls == 0


def test_une_entree_explicite_ecrase_le_contexte_du_projet(tmp_path):
    executor, provider = make(tmp_path, context={"brief": {"constraints": {"max_shots": 1}}})

    asyncio.run(
        executor.run(
            "studio:workflow/ctx",
            {"treatment": "un phare", "brief": {"constraints": {"max_shots": 3}}},
        )
    )

    assert '"max_shots": 3' in provider.records[0]["prompt"]


def test_un_contexte_optionnel_absent_ne_bloque_pas(tmp_path):
    executor, provider = make(tmp_path, workflow=OPTIONAL, name="optional")

    result = asyncio.run(
        executor.run("studio:workflow/optional", {"items": [{"n": 1}, {"n": 2}]})
    )

    assert result.status == "completed", result.error
    assert provider.calls == 2


def test_une_borne_issue_du_contexte_est_appliquee(tmp_path):
    executor, provider = make(
        tmp_path, workflow=OPTIONAL, name="optional", context={"brief": {"max_shots": 1}}
    )

    result = asyncio.run(
        executor.run("studio:workflow/optional", {"items": [{"n": 1}, {"n": 2}, {"n": 3}]})
    )

    assert result.status == "completed", result.error
    assert provider.calls == 1
    truncated = [e for e in executor.journal.events() if e["type"] == "items_truncated"]
    assert truncated[-1]["kept"] == 1


def test_changer_le_contexte_change_le_run_id(tmp_path):
    executor, _ = make(tmp_path, context={"brief": {"max_shots": 1}})
    one = asyncio.run(executor.run("studio:workflow/ctx", {"treatment": "un phare"}))

    executor, _ = make(tmp_path / "autre", context={"brief": {"max_shots": 2}})
    two = asyncio.run(executor.run("studio:workflow/ctx", {"treatment": "un phare"}))

    assert one.run_id != two.run_id


def test_cli_projet_nourrit_m1_avec_son_contexte(tmp_path):
    write(
        tmp_path / "studio.yaml",
        """
        name: demo
        context:
          brief:
            constraints:
              max_shots: 1
        providers:
          llm: {kind: mock}
          image: {kind: mock}
          video: {kind: mock}
        """,
    )

    completed = subprocess.run(
        [
            sys.executable, "-m", "studio.cli", "--project", str(tmp_path), "run",
            "studio:workflow/m1-scene",
            "--input", json.dumps({
                "treatment": "Sequence 1 : le phare. Sequence 2 : la lanterne.",
                "reference": "Phare blanc et rouge, mer grise, crepuscule froid",
            }),
            "--answer", "gate_images=continue",
            "--answer", "gate_verdict=continue",
        ],
        cwd=STUDIO_ROOT.parent,
        capture_output=True,
        text=True,
        env={**os.environ},
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    events = [
        json.loads(line)
        for line in (tmp_path / "journal.jsonl").read_text().splitlines()
    ]
    assert any(e["type"] == "run_finished" and e["status"] == "completed" for e in events)
