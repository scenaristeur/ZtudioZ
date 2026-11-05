"""D23 — le controleur de continuite voit les pixels, pas seulement des refs.

Albert expose `gemma-4-31b-it` en multimodal. Le noyau charge les binaires des
images declarees en entree et le provider les envoie en `image_url` (data URI).
"""

import asyncio

from studio.kernel.artifacts import ArtifactStore, Binary
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import STUDIO_ROOT, load_registry
from studio.tools.images import MockImageProvider
from studio.tools.llm import MockProvider, OpenAICompatProvider

SHOTS = {
    "shots": [
        {"id": 1, "prompt": "Plan large de la lande, lanterne contre feu follet."},
        {"id": 2, "prompt": "Plan rapproche, la lumiere se fond dans la brume."},
    ]
}
VERDICT = {"ok": True, "notes": "Deux images vues, decor coherent."}
BRIEF = {"constraints": {"max_shots": 2, "freeform": ["pas de dialogue"]}}
INPUTS = {
    "treatment": "La lande, la lanterne, la brume.",
    "reference": "lande bretonne, nuit, lumiere bleue",
    "brief": BRIEF,
}


def run_m1(tmp_path):
    provider = MockProvider(
        responses={"shot-list": SHOTS, "continuity-checker": {"verdict": VERDICT}}
    )
    executor = Executor(
        registry=load_registry(STUDIO_ROOT),
        provider=provider,
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
        image=MockImageProvider(),
    )
    result = asyncio.run(
        executor.run("studio:workflow/m1-scene", INPUTS, answers={"gate_images": "continue", "gate_verdict": "continue"})
    )
    return result, provider


def test_le_controleur_utilise_le_modele_vision_d_albert():
    agent = load_registry(STUDIO_ROOT).agent("studio:agent/continuity-checker")

    assert agent["model"] == "gemma-4-31b-it"
    assert "reference" in (agent.get("inputs") or {})
    assert "pixels" in agent["prompt"]
    assert "Tu ne vois pas les pixels" not in agent["prompt"]


def test_le_provider_recoit_les_images_du_controleur(tmp_path):
    result, provider = run_m1(tmp_path)

    assert result.status == "completed", result.error
    record = next(
        item for item in provider.records if item["agent"].endswith("continuity-checker")
    )
    assert len(record["images"]) == 2
    assert {image["media_type"] for image in record["images"]} == {"image/png"}
    assert all(image["hash"] for image in record["images"])


def test_le_controleur_recoit_l_ancre_texte_en_plus_des_images(tmp_path):
    _, provider = run_m1(tmp_path)

    record = next(
        item for item in provider.records if item["agent"].endswith("continuity-checker")
    )
    assert INPUTS["reference"] in record["prompt"]


def test_le_contenu_multimodal_est_un_data_uri():
    content = OpenAICompatProvider._message_content(
        "regarde", [Binary(b"png-bytes", "image/png")]
    )

    assert content[0] == {"type": "text", "text": "regarde"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_une_reponse_vision_enrobee_est_parsee():
    parsed = OpenAICompatProvider._parse_json(
        'Voici le verdict :\n```json\n{"verdict": {"ok": true, "notes": "vu"}}\n```'
    )

    assert parsed["verdict"]["ok"] is True
