"""Providers video : protocole commun, mock deterministe, adaptateur Pollinations.

Contrairement aux images, une video se facture a la seconde : `estimate` prend
donc la duree, et le moteur refuse un plan trop cher **avant** l'appel reseau.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from studio.kernel.artifacts import Binary

from .public import upload_public as _upload_public

USER_AGENT = "ztudioz-studio/0.1"


@runtime_checkable
class VideoProvider(Protocol):
    """Contrat d'un provider image -> video."""

    def estimate(self, duration: float) -> float:
        """Cout estime d'une video de `duration` secondes, en credits."""

    async def generate(
        self,
        *,
        agent_id: str,
        model: str,
        system: str,
        prompt: str,
        reference: Binary | None = None,
        references: list[Binary] | None = None,
        duration: float = 5.0,
    ) -> Binary:
        """Produit un mp4 a partir d'un prompt et, si fournies, d'images."""


def _require_image(reference: Binary | None) -> None:
    if reference is None:
        return
    if not isinstance(reference, Binary) or not str(reference.media_type).startswith("image/"):
        media_type = getattr(reference, "media_type", type(reference).__name__)
        raise ValueError(f"video: la reference doit etre une image, recu: {media_type}")


def _color_clip(color: str, duration: float, size: int = 64) -> Binary:
    """Un mp4 h264 deterministe : sert de sortie video en test, sans reseau."""
    out = Path(tempfile.mkstemp(prefix="ztudioz-video-", suffix=".mp4")[1])
    argv = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c=0x{color}:s={size}x{size}:d={duration}:r=25",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-fflags", "+bitexact", "-flags:v", "+bitexact",
        str(out),
    ]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg video mock: {proc.stderr[-300:]}")
        return Binary(out.read_bytes(), "video/mp4")
    finally:
        out.unlink(missing_ok=True)


class MockVideoProvider:
    """Provider deterministe : aucun reseau, un vrai mp4, cout parametrable."""

    def __init__(
        self,
        cost_per_second: float = 0.0,
        delay: float = 0.0,
        size: int = 64,
        fail_on: int | None = None,
    ):
        self.cost_per_second = cost_per_second
        self.delay = delay
        self.size = size
        self.fail_on = fail_on
        self.calls = 0
        self.max_concurrency = 0
        self.records: list[dict[str, Any]] = []
        self._active = 0

    def estimate(self, duration: float) -> float:
        return self.cost_per_second * float(duration)

    async def generate(
        self,
        *,
        agent_id: str,
        model: str,
        system: str,
        prompt: str,
        reference: Binary | None = None,
        references: list[Binary] | None = None,
        duration: float = 5.0,
    ) -> Binary:
        refs = references or ([reference] if isinstance(reference, Binary) else [])
        for ref in refs:
            _require_image(ref)
        self._active += 1
        self.max_concurrency = max(self.max_concurrency, self._active)
        self.calls += 1
        self.records.append(
            {
                "agent": agent_id,
                "model": model,
                "system": system,
                "prompt": prompt,
                "reference": reference.hash if isinstance(reference, Binary) else None,
                "references": [ref.hash for ref in refs],
                "duration": float(duration),
            }
        )
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.fail_on is not None and self.calls == self.fail_on:
                raise RuntimeError(f"video mock: echec simule sur l'appel {self.calls}")
            digest = hashlib.sha256(
                f"{prompt}|{duration}|{'|'.join(ref.hash for ref in refs)}".encode()
            ).digest()
            return await asyncio.to_thread(_color_clip, digest[:3].hex(), float(duration), self.size)
        finally:
            self._active -= 1


class PollinationsVideoProvider:
    """Adaptateur reel (https://gen.pollinations.ai/video) : image -> video.

    Facturation a la seconde, donc `estimate` multiplie le tarif par la duree.
    L'image de reference doit etre publiquement joignable : elle est televersee
    vers tmpfiles.org au moment de l'appel, comme le faisait la v1.
    """

    def __init__(
        self,
        base: str = "https://gen.pollinations.ai",
        model: str = "wan-fast",
        api_key_env: str = "POLLINATION_API_KEY",
        width: int = 512,
        height: int = 512,
        cost_per_second: float = 0.01,
        max_retries: int = 3,
        timeout: float = 600.0,
    ):
        self.base = base.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.width = width
        self.height = height
        self.cost_per_second = cost_per_second
        self.max_retries = max_retries
        self.timeout = timeout
        self._gate = asyncio.Lock()

    def estimate(self, duration: float) -> float:
        return self.cost_per_second * float(duration)

    async def generate(
        self,
        *,
        agent_id: str,
        model: str,
        system: str,
        prompt: str,
        reference: Binary | None = None,
        references: list[Binary] | None = None,
        duration: float = 5.0,
    ) -> Binary:
        refs = references or ([reference] if isinstance(reference, Binary) else [])
        for ref in refs:
            _require_image(ref)
        reference = refs[0] if refs else None
        api_key = os.environ.get(self.api_key_env, "") if self.api_key_env else ""
        seed = int.from_bytes(
            hashlib.sha256(
                f"{prompt}|{duration}|{'|'.join(ref.hash for ref in refs)}".encode()
            ).digest()[:8],
            "big",
        ) % 2_147_483_647
        params = {
            "width": self.width,
            "height": self.height,
            "duration": duration,
            "model": model or self.model,
            "seed": seed,
        }
        if api_key:
            params["key"] = api_key
        if reference is not None:
            suffix = ".jpg" if "jpeg" in reference.media_type else ".png"
            params["image"] = await asyncio.to_thread(
                _upload_public, reference.data, f"{reference.hash}{suffix}"
            )

        url = f"{self.base}/video/{urllib.parse.quote(prompt, safe='')}?{urllib.parse.urlencode(params)}"
        async with self._gate:
            return await asyncio.to_thread(self._fetch, url, api_key)

    def _fetch(self, url: str, api_key: str) -> Binary:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            delay: float | None = None
            try:
                headers = {"User-Agent": USER_AGENT}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    data = response.read()
                    content_type = response.headers.get("content-type", "")
                if not data:
                    raise RuntimeError("pollinations video: reponse vide")
                if "video" not in content_type and data[4:8] != b"ftyp":
                    raise RuntimeError(f"pollinations video: reponse non video ({content_type[:60]})")
                return Binary(data, "video/mp4")
            except urllib.error.HTTPError as exc:
                try:
                    body = exc.read().decode("utf-8", "replace")[:400]
                except Exception:
                    body = ""
                last_exc = RuntimeError(f"HTTP {exc.code}: {body}") if body else exc
                delay = _retry_delay(exc)
                if delay is None and exc.code in (429, 500, 502, 503, 504):
                    delay = 5.0 * (2**attempt)
            except OSError as exc:
                last_exc = exc
                delay = 5.0 * (2**attempt)
            if delay is not None and attempt < self.max_retries:
                import time

                time.sleep(delay)
                continue
            break
        raise RuntimeError(f"pollinations video: echec ({last_exc})") from last_exc


def _retry_delay(exc: urllib.error.HTTPError) -> float | None:
    """Delai annonce par le serveur (Retry-After), en secondes."""
    header = exc.headers.get("Retry-After") if exc.headers else None
    if not header:
        return None
    try:
        return max(0.0, float(header))
    except ValueError:
        return None
