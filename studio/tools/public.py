"""Export public d'un média vers tmpfiles.org (exigence de certains providers).

C'est un export vers un tiers : le noyau ne l'appelle que lorsqu'un provider réel
le demande, jamais en mock. D30 assume ce choix pour les runs vidéo/image réels.
"""

from __future__ import annotations

import json
import re
import urllib.request
import uuid

USER_AGENT = "ztudioz-studio/0.1"


def upload_public(data: bytes, filename: str, expire: int = 172800) -> str:
    """Rend un média publiquement joignable et retourne l'URL directe."""
    boundary = uuid.uuid4().hex
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="expire"\r\n\r\n{expire}\r\n'.encode(),
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            data,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    request = urllib.request.Request(
        "https://tmpfiles.org/api/v1/upload",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    page_url = payload["data"]["url"]
    page_request = urllib.request.Request(page_url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(page_request, timeout=30) as response:
        page = response.read().decode("utf-8", "replace")
    match = re.search(r'tmpfiles\.org(/dl/[^"\']+)', page)
    return "https://tmpfiles.org" + match.group(1) if match else page_url
