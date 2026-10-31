from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from openai import OpenAI

from pipeline.config import get_clip_audio_config, get_clip_config
from pipeline.consumption import get_tracker

logger = logging.getLogger(__name__)


class ClipAudioService:
    """Transcription et analyse audio pour la production de clips musicaux.

    S'appuie sur les modèles de transcription d'Albert (whisper-large-v3) exposés via
    l'endpoint OpenAI-compatible (/v1/audio/transcriptions). La transcription d'une
    piste voix seule (.wav) est beaucoup plus fidèle que celle du mix complet.
    """

    def __init__(self, config: dict | None = None):
        cfg = config or get_clip_audio_config()
        self.config = cfg
        self.client = OpenAI(api_key=cfg.get("api_key"), base_url=cfg.get("base_url"))

    # ── Transcription ───────────────────────────────────────────

    def transcribe_segment(
        self,
        audio_path: str | Path,
        start: float = 0.0,
        end: float | None = None,
        language: str = "en",
        model: str | None = None,
    ) -> str:
        """Transcrire un segment audio. Retourne le texte (ou '' si aucune parole)."""
        model = model or get_clip_config().get("transcription_model", "openai/whisper-large-v3")
        seg = self._segment(audio_path, start, end)
        with open(seg, "rb") as f:
            r = self.client.audio.transcriptions.create(
                model=model, file=f, language=language
            )
        txt = (r.text or "").strip()
        if txt in (".", ".."):
            return ""
        return txt

    def transcribe_full(
        self,
        audio_path: str | Path,
        window: float = 20.0,
        language: str = "en",
        model: str | None = None,
    ) -> list[dict]:
        """Transcrire tout le fichier en fenêtres de `window` secondes.

        Returns:
            list[{start, end, text}] — segments non vides, triés par time.
        """
        import soundfile as sf
        y, sr = sf.read(str(audio_path))
        dur = len(y) / sr
        results = []
        t = 0.0
        while t < dur:
            end = min(t + window, dur)
            text = self.transcribe_segment(audio_path, t, end, language, model)
            if text:
                results.append({"start": round(t, 1), "end": round(end, 1), "text": text})
            t = end
        return results

    def transcribe_with_isolation(
        self,
        audio_path: str | Path,
        start: float,
        end: float,
        language: str = "en",
        model: str | None = None,
    ) -> str:
        """Transcrire un segment du MIX en isolant la voix (voie centrale + band-pass vocal).

        Utile quand on n'a pas de piste voix seule : le chant noyé dans le mix est isolé
        pour aider le transcriber. Moins fiable qu'une vraie piste stem.
        """
        seg = self._segment(audio_path, start, end, filter_vocal=True)
        with open(seg, "rb") as f:
            r = self.client.audio.transcriptions.create(
                model=model or get_clip_config().get("transcription_model", "openai/whisper-large-v3"),
                file=f, language=language,
            )
        txt = (r.text or "").strip()
        return "" if txt in (".", "..") else txt

    # ── Structure / durée ───────────────────────────────────────

    def get_duration(self, audio_path: str | Path) -> float:
        import soundfile as sf
        y, sr = sf.read(str(audio_path))
        return len(y) / sr

    def analyze_structure(self, audio_path: str | Path, window: float = 6.0) -> dict:
        """Analyse énergétique RMS par fenêtre pour repérer sections et fade.

        Returns:
            {duration, tempo_hint, windows:[{time, rms}], quiet_segments:[...]}
        """
        import numpy as np
        import soundfile as sf
        y, sr = sf.read(str(audio_path))
        if y.ndim > 1:
            y = y.mean(axis=1)
        dur = len(y) / sr
        windows = []
        hop = int(sr * window)
        for i in range(0, len(y), hop):
            seg = y[i : i + hop]
            rms = float(np.sqrt((seg ** 2).mean())) if len(seg) else 0.0
            windows.append({"time": round(i / sr, 1), "rms": round(rms, 4)})
        return {"duration": round(dur, 2), "windows": windows}

    # ── Helpers ─────────────────────────────────────────────────

    def _segment(
        self,
        audio_path: str | Path,
        start: float,
        end: float | None,
        filter_vocal: bool = False,
    ) -> Path:
        """Extraire un segment en WAV 16k mono (ou voie centrale filtrée) dans /tmp."""
        src = str(audio_path)
        key = hashlib.md5(f"{src}:{start}:{end}:{filter_vocal}".encode()).hexdigest()
        out = Path("/tmp/ztudioz_clip") / f"{key}.wav"
        if out.exists():
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", str(start)]
        if end is not None:
            cmd += ["-to", str(end)]
        cmd += ["-i", src]
        if filter_vocal:
            cmd += ["-af", "pan=mono|c0=0.5*c0+0.5*c1,highpass=f=250,lowpass=f=3500,afftdn=nf=-30"]
        cmd += ["-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(out)]
        subprocess.run(cmd, check=True, capture_output=True)
        return out
