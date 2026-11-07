"""Restauration d'une version anterieure d'un artefact, sans reecrire le journal.

Le journal reste append-only : restaurer un clip, c'est ajouter un
`node_finished` qui pointe vers l'ancien artefact. Le prochain replay repart de
cette version ; les autres unites gardent la leur.
"""

from types import SimpleNamespace

import pytest

from studio import cli
from studio.kernel.artifacts import ArtifactStore
from studio.kernel.journal import Journal
from studio.kernel.errors import StudioError
from tests.helpers import write


def project(tmp_path):
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
    return tmp_path


def setup(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    old = store.put({"clip": {"artifact": "ancien"}})
    new = store.put({"clip": {"artifact": "nouveau"}})
    journal = Journal(tmp_path / "journal.jsonl")
    journal.append("run_started", run="r", workflow="w", inputs={})
    journal.append("node_finished", run="r", node="clips#1", artifact=old, cost=0)
    journal.append("node_finished", run="r", node="clips#1", artifact=new, cost=0)
    return old, new, journal


def test_restore_repointe_le_noeud_sans_reecrire_le_journal(tmp_path):
    root = project(tmp_path)
    old, new, journal = setup(tmp_path)
    assert journal.state("r").artifacts["clips#1"] == new

    args = SimpleNamespace(project=str(root), run_id="r", node="clips#1", artifact=old)
    assert cli.cmd_restore(args) == 0

    state = journal.state("r")
    assert state.artifacts["clips#1"] == old
    last = journal.events()[-1]
    assert last["type"] == "node_finished"
    assert last["restored"] is True
    assert last["cost"] == 0.0


def test_restore_refuse_un_noeud_absent_du_run(tmp_path):
    root = project(tmp_path)
    old, _, _ = setup(tmp_path)
    args = SimpleNamespace(project=str(root), run_id="r", node="clips#9", artifact=old)

    with pytest.raises(StudioError, match="noeud inconnu"):
        cli.cmd_restore(args)


def test_restore_refuse_un_artefact_inconnu(tmp_path):
    root = project(tmp_path)
    setup(tmp_path)
    args = SimpleNamespace(project=str(root), run_id="r", node="clips#1", artifact="absent")

    with pytest.raises(StudioError, match="artefact introuvable"):
        cli.cmd_restore(args)
