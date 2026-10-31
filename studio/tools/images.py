"""Adaptateurs image : protocol commun, mock deterministe, adaptateur OpenAI-compatible.

Le mock produit de vrais fichiers PNG (8x8, couleur derivee de l'empreinte du
prompt) : les tests manipulent donc des octets reels, sans aucun acces reseau.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import struct
import zlib
from typing import Any, Protocol, runtime_checkable

from studio.kernel.artifacts import Binary


@runtime_checkable
class ImageProvider(Protocol):
    """Contrat minimal d'un adaptateur image : estimation de cout et generation."""

    def estimate(self) -> float:
        """Cout estime d'une image, en credits."""

    async def generate(
        self,
        *,
        agent_id: str,
        model: str,
        system: str,
        prompt: str,
        reference: Binary | None = None,
    ) -> Binary:
        """Produit une image binaire pour un agent."""


def encode_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """PNG 24 bits monochrome plein, encode avec la seule bibliotheque standard."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    row = b"\x00" + bytes(rgb) * width
    raw = row * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


class MockImageProvider:
    """Adaptateur deterministe : aucun reseau, bytes reels, cout parametrible."""

    def __init__(
        self,
        cost: float = 0.0,
        delay: float = 0.0,
        media_type: str = "image/png",
        size: tuple[int, int] = (8, 8),
    ):
        self.cost = cost
        self.delay = delay
        self.media_type = media_type
        self.size = size
        self.calls = 0
        self.max_concurrency = 0
        self.records: list[dict[str, Any]] = []
        self._active = 0

    def estimate(self) -> float:
        """Cout fixe par image."""
        return self.cost

    async def generate(
        self,
        *,
        agent_id: str,
        model: str,
        system: str,
        prompt: str,
        reference: Binary | None = None,
    ) -> Binary:
        """Retourne un PNG dont la couleur derive du prompt et de la reference."""
        self._active += 1
        self.max_concurrency = max(self.max_concurrency, self._active)
        self.calls += 1
        self.records.append(
            {
                "agent": agent_id,
                "model": model,
                "prompt": prompt,
                "reference": reference.hash if isinstance(reference, Binary) else None,
            }
        )
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            seed = hashlib.sha256(
                prompt.encode("utf-8")
                + (reference.data if isinstance(reference, Binary) else b"")
            ).digest()
            return Binary(encode_png(self.size[0], self.size[1], (seed[0], seed[1], seed[2])), self.media_type)
        finally:
            self._active -= 1


class OpenAICompatImageProvider:
    """Adaptateur minimal pour toute API d'images compatible OpenAI.

    Ecrit en M1, jamais exerce en test : un run reel est lance sur demande
    explicite de l'humain (dette D14).
    """

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key_env: str = "OPENAI_API_KEY",
        size: str = "256x256",
        cost: float = 0.0,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.size = size
        self.cost = cost

    def estimate(self) -> float:
        """Cout declare par image ; affinable quand la table de prix existe."""
        return self.cost

    async def generate(
        self,
        *,
        agent_id: str,
        model: str,
        system: str,
        prompt: str,
        reference: Binary | None = None,
    ) -> Binary:
        """Appelle /images/generations et retourne les octets decodes."""
        import json

        import urllib.request

        api_key = os.environ.get(self.api_key_env, "")
        if not api_key:
            raise RuntimeError(f"cle absente: {self.api_key_env}")

        body: dict[str, Any] = {
            "model": model or self.model,
            "prompt": f"{system}\n\n{prompt}" if system else prompt,
            "size": self.size,
            "n": 1,
            "response_format": "b64_json",
        }
        request = urllib.request.Request(
            f"{self.base_url}/images/generations",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )

        def call() -> Binary:
            import urllib.request as urllib_request

            with urllib_request.urlopen(request, timeout=300) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return Binary(base64.b64decode(payload["data"][0]["b64_json"]), "image/png")

        return await asyncio.to_thread(call)


__all__ = ["ImageProvider", "MockImageProvider", "OpenAICompatImageProvider", "encode_png"]
