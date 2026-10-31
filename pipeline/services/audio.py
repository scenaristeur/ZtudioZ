from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

import requests

from pipeline.config import get_service_config
from pipeline.consumption import get_tracker

logger = logging.getLogger(__name__)


class AudioService:
    def __init__(self, config: dict | None = None):
        self.config = config or get_service_config("audio")

    def say(self, text: str, voice: str | None = None, output_dir: str | Path | None = None, filename: str | None = None) -> str:
        import time as _time
        t0 = _time.time()
        cfg = self.config
        v = voice or cfg.get("voice", "onyx")
        cache_key = hashlib.md5(f"say:{text}:{v}".encode()).hexdigest()
        if output_dir:
            out = Path(output_dir)
            out.mkdir(parents=True, exist_ok=True)
            fname = filename or f"{cache_key}.mp3"
            out_path = out / fname
            if out_path.exists():
                logger.info(f"Cache hit: {out_path}")
                get_tracker().track("audio", "say", "cache_hit", duration_ms=0)
                return str(out_path)

        encoded = text.replace(" ", "%20")
        url = f"https://text.pollinations.ai/say/{encoded}?voice={v}"
        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            elapsed = (_time.time() - t0) * 1000
            get_tracker().track("audio", "say", "generate", duration_ms=elapsed)
            if output_dir:
                out_path.write_bytes(resp.content)
                return str(out_path)
            return resp.url
        except requests.RequestException as e:
            elapsed = (_time.time() - t0) * 1000
            get_tracker().track("audio", "say", "generate", duration_ms=elapsed, status="error", error=str(e))
            raise RuntimeError(f"TTS failed: {e}")

    def respond(self, prompt: str, voice: str | None = None, output_dir: str | Path | None = None, filename: str | None = None) -> str:
        import time as _time
        t0 = _time.time()
        cfg = self.config
        v = voice or cfg.get("voice", "onyx")
        cache_key = hashlib.md5(f"respond:{prompt}:{v}".encode()).hexdigest()
        if output_dir:
            out = Path(output_dir)
            out.mkdir(parents=True, exist_ok=True)
            fname = filename or f"{cache_key}.mp3"
            out_path = out / fname
            if out_path.exists():
                get_tracker().track("audio", "respond", "cache_hit", duration_ms=0)
                return str(out_path)

        resp = requests.post(
            "https://text.pollinations.ai/respond",
            json={"prompt": prompt, "voice": v},
            timeout=60,
        )
        resp.raise_for_status()
        elapsed = (_time.time() - t0) * 1000
        get_tracker().track("audio", "respond", "generate", duration_ms=elapsed)
        if output_dir:
            out_path.write_bytes(resp.content)
            return str(out_path)
        return resp.url
