"""Providers Agnes locaux (image et vidéo), compatibles avec le serveur :8765.

L'image accepte une référence (image->image) ; plusieurs références passent par
la composition apihub (`AGNES_API_KEY`). La vidéo accepte 1 image (i2v) ou 2
images (keyframes start/end), comme le pipeline v1.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from studio.kernel.artifacts import Binary

USER_AGENT = "ztudioz-studio/0.1"

_sleep = time.sleep


def _media_type(data: bytes, content_type: str = "") -> str:
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


def _get_bytes(url: str, timeout: float = 300.0) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(), response.headers.get("Content-Type", "")


def _get_json(url: str, timeout: float = 60.0) -> dict:
    data, _ = _get_bytes(url, timeout)
    return json.loads(data.decode("utf-8"))


def _post_multipart(
    url: str,
    fields: dict[str, str],
    files: dict[str, tuple[str, bytes, str]] | None = None,
    timeout: float = 300.0,
) -> dict:
    """POST multipart minimal (urllib), pour les routes locales Agnes."""
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode(),
                b"\r\n",
            ]
        )
    for name, (filename, data, media_type) in (files or {}).items():
        parts.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode(),
                f"Content-Type: {media_type}\r\n\r\n".encode(),
                data,
                b"\r\n",
            ]
        )
    parts.append(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        url,
        data=b"".join(parts),
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(
    url: str, body: dict, headers: dict[str, str], timeout: float = 300.0
) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT, **headers},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class AgnesImageProvider:
    """Image locale Agnes : texte->image et image->image."""

    VALID_SIZES = (
        "1024x1024", "768x1152", "1152x768", "768x1344",
        "1344x768", "1792x1024", "1024x1792",
    )

    def __init__(
        self,
        base_url: str = "http://localhost:8765",
        apihub_url: str = "https://apihub.agnes-ai.com",
        api_key_env: str = "AGNES_API_KEY",
        size: str = "1152x768",
        compose_model: str = "agnes-image-2.1-flash",
        cost: float = 0.0,
        timeout: float = 300.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.apihub_url = apihub_url.rstrip("/")
        self.api_key_env = api_key_env
        self.size = size
        self.compose_model = compose_model
        self.cost = cost
        self.timeout = timeout

    def estimate(self) -> float:
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
        refs = [
            ref
            for ref in (references or [])
            if isinstance(ref, Binary) and str(ref.media_type).startswith("image/")
        ]
        if not refs and isinstance(reference, Binary):
            refs = [reference]
        if len(refs) > 1:
            return await asyncio.to_thread(self._compose, prompt, refs)
        return await asyncio.to_thread(self._generate, prompt, refs[0] if refs else None)

    def _generate(self, prompt: str, reference: Binary | None) -> Binary:
        fields = {"prompt": prompt, "size": self.size}
        files = None
        if reference is not None:
            suffix = ".jpg" if "jpeg" in reference.media_type else ".png"
            files = {
                "reference_image": (
                    f"{reference.hash}{suffix}",
                    reference.data,
                    reference.media_type,
                )
            }
        payload = _post_multipart(
            f"{self.base_url}/api/image/generate", fields, files, self.timeout
        )
        task_id = payload.get("task_id")
        if not task_id:
            raise RuntimeError(f"agnes image: task_id absent ({payload})")
        data, content_type = _get_bytes(f"{self.base_url}/api/image/{task_id}", self.timeout)
        return Binary(data, _media_type(data, content_type))

    def _compose(self, prompt: str, refs: list[Binary]) -> Binary:
        api_key = os.environ.get(self.api_key_env, "")
        if not api_key:
            raise RuntimeError(f"agnes compose: cle absente: {self.api_key_env}")
        images = [
            f"data:{ref.media_type};base64,{base64.b64encode(ref.data).decode()}"
            for ref in refs
        ]
        body = {
            "model": self.compose_model,
            "prompt": prompt,
            "size": self.size,
            "extra_body": {"image": images, "response_format": "url"},
        }
        payload = _post_json(
            f"{self.apihub_url}/v1/images/generations",
            body,
            {"Authorization": f"Bearer {api_key}"},
            self.timeout,
        )
        url = (payload.get("data") or [{}])[0].get("url")
        if not url:
            raise RuntimeError(f"agnes compose: URL absente ({payload})")
        data, content_type = _get_bytes(url, self.timeout)
        return Binary(data, _media_type(data, content_type))


class AgnesVideoProvider:
    """Vidéo locale Agnes : t2v, i2v, et keyframes start/end avec 2 références."""

    VALID_DURATIONS = (5, 10, 15, 18, 20)

    def __init__(
        self,
        base_url: str = "http://localhost:8765",
        width: int = 1152,
        height: int = 768,
        duration: int = 5,
        dev: bool = True,
        dev_width: int = 480,
        dev_height: int = 270,
        cost_per_second: float = 0.0,
        timeout: float = 3600.0,
        poll_interval: float = 10.0,
        max_retries: int = 2,
        retry_delay: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.width = width
        self.height = height
        self.duration = duration
        self.dev = dev
        self.dev_width = dev_width
        self.dev_height = dev_height
        self.cost_per_second = cost_per_second
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.max_retries = max_retries
        self.retry_delay = retry_delay

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
        refs = [
            ref
            for ref in (references or [])
            if isinstance(ref, Binary) and str(ref.media_type).startswith("image/")
        ]
        if not refs and isinstance(reference, Binary):
            refs = [reference]
        refs = refs[:2]
        return await asyncio.to_thread(self._generate, prompt, refs, duration)

    def _generate(self, prompt: str, refs: list[Binary], duration: float) -> Binary:
        target = duration if duration and duration > 0 else self.duration
        d = min(self.VALID_DURATIONS, key=lambda value: abs(value - target))
        width, height = (self.dev_width, self.dev_height) if self.dev else (self.width, self.height)
        mode = "keyframes" if len(refs) >= 2 else ("i2v" if refs else "t2v")
        fields = {
            "prompt": prompt,
            "mode": mode,
            "duration": str(d),
            "video_width": str(width),
            "video_height": str(height),
        }
        files = {}
        if refs:
            suffix = ".jpg" if "jpeg" in refs[0].media_type else ".png"
            files["reference_image"] = (
                f"start{refs[0].hash}{suffix}",
                refs[0].data,
                refs[0].media_type,
            )
        if len(refs) >= 2:
            suffix = ".jpg" if "jpeg" in refs[1].media_type else ".png"
            files["end_frame_image"] = (
                f"end{refs[1].hash}{suffix}",
                refs[1].data,
                refs[1].media_type,
            )
        payload = self._submit(fields, files)
        task_id = payload.get("task_id")
        if not task_id:
            raise RuntimeError(f"agnes video: task_id absent ({payload})")
        self._poll(task_id)
        data, content_type = _get_bytes(f"{self.base_url}/api/video/{task_id}", self.timeout)
        if not data:
            raise RuntimeError("agnes video: reponse vide")
        return Binary(data, "video/mp4")

    def _submit(self, fields: dict, files: dict) -> dict:
        """Soumet la tache ; la file Agnes pleine (503) est retentee puis expliquee."""
        attempts = max(0, int(self.max_retries)) + 1
        for attempt in range(1, attempts + 1):
            try:
                return _post_multipart(
                    f"{self.base_url}/api/tasks/simple", fields, files, timeout=120
                )
            except urllib.error.HTTPError as exc:
                try:
                    body = exc.read().decode("utf-8", "replace")[:400]
                except Exception:
                    body = ""
                queue_full = exc.code == 503 and "video_queue_full" in body
                if queue_full and attempt < attempts:
                    _sleep(self.retry_delay)
                    continue
                if queue_full:
                    raise RuntimeError(
                        "agnes video: file d'attente pleine (503 video_queue_full) "
                        f"apres {attempt} tentative(s) ; reessayez plus tard, "
                        "idealement hors des heures de pointe"
                    ) from exc
                raise RuntimeError(
                    f"agnes video: soumission refusee (HTTP {exc.code}): {body}"
                ) from exc
        raise RuntimeError("agnes video: soumission impossible")

    def _poll(self, task_id: str) -> None:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                task = _get_json(f"{self.base_url}/api/tasks/{task_id}", timeout=60)
            except OSError:
                _sleep(self.poll_interval)
                continue
            status = task.get("status", "")
            if status == "completed":
                return
            if status == "failed":
                raise RuntimeError(
                    f"agnes video: tache {task_id} en echec: "
                    f"{task.get('current_message') or task.get('error') or ''}"
                )
            _sleep(self.poll_interval)
        raise RuntimeError(f"agnes video: tache {task_id} expiree")
