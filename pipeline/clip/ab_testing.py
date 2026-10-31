"""A/B testing interactif pour la création de clips.

Génère 2-4 variantes d'un plan, les analyse via vision LLM, et propose
des choix interactifs à l'utilisateur avec des suggestions IA.

Modes :
- "auto" : l'IA choisit la meilleure variante (score max)
- "interactive" : l'utilisateur voit les options et choisit
- "suggest" : l'IA suggère, l'utilisateur valide ou refuse
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_variants(video_service, prompt: str, ref_image: str,
                      seed: int, n_variants: int = 3,
                      output_dir: str | Path = None,
                      plan_id: str = "variant",
                      **kwargs) -> list[dict]:
    """Générer N variantes d'un même plan avec des seeds différents.

    Args:
        video_service: instance de VideoService
        prompt: prompt de base
        ref_image: image de référence
        seed: seed de base (sera offset pour chaque variante)
        n_variants: nombre de variantes (2-4)
        output_dir: répertoire de sortie
        plan_id: identifiant du plan
        **kwargs: args supplémentaires pour video.generate()

    Returns:
        [{"variant": "A", "video": path, "seed": int}, ...]
    """
    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    variants = []
    for i in range(n_variants):
        variant_label = chr(65 + i)  # A, B, C, D
        variant_seed = seed + i * 100  # Seeds différents mais proches
        filename = f"{plan_id}_{variant_label}.mp4"

        try:
            path = video_service.generate(
                prompt=prompt,
                ref_image=ref_image,
                seed=variant_seed,
                output_dir=str(output_dir) if output_dir else None,
                filename=filename,
                **kwargs,
            )
            variants.append({
                "variant": variant_label,
                "video": path,
                "seed": variant_seed,
            })
            logger.info("Variante %s générée: %s", variant_label, path)
        except Exception as e:
            logger.warning("Variante %s échouée: %s", variant_label, e)

    return variants


def analyze_variants(variants: list[dict], beat: str,
                     vision_service=None) -> list[dict]:
    """Analyser chaque variante via vision LLM et attribuer un score.

    Args:
        variants: liste de variantes (de generate_variants)
        beat: micro-action attendue
        vision_service: service de vision

    Returns:
        variantes enrichies avec "score" et "analysis"
    """
    for v in variants:
        if vision_service is None:
            v["score"] = 0.5
            v["analysis"] = "vision service not available"
            continue

        try:
            from pipeline.clip.verify import _extract_middle_frame, _build_verify_prompt
            frame = _extract_middle_frame(Path(v["video"]))
            if not frame:
                v["score"] = 0.0
                v["analysis"] = "could not extract frame"
                continue

            prompt = _build_verify_prompt(beat, "", "", "", "")
            result = vision_service.analyze(
                image_path=str(frame),
                prompt=prompt,
            )
            v["analysis"] = result
            # Score simple : compter les mentions positives
            text = result.lower()
            score = 0.5
            if "good" in text or "correct" in text or "matches" in text:
                score += 0.2
            if "clear" in text or "well" in text:
                score += 0.1
            if "blurry" in text or "wrong" in text or "missing" in text:
                score -= 0.2
            v["score"] = max(0.0, min(1.0, score))
        except Exception as e:
            logger.warning("Analyse variante %s échouée: %s", v["variant"], e)
            v["score"] = 0.5
            v["analysis"] = str(e)

    return sorted(variants, key=lambda v: -v["score"])


def suggest_best(variants: list[dict]) -> dict:
    """Sélectionner la meilleure variante (score max)."""
    if not variants:
        return {}
    return max(variants, key=lambda v: v.get("score", 0))


def interactive_choose(variants: list[dict], plan_id: str = "",
                       beat: str = "") -> dict | None:
    """Mode interactif : afficher les options et demander le choix de l'utilisateur.

    Si pas de TTY (exécution non-interactive), retourne la meilleure variante auto.
    """
    if not sys.stdin.isatty():
        return suggest_best(variants)

    print(f"\n{'='*60}")
    print(f"  A/B TEST — {plan_id}")
    if beat:
        print(f"  Beat: {beat[:80]}")
    print(f"{'='*60}")

    for i, v in enumerate(variants):
        score = v.get("score", 0)
        marker = " ★" if i == 0 else ""
        print(f"\n  [{v['variant']}] Score: {score:.2f}{marker}")
        print(f"      Seed: {v['seed']}")
        print(f"      Vidéo: {v['video']}")
        if v.get("analysis"):
            analysis = str(v["analysis"])[:120]
            print(f"      Analyse: {analysis}")

    print(f"\n  [S] Meilleure automatique ({variants[0]['variant']})")
    print(f"  [Q] Passer (garder le plan précédent)")

    while True:
        try:
            choice = input("\n  Votre choix: ").strip().upper()
            if choice == "S":
                return variants[0]
            if choice == "Q":
                return None
            for v in variants:
                if v["variant"].upper() == choice:
                    return v
            print("  Choix invalide. Entrez A, B, C, D, S ou Q.")
        except (EOFError, KeyboardInterrupt):
            return suggest_best(variants)


def ab_test_plan(video_service, prompt: str, ref_image: str, seed: int,
                 plan_id: str, beat: str = "", n_variants: int = 3,
                 output_dir: str | Path = None,
                 vision_service=None, mode: str = "suggest",
                 force_single: bool = False) -> dict | None:
    """Pipeline complet d'A/B testing pour un plan.

    Modes :
    - "auto": génère N variantes, garde la meilleure (pas d'interaction)
    - "interactive": affiche les options, l'utilisateur choisit
    - "suggest": génère + analyse, suggère, l'utilisateur valide

    Si force_single=True ou n_variants < 2, génère une seule variante (pas d'A/B).
    """
    if force_single or n_variants < 2:
        # Pas d'A/B : générer une seule variante
        variants = generate_variants(
            video_service, prompt, ref_image, seed,
            n_variants=1, output_dir=output_dir, plan_id=plan_id,
        )
        return variants[0] if variants else None

    # Générer les variantes
    variants = generate_variants(
        video_service, prompt, ref_image, seed,
        n_variants=n_variants, output_dir=output_dir, plan_id=plan_id,
    )
    if not variants:
        return None

    # Analyser
    variants = analyze_variants(variants, beat, vision_service)

    # Choix selon le mode
    if mode == "auto":
        return suggest_best(variants)
    elif mode == "interactive":
        return interactive_choose(variants, plan_id, beat)
    elif mode == "suggest":
        best = suggest_best(variants)
        if sys.stdin.isatty():
            print(f"\n  IA suggère: variante {best['variant']} (score: {best.get('score', 0):.2f})")
            try:
                valid = input("  Valider ? (O/n): ").strip().upper()
                if valid in ("N", "NON", "NO"):
                    return interactive_choose(variants, plan_id, beat)
            except (EOFError, KeyboardInterrupt):
                pass
        return best
    else:
        return suggest_best(variants)


def save_ab_report(plan_id: str, variants: list[dict], chosen: dict,
                   output_path: str | Path) -> str:
    """Sauvegarder le rapport A/B testing."""
    report = {
        "plan_id": plan_id,
        "n_variants": len(variants),
        "chosen": chosen.get("variant") if chosen else None,
        "variants": [
            {"variant": v["variant"], "seed": v["seed"],
             "score": v.get("score", 0), "video": v["video"]}
            for v in variants
        ],
    }
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return str(output_path)
