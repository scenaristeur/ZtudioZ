import os
from pathlib import Path
from typing import Any
from dotenv import load_dotenv

import yaml

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")

_CONFIG_CACHE: dict[str, Any] | None = None


def _resolve_env(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    return value


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    global _CONFIG_CACHE
    if path is None:
        path = Path(__file__).parent / "config.yaml"
    with open(path) as f:
        cfg = yaml.safe_load(f)
    cfg = _resolve_env(cfg)
    _CONFIG_CACHE = cfg
    return cfg


def get_config() -> dict[str, Any]:
    if _CONFIG_CACHE is None:
        return load_config()
    return _CONFIG_CACHE


def get_service_config(service: str) -> dict[str, Any]:
    cfg = get_config()
    providers = cfg.get("services", {}).get(service, {})
    active = providers.get("active", list(providers.get("providers", {}).keys())[0])
    return providers.get("providers", {}).get(active, {})


def get_service_providers_ordered(service: str) -> list[tuple[str, dict[str, Any]]]:
    """Providers d'un service, ordonnés [actif en premier, puis backups].

    Permet un fallback provider automatique : si le provider actif échoue (402/503,
    serveur down, crédits épuisés), on retente avec le backup suivant. Leçons tirées du
    clip V4 où un 402 de Pollinations a pu bloquer la production jusqu'à bascule manuelle
    sur Agnes.

    Returns:
        Liste de (nom_provider, config_provider), actif en premier.
    """
    cfg = get_config()
    providers = cfg.get("services", {}).get(service, {})
    all_prov = providers.get("providers", {})
    if not all_prov:
        return []
    active = providers.get("active", list(all_prov.keys())[0])
    order = [active] + [n for n in all_prov.keys() if n != active]
    # On injecte le nom dans la config du provider (utile au fallback qui compare les noms).
    return [(n, {**all_prov[n], "name": n}) for n in order]


def get_production_config() -> dict[str, Any]:
    return get_config().get("production", {})


def get_clip_config() -> dict[str, Any]:
    return get_config().get("clip", {})


def get_clip_audio_config() -> dict[str, Any]:
    providers = get_clip_config().get("audio", {})
    active = providers.get("active", list(providers.get("providers", {}).keys())[0])
    return providers.get("providers", {}).get(active, {})


def get_bible_config() -> dict[str, Any]:
    providers = get_config().get("bible", {})
    active = providers.get("active", "yaml")
    return providers.get("providers", {}).get(active, {})


def get_project_root(slug: str | None = None) -> Path:
    cfg = get_config()
    base = Path(cfg.get("project", {}).get("root", "../projets"))
    if slug:
        base = base / slug
    return base.resolve()
