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
from pathlib import Path
from typing import Any

from .artifacts import ArtifactStore
from .errors import LoadError
from .registry import Registry

TOOL_KINDS = {"python", "cli", "mcp"}


@dataclass
class ToolContext:
    """Ce qu'un outil a le droit de connaitre du monde : le store, le registre et la bible."""

    store: ArtifactStore
    registry: Registry
    bible_root: Path | None = None


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
        return await _call_mcp(name, entry, params, ctx)
    raise LoadError(f"outil {name}: kind inconnu: {kind!r}")


def _arguments_for(entry: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Cartographie entrees du noeud -> arguments du tool. Sans mapping, identite."""
    mapping = entry.get("arguments")
    if not isinstance(mapping, dict) or not mapping:
        return params
    out: dict[str, Any] = {}
    for arg, template in mapping.items():
        out[arg] = _template(str(template), params) if isinstance(template, str) else template
    return out


async def _call_mcp(name: str, entry: dict[str, Any], params: dict[str, Any], ctx: ToolContext) -> Any:
    """D19 : client MCP stdio. Le serveur vient de la table `servers:` du registre."""
    server_name = str(entry.get("server", ""))
    tool_name = str(entry.get("tool", ""))
    spec = ctx.registry.servers.get(server_name)
    if spec is None:
        raise LoadError(f"outil {name}: serveur mcp inconnu: {server_name!r} (declare-le dans 'servers:')")
    if not tool_name:
        raise LoadError(f"outil {name}: kind mcp sans 'tool'")

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    argv = [str(a) for a in (spec.get("args") or [])]
    env = {str(k): str(v) for k, v in (spec.get("env") or {}).items()} or None
    server_params = StdioServerParameters(command=str(spec["command"]), args=argv, env=env)
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments=_arguments_for(entry, params))
    texts = [c.text for c in result.content if getattr(c, "type", "") == "text"]
    joined = "\n".join(texts)
    if getattr(result, "isError", None) or getattr(result, "is_error", None):
        raise LoadError(f"outil {name}: le serveur mcp a renvoye une erreur: {joined[:200]}")
    try:
        return json.loads(joined)
    except json.JSONDecodeError:
        return {"text": joined}


__all__ = ["ToolContext", "run_tool", "TOOL_KINDS"]
