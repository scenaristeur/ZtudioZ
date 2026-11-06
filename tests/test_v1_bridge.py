"""Pont lecture seule vers les projets v1 : inspection, aucun ecrit dans `projets/`."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from studio.kernel.artifacts import ArtifactStore
from studio.kernel.dispatch import ToolContext
from studio.kernel.registry import STUDIO_ROOT, load_registry
from studio.tools import v1_bridge as tool
from studio.v1_bridge import V1Error, list_projects, project_files, project_status
from tests.helpers import write

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 2048
MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 2048
MP3 = b"ID3" + b"\x00" * 2048


def make_v1(root: Path) -> Path:
    alpha = root / "alpha"
    write(alpha / "script" / "parsed" / "scene_001.yaml", "id: 1\n")
    write(alpha / "script" / "parsed" / "scene_002.yaml", "id: 2\n")
    write(
        alpha / "bible" / "personnage" / "mael" / "fiche.yaml",
        """
        name: Mael
        slug: mael
        type: personnage
        active_version: v2
        versions:
          v1: {}
          v2: {}
        """,
    )
    (alpha / "bible" / "personnage" / "mael" / "sheet_v2.png").write_bytes(PNG)
    (alpha / ".checkpoint.json").write_text(
        json.dumps(
            {
                "init": {"status": "done"},
                "parse": {"status": "done"},
                "board": {"status": "done"},
                "production": {"status": "done"},
                "audio": {"status": "done"},
                "_project": "alpha",
            }
        ),
        encoding="utf-8",
    )
    (alpha / "preprod" / "storyboard").mkdir(parents=True)
    (alpha / "preprod" / "storyboard" / "storyboard_board_v1.png").write_bytes(PNG)
    (alpha / "production" / "video").mkdir(parents=True)
    (alpha / "production" / "video" / "clip_001.mp4").write_bytes(MP4)
    (alpha / "production" / "audio").mkdir(parents=True)
    (alpha / "production" / "audio" / "voix.mp3").write_bytes(MP3)

    beta = root / "beta"
    write(beta / "script" / "parsed" / "scene_001.yaml", "id: 1\n")
    return root


def test_liste_les_projets_avec_scenes_et_entites(tmp_path):
    root = make_v1(tmp_path / "projets")

    projects = list_projects(root)

    assert [p["slug"] for p in projects] == ["alpha", "beta"]
    alpha = projects[0]
    assert alpha["scenes"] == 2
    assert alpha["entities"] == 1
    assert projects[1]["scenes"] == 1
    assert projects[1]["entities"] == 0


def test_le_statut_lit_checkpoint_et_artefacts_sans_ecrire(tmp_path):
    root = make_v1(tmp_path / "projets")
    checkpoint = root / "alpha" / ".checkpoint.json"
    before = checkpoint.read_bytes()

    status = project_status(root, "alpha")

    assert checkpoint.read_bytes() == before, "le pont ne doit jamais reecrire le checkpoint"
    assert status["checkpoint"]["produce"]["status"] == "done"
    assert "production" not in status["checkpoint"], "cle legacy migree en memoire"
    assert set(status["completed_steps"]) == {"init", "parse", "board", "produce", "audio"}
    assert status["entities"] == [
        {
            "name": "Mael",
            "slug": "mael",
            "type": "personnage",
            "active_version": "v2",
            "sheet": "sheet_v2.png",
        }
    ]
    assert status["counts"] == {"scenes": 2, "entities": 1}


def test_les_fichiers_sont_listes_avec_taille_et_pattern(tmp_path):
    root = make_v1(tmp_path / "projets")

    files = project_files(root, "alpha", "**/*.png")

    paths = {item["path"] for item in files}
    assert "preprod/storyboard/storyboard_board_v1.png" in paths
    assert "bible/personnage/mael/sheet_v2.png" in paths
    assert all(item["size"] > 0 for item in files)


def test_une_fiche_yaml_cassee_n_empeche_pas_l_inspection(tmp_path):
    root = make_v1(tmp_path / "projets")
    broken = root / "gamma" / "bible" / "lieu" / "serre" / "fiche.yaml"
    write(
        broken,
        """
        name: Serre
        slug: serre
        type: lieu
        active_version: v1
        versions:
          v1:
            attributs:
              - "toit translucide
                sur plusieurs lignes
        """,
    )

    projects = list_projects(root)
    gamma = next(p for p in projects if p["slug"] == "gamma")
    status = project_status(root, "gamma")

    assert gamma["entities"] == 0
    assert gamma["errors"] == 1
    assert status["invalid_entities"][0]["slug"] == "serre"
    assert status["entities"] == []


def test_un_slug_hors_racine_est_refuse(tmp_path):
    root = make_v1(tmp_path / "projets")

    for slug in ("../alpha", "/etc", "alpha/../../etc"):
        with pytest.raises(V1Error, match="slug"):
            project_status(root, slug)


def test_un_pattern_hors_racine_est_refuse(tmp_path):
    root = make_v1(tmp_path / "projets")

    with pytest.raises(V1Error, match="pattern"):
        project_files(root, "alpha", "../*.py")


def test_les_outils_v1_sont_dans_le_registre(tmp_path):
    root = make_v1(tmp_path / "projets")
    registry = load_registry(STUDIO_ROOT)
    ctx = ToolContext(store=ArtifactStore(tmp_path / "artifacts"), registry=registry)

    listed = tool.list_projects({"projects_root": str(root)}, ctx)
    status = tool.project_status({"projects_root": str(root), "slug": "alpha"}, ctx)
    files = tool.project_files(
        {"projects_root": str(root), "slug": "alpha", "pattern": "**/*.mp4"}, ctx
    )

    assert listed["projects"][0]["slug"] == "alpha"
    assert status["status"]["entities"][0]["slug"] == "mael"
    assert files["files"][0]["path"] == "production/video/clip_001.mp4"


def test_sans_racine_v1_l_outil_refuse(tmp_path, monkeypatch):
    monkeypatch.delenv("STUDIO_V1_PROJECTS", raising=False)
    registry = load_registry(STUDIO_ROOT)
    ctx = ToolContext(store=ArtifactStore(tmp_path / "artifacts"), registry=registry)

    with pytest.raises(Exception, match="STUDIO_V1_PROJECTS|projects_root"):
        tool.list_projects({}, ctx)


def test_cli_v1_liste_les_projets(tmp_path):
    root = make_v1(tmp_path / "projets")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "studio.cli",
            "v1",
            "list",
            "--projects-root",
            str(root),
        ],
        cwd=STUDIO_ROOT.parent,
        capture_output=True,
        text=True,
        env={**os.environ},
    )

    assert completed.returncode == 0, completed.stderr
    assert "alpha" in completed.stdout
    assert "2 scenes" in completed.stdout
    assert "1 entite" in completed.stdout
