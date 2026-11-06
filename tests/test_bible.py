"""Bible du projet : planches de reference utilisables par les ImageAgents."""

import asyncio
from pathlib import Path

import pytest

from studio.bible import Bible
from studio.kernel.artifacts import ArtifactStore
from studio.kernel.dispatch import ToolContext
from studio.kernel.errors import LoadError
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import STUDIO_ROOT, load_registry
from studio.tools import bible as bible_tool
from studio.tools.images import MockImageProvider
from studio.tools.llm import MockProvider
from tests.helpers import write

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def make_bible(root: Path):
    write(
        root / "lieu" / "desert" / "fiche.yaml",
        """
        name: Desert de sel
        slug: desert
        type: lieu
        active_version: v1
        versions:
          v1:
            seed: 42
            prompt_image: desert de sel, nuit, brume
        """,
    )
    (root / "lieu" / "desert" / "sheet_v1.png").write_bytes(PNG)
    return root


def test_la_bible_lit_les_entites_et_resout_les_planches(tmp_path):
    root = make_bible(tmp_path / "bible")
    bible = Bible(root)

    entity = bible.get("desert")
    assert entity is not None
    assert entity.name == "Desert de sel"
    assert entity.sheet == root / "lieu" / "desert" / "sheet_v1.png"
    assert bible.resolve(["desert", "lieu/desert", {"type": "lieu", "slug": "desert"}]) == [
        root / "lieu" / "desert" / "sheet_v1.png"
    ]


def test_l_outil_bible_stocke_les_planches_comme_references(tmp_path):
    root = make_bible(tmp_path / "bible")
    store = ArtifactStore(tmp_path / "artifacts")
    ctx = ToolContext(store=store, registry=load_registry(STUDIO_ROOT), bible_root=root)

    result = bible_tool.sheets({"references": ["desert"]}, ctx)

    assert len(result["references"]) == 1
    assert result["references"][0]["media_type"] == "image/png"
    assert store.get(result["references"][0]["artifact"]).data == PNG


def test_sans_bible_et_sans_reference_l_outil_rend_vide(tmp_path):
    ctx = ToolContext(
        store=ArtifactStore(tmp_path / "artifacts"),
        registry=load_registry(STUDIO_ROOT),
        bible_root=None,
    )

    assert bible_tool.sheets({"references": []}, ctx) == {"references": []}


def test_une_reference_demandee_sans_bible_est_une_erreur(tmp_path):
    ctx = ToolContext(
        store=ArtifactStore(tmp_path / "artifacts"),
        registry=load_registry(STUDIO_ROOT),
        bible_root=None,
    )

    with pytest.raises(LoadError, match="bible"):
        bible_tool.sheets({"references": ["desert"]}, ctx)


def test_cli_bible_liste_les_entites(tmp_path):
    import os
    import subprocess
    import sys

    project = tmp_path / "projet"
    make_bible(project / "bible")
    write(
        project / "studio.yaml",
        """
        name: bible-cli
        providers:
          llm: {kind: mock}
          image: {kind: mock}
          video: {kind: mock}
        """,
    )

    completed = subprocess.run(
        [sys.executable, "-m", "studio.cli", "--project", str(project), "bible"],
        cwd=STUDIO_ROOT.parent,
        capture_output=True,
        text=True,
        env={**os.environ},
    )

    assert completed.returncode == 0, completed.stderr
    assert "lieu/desert" in completed.stdout
    assert "sheet_v1.png" in completed.stdout


def test_m1_transmet_les_planches_de_la_bible_aux_images(tmp_path):
    bible_root = make_bible(tmp_path / "bible")
    image = MockImageProvider()
    provider = MockProvider(
        responses={
            "shot-list": {"shots": [{"id": 1, "prompt": "le desert"}, {"id": 2, "prompt": "la forteresse"}]},
            "continuity-checker": {"verdict": {"ok": True, "notes": "ok"}},
        }
    )
    executor = Executor(
        registry=load_registry(STUDIO_ROOT),
        provider=provider,
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
        image=image,
        bible_root=bible_root,
    )

    result = asyncio.run(
        executor.run(
            "studio:workflow/m1-scene",
            {
                "treatment": "Le desert puis la forteresse.",
                "reference": "desert de sel, nuit",
                "references": ["desert"],
            },
        )
    )

    assert result.status == "blocked"
    creator = [record for record in image.records if record["agent"].endswith("image-creator")]
    assert creator
    assert len(creator[0]["references"]) >= 2  # planche generee + planche bible
