"""Génération multi-passe pour les clips vidéo.

Pipeline en 3 étapes :
1. Preview basse résolution (dev_size, rapide, ~30s/plan)
2. Validation humaine ou automatique
3. Production haute résolution (taille complète, ~5-8min/plan)

Réduit les coûts en rejetant les plans insuffisants avant la HQ pass.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def preview_pass(video_service, prompt: str, ref_image: str, seed: int,
                 output_dir: str | Path, plan_id: str,
                 preview_size: str = "512x288",
                 duration: int = 3) -> dict:
    """Passe preview : génération rapide basse résolution.

    Args:
        video_service: instance VideoService
        prompt: prompt de base
        ref_image: image de référence
        seed: seed pour reproductibilité
        output_dir: répertoire de sortie
        plan_id: identifiant du plan
        preview_size: taille preview (défaut: 512x288 = 16:9)
        duration: durée réduite (défaut 3s au lieu de 5s)

    Returns:
        {
            "path": str,
            "size": str,
            "duration": int,
            "plan_id": str,
        }
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    width, height = [int(x) for x in preview_size.split("x")]
    filename = f"{plan_id}_preview.mp4"

    try:
        path = video_service.generate(
            prompt=prompt,
            ref_image=ref_image,
            seed=seed,
            width=width,
            height=height,
            duration=duration,
            output_dir=str(output_dir),
            filename=filename,
        )
        return {
            "path": path,
            "size": preview_size,
            "duration": duration,
            "plan_id": plan_id,
        }
    except Exception as e:
        logger.warning("Preview échouée pour %s: %s", plan_id, e)
        return {"path": None, "error": str(e)}


def hq_pass(video_service, prompt: str, ref_image: str, seed: int,
            output_dir: str | Path, plan_id: str,
            hq_size: str = "1024x576",
            duration: int = 5) -> dict:
    """Passe haute résolution : production finale.

    Args:
        hq_size: taille finale (défaut: 1024x576)
        duration: durée complète (défaut: 5s)
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    width, height = [int(x) for x in hq_size.split("x")]
    filename = f"{plan_id}.mp4"

    try:
        path = video_service.generate(
            prompt=prompt,
            ref_image=ref_image,
            seed=seed,
            width=width,
            height=height,
            duration=duration,
            output_dir=str(output_dir),
            filename=filename,
        )
        return {
            "path": path,
            "size": hq_size,
            "duration": duration,
            "plan_id": plan_id,
        }
    except Exception as e:
        logger.warning("HQ pass échouée pour %s: %s", plan_id, e)
        return {"path": None, "error": str(e)}


def validate_preview(preview: dict, vision_service=None,
                     beat: str = "", mode: str = "auto") -> dict:
    """Valider une preview (auto ou interactive).

    Args:
        preview: résultat de preview_pass()
        vision_service: service de vision LLM (optionnel)
        beat: micro-action attendue
        mode: "auto" (score > seuil) ou "interactive" (question utilisateur)

    Returns:
        {
            "approved": bool,
            "score": float,
            "reason": str,
        }
    """
    if preview.get("path") is None:
        return {"approved": False, "score": 0.0, "reason": "preview not generated"}

    if vision_service is None:
        # Pas de vérification : approuver par défaut
        return {"approved": True, "score": 0.5, "reason": "no vision service, auto-approved"}

    try:
        from pipeline.clip.verify import verify_plan
        result = verify_plan(
            video_path=preview["path"],
            beat=beat,
            section_light="",
            section_emotion="",
            vision_service=vision_service,
        )
        score = result.get("score", 0.0)
        approved = score >= 0.6

        if mode == "interactive" and not approved:
            import sys
            if sys.stdin.isatty():
                print(f"\n  Preview {preview['plan_id']} : score {score:.2f}")
                print(f"  Raison: {result.get('issues', ['low quality'])[0]}")
                try:
                    choice = input("  Forcer la HQ pass quand même ? (o/N): ").strip().upper()
                    if choice in ("O", "OUI", "YES", "Y"):
                        approved = True
                except (EOFError, KeyboardInterrupt):
                    pass

        return {
            "approved": approved,
            "score": score,
            "reason": "score sufficient" if approved else f"score {score:.2f} < threshold",
        }
    except Exception as e:
        logger.warning("Validation preview échouée: %s", e)
        return {"approved": True, "score": 0.5, "reason": f"validation error: {e}"}


def multipass_plan(video_service, prompt: str, ref_image: str, seed: int,
                   plan_id: str, output_dir: str | Path,
                   preview_size: str = "512x288",
                   hq_size: str = "1024x576",
                   preview_duration: int = 3,
                   hq_duration: int = 5,
                   vision_service=None, beat: str = "",
                   mode: str = "auto",
                   force_hq: bool = False) -> dict:
    """Pipeline multi-passe complet pour un plan.

    1. Preview basse résolution
    2. Validation (auto ou interactive)
    3. HQ pass si approuvé

    Si force_hq=True, skip la preview et va directement en HQ.
    """
    if force_hq:
        return hq_pass(video_service, prompt, ref_image, seed,
                       output_dir, plan_id, hq_size, hq_duration)

    # Passe 1 : Preview
    preview = preview_pass(video_service, prompt, ref_image, seed,
                           output_dir, plan_id, preview_size, preview_duration)

    if preview.get("path") is None:
        return preview

    # Passe 2 : Validation
    validation = validate_preview(preview, vision_service, beat, mode)
    preview["validation"] = validation

    if not validation["approved"]:
        logger.info("Preview %s rejetée (score: %.2f)", plan_id, validation["score"])
        return preview

    # Passe 3 : HQ
    hq = hq_pass(video_service, prompt, ref_image, seed,
                 output_dir, plan_id, hq_size, hq_duration)
    hq["preview_validation"] = validation

    return hq


def multipass_section(video_service, beats: list[dict], section_id: str,
                      output_dir: str | Path,
                      ref_image: str, seed_base: int = 0,
                      preview_size: str = "512x288",
                      hq_size: str = "1024x576",
                      vision_service=None,
                      mode: str = "auto",
                      force_hq: bool = False) -> list[dict]:
    """Pipeline multi-passe pour une section complète.

    Génère chaque beat en preview, valide, puis passe en HQ.
    Retourne les résultats de tous les plans.
    """
    results = []
    for i, beat_data in enumerate(beats):
        plan_id = f"{section_id}_p{i:02d}"
        beat_text = beat_data.get("beat", "")
        seed = seed_base + i * 7

        result = multipass_plan(
            video_service=video_service,
            prompt=beat_text,
            ref_image=ref_image,
            seed=seed,
            plan_id=plan_id,
            output_dir=output_dir,
            preview_size=preview_size,
            hq_size=hq_size,
            vision_service=vision_service,
            beat=beat_text,
            mode=mode,
            force_hq=force_hq,
        )
        result["beat_index"] = i
        result["section_id"] = section_id
        results.append(result)

    # Stats
    total = len(results)
    generated = sum(1 for r in results if r.get("path"))
    preview_only = sum(1 for r in results if r.get("validation"))
    logger.info(
        "Section %s: %d/%d plans générés (%d en preview seule)",
        section_id, generated, total, preview_only,
    )

    return results
