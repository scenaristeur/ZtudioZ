"""Vérification de qualité des plans générés par vision LLM.

Après chaque génération de plan vidéo, vérifie :
1. Identité du personnage (correspond à la sheet)
2. Conformité au beat (l'action décrite est visible)
3. Continuité lumineuse (suit l'arc émotionnel)
4. Absence d'éléments interdits (autres personnes, objets modernes)

Retourne un score 0-1 et des suggestions de correction.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Seuil minimum de qualité (en dessous → régénérer)
QUALITY_THRESHOLD = 0.6

# Nombre max de tentatives de régénération
MAX_RETRY = 3


def verify_plan(video_path: str | Path, beat: str, section_light: str,
                section_emotion: str, character_desc: str = "",
                location_desc: str = "",
                vision_service=None) -> dict:
    """Vérifier un plan généré via vision LLM.

    Args:
        video_path: chemin vers la vidéo ou image du plan
        beat: micro-action attendue
        section_light: lumière attendue
        section_emotion: émotion attendue
        character_desc: description du personnage (de la fiche)
        location_desc: du lieu (de la fiche)
        service: service de vision (VisionAnalysisService ou similaire)

    Returns:
        {
            "score": float (0-1),
            "identity_ok": bool,
            "beat_ok": bool,
            "light_ok": bool,
            "no_forbidden": bool,
            "issues": [str],
            "suggestions": [str],
        }
    """
    if vision_service is None:
        # Fallback : retourner un score par défaut (pas de vérification)
        return _default_result()

    video_path = Path(video_path)

    # Extraire une frame representative (milieu de la vidéo)
    frame_path = _extract_middle_frame(video_path)
    if not frame_path:
        return _default_result()

    # Construire le prompt de vérification
    prompt = _build_verify_prompt(
        beat, section_light, section_emotion, character_desc, location_desc
    )

    try:
        # Appel vision LLM
        result_text = vision_service.analyze(
            image_path=str(frame_path),
            prompt=prompt,
        )
        return _parse_verification(result_text)
    except Exception as e:
        logger.warning("Vérification échouée pour %s: %s", video_path.name, e)
        return _default_result()


def _extract_middle_frame(video_path: Path) -> Path | None:
    """Extraire la frame du milieu d'une vidéo pour vérification."""
    import subprocess

    if video_path.suffix in (".png", ".jpg", ".jpeg"):
        return video_path

    out = video_path.with_suffix(".verify_frame.png")
    try:
        # Obtenir la durée
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True, timeout=10,
        )
        duration = float(probe.stdout.strip() or "2.5")
        mid = duration / 2

        subprocess.run(
            ["ffmpeg", "-y", "-v", "error",
             "-ss", str(mid), "-i", str(video_path),
             "-frames:v", "1", str(out)],
            check=True, capture_output=True, timeout=30,
        )
        return out if out.exists() else None
    except Exception as e:
        logger.warning("Extraction frame moyenne échouée: %s", e)
        return None


def _build_verify_prompt(beat: str, light: str, emotion: str,
                         character_desc: str, location_desc: str) -> str:
    """Construire le prompt de vérification pour le vision LLM."""
    parts = [
        "You are a film quality inspector. Analyze this frame and answer in JSON:",
        "",
        f"EXPECTED BEAT: {beat}",
        f"EXPECTED LIGHT: {light}",
        f"EXPECTED EMOTION: {emotion}",
    ]
    if character_desc:
        parts.append(f"CHARACTER: {character_desc}")
    if location_desc:
        parts.append(f"LOCATION: {location_desc}")

    parts.extend([
        "",
        "Check 4 criteria and return JSON:",
        '{"',
        '  "identity": 0-1 (does the character match the description?),',
        '  "beat": 0-1 (does the image show the expected action?),',
        '  "light": 0-1 (does the lighting match?),',
        '  "no_forbidden": 0-1 (no unexpected people/objects?),',
        '  "issues": ["list of problems found"],',
        '  "suggestions": ["how to fix each issue"]',
        '}',
        "",
        "Be strict but fair. Score 0.7+ is acceptable.",
    ])

    return "\n".join(parts)


def _parse_verification(result_text: str) -> dict:
    """Parser la réponse JSON du vision LLM."""
    try:
        # Extraire le JSON de la réponse
        text = result_text.strip()
        # Chercher le JSON dans la réponse
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(text[start:end])
        else:
            data = json.loads(text)

        scores = {
            "identity": float(data.get("identity", 0.5)),
            "beat": float(data.get("beat", 0.5)),
            "light": float(data.get("light", 0.5)),
            "no_forbidden": float(data.get("no_forbidden", 0.5)),
        }

        # Score moyen pondéré
        weights = {"identity": 0.35, "beat": 0.30, "light": 0.20, "no_forbidden": 0.15}
        total = sum(scores[k] * weights[k] for k in weights)

        return {
            "score": round(total, 3),
            "identity_ok": scores["identity"] >= 0.7,
            "beat_ok": scores["beat"] >= 0.7,
            "light_ok": scores["light"] >= 0.7,
            "no_forbidden": scores["no_forbidden"] >= 0.7,
            "issues": data.get("issues", []),
            "suggestions": data.get("suggestions", []),
        }
    except Exception as e:
        logger.warning("Parsing vérification échoué: %s", e)
        return _default_result()


def _default_result() -> dict:
    """Résultat par défaut quand la vérification n'est pas disponible."""
    return {
        "score": 0.5,
        "identity_ok": True,
        "beat_ok": True,
        "light_ok": True,
        "no_forbidden": True,
        "issues": ["verification not available"],
        "suggestions": [],
    }


def should_regenerate(verification: dict, threshold: float = QUALITY_THRESHOLD) -> bool:
    """Déterminer si un plan doit être régénéré."""
    if verification["score"] < threshold:
        return True
    if not verification["no_forbidden"]:
        return True  # Toujours régénérer si éléments interdits
    return False


def build_correction_prompt(verification: dict, original_prompt: str) -> str:
    """Construire un prompt corrigé à partir des suggestions de vérification.

    Prend le prompt original et y ajoute les corrections suggérées par le vision.
    """
    if not verification.get("suggestions"):
        return original_prompt

    corrections = "; ".join(verification["suggestions"][:3])  # Max 3 corrections
    return f"{original_prompt} CORRECTIONS: {corrections}"
