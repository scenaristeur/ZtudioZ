"""Graphe de workflow : niveaux paralleles, dependances, gates, cycles."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .context import expand_list
from .errors import GraphError, LoadError

if TYPE_CHECKING:
    from .registry import Registry

NODE_KEYS = {
    "id",
    "agent",
    "type",
    "needs",
    "foreach",
    "parallel",
    "on_item_failure",
    "question",
    "options",
    "inputs",
    "outputs",
    "budget",
    "name",
    "description",
}


@dataclass(frozen=True)
class Node:
    """Noeud executable d'un workflow."""

    id: str
    agent_id: str | None
    needs: tuple[str, ...] = ()
    foreach: str | None = None
    parallel: int | None = None
    on_item_failure: str = "fail"
    is_gate: bool = False
    question: str | None = None
    options: tuple[str, ...] = ()
    raw: dict = field(default_factory=dict, compare=False)


class Graph:
    """DAG fige d'un workflow, avec ses niveaux d'execution parallele."""

    def __init__(self, workflow: dict, nodes: list[Node]):
        self.workflow = workflow
        self.nodes = {node.id: node for node in nodes}
        self.levels = self._compute_levels()

    def node(self, node_id: str) -> Node:
        """Retourne un noeud par identifiant."""
        return self.nodes[node_id]

    @property
    def order(self) -> list[str]:
        """Ordre topologique aplati, niveau par niveau."""
        return [node_id for level in self.levels for node_id in level]

    def _compute_levels(self) -> list[list[str]]:
        remaining = {node_id: set(node.needs) for node_id, node in self.nodes.items()}
        done: set[str] = set()
        levels: list[list[str]] = []
        while remaining:
            ready = sorted(nid for nid, needs in remaining.items() if not (needs - done))
            if not ready:
                raise GraphError(f"cycle detecte dans le graphe: {sorted(remaining)}")
            levels.append(ready)
            for node_id in ready:
                remaining.pop(node_id)
            done.update(ready)
        return levels


def build_graph(workflow: dict, registry: "Registry") -> Graph:
    """Construit et valide le graphe d'un workflow."""
    raw_nodes = workflow.get("nodes") or []
    if not isinstance(raw_nodes, list):
        raise LoadError(f"{workflow.get('@id')}: 'nodes' doit etre une liste")

    nodes: list[Node] = []
    seen: set[str] = set()
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            raise LoadError(f"{workflow.get('@id')}: chaque noeud doit etre un mapping")
        unknown = sorted(set(raw) - NODE_KEYS)
        if unknown:
            raise LoadError(f"noeud {raw.get('id')}: terme(s) non declare(s): {unknown}")

        node_id = raw.get("id")
        if not node_id:
            raise LoadError("un noeud sans 'id' est invalide")
        if node_id in seen:
            raise GraphError(f"duplicate node id: {node_id}")
        seen.add(node_id)

        is_gate = str(raw.get("type", "")).endswith("HumanGate")
        agent_id = None
        if not is_gate:
            ref = raw.get("agent")
            if not ref:
                raise LoadError(f"noeud {node_id}: 'agent' est requis")
            agent_id = ref if ref.startswith("http") else ref
            resolved = registry.resolve(agent_id)
            if resolved not in registry.agents:
                raise GraphError(f"noeud {node_id}: agent inconnu: {ref}")
            agent_id = resolved

        nodes.append(
            Node(
                id=node_id,
                agent_id=agent_id,
                needs=tuple(expand_list(raw.get("needs"))),
                foreach=raw.get("foreach"),
                parallel=raw.get("parallel"),
                on_item_failure=raw.get("on_item_failure", "fail"),
                is_gate=is_gate,
                question=raw.get("question"),
                options=tuple(expand_list(raw.get("options"))),
                raw=raw,
            )
        )

    known = {node.id for node in nodes}
    for node in nodes:
        missing = [dep for dep in node.needs if dep not in known]
        if missing:
            raise GraphError(
                f"noeud {node.id}: dependance inconnue: {', '.join(missing)}"
            )

    graph = Graph(workflow, nodes)
    for node in nodes:
        if node.is_gate and not node.question:
            raise LoadError(f"noeud {node.id}: une gate doit poser une question")
    return graph
