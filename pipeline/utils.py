from __future__ import annotations

import logging
import re
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


def upload_to_tmpfiles(file_path: str | Path, expire: int = 172800) -> str:
    """Upload a file to tmpfiles.org and return the direct image URL.

    Args:
        file_path: Chemin local du fichier
        expire: Durée de vie en secondes (60-172800, défaut 172800 = 48h)

    Returns:
        Direct image URL (tmpfiles.org/dl/...)
    """
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    resp = requests.post(
        "https://tmpfiles.org/api/v1/upload",
        files={"file": (p.name, p.read_bytes())},
        data={"expire": str(max(60, min(expire, 172800)))},
        timeout=30,
    )
    resp.raise_for_status()
    page_url = resp.json()["data"]["url"]

    # La page contient un lien direct vers l'image: tmpfiles.org/dl/{hash}/{id}/{name}
    page = requests.get(page_url, timeout=15)
    m = re.search(r'tmpfiles\.org(/dl/[^"\']+)', page.text)
    if m:
        direct_url = "https://tmpfiles.org" + m.group(1)
    else:
        direct_url = page_url

    logger.info(f"Uploaded {p.name} → {direct_url}")
    return direct_url
