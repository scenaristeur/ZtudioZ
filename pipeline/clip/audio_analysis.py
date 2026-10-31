"""Analyse musicale de MP3 pour caler les beats visuels.

Utilise ffprobe/ffmpeg (pas de dépendances Python lourdes).
Détecte la durée, calcule l'énergie RMS par segment, identifie les sections.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def analyze_audio(mp3_path: str | Path, segment_dur: float = 0.5) -> dict:
    """Analyser un fichier audio via ffprobe + ffmpeg.

    Returns:
        {
            "duration": float,
            "rms": [{"start": float, "end": float, "energy": float}],
            "sections": [{"start": float, "end": float, "label": str}],
        }
    """
    mp3_path = Path(mp3_path)
    logger.info("Analyse musicale de %s", mp3_path.name)

    # Durée via ffprobe
    duration = _get_duration(mp3_path)
    if duration <= 0:
        raise RuntimeError(f"Durée invalide pour {mp3_path}")

    # RMS energy via ffmpeg astats
    rms_segments = _compute_rms(mp3_path, duration, segment_dur)

    # Sections par changement d'énergie
    sections = _detect_sections(rms_segments, duration)

    # BPM estimé via beat detection basique (onsets par energy threshold)
    bpm = _estimate_bpm(rms_segments)

    result = {
        "duration": round(duration, 3),
        "bpm": round(bpm, 1),
        "rms": rms_segments,
        "sections": sections,
    }

    logger.info(
        "Analyse: %.1fs, BPM≈%.0f, %d segments, %d sections",
        duration, bpm, len(rms_segments), len(sections),
    )
    return result


def _get_duration(mp3_path: Path) -> float:
    """Obtenir la durée d'un fichier audio via ffprobe."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(mp3_path)],
            capture_output=True, text=True, timeout=15,
        )
        return float(r.stdout.strip())
    except Exception as e:
        logger.error("ffprobe durée échoué: %s", e)
        return 0.0


def _compute_rms(mp3_path: Path, duration: float,
                 segment_dur: float = 0.5) -> list[dict]:
    """Calculer l'énergie RMS par segment via ffmpeg astats."""
    segments = []
    t = 0.0
    while t < duration:
        end = min(t + segment_dur, duration)
        dur = end - t
        if dur <= 0:
            break

        try:
            # ffmpeg : extraire le segment, calculer RMS via astats
            r = subprocess.run(
                ["ffmpeg", "-v", "info",
                 "-ss", str(t), "-t", str(dur),
                 "-i", str(mp3_path),
                 "-af", "astats=metadata=1:reset=0",
                 "-f", "null", "-"],
                capture_output=True, text=True, timeout=30,
            )
            # Parser le RMS depuis stderr (astats output)
            rms = _parse_rms_from_astats(r.stderr)
        except Exception:
            rms = 0.0

        segments.append({
            "start": round(t, 3),
            "end": round(end, 3),
            "energy": round(rms, 4),
        })
        t = end

    return segments


def _parse_rms_from_astats(output: str) -> float:
    """Parser le RMS level depuis la sortie ffmpeg astats."""
    for line in output.split("\n"):
        if "RMS level dB:" in line:
            try:
                # Format: "[Parsed_astats_0 @ 0x...] RMS level dB: -26.470955"
                val = line.split("RMS level dB:")[-1].strip()
                db = float(val)
                # Convertir dB en linéaire (0-1 approximatif)
                # -60dB ≈ 0, 0dB = 1
                return max(0.0, min(1.0, 10 ** (db / 20)))
            except (ValueError, IndexError):
                pass
    return 0.0


def _detect_sections(rms_segments: list[dict], duration: float,
                     threshold_factor: float = 0.3) -> list[dict]:
    """Détecter les sections musicales par changement d'énergie."""
    if not rms_segments:
        return [{"start": 0, "end": duration, "label": "unknown"}]

    energies = [s["energy"] for s in rms_segments]
    mean_energy = sum(energies) / len(energies) if energies else 0
    threshold = mean_energy * threshold_factor

    # Points de rupture
    breakpoints = [0.0]
    for i in range(1, len(rms_segments)):
        delta = abs(rms_segments[i]["energy"] - rms_segments[i - 1]["energy"])
        if delta > threshold:
            breakpoints.append(rms_segments[i]["start"])
    breakpoints.append(duration)

    # Classifier
    sections = []
    for i in range(len(breakpoints) - 1):
        start = breakpoints[i]
        end = breakpoints[i + 1]
        section_energy = [
            s["energy"] for s in rms_segments
            if s["start"] >= start and s["end"] <= end
        ]
        avg_energy = sum(section_energy) / len(section_energy) if section_energy else 0

        if avg_energy < mean_energy * 0.3:
            label = "silence"
        elif avg_energy < mean_energy * 0.7:
            label = "calme"
        elif avg_energy < mean_energy * 1.3:
            label = "modere"
        else:
            label = "intense"

        sections.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "label": label,
            "energy": round(avg_energy, 4),
        })

    return sections


def _estimate_bpm(rms_segments: list[dict]) -> float:
    """Estimer le BPM via les pics d'énergie (méthode basique)."""
    if len(rms_segments) < 4:
        return 120.0

    energies = [s["energy"] for s in rms_segments]
    mean_e = sum(energies) / len(energies)
    # Seuil = moyenne * 1.2
    threshold = mean_e * 1.2

    # Trouver les pics
    peaks = []
    for i in range(1, len(energies) - 1):
        if energies[i] > threshold and energies[i] > energies[i-1] and energies[i] > energies[i+1]:
            peaks.append(rms_segments[i]["start"])

    if len(peaks) < 2:
        return 120.0

    # Intervalle moyen entre pics
    intervals = [peaks[i+1] - peaks[i] for i in range(len(peaks)-1)]
    avg_interval = sum(intervals) / len(intervals) if intervals else 0.5

    if avg_interval <= 0:
        return 120.0

    bpm = 60.0 / avg_interval
    # Filtrer les BPM absurdes (< 60 ou > 200)
    if bpm < 60:
        bpm *= 2
    elif bpm > 200:
        bpm /= 2

    return max(60.0, min(200.0, bpm))


def beats_to_timeline(analysis: dict, section_definitions: list[dict],
                      max_plan_duration: float = 5.0) -> list[dict]:
    """Convertir l'analyse musicale en beats visuels pour le timeline YAML.

    Pour chaque section du timeline, aligne les beats sur les onset musicaux.
    """
    rms_segments = analysis.get("rms", [])
    enriched_beats = []

    for sec in section_definitions:
        sec_start = float(sec.get("start", 0))
        sec_end = float(sec.get("end", 0))

        # Segments RMS dans cette section
        sec_segments = [
            s for s in rms_segments
            if s["start"] >= sec_start and s["end"] <= sec_end
        ]

        # Beats existants dans le timeline
        beats = sec.get("beats", [])
        if beats and len(beats) > 0:
            # Enrichir les beats existants avec l'énergie musicale
            for beat in beats:
                enriched = {**beat}
                beat_time = float(beat.get("start", 0))
                # Trouver l'énergie la plus proche
                closest = min(sec_segments,
                              key=lambda s: abs(s["start"] - beat_time),
                              default=None)
                enriched["energy"] = closest["energy"] if closest else 0.0
                enriched_beats.append(enriched)
        else:
            # Pas de beats → générer automatiquement à partir des pics d'énergie
            peaks = [s for s in sec_segments if s["energy"] > 0.01]
            if not peaks:
                peaks = sec_segments[:max(1, int((sec_end - sec_start) / max_plan_duration))]

            for i, peak in enumerate(peaks):
                next_time = peaks[i+1]["start"] if i+1 < len(peaks) else sec_end
                dur = min(next_time - peak["start"], max_plan_duration)
                if dur <= 0.5:
                    continue
                enriched_beats.append({
                    "start": round(peak["start"], 3),
                    "end": round(peak["start"] + dur, 3),
                    "duration": round(dur, 3),
                    "beat": f"Musical beat at {peak['start']:.1f}s (energy: {peak['energy']:.2f})",
                    "camera": "Dynamic, music-driven.",
                    "energy": peak["energy"],
                })

    return enriched_beats


def save_analysis(analysis: dict, output_path: str | Path) -> str:
    """Sauvegarder l'analyse audio en JSON."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(analysis, indent=2, ensure_ascii=False))
    logger.info("Analyse sauvegardée: %s", output_path)
    return str(output_path)
