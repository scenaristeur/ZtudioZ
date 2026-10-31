"""Métriques de diversité visuelle pour les plans de clip.

Mesure la similarité entre les plans générés via les embeddings CLIP.
Détecte les doublons visuels et les plans trop similaires.

Utilisable pour :
- Vérifier la diversité d'un clip avant assemblage
- Comparer A/B de variantes
- Suivre l'évolution de la cohérence au fil de la production
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Seuil de similarité au-delà duquel deux plans sont considérés comme doublons
DUPLICATE_THRESHOLD = 0.92
SIMILAR_THRESHOLD = 0.85


def compute_clip_embeddings(image_paths: list[str | Path],
                            model_name: str = "clip-vit-base-patch32"
                            ) -> list[list[float]]:
    """Calculer les embeddings CLIP pour une liste d'images.

    Retourne une liste de vecteurs (un par image).
    """
    try:
        import torch
        from PIL import Image
        from transformers import CLIPModel, CLIPProcessor
    except ImportError:
        logger.warning("transformers/pillow non installés, embeddings CLIP indisponibles")
        return [[] for _ in image_paths]

    try:
        processor = CLIPProcessor.from_pretrained(model_name)
        model = CLIPModel.from_pretrained(model_name)
        model.eval()
    except Exception as e:
        logger.warning("Erreur chargement modèle CLIP: %s", e)
        return [[] for _ in image_paths]

    embeddings = []
    for path in image_paths:
        try:
            img = Image.open(str(path)).convert("RGB")
            inputs = processor(images=img, return_tensors="pt")
            with torch.no_grad():
                features = model.get_image_features(**inputs)
            emb = features[0].tolist()
            embeddings.append(emb)
        except Exception as e:
            logger.warning("Erreur embedding %s: %s", path, e)
            embeddings.append([])

    return embeddings


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Similarité cosinus entre deux vecteurs."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def analyze_diversity(plan_embeddings: dict[str, list[float]],
                      ) -> dict:
    """Analyser la diversité visuelle d'un ensemble de plans.

    Args:
        plan_embeddings: {plan_id: embedding_vector}

    Returns:
        {
            "pairs": [{"a": id, "b": id, "similarity": float}],
            "duplicates": [...],  # similarity > DUPLICATE_THRESHOLD
            "similar": [...],     # similarity > SIMILAR_THRESHOLD
            "avg_similarity": float,
            "diversity_score": float,  # 0 = tout identique, 1 = tout différent
            "coverage": {  # distribution des cadrages
                "close_up": int,
                "medium": int,
                "wide": int,
            },
        }
    """
    plan_ids = list(plan_embeddings.keys())
    if len(plan_ids) < 2:
        return {
            "pairs": [], "duplicates": [], "similar": [],
            "avg_similarity": 0.0, "diversity_score": 1.0,
            "coverage": {"close_up": 0, "medium": 0, "wide": 0},
        }

    pairs = []
    similarities = []

    for i in range(len(plan_ids)):
        for j in range(i + 1, len(plan_ids)):
            id_a, id_b = plan_ids[i], plan_ids[j]
            emb_a, emb_b = plan_embeddings[id_a], plan_embeddings[id_b]
            sim = cosine_similarity(emb_a, emb_b)
            pairs.append({"a": id_a, "b": id_b, "similarity": round(sim, 4)})
            similarities.append(sim)

    duplicates = [p for p in pairs if p["similarity"] >= DUPLICATE_THRESHOLD]
    similar = [p for p in pairs if SIMILAR_THRESHOLD <= p["similarity"] < DUPLICATE_THRESHOLD]
    avg_sim = sum(similarities) / len(similarities) if similarities else 0.0
    diversity = 1.0 - avg_sim

    return {
        "pairs": sorted(pairs, key=lambda p: -p["similarity"]),
        "duplicates": duplicates,
        "similar": similar,
        "avg_similarity": round(avg_sim, 4),
        "diversity_score": round(diversity, 4),
        "total_plans": len(plan_ids),
        "total_pairs": len(pairs),
    }


def extract_frame_descriptions(video_paths: dict[str, str],
                                vision_service=None) -> dict[str, str]:
    """Extraire les descriptions textuelles de chaque plan via vision LLM.

    Utile pour calculer la similarité sémantique (pas seulement visuelle).
    """
    descriptions = {}
    if vision_service is None:
        return descriptions

    for plan_id, video_path in video_paths.items():
        try:
            # Extraire une frame
            from pipeline.clip.verify import _extract_middle_frame
            frame = _extract_middle_frame(Path(video_path))
            if frame:
                desc = vision_service.analyze(
                    image_path=str(frame),
                    prompt="Describe this image in one sentence, focusing on the main action, "
                           "character position, and lighting.",
                )
                descriptions[plan_id] = desc.strip()
        except Exception as e:
            logger.warning("Description échouée pour %s: %s", plan_id, e)

    return descriptions


def save_diversity_report(report: dict, output_path: str | Path) -> str:
    """Sauvegarder le rapport de diversité en JSON."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    logger.info("Rapport de diversité: %s", output_path)
    return str(output_path)
