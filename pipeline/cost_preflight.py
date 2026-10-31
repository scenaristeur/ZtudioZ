"""Pré-vol de coût : savoir ce qu'une étape va coûter avant de la lancer.

Le problème
-----------
Le coût n'est connu qu'après coup, via `ConsumptionTracker`. Sur une scène de 27 plans,
une régénération i2v part à 0,01 € l'unité : un plan raté est un plan payé deux fois. Pire :
la boucle storyboard peut épuiser un budget sans que personne ne le voie, parce que le
coût est journalisé mais jamais prévu.

Ce module fait l'inverse : il compte ce qui *va* être généré, applique la table de coûts
unitaires de `consumption.py`, et compare au plafond déclaré. Il n'invente pas de prix —
un modèle absent de la table coûte 0 et le signale, pour ne pas fabriquer une fausse
précision.

Il n'écrit rien et ne bloque rien tout seul : il rend un verdict, et l'appelant décide.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from pipeline.consumption import _estimates

logger = logging.getLogger(__name__)

# Nombre de plans estimé quand le storyboard n'existe pas encore.
DEFAULT_SHOTS_PER_SCENE = 12

# Ordre de grandeur des étapes à l'unité, utilisé seulement si la table de coûts ne
# connaît pas le modèle actif. Volontairement pessimiste : mieux vaut surestimer.
_FALLBACK_UNIT = {
    "visualize": 0.005,
    "produce": 0.02,
    "keyframes": 0.02,
    "audio": 0.002,
}

# Étapes qui consomment des crédits. `sheets` est inclus : une feuille manquante se
# régénère en silence pendant `visualize`, et c'est une dépense invisible.
COSTLY_STEPS = ("sheets", "visualize", "produce", "keyframes", "audio")


def _active_model(config: dict, service: str) -> str:
    cfg = config.get("services", {}).get(service, {}) or {}
    active = cfg.get("active")
    providers = cfg.get("providers", {}) or {}
    return str((providers.get(active) or {}).get("model") or active or "")


def _count_shots(project_dir: Path, scene: int) -> int:
    """Plans réellement prévus pour la scène, si le storyboard existe."""
    sb = project_dir / "preprod" / "storyboard" / f"scene_{scene:02d}" / "storyboard.json"
    if sb.exists():
        try:
            data = json.loads(sb.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return len(data)
            if isinstance(data, dict) and isinstance(data.get("shots"), list):
                return len(data["shots"])
        except (json.JSONDecodeError, OSError):
            pass
    return DEFAULT_SHOTS_PER_SCENE


def _count_sheets(project_dir: Path, entities: int) -> int:
    """Feuilles de référence manquantes : celles qui seront bel et bien régénérées."""
    bible = project_dir / "bible"
    missing = 0
    for etype in ("personnage", "lieu", "objet"):
        d = bible / etype
        if not d.is_dir():
            continue
        for fiche in d.iterdir():
            if fiche.is_dir() and not any(fiche.glob("*.png")):
                missing += 1
    return missing


def _lookup_price(service: str, model: str) -> tuple[float, bool, bool]:
    """Prix unitaire d'un modèle, sa présence dans la table, et son mode de facturation.

    Returns:
        (prix, connu, par_token)

    La table de `consumption.py` est hétérogène : les médias sont indexés
    `image/gptimage`, les LLM sont indexés par leur seul identifiant
    `openai/gpt-oss-120b`. On tente donc les deux écritures.

    On interroge `_estimates` directement et non `_cost_estimate` : ce dernier renvoie 0
    pour un modèle facturé au token quand on ne lui passe pas de compteurs, ce qui ferait
    croire à un modèle gratuit. Une tarification au token n'est pas non plus un prix
    unitaire — elle ne peut pas être Additionnée plan par plan, et le rapport le dit.
    """
    for key in (f"{service}/{model}", model):
        info = _estimates.get(key)
        if not info:
            continue
        if "input" in info or "output" in info:
            return 0.0, True, True
        unit = float(info.get("unit", 0.0))
        if unit:
            return unit, True, False
        return 0.0, True, False
    return 0.0, False, False


def estimate_step(step: str, project_dir: str | Path, scene: int,
                  config: dict, entities: int = 0) -> dict:
    """Estime le coût d'une étape pour une scène, sans rien générer."""
    project_dir = Path(project_dir)
    service = {"visualize": "image", "sheets": "image",
               "produce": "video", "keyframes": "video", "audio": "audio"}.get(step)

    if step == "sheets":
        units = _count_sheets(project_dir, entities)
    elif service:
        units = _count_shots(project_dir, scene)
    else:
        units = 0

    model = _active_model(config, service) if service else ""
    unit_cost, known, per_token = _lookup_price(service, model) if (service and model) else (0.0, False, False)
    if not known and service:
        unit_cost = _FALLBACK_UNIT.get(step, 0.0)

    return {
        "step": step,
        "scene": scene,
        "units": units,
        "model": model or None,
        "unit_cost": round(unit_cost, 6),
        "estimated": round(units * unit_cost, 4),
        "pricing_known": known,
        "per_token": per_token,
    }


def estimate_run(steps, project_dir: str | Path, scenes, config: dict,
                 entities: int = 0) -> dict:
    """Estime le coût total d'un lot d'étapes, poste par poste."""
    per_scene: dict[int, float] = {}
    details: list[dict] = []
    for step in steps:
        if step not in COSTLY_STEPS:
            continue
        for scene in scenes:
            est = estimate_step(step, project_dir, scene, config, entities)
            details.append(est)
            per_scene[scene] = round(per_scene.get(scene, 0.0) + est["estimated"], 4)
    return {
        "details": details,
        "per_scene": per_scene,
        "total": round(sum(per_scene.values()), 4),
        "any_unknown_pricing": any(not d["pricing_known"] for d in details if d["units"]),
    }


def check_budget(estimate: dict, budget_per_scene: float) -> dict:
    """Compare l'estimation au plafond. `budget_per_scene: 0` désactive le contrôle."""
    budget = float(budget_per_scene or 0)
    over = [s for s, cost in estimate["per_scene"].items() if budget > 0 and cost > budget]
    if budget <= 0:
        verdict = "unbounded"
    elif over:
        verdict = "refuse"
    else:
        verdict = "ok"
    return {
        "verdict": verdict,
        "budget_per_scene": budget,
        "over_budget_scenes": sorted(over),
        "refuse": bool(over),
    }


def format_report(estimate: dict, budget: dict) -> str:
    """Rapport lisible pour le journal, avant toute génération."""
    lines = ["Pré-vol de coût :"]
    for d in estimate["details"]:
        if not d["units"]:
            continue
        price = f"{d['unit_cost']:.4f} €" + ("" if d["pricing_known"] else " (estimé, prix inconnu)")
        lines.append(f"  {d['step']:<10} scène {d['scene']:>2} : {d['units']:>3} × {price} = {d['estimated']:.3f} €")
    lines.append(f"  TOTAL : {estimate['total']:.3f} €")
    if budget["verdict"] == "unbounded":
        lines.append("  Plafond : désactivé (budget_per_scene: 0).")
    elif budget["verdict"] == "ok":
        lines.append(f"  Plafond : {budget['budget_per_scene']:.3f} €/scène — respecté.")
    else:
        scenes = ", ".join(str(s) for s in budget["over_budget_scenes"])
        lines.append(
            f"  Plafond : {budget['budget_per_scene']:.3f} €/scène — DÉPASSÉ "
            f"(scène(s) {scenes}). Relancer avec --force pour passer outre."
        )
    if estimate["any_unknown_pricing"]:
        lines.append("  Attention : certains modèles n'ont pas de prix connu, l'estimation est partielle.")
    return "\n".join(lines)
