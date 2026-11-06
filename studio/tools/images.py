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
        references: list[Binary] | None = None,
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
        references: list[Binary] | None = None,
    ) -> Binary:
        """Retourne un PNG dont la couleur derive du prompt et des references."""
        refs = references or ([reference] if isinstance(reference, Binary) else [])
        self._active += 1
        self.max_concurrency = max(self.max_concurrency, self._active)
        self.calls += 1
        self.records.append(
            {
                "agent": agent_id,
                "model": model,
                "prompt": prompt,
                "reference": reference.hash if isinstance(reference, Binary) else None,
                "references": [ref.hash for ref in refs],
            }
        )
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            seed = hashlib.sha256(
                prompt.encode("utf-8") + b"".join(ref.data for ref in refs)
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

    def _resolve_model(self, agent_model: str) -> str:
        """Meme regle que le provider LLM : les placeholders mock* cèdent la place."""
        if agent_model and not agent_model.startswith("mock"):
            return agent_model
        return self.model

    async def generate(
        self,
        *,
        agent_id: str,
        model: str,
        system: str,
        prompt: str,
        reference: Binary | None = None,
        references: list[Binary] | None = None,
    ) -> Binary:
        """Appelle /images/generations et retourne les octets decodes.

        Cet adaptateur ne transmet pas encore les references : le contrat les
        accepte pour rester compatible, mais le provider doit les supporter.
        """
        import json

        import urllib.request

        api_key = os.environ.get(self.api_key_env, "")
        if not api_key:
            raise RuntimeError(f"cle absente: {self.api_key_env}")

        body: dict[str, Any] = {
            "model": self._resolve_model(model),
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


class PollinationsImageProvider:
    """Adaptateur reel Pollinations : texte->image, et image(s)->image via gptimage.

    Sans reference : route GET `image.pollinations.ai/prompt/...` (flux).
    Avec reference(s) : route `gen.pollinations.ai` avec `image=URL` (gptimage),
    ou POST `/v1/images/generations` avec une liste d'images pour le multi-ref.
    """

    REF_SUPPORT = {"gptimage", "gpt-image-2", "gptimage-large"}

    def __init__(
        self,
        base: str = "https://image.pollinations.ai",
        model: str = "flux",
        api_key_env: str = "POLLINATION_API_KEY",
        width: int = 512,
        height: int = 512,
        cost: float = 0.0,
        max_retries: int = 5,
        min_interval: float = 45.0,
        ref_base: str = "https://gen.pollinations.ai",
        ref_model: str = "gptimage",
        multi_ref_model: str = "gptimage",
        strict_refs: bool = False,
    ):
        self.base = base.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.width = width
        self.height = height
        self.cost = cost
        self.max_retries = max_retries
        self.min_interval = min_interval
        self.ref_base = ref_base.rstrip("/")
        self.ref_model = ref_model
        self.multi_ref_model = multi_ref_model
        self.strict_refs = strict_refs
        self._gate = asyncio.Lock()
        self._last_call: float | None = None

    def estimate(self) -> float:
        """Cout declare par image ; Pollinations est gratuit sous quota."""
        return self.cost

    async def _pace(self) -> None:
        import time

        now = time.monotonic()
        if self._last_call is not None and self.min_interval > 0:
            wait = self.min_interval - (now - self._last_call)
            if wait > 0:
                await asyncio.to_thread(_sleep, wait)
        self._last_call = time.monotonic()

    async def generate(
        self,
        *,
        agent_id: str,
        model: str,
        system: str,
        prompt: str,
        reference: Binary | None = None,
        references: list[Binary] | None = None,
    ) -> Binary:
        """Genere une image ; les references sont reellement envoyees si fournies."""
        refs = [
            ref
            for ref in (references or [])
            if isinstance(ref, Binary) and str(ref.media_type).startswith("image/")
        ]
        if not refs and isinstance(reference, Binary):
            refs = [reference]
        if refs:
            return await self._generate_with_refs(prompt, model, refs)
        return await self._generate_text(prompt)

    async def _generate_text(self, prompt: str) -> Binary:
        import hashlib
        from urllib.parse import quote

        seed = int.from_bytes(hashlib.sha256(prompt.encode()).digest()[:8], "big") % 2147483647
        url = (
            f"{self.base}/prompt/{quote(prompt, safe='')}"
            f"?model={self.model}&width={self.width}&height={self.height}"
            f"&seed={seed}&nologo=true&safe=true"
        )
        api_key = os.environ.get(self.api_key_env, "") if self.api_key_env else ""
        async with self._gate:
            await self._pace()
            return await self._fetch(url, api_key or None)

    async def _generate_with_refs(self, prompt: str, model: str, refs: list[Binary]) -> Binary:
        import hashlib
        from urllib.parse import quote

        ref_model = model if model in self.REF_SUPPORT else self.ref_model
        if ref_model not in self.REF_SUPPORT:
            if self.strict_refs:
                raise RuntimeError(
                    f"strict_refs: le modele {ref_model} ne supporte pas les references image"
                )
            ref_model = "gptimage"

        from .public import upload_public

        urls = [
            await asyncio.to_thread(
                upload_public, ref.data, f"{ref.hash}{_media_suffix(ref.media_type)}"
            )
            for ref in refs
        ]
        seed = int.from_bytes(
            hashlib.sha256(
                prompt.encode() + b"".join(ref.data for ref in refs)
            ).digest()[:8],
            "big",
        ) % 2147483647
        api_key = os.environ.get(self.api_key_env, "") if self.api_key_env else ""

        async with self._gate:
            await self._pace()
            if len(urls) == 1:
                url = (
                    f"{self.ref_base}/image/{quote(prompt, safe='')}"
                    f"?model={ref_model}&width={self.width}&height={self.height}"
                    f"&seed={seed}&nologo=true&image={quote(urls[0], safe='')}"
                )
                if api_key:
                    url += f"&key={quote(api_key, safe='')}"
                return await self._fetch(url, api_key or None)

            payload = {
                "model": self.multi_ref_model or ref_model,
                "prompt": prompt,
                "n": 1,
                "image": urls,
                "response_format": "b64_json",
                "size": f"{self.width}x{self.height}",
            }
            try:
                data = await asyncio.to_thread(
                    _http_post_json,
                    f"{self.ref_base}/v1/images/generations",
                    payload,
                    api_key or None,
                )
            except Exception as exc:
                detail = _http_error_detail(exc)
                message = f"pollinations multiref: echec ({exc})"
                if detail:
                    message += f": {detail}"
                raise RuntimeError(message) from exc
            try:
                raw = base64.b64decode(data["data"][0]["b64_json"])
            except (KeyError, IndexError, TypeError) as exc:
                raise RuntimeError(f"pollinations multiref: reponse invalide: {data}") from exc
            return Binary(raw, _detect_media_type(raw, ""))

    async def _fetch(self, url: str, api_key: str | None) -> Binary:
        import urllib.error

        last_exc: OSError | None = None
        detail = ""
        for attempt in range(self.max_retries + 1):
            delay: float | None = None
            try:
                data, content_type = await asyncio.to_thread(_http_get, url, api_key or None)
                return Binary(data, _detect_media_type(data, content_type))
            except urllib.error.HTTPError as exc:
                last_exc = exc
                detail = _http_error_detail(exc)
                delay = _rate_limit_delay(exc)
                if delay is None and exc.code in (429, 500, 502, 503, 504):
                    delay = 5.0 * (2**attempt)  # incident passager sans delai annonce
            except OSError as exc:
                last_exc = exc
                delay = 5.0 * (2**attempt)  # panne reseau : meme politesse
            if delay is not None and attempt < self.max_retries:
                await asyncio.to_thread(_sleep, delay)
                continue
            break
        message = f"pollinations: echec ({last_exc})"
        if detail:
            message += f": {detail}"
        raise RuntimeError(message) from last_exc


def _http_error_body(exc: BaseException) -> bytes:
    """Lit le corps d'une erreur HTTP une seule fois (le flux n'est pas rejouable)."""
    cached = getattr(exc, "_studio_body", None)
    if cached is not None:
        return cached
    read = getattr(exc, "read", None)
    if not callable(read):
        return b""
    try:
        raw = read()
    except Exception:
        return b""
    body = raw.encode("utf-8", "replace") if isinstance(raw, str) else raw
    try:
        setattr(exc, "_studio_body", body)
    except Exception:
        pass
    return body


def _http_error_detail(exc: BaseException) -> str:
    """Corps d'une erreur HTTP, borne : l'API dit souvent pourquoi (400, 403...)."""
    text = _http_error_body(exc).decode("utf-8", "replace")
    return " ".join(text.split())[:500]


def _media_suffix(media_type: str) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }.get(str(media_type), ".png")


def _http_post_json(url: str, payload: dict, token: str | None = None, timeout: float = 300.0) -> dict:
    """POST JSON minimal ; separe pour que les tests restent hors reseau."""
    import json
    import urllib.request

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _http_get(url: str, token: str | None = None, timeout: float = 120.0) -> tuple[bytes, str]:
    """GET minimal ; separe pour que les tests restent hors reseau."""
    import urllib.request

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(), response.headers.get("Content-Type", "")


def _sleep(seconds: float) -> None:
    """Point de sommeil isolé, bouchonné en test."""
    import time

    time.sleep(seconds)


def _rate_limit_delay(exc) -> float | None:
    """Si l'erreur HTTP annonce un rythme (« Retry after Ns »), on le retourne borne.

    Le tier gratuit connait deux limitations : par IP (FREE_LIMIT) et globale
    (SERVICE_UNAVAILABLE), presentees tantot en 402, tantot en 503. L'important
    est le delai annonce, pas le code.
    """
    import json
    import re

    if getattr(exc, "code", 0) not in (402, 429, 500, 502, 503, 504):
        return None
    try:
        payload = json.loads(_http_error_body(exc).decode("utf-8", "replace"))
    except Exception:
        return None
    error = payload.get("error") or {}
    message = str(error.get("message", ""))
    match = re.search(r"Retry after (\d+)s", message)
    if match:
        return min(float(match.group(1)), 90.0)
    if error.get("code") in ("FREE_LIMIT", "SERVICE_UNAVAILABLE"):
        return 45.0
    return None


def _detect_media_type(data: bytes, content_type: str) -> str:
    for known in ("image/png", "image/jpeg", "image/webp"):
        if known in content_type:
            return known
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


__all__ = [
    "ImageProvider",
    "MockImageProvider",
    "OpenAICompatImageProvider",
    "PollinationsImageProvider",
    "encode_png",
]
