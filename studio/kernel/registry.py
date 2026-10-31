"""Chargement et validation du catalogue : agents, workflows, outils, skills."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from .context import CONTEXT_IRI, expand_document, expand_iri, expand_list
from .errors import LoadError
from .graph import Graph, build_graph

STUDIO_ROOT = Path(__file__).resolve().parents[1]

AGENT_KEYS = {
    "@id",
    "type",
    "name",
    "description",
    "version",
    "model",
    "prompt",
    "skills",
    "tools",
    "inputs",
    "outputs",
    "budget",
}

WORKFLOW_KEYS = {
    "@id",
    "type",
    "name",
    "description",
    "version",
    "inputs",
    "outputs",
    "nodes",
}

TOOLS_KEYS = {"@id", "name", "tools", "servers"}


def _read_yaml(path: Path) -> dict:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise LoadError(f"{path}: YAML invalide: {exc}") from exc
    if not isinstance(raw, dict):
        raise LoadError(f"{path}: le document doit etre un mapping")
    return raw


class Registry:
    """Catalogue fige en memoire, construit une fois et verifie au chargement."""

    def __init__(
        self,
        root: Path,
        agents: dict[str, dict],
        workflows: dict[str, dict],
        tools: dict[str, dict],
        skills: dict[str, str],
        servers: dict[str, dict] | None = None,
    ):
        self.root = Path(root)
        self.agents = agents
        self.workflows = workflows
        self.tools = tools
        self.servers = servers or {}
        self.skills = skills
        self._graphs: dict[str, Graph] = {}

    @staticmethod
    def resolve(ref: str) -> str:
        """Developpe une reference courte en IRI."""
        return expand_iri(ref)

    def agent(self, ref: str) -> dict:
        """Retourne la declaration d'un agent."""
        return self.agents[self._lookup(self.agents, ref, "agent")]

    def workflow(self, ref: str) -> dict:
        """Retourne la declaration d'un workflow."""
        return self.workflows[self._lookup(self.workflows, ref, "workflow")]

    def graph(self, ref: str) -> Graph:
        """Retourne le graphe valide d'un workflow, construit une seule fois."""
        key = self._lookup(self.workflows, ref, "workflow")
        if key not in self._graphs:
            self._graphs[key] = build_graph(self.workflows[key], self)
        return self._graphs[key]

    def skill(self, name: str) -> str:
        """Retourne le contenu d'un pack de connaissance."""
        if name not in self.skills:
            raise LoadError(f"skill inconnu: {name}")
        return self.skills[name]

    def tool(self, name: str) -> dict:
        """Retourne la declaration d'un outil."""
        if name not in self.tools:
            raise LoadError(f"outil inconnu: {name}")
        return self.tools[name]

    def schema(self, rel: str) -> dict:
        """Charge un schema JSON declare par un agent, relatif a la racine studio."""
        path = self.root / rel
        if not path.exists():
            raise LoadError(f"schema introuvable: {rel}")
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _lookup(table: dict, ref: str, kind: str) -> str:
        key = expand_iri(ref)
        if key not in table:
            raise LoadError(f"{kind} inconnu: {ref}")
        return key


def _load_tools(catalog: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    path = catalog / "tools.yamlld"
    if not path.exists():
        return {}, {}
    doc = expand_document(_read_yaml(path), TOOLS_KEYS, str(path))
    tools = doc.get("tools") or {}
    if not isinstance(tools, dict):
        raise LoadError(f"{path}: 'tools' doit etre un mapping")
    servers = doc.get("servers") or {}
    if not isinstance(servers, dict):
        raise LoadError(f"{path}: 'servers' doit etre un mapping")
    for name, entry in tools.items():
        kind = (entry or {}).get("kind") if isinstance(entry, dict) else None
        if kind is not None and kind not in {"python", "cli", "mcp"}:
            raise LoadError(f"outil {name}: kind invalide: {kind!r} (attendu python | cli | mcp)")
    for name, spec in servers.items():
        if not isinstance(spec, dict) or not spec.get("command"):
            raise LoadError(f"serveur {name}: 'command' requis pour un serveur stdio")
    return tools, servers


def _load_documents(folder: Path, keys: set[str]) -> dict[str, dict]:
    docs: dict[str, dict] = {}
    if not folder.exists():
        return docs
    for path in sorted(folder.glob("*.yamlld")):
        raw = _read_yaml(path)
        doc = expand_document(raw, keys, str(path))
        doc_id = doc["@id"]
        if not doc_id:
            raise LoadError(f"{path}: '@id' manquant")
        if doc_id in docs:
            raise LoadError(f"duplicate @id: {doc_id} ({path})")
        docs[doc_id] = doc
    return docs


def _load_skills(catalog: Path, extra_roots: list[Path]) -> dict[str, str]:
    skills: dict[str, str] = {}
    roots = [catalog / "skills", *extra_roots]
    for root in roots:
        if not Path(root).exists():
            continue
        for path in sorted(Path(root).glob("*/SKILL.md")):
            skills.setdefault(path.parent.name, path.read_text(encoding="utf-8"))
    return skills


def _validate_references(agents: dict[str, dict], tools: dict[str, dict], skills: dict[str, str]) -> None:
    for doc in agents.values():
        for name in expand_list(doc.get("tools")):
            if name not in tools:
                raise LoadError(f"{doc['@id']}: outil inconnu: {name}")
        for name in expand_list(doc.get("skills")):
            if name not in skills:
                raise LoadError(f"{doc['@id']}: skill inconnu: {name}")


def load_registry(root: str | Path = STUDIO_ROOT, extra_skill_roots: list[Path] | None = None) -> Registry:
    """Charge le catalogue d'un studio et echoue sur toute reference morte."""
    root = Path(root).resolve()
    catalog = root / "catalog"
    if not catalog.exists():
        raise LoadError(f"{root}: dossier 'catalog' introuvable")

    tools, servers = _load_tools(catalog)
    agents = _load_documents(catalog / "agents", AGENT_KEYS)
    workflows = _load_documents(catalog / "workflows", WORKFLOW_KEYS)
    skills = _load_skills(catalog, [Path(p) for p in (extra_skill_roots or [])])

    _validate_references(agents, tools, skills)
    return Registry(
        root=root, agents=agents, workflows=workflows, tools=tools, skills=skills, servers=servers
    )


__all__ = ["Registry", "load_registry", "STUDIO_ROOT", "CONTEXT_IRI"]
