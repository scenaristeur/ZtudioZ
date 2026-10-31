from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def get_duration(video_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", video_path],
        capture_output=True, text=True, timeout=30,
    )
    data = json.loads(result.stdout)
    return float(data.get("format", {}).get("duration", 5))


def assemble_scene(video_paths: list[str], audio_paths: list[str] | None = None, output_path: str = "scene.mp4") -> str:
    if not video_paths:
        raise ValueError("No videos to assemble")
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if len(video_paths) == 1:
        inputs = []
        filter_chains = []
        if audio_paths:
            inputs.extend(["-i", video_paths[0], "-i", audio_paths[0]])
            filter_chains.append(f"[0:v]format=yuv420p[v]")
            filter_chains.append(f"[1:a]adelay=0[a]")
            cmd = ["ffmpeg", "-y"] + inputs + [
                "-filter_complex", ";".join(filter_chains),
                "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-c:a", "aac", "-shortest", str(out),
            ]
        else:
            cmd = ["ffmpeg", "-y", "-i", video_paths[0], "-c:v", "libx264", "-c:a", "aac", str(out)]
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        return str(out)

    # Étape 1 : concat des plans en une vidéo SANS audio (les plans sont volontairement sans
    # piste audio — cf. clip _trim_to -an). Le MP3 est ensuite muxé comme UNIQUE piste audio.
    # On recopie chaque plan dans un dossier temp SANS apostrophe (le concat demuxer de ffmpeg
    # mange l'apostrophe des chemins type "projets/Melody'sGhost/..." → fichier introuvable).
    stage_dir = Path("/tmp/ztudioz_stage")
    stage_dir.mkdir(parents=True, exist_ok=True)
    stage_inputs: list[str] = []
    for i, v in enumerate(video_paths):
        staged = stage_dir / f"plan_{i:03d}.mp4"
        if not staged.exists() or Path(v).stat().st_mtime > staged.stat().st_mtime:
            subprocess.run(["cp", str(Path(v)), str(staged)],
                           check=True, capture_output=True, timeout=120)
        stage_inputs.append(str(staged))
    concat_file = "/tmp/ztudioz_concat.txt"
    with open(concat_file, "w") as f:
        for v in stage_inputs:
            f.write(f"file '{Path(v).resolve()}'\n")

    intermediate = "/tmp/ztudioz_concat_video.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
         "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "23",
         intermediate],
        check=True, capture_output=True, timeout=600,
    )

    if audio_paths and Path(audio_paths[0]).exists():
        # Étape 2 : muxer le MP3 (source de vérité) comme unique piste audio, -shortest.
        subprocess.run(
            ["ffmpeg", "-y", "-i", intermediate, "-i", audio_paths[0],
             "-map", "0:v", "-map", "1:a",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)],
            check=True, capture_output=True, timeout=600,
        )
        return str(out)

    # Pas d'audio : renommer l'intermédiaire en sortie.
    out_path = Path(output_path)
    out_path.write_bytes(Path(intermediate).read_bytes())
    return str(out)


def assemble_film(scene_paths: list[str], output_path: str = "film_final.mp4") -> str:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open("/tmp/ztudioz_film_concat.txt", "w") as f:
        for s in scene_paths:
            f.write(f"file '{Path(s).resolve()}'\n")
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", "/tmp/ztudioz_film_concat.txt",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=600)
    return str(out)
