"""Moteur d'execution : niveaux paralleles, cache, budget, gates, reprise."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from .artifacts import ArtifactStore, canonical
from .errors import BudgetError, GateError, LoadError, SchemaError
from .graph import Graph, Node
from .journal import Journal
from .registry import Registry


@dataclass
class GateRequest:
    """Demande de validation humaine en attente."""

    id: str
    question: str
    options: tuple[str, ...] = ()


@dataclass
class RunResult:
    """Resultat d'un run, complet, bloque, arrete ou echoue."""

    status: str
    run_id: str
    completed: set[str] = field(default_factory=set)
    outputs: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    pending_gate: GateRequest | None = None
    error: str | None = None


def _run_id(workflow_id: str, inputs: dict) -> str:
    from .artifacts import digest

    return digest([workflow_id, inputs])


class Executor:
    """Execute un workflow : un noeud = un agent, une gate ou un sous-workflow."""

    def __init__(
        self,
        registry: Registry,
        provider: Any,
        store: ArtifactStore,
        journal: Journal,
        answers: dict[str, str] | None = None,
        max_parallel: int = 8,
        image: Any | None = None,
    ):
        self.registry = registry
        self.provider = provider
        self.store = store
        self.journal = journal
        self.answers = dict(answers or {})
        self.max_parallel = max_parallel
        self.image = image
        self._global = asyncio.Semaphore(max_parallel)
        self._artifacts: dict[str, str] = {}
        self._unit_costs: dict[str, float] = {}

    async def run(
        self,
        workflow_id: str,
        inputs: dict[str, Any] | None = None,
        answers: dict[str, str] | None = None,
        run_id: str | None = None,
    ) -> RunResult:
        """Execute ou reprend un workflow jusqu'a completion, gate ou echec."""
        inputs = dict(inputs or {})
        workflow = self.registry.workflow(workflow_id)
        graph = self.registry.graph(workflow_id)
        run_id = run_id or _run_id(workflow["@id"], inputs)

        # On ne reprend que les gate answers du run en cours (pas les artifacts
        # des runs precedents : ils peuvent avoir ete generes avec d'autres inputs).
        state = self.journal.state(run_id)
        merged = {**state.gate_answers, **self.answers, **dict(answers or {})}
        completed = set(state.completed)
        self._artifacts = dict(state.artifacts)
        self._unit_costs = {}
        values: dict[str, Any] = {}
        for event in self.journal.events():
            if event.get("run") != run_id or event.get("type") not in (
                "node_finished",
                "node_skipped",
            ):
                continue
            node_id = event["node"]
            if "#" in node_id:
                continue
            artifact = event["artifact"]
            values[node_id] = self.store.get(artifact)

        self.journal.append("run_started", run=run_id, workflow=workflow["@id"], inputs=inputs)

        try:
            for level in graph.levels:
                gates = [
                    graph.node(node_id)
                    for node_id in level
                    if graph.node(node_id).is_gate and node_id not in completed
                ]
                for gate in gates:
                    verdict = self._exec_gate(gate, run_id, merged)
                    if verdict != "continue":
                        status = "blocked" if verdict == "blocked" else "stopped"
                        self.journal.append("run_finished", run=run_id, status=status)
                        result = self._result(status, run_id, graph, values, completed)
                        if verdict == "blocked":
                            result.pending_gate = GateRequest(gate.id, gate.question or "", gate.options)
                        return result
                    completed.add(gate.id)

                pending = [
                    graph.node(node_id)
                    for node_id in level
                    if not graph.node(node_id).is_gate and node_id not in completed
                ]
                if not pending:
                    continue

                outcomes = await asyncio.gather(
                    *[self._exec_node(node, inputs, values, run_id) for node in pending],
                    return_exceptions=True,
                )
                failure: Exception | None = None
                for node, outcome in zip(pending, outcomes):
                    if isinstance(outcome, BaseException):
                        failure = failure or outcome
                        self.journal.append(
                            "node_failed", run=run_id, node=node.id, error=str(outcome)
                        )
                    else:
                        completed.add(node.id)
                        values[node.id] = outcome

                if failure is not None:
                    self.journal.append("run_finished", run=run_id, status="failed")
                    result = self._result("failed", run_id, graph, values, completed)
                    result.error = str(failure)
                    return result

            self.journal.append("run_finished", run=run_id, status="completed")
            return self._result("completed", run_id, graph, values, completed)

        except Exception as exc:  # gate invalide, erreur de chargement, etc.
            self.journal.append("run_finished", run=run_id, status="failed")
            result = self._result("failed", run_id, graph, values, completed)
            result.error = str(exc)
            return result

    def _exec_gate(self, node: Node, run_id: str, answers: dict[str, str]) -> str:
        self.journal.append(
            "gate_asked",
            run=run_id,
            node=node.id,
            question=node.question,
            options=list(node.options),
        )
        answer = answers.get(node.id)
        if answer is None:
            return "blocked"
        if answer == "retry":
            return "blocked"
        if node.options and answer not in node.options:
            raise GateError(f"reponse invalide pour {node.id}: {answer}")

        self.journal.append("gate_answered", run=run_id, node=node.id, answer=answer)
        artifact = self.store.put(
            {"answer": answer}, parents=self._upstream(node), producer="studio:HumanGate", cost=0.0
        )
        self._artifacts[node.id] = artifact
        self.journal.append(
            "node_finished", run=run_id, node=node.id, artifact=artifact, cost=0.0
        )
        return "stopped" if answer == "stop" else "continue"

    async def _exec_node(
        self, node: Node, inputs: dict[str, Any], values: dict[str, Any], run_id: str
    ) -> Any:
        agent = self.registry.agent(node.agent_id)
        if node.foreach:
            items = self._resolve(node.foreach, self._scope(node, inputs, values))
            if not isinstance(items, list):
                raise SchemaError(f"noeud {node.id}: foreach attend une liste")
            semaphore = asyncio.Semaphore(node.parallel or self.max_parallel)

            async def one(index: int, item: Any) -> Any:
                async with semaphore:
                    scope = self._scope(node, inputs, values, {"item": item, "index": index})
                    return await self._exec_agent(node, agent, scope, run_id, f"{node.id}#{index}")

            outcomes = await asyncio.gather(*[one(i, item) for i, item in enumerate(items)])
            units = tuple(f"{node.id}#{index}" for index in range(len(items)))
            total = sum(self._unit_costs.get(unit, 0.0) for unit in units)
            artifact = self.store.put(
                outcomes,
                parents=tuple(
                    hash_ for hash_ in (self._artifacts.get(unit) for unit in units) if hash_
                ),
                producer=agent["@id"],
                cost=total,
            )
            self._artifacts[node.id] = artifact
            self._unit_costs[node.id] = total
            self.journal.append(
                "node_finished", run=run_id, node=node.id, artifact=artifact, cost=total
            )
            return outcomes

        scope = self._scope(node, inputs, values)
        return await self._exec_agent(node, agent, scope, run_id, node.id)

    def _scope(
        self,
        node: Node,
        inputs: dict[str, Any],
        values: dict[str, Any],
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        scope = dict(inputs)
        for dep in node.needs:
            value = values.get(dep)
            if value is None:
                continue
            if isinstance(value, dict):
                scope.update(value)
            else:
                scope[dep] = value
        if extra:
            scope.update(extra)
        return scope

    async def _exec_agent(
        self, node: Node, agent: dict, scope: dict[str, Any], run_id: str, unit: str
    ) -> Any:
        if str(agent.get("@type", "")).endswith("ImageAgent"):
            return await self._exec_image(node, agent, scope, run_id, unit)

        resolved = self._resolve_inputs(agent, scope)
        cache_key = self.store.key(
            [node.id, agent["@id"], agent.get("version", 1), agent.get("model", ""),
             agent.get("prompt", ""), resolved, scope.get("item")]
        )
        cached = self.store.cached(cache_key)
        if cached is not None:
            self._unit_costs[unit] = 0.0
            self.journal.append(
                "node_skipped", run=run_id, node=unit, artifact=cached, reason="cache", cost=0.0
            )
            return self.store.get(cached)

        budget = agent.get("budget")
        cost = float(self.provider.estimate())
        if budget is not None and cost > float(budget):
            raise BudgetError(
                f"budget depasse pour {node.id}: cout estime {cost} > budget {budget}"
            )

        async with self._global:
            produced = await self.provider.complete(
                agent_id=agent["@id"],
                model=agent.get("model", ""),
                system=self._system(agent),
                prompt=json.dumps(resolved, ensure_ascii=False, sort_keys=True),
                schema=self._output_schema(agent),
                output_key=self._output_key(agent),
            )

        if not isinstance(produced, dict):
            produced = {"text": produced}
        self._validate_outputs(node, agent, produced)

        artifact = self.store.put(
            produced,
            parents=self._consumed(node, resolved),
            producer=agent["@id"],
            cost=cost,
        )
        self.store.remember(cache_key, artifact)
        self._artifacts[unit] = artifact
        self._unit_costs[unit] = cost
        self.journal.append(
            "node_finished", run=run_id, node=unit, artifact=artifact, cost=cost
        )
        return produced

    async def _exec_image(
        self, node: Node, agent: dict, scope: dict[str, Any], run_id: str, unit: str
    ) -> Any:
        """ImageAgent : meme contrat d'entrees/sorties qu'un LlmAgent, sortie binaire."""
        if self.image is None:
            raise LoadError(
                f"noeud {node.id}: un ImageAgent demande un adaptateur image (parametre 'image')"
            )

        resolved = self._resolve_inputs(agent, scope)
        cache_key = self.store.key(
            [node.id, agent["@id"], agent.get("version", 1), agent.get("model", ""),
             agent.get("prompt", ""), resolved, scope.get("item")]
        )
        cached = self.store.cached(cache_key)
        if cached is not None:
            self._unit_costs[unit] = 0.0
            self.journal.append(
                "node_skipped", run=run_id, node=unit, artifact=cached, reason="cache", cost=0.0
            )
            return self.store.get(cached)

        budget = agent.get("budget")
        cost = float(self.image.estimate())
        if budget is not None and cost > float(budget):
            raise BudgetError(
                f"budget depasse pour {node.id}: cout estime {cost} > budget {budget}"
            )

        reference = self._reference(resolved)
        async with self._global:
            binary = await self.image.generate(
                agent_id=agent["@id"],
                model=agent.get("model", ""),
                system=self._system(agent),
                prompt=json.dumps(resolved, ensure_ascii=False, sort_keys=True),
                reference=reference,
            )

        name = self._output_key(agent) or "image"
        produced = {name: binary.ref()}
        self._validate_outputs(node, agent, produced)

        consumed = self._consumed(node, resolved)
        binary_key = self.store.put(
            binary, parents=consumed, producer=agent["@id"], cost=cost
        )
        artifact = self.store.put(
            produced, parents=(binary_key, *consumed), producer=agent["@id"], cost=cost
        )
        self.store.remember(cache_key, artifact)
        self._artifacts[unit] = artifact
        self._unit_costs[unit] = cost
        self.journal.append(
            "node_finished", run=run_id, node=unit, artifact=artifact, cost=cost
        )
        return produced

    def _upstream(self, node: Node) -> tuple[str, ...]:
        """Hashs des artefacts produits par les dependances directes du noeud."""
        return tuple(
            hash_ for hash_ in (self._artifacts.get(dep) for dep in node.needs) if hash_
        )

    def _consumed(self, node: Node, resolved: dict[str, Any]) -> tuple[str, ...]:
        """Lignee consommee : amont direct + references d'artefacts dans les entrees."""
        hashes = list(self._upstream(node))
        for value in resolved.values():
            hashes.extend(self._embedded(value))
        return tuple(dict.fromkeys(hashes))

    @staticmethod
    def _embedded(value: Any) -> list[str]:
        """Hashs des references {"artifact": ...} portees par une valeur, recursivement."""
        found: list[str] = []
        if isinstance(value, dict):
            artifact = value.get("artifact")
            if isinstance(artifact, str):
                found.append(artifact)
            for nested in value.values():
                found.extend(Executor._embedded(nested))
        elif isinstance(value, list):
            for nested in value:
                found.extend(Executor._embedded(nested))
        return found

    def _reference(self, resolved: dict[str, Any]) -> Any:
        """Charge le premier artefact binaire consomme, comme reference de generation."""
        for value in resolved.values():
            for hash_ in self._embedded(value):
                return self.store.get(hash_)
        return None

    def _resolve_inputs(self, agent: dict, scope: dict[str, Any]) -> dict[str, Any]:
        declared = agent.get("inputs") or {}
        resolved: dict[str, Any] = {}
        for name, spec in declared.items():
            if name in scope:
                resolved[name] = scope[name]
                continue
            if isinstance(spec, dict) and spec.get("required"):
                raise LoadError(f"input requis manquant: {name} (agent {agent['@id']})")
        return resolved

    def _system(self, agent: dict) -> str:
        parts = [agent.get("prompt", "")]
        from .context import expand_list

        for name in expand_list(agent.get("skills")):
            parts.append(f"# Pack de connaissance : {name}\n{self.registry.skill(name)}")
        return "\n\n".join(part for part in parts if part)

    def _output_schema(self, agent: dict) -> dict | None:
        outputs = agent.get("outputs") or {}
        if not outputs:
            return None
        # Si une sortie referene un fichier schema, l'utiliser tel quel.
        for spec in outputs.values():
            if isinstance(spec, dict) and spec.get("schema"):
                return self.registry.schema(spec["schema"])
        # Sinon, construire le schema de CHAQUE sortie individuellement.
        schemas: list[dict] = []
        for name, spec in outputs.items():
            if isinstance(spec, dict):
                schemas.append({"type": spec.get("type", "string")})
        if not schemas:
            return None
        if len(schemas) == 1:
            return schemas[0]
        return {"type": "object", "properties": {name: schemas[i] for i, name in enumerate(outputs.keys())}}

    @staticmethod
    def _output_key(agent: dict) -> str | None:
        """Retourne le nom de la premiere cle de sortie declaree, ou None."""
        for name in (agent.get("outputs") or {}).keys():
            return name
        return None

    def _validate_outputs(self, node: Node, agent: dict, produced: dict) -> None:
        for name, spec in (agent.get("outputs") or {}).items():
            if not isinstance(spec, dict) or not spec.get("schema"):
                continue
            if name not in produced:
                raise SchemaError(f"noeud {node.id}: sortie manquante: {name}")
            jsonschema.validate(produced[name], self.registry.schema(spec["schema"]))

    def _resolve(self, expression: str, scope: dict[str, Any]) -> Any:
        if not expression.startswith("${") or not expression.endswith("}"):
            return expression
        path = expression[2:-1].strip()
        current: Any = scope
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                raise LoadError(f"reference non resolue: {expression}")
            current = current[part]
        return current

    def _result(
        self,
        status: str,
        run_id: str,
        graph: Graph,
        values: dict[str, Any],
        completed: set[str],
    ) -> RunResult:
        outputs: dict[str, Any] = {}
        for node_id in graph.order:
            node = graph.node(node_id)
            if node.is_gate:
                continue
            value = values.get(node_id)
            agent = self.registry.agent(node.agent_id)
            if isinstance(value, list):
                # sortie de foreach : on accumule chaque cle declaree, item par item
                for item in value:
                    if not isinstance(item, dict):
                        continue
                    for name in (agent.get("outputs") or {}):
                        if name in item:
                            outputs.setdefault(name, []).append(item[name])
                continue
            if not isinstance(value, dict):
                continue
            for name in (agent.get("outputs") or {}):
                if name in value:
                    outputs[name] = value[name]

        state = self.journal.state(run_id)
        return RunResult(
            status=status,
            run_id=run_id,
            completed=set(completed),
            outputs=outputs,
            artifacts=state.artifacts,
        )


__all__ = ["Executor", "RunResult", "GateRequest", "canonical"]
