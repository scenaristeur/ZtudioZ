"""Catalogue live des modeles Pollinations : sante, prix et options.

Les modeles changent (retires, renommes, prix modifies, service down). Le studio
ne fige pas une liste : il interroge `/v1/models`, `/image/models` et
`/video/models`, normalise les champs utiles et classe par prix puis sante.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Callable

CATALOG_URLS = {
    "text": "https://gen.pollinations.ai/v1/models",
    "image": "https://gen.pollinations.ai/image/models",
    "video": "https://gen.pollinations.ai/video/models",
}

Fetch = Callable[[str], Any]


def _fetch_json(url: str) -> Any:
    request = urllib.request.Request(
        url, headers={"User-Agent": "ztudioz-studio/0.1"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_models(kind: str, *, fetch: Fetch | None = None) -> list[dict[str, Any]]:
    """Retourne les modeles bruts d'une categorie (`text`, `image`, `video`)."""
    if kind not in CATALOG_URLS:
        raise ValueError(f"categorie inconnue: {kind!r} (attendu: {', '.join(CATALOG_URLS)})")
    payload = (fetch or _fetch_json)(CATALOG_URLS[kind])
    if kind == "text":
        if not isinstance(payload, dict):
            return []
        return list(payload.get("data") or [])
    return list(payload or [])


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _price(kind: str, raw: dict[str, Any]) -> tuple[float | None, str]:
    pricing = raw.get("pricing") or {}
    if kind == "video":
        return _as_float(pricing.get("completionVideoSeconds")), "pollen/s"
    if kind == "image":
        value = _as_float(pricing.get("completionImageTokens"))
        units = raw.get("pricing_units") or {}
        unit = ((units.get("completionImageTokens") or {}).get("unit") or "image")
        return value, f"pollen/{unit}"
    value = _as_float(pricing.get("completionTextTokens"))
    return (value * 1_000_000 if value is not None else None), "pollen/Mtok"


def normalize(kind: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Reduit un modele brut aux champs utiles au choix operateur."""
    health = raw.get("health") or {}
    price, unit = _price(kind, raw)
    capabilities = raw.get("capabilities") or []
    return {
        "category": kind,
        "name": str(raw.get("name") or raw.get("id") or ""),
        "title": str(raw.get("title") or ""),
        "aliases": list(raw.get("aliases") or []),
        "status": str(health.get("status") or "unknown"),
        "success_rate": _as_float(health.get("success_rate")),
        "requests": health.get("requests"),
        "paid_only": bool(raw.get("paid_only")),
        "price": price,
        "price_unit": unit,
        "min_duration": raw.get("min_duration"),
        "max_duration": raw.get("max_duration"),
        "default_duration": raw.get("default_duration"),
        "input_modalities": list(raw.get("input_modalities") or []),
        "max_reference_images": raw.get("max_reference_images"),
        "resolutions": list(raw.get("resolutions") or []),
        "capabilities": list(capabilities),
    }


def ranked(
    rows: list[dict[str, Any]],
    *,
    available: bool = False,
    max_price: float | None = None,
) -> list[dict[str, Any]]:
    """Filtre puis classe : prix croissant, puis taux de succes decroissant."""
    kept: list[dict[str, Any]] = []
    for row in rows:
        if available and row.get("status") == "down":
            continue
        price = row.get("price")
        if max_price is not None and (price is None or price > max_price):
            continue
        kept.append(row)
    return sorted(
        kept,
        key=lambda row: (
            row.get("price") is None,
            row.get("price") or 0.0,
            -(row.get("success_rate") or 0.0),
        ),
    )


def format_price(row: dict[str, Any]) -> str:
    price = row.get("price")
    if price is None:
        return "-"
    return f"{price:g} {row.get('price_unit')}"


def format_options(row: dict[str, Any]) -> str:
    parts = []
    if row.get("min_duration") is not None or row.get("max_duration") is not None:
        parts.append(f"{row.get('min_duration') or '?'}-{row.get('max_duration') or '?'}s")
    if row.get("max_reference_images"):
        parts.append(f"{row['max_reference_images']} refs")
    if row.get("input_modalities"):
        parts.append("+".join(row["input_modalities"]))
    if row.get("resolutions"):
        parts.append("/".join(row["resolutions"]))
    return ", ".join(parts) or "-"


def suitable(row: dict[str, Any], kind: str, duration: float = 5.0) -> bool:
    """Un remplacant doit accepter le mode et la duree demandes."""
    if kind == "video":
        if "image" not in (row.get("input_modalities") or []):
            return False
        low = row.get("min_duration")
        high = row.get("max_duration")
        if low is not None and duration < float(low):
            return False
        if high is not None and duration > float(high):
            return False
    return True


def matches(row: dict[str, Any], current: str) -> bool:
    """Le modele courant peut etre un nom complet, un alias ou le suffixe."""
    if not current:
        return False
    return (
        current == row.get("name")
        or current == str(row.get("name", "")).rsplit("/", 1)[-1]
        or current in (row.get("aliases") or [])
    )
