"""Outils media : clips, montage, sonde — adaptateurs minces autour de ffmpeg/ffprobe.

Pas de logique metier ici : construire la commande, l'executer, lire le resultat.
Le cache, la lignee et le journal sont la charge du noyau qui appelle.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from studio.kernel.artifacts import Binary

# formats conteneurs par type de media, pour re-materialiser un artefact en fichier
_EXT = {"video/mp4": ".mp4", "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


def _run(argv: list[str]) -> None:
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"{argv[0]} a echoue: {proc.stderr[-300:]}")


def _materialize(ref: dict, ctx: Any) -> Path:
    """Sort une reference d'arteffact binaire dans un fichier temporaire."""
    binary = ctx.store.get(ref["artifact"])
    if not isinstance(binary, Binary):
        raise RuntimeError(f"reference non binaire: {ref}")
    suffix = _EXT.get(binary.media_type, ".bin")
    tmp = Path(tempfile.mkstemp(prefix="ztudioz-tool-", suffix=suffix)[1])
    tmp.write_bytes(binary.data)
    return tmp


def _artifact_ref(value: Any, ctx: Any) -> dict:
    """Extrait la reference {"artifact": ...} d'une entree, sous quelque forme que ce soit."""
    if isinstance(value, dict):
        if "artifact" in value:
            return value
        for nested in value.values():
            found = _artifact_ref(nested, ctx)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _artifact_ref(nested, ctx)
            if found is not None:
                return found
    return None


def clip(params: dict, ctx: Any) -> Binary:
    """Un clip video : depuis une image (M1) ou une couleur pure (tests, storyboard).

    Une fiche qui porte une reference d'artefact image devient un plan fixe de la
    duree demandee — c'est le pont M1 -> M2. Une fiche `color` reste un clip
    synthetique deterministe. Toute autre reference est refusee : mieux vaut un
    item en panne journalise qu'un montage silencieusement faux.
    """
    spec = params.get("item") if isinstance(params.get("item"), dict) else params
    duration = float(spec.get("duration", 0.5))

    ref = _artifact_ref(spec, ctx)
    if ref is not None:
        binary = ctx.store.get(ref["artifact"])
        media_type = getattr(binary, "media_type", "")
        if not isinstance(binary, Binary) or not str(media_type).startswith("image/"):
            raise RuntimeError(f"clip: reference non image: {media_type or type(binary).__name__}")
        source = _materialize(ref, ctx)
        out = Path(tempfile.mkstemp(prefix="ztudioz-clip-", suffix=".mp4")[1])
        try:
            _run([
                "ffmpeg", "-y", "-v", "error",
                "-loop", "1", "-i", str(source),
                "-t", str(duration), "-r", "25",
                "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-fflags", "+bitexact", "-flags:v", "+bitexact",
                str(out),
            ])
            return Binary(out.read_bytes(), "video/mp4")
        finally:
            source.unlink(missing_ok=True)
            out.unlink(missing_ok=True)

    color = str(spec["color"])
    out = Path(tempfile.mkstemp(prefix="ztudioz-clip-", suffix=".mp4")[1])
    try:
        _run([
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"color=c={color}:s=64x64:d={duration}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-fflags", "+bitexact", "-flags:v", "+bitexact",
            str(out),
        ])
        return Binary(out.read_bytes(), "video/mp4")
    finally:
        out.unlink(missing_ok=True)


def montage(params: dict, ctx: Any) -> Binary:
    """Concatene la liste des clips references, dans l'ordre."""
    raw = params.get("clips", params.get("items", []))
    refs = [ref for item in raw if (ref := _artifact_ref(item, ctx)) is not None]
    if not refs:
        raise RuntimeError("montage: aucun clip en entree")

    paths = [_materialize(ref, ctx) for ref in refs]
    out = Path(tempfile.mkstemp(prefix="ztudioz-montage-", suffix=".mp4")[1])
    inputs = []
    for index, path in enumerate(paths):
        inputs += ["-i", str(path)]
    if len(paths) == 1:
        argv = ["ffmpeg", "-y", "-v", "error", *inputs, "-c", "copy", str(out)]
    else:
        concat = "".join(f"[{i}:v]" for i in range(len(paths))) + f"concat=n={len(paths)}:v=1:a=0[v]"
        argv = [
            "ffmpeg", "-y", "-v", "error", *inputs,
            "-filter_complex", concat, "-map", "[v]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-fflags", "+bitexact", "-flags:v", "+bitexact",
            str(out),
        ]
    try:
        _run(argv)
        return Binary(out.read_bytes(), "video/mp4")
    finally:
        for path in paths:
            path.unlink(missing_ok=True)
        out.unlink(missing_ok=True)


def probe(params: dict, ctx: Any) -> dict:
    """ffprobe conforme sur le montage : duree, codec, nombre de flux."""
    ref = _artifact_ref(params.get("montage"), ctx)
    if ref is None:
        raise RuntimeError("probe: aucune reference d'artefact dans 'montage'")
    path = _materialize(ref, ctx)
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", str(path)],
            capture_output=True, text=True, timeout=60, check=True,
        )
    finally:
        path.unlink(missing_ok=True)
    report = json.loads(proc.stdout)
    video = [s for s in report.get("streams", []) if s.get("codec_type") == "video"]
    return {
        "duration": round(float(report.get("format", {}).get("duration", 0.0)), 3),
        "codec": video[0]["codec_name"] if video else "",
        "streams": len(report.get("streams", [])),
    }


__all__ = ["clip", "montage", "probe"]
