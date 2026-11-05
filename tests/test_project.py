"""M3 tranche 1 — un projet porte sa config et ses providers.

Le noyau savait executer un workflow avec des providers passes en flags. Un
projet doit declarer une fois pour toutes ou vivent ses artefacts et quels
providers il utilise ; la CLI ne doit plus exiger de flags provider.
"""

import json
import os
import subprocess
import sys

import pytest

from studio.kernel.registry import STUDIO_ROOT
from studio.project import Project, ProjectError
from studio.tools.images import MockImageProvider, PollinationsImageProvider
from studio.tools.llm import MockProvider, OpenAICompatProvider
from studio.tools.video import MockVideoProvider, PollinationsVideoProvider
from tests.helpers import write

MONTAGE = '{"clips": [{"color": "red", "duration": 0.2}]}'


def project(tmp_path, body):
    write(tmp_path / "studio.yaml", body)
    return Project.load(tmp_path)


def test_un_projet_lit_son_nom_et_ses_providers(tmp_path):
    loaded = project(
        tmp_path,
        """
        name: feu-follet
        providers:
          llm: {kind: mock}
          image: {kind: mock}
          video: {kind: mock}
        """,
    )

    assert loaded.name == "feu-follet"
    assert loaded.root == tmp_path
    assert loaded.providers["llm"].kind == "mock"
    assert loaded.providers["image"].kind == "mock"
    assert loaded.providers["video"].kind == "mock"


def test_le_nom_du_projet_par_defaut_est_le_dossier(tmp_path):
    loaded = project(tmp_path, "providers:\n  llm: {kind: mock}\n")

    assert loaded.name == tmp_path.name


def test_les_variables_d_environnement_sont_interpolees(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_TEST_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("STUDIO_TEST_URL", "https://api.example/v1")

    loaded = project(
        tmp_path,
        """
        providers:
          llm:
            kind: openai
            model: ${STUDIO_TEST_MODEL}
            base_url: ${STUDIO_TEST_URL}
        """,
    )

    assert loaded.providers["llm"].options["model"] == "openai/gpt-oss-120b"
    assert loaded.providers["llm"].options["base_url"] == "https://api.example/v1"


def test_un_projet_porte_un_contexte_interpole(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_TEST_REF", "phare blanc")

    loaded = project(
        tmp_path,
        """
        providers:
          llm: {kind: mock}
        context:
          reference: ${STUDIO_TEST_REF}
          brief:
            constraints: {max_shots: 1}
        """,
    )

    assert loaded.context["reference"] == "phare blanc"
    assert loaded.context["brief"]["constraints"]["max_shots"] == 1


def test_une_variable_absente_est_une_erreur_nette(tmp_path, monkeypatch):
    monkeypatch.delenv("STUDIO_TEST_ABSENTE", raising=False)

    with pytest.raises(ProjectError, match="STUDIO_TEST_ABSENTE"):
        project(
            tmp_path,
            """
            providers:
              llm: {kind: openai, model: ${STUDIO_TEST_ABSENTE}, base_url: "http://x"}
            """,
        )


def test_un_kind_de_provider_inconnu_est_refuse(tmp_path):
    with pytest.raises(ProjectError, match="magique"):
        project(tmp_path, "providers:\n  llm: {kind: magique}\n")


def test_un_terme_inconnu_est_refuse(tmp_path):
    with pytest.raises(ProjectError, match="provider"):
        project(tmp_path, "provider:\n  llm: {kind: mock}\n")


def test_un_projet_construit_ses_providers(tmp_path):
    loaded = project(
        tmp_path,
        """
        providers:
          llm: {kind: mock, cost: 0.5}
          image: {kind: pollinations, model: flux, width: 256, height: 256}
          video: {kind: pollinations, model: wan-fast, width: 512, height: 512}
        """,
    )

    llm = loaded.build("llm")
    image = loaded.build("image")
    video = loaded.build("video")

    assert isinstance(llm, MockProvider)
    assert llm.estimate() == 0.5
    assert isinstance(image, PollinationsImageProvider)
    assert (image.model, image.width, image.height) == ("flux", 256, 256)
    assert isinstance(video, PollinationsVideoProvider)
    assert (video.model, video.width, video.height) == ("wan-fast", 512, 512)


def test_un_projet_reel_construit_l_adaptateur_openai(tmp_path):
    loaded = project(
        tmp_path,
        """
        providers:
          llm:
            kind: openai
            model: openai/gpt-oss-120b
            base_url: https://api.albert.etalab.gouv.fr/v1
          image: {kind: mock}
          video: {kind: mock}
        """,
    )

    llm = loaded.build("llm")

    assert isinstance(llm, OpenAICompatProvider)
    assert llm.model == "openai/gpt-oss-120b"
    assert llm.base_url == "https://api.albert.etalab.gouv.fr/v1"


def test_un_projet_sans_config_est_refuse(tmp_path):
    with pytest.raises(ProjectError, match="studio.yaml"):
        Project.load(tmp_path)


def test_cli_run_avec_projet_ecrit_dans_le_projet(tmp_path):
    write(
        tmp_path / "studio.yaml",
        """
        name: demo
        providers:
          llm: {kind: mock}
          image: {kind: mock}
          video: {kind: mock}
        """,
    )

    completed = subprocess.run(
        [
            sys.executable, "-m", "studio.cli", "--project", str(tmp_path), "run",
            "studio:workflow/m2-montage",
            "--input", MONTAGE,
            "--answer", "gate_clips=continue",
            "--answer", "gate_montage=continue",
        ],
        cwd=STUDIO_ROOT.parent,
        capture_output=True,
        text=True,
        env={**os.environ},
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert len(list((tmp_path / "artifacts").glob("*.mp4"))) == 2
    assert (tmp_path / "journal.jsonl").exists()
    events = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text().splitlines()]
    assert any(e["type"] == "run_finished" and e["status"] == "completed" for e in events)


def test_cli_run_projet_invalide_sort_en_erreur(tmp_path):
    write(tmp_path / "studio.yaml", "providers:\n  llm: {kind: magique}\n")

    completed = subprocess.run(
        [
            sys.executable, "-m", "studio.cli", "--project", str(tmp_path),
            "run", "studio:workflow/m2-montage", "--input", MONTAGE,
        ],
        cwd=STUDIO_ROOT.parent,
        capture_output=True,
        text=True,
        env={**os.environ},
    )

    assert completed.returncode != 0
    assert "magique" in completed.stderr
