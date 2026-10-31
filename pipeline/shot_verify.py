"""Vérification structurée d'un plan, pilotée par les contraintes de scène.

Pourquoi un vérificateur dédié au cinéma
----------------------------------------
`clip/verify.py` note quatre axes posés sur la *musique* (identité, beat, lumière,
interdits). Les mêmes axes ne suffisent pas pour un plan de fiction, où les deux dérives
qui coûtent le plus cher sont :

1. le **nombre de personnes** — une sheet ne contraint pas le casting d'une image ;
2. la **variante de tenue** — un personnage peut être cohérent etporter la tenue d'une
   autre scène.

Ces deux valeurs sont précisément celles que `bible/scene/scene_NN.yaml` déclare. Ce module
les consomme et en fait des notes, sans jamais prétendre valider ce qui n'est pas déclaré :
une scène sans contraintes renvoie `unverified`, pas `ok`.

Robustesse
----------
Ce module ne modifie pas le service de vision et ne s'y lie pas à l'import : il reçoit une
instance, résout l'appel par `getattr`, et tolère une dérive de signature comme une panne
de provider. Une vérification impossible vaut `unverified` — elle ne doit jamais faire
échouer une production.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Axes pondérés. Le décompte de personnes est le plus lourd : c'est la dérive la plus
# visible à l'image et la plusTolérante pour le spectateur.
WEIGHTS = {
    "people": 0.30,
    "identity": 0.25,
    "outfit": 0.20,
    "location": 0.15,
    "forbidden": 0.10,
}

# Sous-score en dessous duquel un axe est réputé en échec.
AXIS_FAIL = 0.7


def _extract_json(result_text: str) -> Optional[dict]:
    """Récupère un objet JSON dans une réponse LLM, même enchâssé dans du texte."""
    if not result_text:
        return None
    text = result_text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _people_text(people: Any) -> str:
    """Texte de contrainte de personnes dans le prompt de vérification.

    `people` peut être un entier exact (défaut) ou un dict `{min: N}` quand la scène
    déclare un public non borné (ex: 4 personnages + public). Un `{min: 4}` se lit
    « au moins 4 » : le vérificateur ne doit pas juger faux un plan qui montre le décor
    vivant déclaré dans `must_include`.
    """
    if isinstance(people, dict) and isinstance(people.get("min"), int):
        return f"at least {people['min']}"
    if isinstance(people, int):
        return str(people)
    return "not declared"


def _build_prompt(constraints: dict, shot: dict) -> str:
    """Prompt de vérification, construit sur les valeurs déclarées uniquement."""
    lines = [
        "You are a strict continuity supervisor for a film production.",
        "Compare the IMAGE against the DECLARED constraints only. Judge what is visible.",
        "Score each axis from 0.0 (completely wrong) to 1.0 (exactly as declared).",
        "",
        "DECLARED CONSTRAINTS:",
    ]
    lines.append(f"- people expected in frame: {_people_text(constraints.get('people'))}")
    location = constraints.get("location")
    lines.append(f"- location: {location or 'not declared'}")
    lines.append(f"- time of day: {constraints.get('time_of_day') or 'not declared'}")
    characters = constraints.get("characters") or {}
    if characters:
        lines.append("- characters and their outfits:")
        for slug, entry in characters.items():
            name = entry.get("name") or slug
            tenue = entry.get("outfit") or "not declared"
            lines.append(f"    * {name} must wear: {tenue}")
    else:
        lines.append("- characters: none declared")
    must_not = constraints.get("must_not_include") or []
    lines.append(f"- must NOT appear: {'; '.join(map(str, must_not)) if must_not else 'nothing specific'}")

    shot_desc = (shot.get("description") or shot.get("prompt_image") or "").strip()
    if shot_desc:
        lines += ["", "WHAT THIS SHOT SHOWS:", shot_desc[:400]]

    lines += [
        "",
        'Answer with JSON only, exactly this shape:',
        '{"people": <0-1>, "identity": <0-1>, "outfit": <0-1>, "location": <0-1>, '
        '"forbidden": <0-1>, "issues": ["short observation"], "suggestions": ["short fix"]}',
        "Use 1.0 for any axis you cannot judge from the image rather than 0.0.",
    ]
    return "\n".join(lines)


def _call_vision(vision, image_path: str, prompt: str) -> Optional[str]:
    """Appelle le service de vision sans supposer sa signature exacte.

    La signature canonique est `analyze(prompt, images=[...])` (Albert). Le service est en
    cours d'évolution (autre agent sur `services/vision.py`) : on essaie donc d'abord cette
    forme, puis le produit croisé des noms plausibles pour l'image et pour la consigne. Un
    `TypeError` de signature passe à l'essai suivant ; une panne de provider s'arrête net.
    """
    fn = getattr(vision, "analyze", None) or getattr(vision, "analyze_image", None)
    if not callable(fn):
        logger.info("Service de vision sans méthode analyze : vérification ignorée")
        return None

    # Forme canonique : `analyze(prompt, images=[path])`.
    try:
        return fn(prompt, [image_path])
    except TypeError:
        pass
    except Exception as exc:  # provider en panne : pas une dérive d'API
        logger.warning("Vérification vision en échec : %s", exc)
        return None

    for image_key in ("image_path", "path_or_url", "image"):
        for prompt_key in ("prompt", "question", "text"):
            try:
                return fn(**{image_key: image_path, prompt_key: prompt})
            except TypeError:
                continue
            except Exception as exc:  # provider en panne : pas une dérive d'API
                logger.warning("Vérification vision en échec : %s", exc)
                return None
    logger.info("Signature du service de vision non reconnue : %s", fn)
    return None
    return None


def verify_shot(image_path: str | Path, shot: dict, scene_constraints,
                vision=None) -> dict:
    """Note un plan contre les contraintes déclarées de sa scène.

    Args:
        image_path: image du plan (ou frame extrait d'une vidéo).
        shot: le plan storyboard.
        scene_constraints: instance `SceneConstraints`.
        vision: service de vision injecté. `None` → `unverified`.

    Returns:
        dict avec `status` ("verified" | "unverified" | "not_declared"), `score`,
        les axes, `issues` et `suggestions`.
    """
    scene_no = shot.get("scene") or shot.get("number_scene")
    if scene_no is None:
        return _result("not_declared", reason="numéro de scène absent du plan")

    summary = scene_constraints.summary(int(scene_no))
    if not summary:
        # Aucune contrainte déclarée : on ne note rien plutôt que de noter faux.
        return _result("not_declared", reason=f"aucun bible/scene/scene_{int(scene_no):02d}.yaml")

    if vision is None:
        return _result("unverified", reason="service de vision non fourni",
                       expected=summary)

    image_path = Path(image_path)
    if not image_path.exists():
        return _result("unverified", reason=f"image absente : {image_path}", expected=summary)

    characters = {slug: {"name": entry.get("name") or slug, "outfit": entry.get("outfit")}
                  for slug, entry in (summary.get("characters") or {}).items()}
    expected = {
        "people": summary.get("people"),
        "location": summary.get("location"),
        "time_of_day": summary.get("time_of_day"),
        "characters": characters,
    }
    raw = scene_constraints.load(int(scene_no)) or {}
    expected["must_not_include"] = raw.get("must_not_include") or []

    text = _call_vision(vision, str(image_path), _build_prompt(expected, shot))
    if text is None:
        return _result("unverified", reason="service de vision indisponible ou signature inconnue",
                       expected=summary)
    data = _extract_json(text)
    if not data:
        return _result("unverified", reason="réponse vision inexploitable", expected=summary)

    axes = {k: _clamp(data.get(k)) for k in WEIGHTS}
    score = round(sum(axes[k] * w for k, w in WEIGHTS.items()), 3)
    return {
        "status": "verified",
        "scene": int(scene_no),
        "score": score,
        "axes": axes,
        "failed_axes": sorted(k for k, v in axes.items() if v < AXIS_FAIL),
        "issues": _as_list(data.get("issues")),
        "suggestions": _as_list(data.get("suggestions")),
        "expected": summary,
    }


def _clamp(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 1.0  # axe non jugé : neutre, pas d'accusation gratuite


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _result(status: str, reason: str = "", expected: dict | None = None) -> dict:
    out = {"status": status, "score": None, "axes": {}, "failed_axes": [],
           "issues": [], "suggestions": []}
    if reason:
        out["reason"] = reason
    if expected:
        out["expected"] = expected
    return out


def correction_prompt(verification: dict, original_prompt: str) -> str:
    """Prompt de reprise ciblé sur les axes en échec, pas une régénération à l'aveugle."""
    axes = verification.get("failed_axes") or []
    if not axes:
        return ""
    return (
        "The previous image failed these continuity axes: "
        + ", ".join(axes)
        + ". Regenerate keeping everything else identical, correcting ONLY: "
        + "; ".join(verification.get("suggestions") or axes)
        + ". Original prompt: " + original_prompt
    )
