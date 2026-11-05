"""Chargement local du `.env` pour `studio serve` (sans ecraser l'environnement)."""

import os

from studio.cli import load_env_file


def test_le_fichier_env_est_charge_avec_guillemets_et_commentaires(tmp_path, monkeypatch):
    monkeypatch.delenv("STUDIO_ENV_A", raising=False)
    monkeypatch.delenv("STUDIO_ENV_C", raising=False)
    monkeypatch.setenv("STUDIO_ENV_B", "deja-pose")
    path = tmp_path / ".env"
    path.write_text(
        'STUDIO_ENV_A="valeur # gardee"\n'
        "STUDIO_ENV_B=ne-doit-pas-ecraser\n"
        "STUDIO_ENV_C=simple # commentaire\n",
        encoding="utf-8",
    )

    load_env_file(path)

    assert os.environ["STUDIO_ENV_A"] == "valeur # gardee"
    assert os.environ["STUDIO_ENV_B"] == "deja-pose"
    assert os.environ["STUDIO_ENV_C"] == "simple"


def test_un_env_absent_est_ignore(tmp_path):
    load_env_file(tmp_path / "absent.env")
