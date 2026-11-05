"""D28 — le controleur de continuite doit recevoir de quoi comparer.

Le run reel de feu-follet-v2 a rendu un verdict structurellement vide :
le noeud `verdict` ne dependait que des images et de la gate, et l'agent
ne declarait que `images` en entree. Il n'avait ni les plans, ni le brief,
donc rien a comparer — son `ok=false` ne parlait pas des images.

D23 (pas de pixels) reste ouvert : ici on garantit seulement que le
controleur recoit l'intention et les plans, pas qu'il voit les images.
"""

import asyncio
import json

import pytest

from studio.kernel.artifacts import ArtifactStore
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import STUDIO_ROOT, load_registry
from studio.tools.images import MockImageProvider
from studio.tools.llm import MockProvider

SHOTS = {
    "shots": [
        {"id": 1, "prompt": "Plan large de la lande, lanterne contre feu follet."},
        {"id": 2, "prompt": "Plan rapproche, la lumiere se fond dans la brume."},
    ]
}

BRIEF = {
    "logline": "Un tourbier suit un feu follet.",
    "synopsis": "La lande la nuit, deux plans.",
    "characters": ["Le tourbier"],
    "locations": ["La lande"],
    "open_questions": [],
    "constraints": {"max_shots": 2, "freeform": ["pas de dialogue", "fin ambigue"]},
}

RESPONSES = {
    "shot-list": SHOTS,
    "continuity-checker": {"verdict": {"ok": True, "notes": "les plans tiennent l'intention"}},
}

BASE_INPUTS = {
    "treatment": "Sequence 1 : la lande. Sequence 2 : la lanterne.",
    "reference": "lande bretonne, nuit, lumiere bleue",
}


def run_m1(tmp_path, inputs, responses=RESPONSES):
    provider = MockProvider(responses=responses)
    image = MockImageProvider()
    executor = Executor(
        registry=load_registry(STUDIO_ROOT),
        provider=provider,
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
        image=image,
    )
    result = asyncio.run(
        executor.run("studio:workflow/m1-scene", inputs, answers={"gate_images": "continue", "gate_verdict": "continue"})
    )
    return result, provider


def verdict_prompt(provider):
    """Le prompt effectivement envoye au controleur, decode."""
    record = next(r for r in provider.records if r["agent"].endswith("continuity-checker"))
    return json.loads(record["prompt"])


# --- 1. le contrat du catalogue ---------------------------------------------


def test_le_controleur_declare_les_plans_et_le_brief():
    agent = load_registry(STUDIO_ROOT).agent("studio:agent/continuity-checker")
    inputs = agent.get("inputs") or {}

    assert "images" in inputs
    assert "shots" in inputs, "le controleur ne peut pas comparer sans les plans"
    assert "brief" in inputs, "le controleur ne peut pas comparer sans le brief"


def test_le_brief_reste_optionnel_pour_le_controleur():
    """Un run M1 sans brief doit rester executable (l'entree est optionnelle)."""
    agent = load_registry(STUDIO_ROOT).agent("studio:agent/continuity-checker")

    assert not (agent["inputs"]["brief"] or {}).get("required")


def test_le_noeud_verdict_depend_des_plans():
    workflow = load_registry(STUDIO_ROOT).workflow("studio:workflow/m1-scene")
    verdict = next(n for n in workflow["nodes"] if n["id"] == "verdict")

    assert "shots" in verdict["needs"], "le noeud verdict ne recoit pas les plans"


# --- 2. ce qui arrive reellement au controleur -------------------------------


def test_le_controleur_recoit_les_plans_et_le_brief(tmp_path):
    result, provider = run_m1(tmp_path, {**BASE_INPUTS, "brief": BRIEF})

    assert result.status == "completed", result.error
    sent = verdict_prompt(provider)
    assert sent["shots"] == SHOTS["shots"]
    assert sent["brief"]["constraints"]["max_shots"] == 2
    assert sent["images"], "le controleur doit au moins recevoir les images produites"


def test_les_contraintes_du_brief_arrivent_au_controleur(tmp_path):
    result, provider = run_m1(tmp_path, {**BASE_INPUTS, "brief": BRIEF})

    assert result.status == "completed", result.error
    sent = verdict_prompt(provider)
    assert sent["brief"]["constraints"]["freeform"] == ["pas de dialogue", "fin ambigue"]


def test_un_run_sans_brief_nourrit_quand_meme_le_controleur(tmp_path):
    result, provider = run_m1(tmp_path, BASE_INPUTS)

    assert result.status == "completed", result.error
    sent = verdict_prompt(provider)
    assert sent["shots"] == SHOTS["shots"]
    assert "brief" not in sent, "un brief absent ne doit pas etre invente"


def test_le_controleur_recoit_une_entree_par_plan(tmp_path):
    """Les images sont des references d'artefacts : une par plan produit."""
    result, provider = run_m1(tmp_path, {**BASE_INPUTS, "brief": BRIEF})

    assert result.status == "completed", result.error
    sent = verdict_prompt(provider)
    assert len(sent["shots"]) == len(sent["images"]) == 2
