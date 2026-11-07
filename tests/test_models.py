"""Catalogue live des modeles Pollinations : sante, prix, options.

Les modeles changent (retires, renommes, prix modifies, service down). Le studio
ne fige pas une liste : il interroge les endpoints `/v1/models`, `/image/models`
et `/video/models`, normalise les champs utiles et classe par prix puis sante.
"""

import json
from types import SimpleNamespace

import pytest

from studio import cli, models


VIDEO_RAW = [
    {
        "name": "alibaba/wan-2.2-fast",
        "aliases": ["wan2.2", "wan-fast"],
        "title": "Wan 2.2 Fast",
        "category": "video",
        "pricing": {"currency": "pollen", "completionVideoSeconds": "0.01"},
        "health": {"status": "down", "success_rate": 31.8, "requests": 44},
        "min_duration": 5,
        "max_duration": 5,
        "default_duration": 5,
        "input_modalities": ["text", "image"],
    },
    {
        "name": "alibaba/wan-3.0",
        "aliases": ["wan-3.0"],
        "title": "Wan 3.0",
        "category": "video",
        "pricing": {"currency": "pollen", "completionVideoSeconds": "0.068"},
        "health": {"status": "healthy", "success_rate": 100, "requests": 2},
        "min_duration": 5,
        "max_duration": 5,
        "default_duration": 5,
        "input_modalities": ["text"],
    },
    {
        "name": "alibaba/wan-2.7",
        "aliases": ["wan-2.7"],
        "title": "Wan 2.7",
        "category": "video",
        "pricing": {"currency": "pollen", "completionVideoSeconds": "0.1"},
        "health": {"status": "healthy", "success_rate": 100, "requests": 1},
        "min_duration": 2,
        "max_duration": 15,
        "default_duration": 5,
        "input_modalities": ["text", "image"],
    },
]


def test_normalize_video_prix_sante_et_options():
    model = models.normalize("video", VIDEO_RAW[1])

    assert model["name"] == "alibaba/wan-3.0"
    assert model["aliases"] == ["wan-3.0"]
    assert model["status"] == "healthy"
    assert model["success_rate"] == 100
    assert model["price"] == pytest.approx(0.068)
    assert model["price_unit"] == "pollen/s"
    assert model["min_duration"] == 5
    assert model["max_duration"] == 5
    assert model["input_modalities"] == ["text"]


def test_ranked_masque_les_down_et_classe_par_prix():
    all_models = models.ranked([models.normalize("video", raw) for raw in VIDEO_RAW])
    available = models.ranked(
        [models.normalize("video", raw) for raw in VIDEO_RAW], available=True
    )

    assert [model["name"] for model in all_models] == [
        "alibaba/wan-2.2-fast",
        "alibaba/wan-3.0",
        "alibaba/wan-2.7",
    ]
    assert [model["name"] for model in available] == [
        "alibaba/wan-3.0",
        "alibaba/wan-2.7",
    ]


def test_ranked_respecte_le_prix_maximum():
    available = models.ranked(
        [models.normalize("video", raw) for raw in VIDEO_RAW],
        available=True,
        max_price=0.07,
    )

    assert [model["name"] for model in available] == ["alibaba/wan-3.0"]


def test_suitable_pour_video_exige_image_et_duree():
    no_image = models.normalize(
        "video",
        {
            "name": "mirelo/sfx-2.0",
            "input_modalities": ["text", "video"],
            "min_duration": 1,
            "max_duration": 120,
        },
    )
    too_long = models.normalize(
        "video",
        {
            "name": "x/plan-long",
            "input_modalities": ["text", "image"],
            "min_duration": 10,
            "max_duration": 20,
        },
    )
    ok = models.normalize("video", VIDEO_RAW[2])

    assert not models.suitable(no_image, "video", 5)
    assert not models.suitable(too_long, "video", 5)
    assert models.suitable(ok, "video", 5)


def test_fetch_text_parse_la_forme_openai():
    payload = {"object": "list", "data": [VIDEO_RAW[1]]}

    models_raw = models.fetch_models("text", fetch=lambda url: payload)

    assert models_raw == [VIDEO_RAW[1]]


def test_fetch_video_accepte_une_liste():
    assert models.fetch_models("video", fetch=lambda url: VIDEO_RAW) == VIDEO_RAW


def test_cmd_models_affiche_les_disponibles(monkeypatch, capsys):
    monkeypatch.setattr(models, "fetch_models", lambda kind: VIDEO_RAW)
    args = SimpleNamespace(
        kind="video",
        available=True,
        max_price=None,
        search=None,
        json=False,
        project=None,
    )

    assert cli.cmd_models(args) == 0

    out = capsys.readouterr().out
    assert "alibaba/wan-3.0" in out
    assert "alibaba/wan-2.7" in out
    assert "alibaba/wan-2.2-fast" not in out
    assert "0.068" in out
    assert "healthy" in out


def test_cmd_models_json_est_scriptable(monkeypatch, capsys):
    monkeypatch.setattr(models, "fetch_models", lambda kind: VIDEO_RAW)
    args = SimpleNamespace(
        kind="video",
        available=True,
        max_price=None,
        search=None,
        json=True,
        project=None,
    )

    assert cli.cmd_models(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert [row["name"] for row in payload["models"]] == [
        "alibaba/wan-3.0",
        "alibaba/wan-2.7",
    ]
    assert payload["models"][0]["price_unit"] == "pollen/s"


def test_cmd_models_signale_le_modele_courant_down(monkeypatch, capsys, tmp_path):
    (tmp_path / "studio.yaml").write_text(
        "name: demo\nproviders:\n  video: {kind: pollinations, model: wan-fast}\n"
    )
    monkeypatch.setattr(models, "fetch_models", lambda kind: VIDEO_RAW)
    args = SimpleNamespace(
        kind="video",
        available=False,
        max_price=None,
        search=None,
        json=False,
        project=str(tmp_path),
    )

    assert cli.cmd_models(args) == 0

    out = capsys.readouterr().out
    assert "modele courant" in out
    assert "wan-fast" in out
    assert "down" in out
