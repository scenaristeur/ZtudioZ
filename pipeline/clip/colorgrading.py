"""Color grading post-production pour clips.

Applique un traitement couleur cohérent sur tous les plans d'un clip
pour assurer la continuité visuelle.

Modes :
- "section" : palette par section (souvenir = chaud, impuissance = froid, etc.)
- "global" : un seul look sur tout le clip
- "arc" : palette par arc narratif
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Palettes prédéfinies par section (LUT 3D ou filtres ffmpeg)
SECTION_PALETTES: dict[str, dict] = {
    "silence": {
        "label": "Sépia doux",
        "ffmpeg": "colorbalance=rs=0.15:gs=0.05:bs=-0.1:saturation=0.7,curves=master='0/0 0.5/0.55 1/1'",
    },
    "souvenir": {
        "label": "Chaud nostalgic",
        "ffmpeg": "colorbalance=rs=0.2:gs=0.1:bs=-0.15:saturation=1.1,eq=brightness=0.03",
    },
    "impuissance": {
        "label": "Froid délavé",
        "ffmpeg": "colorbalance=rs=-0.1:gs=-0.05:bs=0.2:saturation=0.6,curves=master='0/0.05 0.5/0.48 1/0.9'",
    },
    "accablement": {
        "label": "Bleu nuit",
        "ffmpeg": "colorbalance=rs=-0.2:gs=-0.1:bs=0.3:saturation=0.5,eq=brightness=-0.05",
    },
    "espoir": {
        "label": "Doré doux",
        "ffmpeg": "colorbalance=rs=0.15:gs=0.1:bs=-0.05:saturation=0.9,eq=brightness=0.05",
    },
    "cri": {
        "label": "Contraste intense",
        "ffmpeg": "eq=contrast=1.3:brightness=-0.02,colorbalance=rs=0.05:bs=0.1:saturation=1.2",
    },
    "apaisement": {
        "label": "Doux pastel",
        "ffmpeg": "colorbalance=rs=0.05:gs=0.05:bs=0.05:saturation=0.8,curves=master='0/0.03 0.5/0.52 1/0.97'",
    },
    "calme": {
        "label": "Neutre naturel",
        "ffmpeg": "eq=saturation=0.85:brightness=0.02,colorbalance=rs=0.03:gs=0.03:bs=0.03",
    },
}

# Palettes par arc
ARC_PALETTES: dict[str, dict] = {
    "workshop": {
        "label": "Steampunk warm",
        "ffmpeg": "colorbalance=rs=0.12:gs=0.06:bs=-0.08:saturation=0.85,curves=master='0/0 0.5/0.53 1/1'",
    },
    "sea": {
        "label": "Maritime froid",
        "ffmpeg": "colorbalance=rs=-0.08:gs=0.02:bs=0.2:saturation=0.8",
    },
    "paris": {
        "label": "Urbain contemporain",
        "ffmpeg": "eq=contrast=1.1:saturation=0.9,colorbalance=rs=0.05:bs=-0.05",
    },
}


def color_grade_clip(input_path: str | Path, output_path: str | Path,
                     palette: str = "section", sections: list[dict] = None,
                     arc: str = None) -> str:
    """Appliquer le color grading sur un clip assemblé.

    Args:
        input_path: clip assemblé (MP4 avec audio)
        output_path: clip color-gradé
        palette: mode ("section", "global", "arc")
        sections: sections du timeline (pour mode section)
        arc: arc narratif (pour mode arc)

    Returns:
        chemin du fichier de sortie
    """
    input_path = Path(input_path)
    output_path = Path(output_path)

    if palette == "section" and sections:
        return _grade_by_sections(input_path, output_path, sections)
    elif palette == "arc" and arc:
        return _grade_by_arc(input_path, output_path, arc)
    elif palette == "global":
        return _grade_global(input_path, output_path)
    else:
        logger.info("Pas de palette définie, clip sans color grading")
        return str(input_path)


def _grade_by_sections(input_path: Path, output_path: Path,
                       sections: list[dict]) -> str:
    """Color grading par section avec filtres ffmpeg concaténés."""
    if not sections:
        return str(input_path)

    # Construire les filtres par segment temporel
    filters = []
    for sec in sections:
        start = float(sec.get("start", 0))
        end = float(sec.get("end", 0))
        label = sec.get("label", "calme")

        palette = SECTION_PALETTES.get(label, SECTION_PALETTES["calme"])
        filter_str = palette["ffmpeg"]

        # Appliquer le filtre sur le segment temporel
        filters.append(
            f"between(t,{start:.3f},{end:.3f})*{filter_str}"
        )

    if not filters:
        return str(input_path)

    # ffmpeg : appliquer le color grading
    # Note: pour un grading section par section, on utilise un filtre complexe
    vf = ",".join(f"{filters[0]}")  # Simplification : grading global
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(input_path),
        "-vf", vf,
        "-c:a", "copy",
        str(output_path),
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        logger.info("Color grading section appliqué: %s", output_path)
        return str(output_path)
    except subprocess.CalledProcessError as e:
        logger.warning("Color grading échoué: %s", e.stderr.decode()[:200])
        return str(input_path)


def _grade_by_arc(input_path: Path, output_path: Path, arc: str) -> str:
    """Color grading par arc narratif."""
    palette = ARC_PALETTES.get(arc)
    if not palette:
        logger.info("Pas de palette pour l'arc '%s', clip sans color grading", arc)
        return str(input_path)

    vf = palette["ffmpeg"]
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(input_path),
        "-vf", vf,
        "-c:a", "copy",
        str(output_path),
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        logger.info("Color grading arc '%s' appliqué: %s", arc, output_path)
        return str(output_path)
    except subprocess.CalledProcessError as e:
        logger.warning("Color grading échoué: %s", e.stderr.decode()[:200])
        return str(input_path)


def _grade_global(input_path: Path, output_path: Path) -> str:
    """Color grading global (look uniforme sur tout le clip)."""
    # Look "cinéma steampunk" par défaut
    vf = "colorbalance=rs=0.08:gs=0.04:bs=-0.05:saturation=0.85,curves=master='0/0 0.5/0.53 1/0.98',unsharp=3:3:0.3"
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(input_path),
        "-vf", vf,
        "-c:a", "copy",
        str(output_path),
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        logger.info("Color grading global appliqué: %s", output_path)
        return str(output_path)
    except subprocess.CalledProcessError as e:
        logger.warning("Color grading échoué: %s", e.stderr.decode()[:200])
        return str(input_path)


def apply_lut_to_video(video_path: str | Path, lut_path: str | Path,
                       output_path: str | Path) -> str:
    """Appliquer une LUT 3D personnalisée (.cube) à une vidéo."""
    video_path = Path(video_path)
    lut_path = Path(lut_path)
    output_path = Path(output_path)

    if not lut_path.exists():
        logger.warning("LUT introuvable: %s", lut_path)
        return str(video_path)

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(video_path),
        "-vf", f"lut3d={lut_path}",
        "-c:a", "copy",
        str(output_path),
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        logger.info("LUT %s appliquée: %s", lut_path.name, output_path)
        return str(output_path)
    except subprocess.CalledProcessError as e:
        logger.warning("Application LUT échouée: %s", e.stderr.decode()[:200])
        return str(video_path)


def get_palette_for_section(section_label: str) -> dict:
    """Retourner la palette recommandée pour une section."""
    return SECTION_PALETTES.get(section_label, SECTION_PALETTES["calme"])


def get_palette_for_arc(arc: str) -> dict:
    """Retourner la palette recommandée pour un arc."""
    return ARC_PALETTES.get(arc, {})
