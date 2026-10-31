"""Dispatch d'outils : la table du registre (kind: python | cli | mcp) -> executables reels.

Contrat d'un outil python : ``fonction(params: dict, ctx: ToolContext) -> valeur``.
La valeur est un dict/liste JSON-able ou un ``Binary`` ; le noeud appelant se charge
du stockage, de la lignee, du cache et du journal — l'outil, lui, ne fait son metier.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
from dataclasses import dataclass
from importlib import import_module
from typing import Any

from .artifacts import ArtifactStore
from .errors import LoadError
from .registry import Registry

TOOL_KINDS = {"python", "cli", "mcp"}


@dataclass
class ToolContext:
    """Ce qu'un outil a le droit de connaitre du monde : le store et le registre."""

    store: ArtifactStore
    registry: Registry


def _load_impl(dotted: str):
    module_name, _, attr = dotted.rpartition(":")
    if not module_name or not attr:
        raise LoadError(f"impl mal formee (attendu module:function): {dotted}")
    try:
        module = import_module(module_name)
        return getattr(module, attr)
    except (ImportError, AttributeError) as exc:
        raise LoadError(f"impl introuvable: {dotted} ({exc})") from exc


def _template(arg: str, params: dict[str, Any]) -> str:
    out = arg
    for key, value in params.items():
        out = out.replace("${" + key + "}", "" if value is None else str(value))
    return out


async def run_tool(
    name: str,
    entry: dict[str, Any],
    params: dict[str, Any],
    ctx: ToolContext,
) -> Any:
    """Execute l'outil declare, selon son kind. Retourne la valeur produite."""
    kind = entry.get("kind")
    if kind == "python":
        fn = _load_impl(str(entry.get("impl", "")))
        result = fn(params, ctx)
        if asyncio.iscoroutine(result):
            result = await result
        return result
    if kind == "cli":
        argv = [_template(str(arg), params) for arg in entry.get("command", [])]
        if not argv:
            raise LoadError(f"outil {name}: kind cli sans 'command'")
        proc = await asyncio.to_thread(
            subprocess.run, argv, capture_output=True, text=True, timeout=120
        )
        if proc.returncode != 0:
            raise LoadError(
                f"outil {name}: echec cli ({shlex.join(argv)}): {proc.stderr[:200]}"
            )
        return {"stdout": proc.stdout, "exit": 0}
    if kind == "mcp":
        raise LoadError(
            f"outil {name}: dispatch mcp branche en M3 (kit v1 ztudioz_toolkit), pas avant"
        )
    raise LoadError(f"outil {name}: kind inconnu: {kind!r}")


__all__ = ["ToolContext", "run_tool", "TOOL_KINDS"]
