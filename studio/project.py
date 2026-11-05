"""Projet studio : config declarative, providers et racine des runs.

Un projet est un dossier qui contient `studio.yaml` et qui recoit ses artefacts
et son journal. La config ne contient aucun secret : elle nomme les providers et
les variables d'environnement qui portent les cles.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .tools.images import (
    MockImageProvider,
    OpenAICompatImageProvider,
    PollinationsImageProvider,
)
from .tools.llm import MockProvider, OpenAICompatProvider
from .tools.video import MockVideoProvider, PollinationsVideoProvider

CONFIG_NAME = "studio.yaml"
TOP_KEYS = {"name", "providers", "context"}
SLOTS = ("llm", "image", "video")

KINDS = {
    "llm": {"mock", "openai"},
    "image": {"mock", "pollinations", "openai"},
    "video": {"mock", "pollinations"},
}

ALLOWED_OPTIONS = {
    ("llm", "mock"): {"cost", "delay"},
    ("llm", "openai"): {"model", "base_url", "api_key_env", "temperature", "cost"},
    ("image", "mock"): {"cost", "delay"},
    ("image", "pollinations"): {
        "base", "model", "api_key_env", "width", "height", "cost", "max_retries", "min_interval",
    },
    ("image", "openai"): {"model", "base_url", "api_key_env", "size", "cost"},
    ("video", "mock"): {"cost_per_second", "delay", "size"},
    ("video", "pollinations"): {
        "base", "model", "api_key_env", "width", "height", "cost_per_second",
        "max_retries", "timeout",
    },
}

REQUIRED_OPTIONS = {
    ("llm", "openai"): {"model", "base_url"},
    ("image", "openai"): {"model", "base_url"},
}

_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ProjectError(Exception):
    """Config de projet absente, illisible ou hors contrat."""


@dataclass(frozen=True)
class ProviderConfig:
    """Un provider declare par le projet : son type et ses options."""

    kind: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Project:
    """Racine d'un projet et providers construits a la demande."""

    root: Path
    name: str
    providers: dict[str, ProviderConfig]
    context: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, root: str | Path) -> "Project":
        root = Path(root)
        path = root / CONFIG_NAME
        if not path.exists():
            raise ProjectError(f"{path}: config de projet absente ({CONFIG_NAME})")

        raw = _read(path)
        unknown = sorted(set(raw) - TOP_KEYS)
        if unknown:
            raise ProjectError(f"{path}: terme(s) inconnu(s): {unknown}")

        name = str(raw.get("name") or root.name)
        providers_raw = raw.get("providers") or {}
        if not isinstance(providers_raw, dict):
            raise ProjectError(f"{path}: 'providers' doit etre un mapping")

        unknown = sorted(set(providers_raw) - set(SLOTS))
        if unknown:
            raise ProjectError(f"{path}: provider(s) inconnu(s): {unknown}")

        context_raw = raw.get("context") or {}
        if not isinstance(context_raw, dict):
            raise ProjectError(f"{path}: 'context' doit etre un mapping")
        context = {name: _interpolate(path, value) for name, value in context_raw.items()}

        providers: dict[str, ProviderConfig] = {}
        for slot in SLOTS:
            body = providers_raw.get(slot, {"kind": "mock"})
            providers[slot] = _provider_config(path, slot, body)

        return cls(root=root, name=name, providers=providers, context=context)

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"

    @property
    def journal(self) -> Path:
        return self.root / "journal.jsonl"

    def build(self, slot: str):
        """Construit l'adaptateur du slot (`llm`, `image`, `video`)."""
        config = self.providers[slot]
        builder = {
            ("llm", "mock"): MockProvider,
            ("llm", "openai"): OpenAICompatProvider,
            ("image", "mock"): MockImageProvider,
            ("image", "pollinations"): PollinationsImageProvider,
            ("image", "openai"): OpenAICompatImageProvider,
            ("video", "mock"): MockVideoProvider,
            ("video", "pollinations"): PollinationsVideoProvider,
        }[(slot, config.kind)]
        return builder(**config.options)


def _read(path: Path) -> dict:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProjectError(f"{path}: YAML invalide: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProjectError(f"{path}: le document doit etre un mapping")
    return raw


def _provider_config(path: Path, slot: str, body: Any) -> ProviderConfig:
    if not isinstance(body, dict):
        raise ProjectError(f"{path}: provider '{slot}' doit etre un mapping")

    kind = body.get("kind")
    if not kind:
        raise ProjectError(f"{path}: provider '{slot}' sans 'kind'")
    kind = str(kind)
    if kind not in KINDS[slot]:
        raise ProjectError(
            f"{path}: provider '{slot}' de kind inconnu: {kind} "
            f"(attendu: {', '.join(sorted(KINDS[slot]))})"
        )

    options = {key: _interpolate(path, value) for key, value in body.items() if key != "kind"}
    allowed = ALLOWED_OPTIONS[(slot, kind)]
    unknown = sorted(set(options) - allowed)
    if unknown:
        raise ProjectError(f"{path}: provider '{slot}/{kind}': option(s) inconnue(s): {unknown}")
    missing = sorted(REQUIRED_OPTIONS.get((slot, kind), set()) - set(options))
    if missing:
        raise ProjectError(f"{path}: provider '{slot}/{kind}': option(s) requise(s): {missing}")
    return ProviderConfig(kind=kind, options=options)


def _interpolate(path: Path, value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _interpolate(path, sub) for key, sub in value.items()}
    if isinstance(value, list):
        return [_interpolate(path, sub) for sub in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in os.environ:
            raise ProjectError(f"{path}: variable d'environnement absente: {name}")
        return os.environ[name]

    return _VARIABLE.sub(replace, value)
