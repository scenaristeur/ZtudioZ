"""Moteur d'execution : niveaux paralleles, cache, budget, gates, reprise."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from .artifacts import ArtifactStore, Binary, canonical
from .context import expand_list
from .dispatch import ToolContext, run_tool
from .errors import BudgetError, GateError, GraphError, LoadError, SchemaError
from .graph import Graph, Node
from .journal import Journal
from .registry import Registry


@dataclass
class GateRequest:
    """Demande de validation humaine en attente."""

    id: str
    question: str
    options: tuple[str, ...] = ()


class _GateBlocked(Exception):
    """Signal interne : un sous-workflow reclame une reponse humaine."""

    def __init__(self, gate: GateRequest):
        super().__init__(f"gate attendue: {gate.id}")
        self.gate = gate


class _RunStopped(Exception):
    """Signal interne : un sous-workflow a recu 'stop'."""


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
        video: Any | None = None,
        context: dict[str, Any] | None = None,
        bible_root: str | Path | None = None,
    ):
        self.registry = registry
        self.provider = provider
        self.store = store
        self.journal = journal
        self.answers = dict(answers or {})
        self.max_parallel = max_parallel
        self.image = image
        self.video = video
        self.context = dict(context or {})
        self.feedback: dict[str, str] = {}
        self._active_feedback: str | None = None
        self._global = asyncio.Semaphore(max_parallel)
        self._artifacts: dict[str, str] = {}
        self._unit_costs: dict[str, float] = {}
        self._tools = ToolContext(
            store=store,
            registry=registry,
            bible_root=Path(bible_root) if bible_root is not None else None,
        )
        self._merged: dict[str, str] = {}

    async def run(
        self,
        workflow_id: str,
        inputs: dict[str, Any] | None = None,
        answers: dict[str, str] | None = None,
        run_id: str | None = None,
        feedback: dict[str, str] | None = None,
    ) -> RunResult:
        """Execute ou reprend un workflow jusqu'a completion, gate ou echec."""
        self.feedback = dict(feedback or {})
        self._active_feedback = None
        inputs = {**self.context, **dict(inputs or {})}
        workflow = self.registry.workflow(workflow_id)
        graph = self.registry.graph(workflow_id)
        missing = [
            name for name, required in graph.context.items()
            if required and name not in inputs
        ]
        if missing:
            raise LoadError(
                f"contexte projet manquant pour {workflow['@id']}: {', '.join(missing)}"
            )
        run_id = run_id or _run_id(workflow["@id"], inputs)

        # On ne reprend que les gate answers du run en cours (pas les artifacts
        # des runs precedents : ils peuvent avoir ete generes avec d'autres inputs).
        state = self.journal.state(run_id)
        merged = {**state.gate_answers, **self.answers, **dict(answers or {})}
        self._merged = merged
        completed = set(state.completed)
        self._artifacts = dict(state.artifacts)
        self._unit_costs = {}
        values: dict[str, Any] = {}
        for event in self.journal.events():
            if event.get("run") != run_id:
                continue
            kind = event.get("type")
            if kind == "nodes_invalidated":
                for node_id in event.get("nodes", []):
                    values.pop(node_id, None)
                continue
            if kind not in ("node_finished", "node_skipped"):
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
                    if verdict == "retry":
                        try:
                            await self._retry_gate(gate, graph, inputs, values, completed, run_id)
                        finally:
                            self._active_feedback = None
                        self.journal.append("run_finished", run=run_id, status="blocked")
                        result = self._result("blocked", run_id, graph, values, completed)
                        result.pending_gate = GateRequest(gate.id, gate.question or "", gate.options)
                        return result
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
                    if isinstance(outcome, _GateBlocked):
                        self.journal.append("run_finished", run=run_id, status="blocked")
                        result = self._result("blocked", run_id, graph, values, completed)
                        result.pending_gate = outcome.gate
                        return result
                    if isinstance(outcome, _RunStopped):
                        self.journal.append("run_finished", run=run_id, status="stopped")
                        return self._result("stopped", run_id, graph, values, completed)
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
            feedback = self.feedback.get(node.id, "")
            self._active_feedback = feedback or None
            self.journal.append(
                "gate_retry", run=run_id, node=node.id, feedback=feedback
            )
            return "retry"
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

    async def _retry_gate(
        self,
        gate: Node,
        graph: Graph,
        inputs: dict[str, Any],
        values: dict[str, Any],
        completed: set[str],
        run_id: str,
    ) -> None:
        """D8 : retry = purge de l'artefact consomme et de son aval, puis rejoue l'amont."""
        targets: set[str] = set()
        for dep in gate.needs:
            hash_ = self._artifacts.get(dep)
            if hash_:
                targets |= self.store.invalidate(hash_)
            # Un foreach produit des unites `dep#i` : leurs caches doivent tomber
            # aussi, sinon le noeud agrege se rejoue mais chaque item est un cache hit.
            for unit, unit_hash in list(self._artifacts.items()):
                if unit.startswith(f"{dep}#"):
                    targets |= self.store.invalidate(unit_hash)
        affected = [
            nid
            for nid, hash_ in self._artifacts.items()
            if hash_ in targets
            and not (nid in graph.nodes and graph.node(nid).is_gate)
        ]
        # Une gate deja franchie reste franchie : corriger en aval ne doit pas
        # reposer les validations humaines precedentes (gate_images -> gate_verdict).
        self.journal.append(
            "nodes_invalidated",
            run=run_id,
            gate=gate.id,
            nodes=sorted(affected),
            artifacts=sorted(targets),
        )
        for node_id in affected:
            completed.discard(node_id)
            values.pop(node_id, None)
            self._artifacts.pop(node_id, None)
        for node_id in graph.order:
            if node_id not in affected or graph.node(node_id).is_gate:
                continue
            outcome = await self._exec_node(graph.node(node_id), inputs, values, run_id)
            values[node_id] = outcome
            completed.add(node_id)

    async def _exec_node(
        self, node: Node, inputs: dict[str, Any], values: dict[str, Any], run_id: str
    ) -> Any:
        if node.workflow_id:
            return await self._exec_subworkflow(node, inputs, values, run_id)

        agent = self.registry.agent(node.agent_id)
        if node.foreach:
            items = self._resolve(node.foreach, self._scope(node, inputs, values))
            if not isinstance(items, list):
                raise SchemaError(f"noeud {node.id}: foreach attend une liste")
            bound = self._resolve_limit(node, inputs, values)
            if bound is not None and bound < len(items):
                self.journal.append(
                    "items_truncated",
                    run=run_id,
                    node=node.id,
                    produced=len(items),
                    kept=bound,
                )
                items = items[:bound]
            semaphore = asyncio.Semaphore(node.parallel or self.max_parallel)

            async def one(index: int, item: Any) -> Any:
                async with semaphore:
                    extra = {"item": item, "index": index}
                    if node.alias:
                        extra[node.alias] = item
                    scope = self._scope(node, inputs, values, extra)
                    return await self._exec_agent(node, agent, scope, run_id, f"{node.id}#{index}")

            tasks = [one(i, item) for i, item in enumerate(items)]
            if node.on_item_failure == "continue":
                outcomes = await asyncio.gather(*tasks, return_exceptions=True)
                kept: list[Any] = []
                first_error: BaseException | None = None
                for index, outcome in enumerate(outcomes):
                    if isinstance(outcome, (_GateBlocked, _RunStopped)):
                        raise outcome
                    if isinstance(outcome, BaseException):
                        first_error = first_error or outcome
                        self.journal.append(
                            "item_failed",
                            run=run_id,
                            node=f"{node.id}#{index}",
                            error=str(outcome),
                        )
                    else:
                        kept.append(outcome)
                if not kept:
                    raise first_error or RuntimeError(f"noeud {node.id}: tous les items ont echoue")
                outcomes = kept
            else:
                outcomes = await asyncio.gather(*tasks)
            units = tuple(
                f"{node.id}#{index}" for index in range(len(items))
            )
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

    def _resolve_limit(
        self, node: Node, inputs: dict[str, Any], values: dict[str, Any]
    ) -> int | None:
        """Resout la borne de fan-out d'un noeud, exprimee en nombre ou en reference.

        La borne porte la contrainte du brief jusqu'au decoupage : un LLM qui
        surcompte les plans ne peut pas faire payer au-dela de ce que l'humain a
        valide. Deux cas distingue :

        - la racine n'est declaree nulle part : erreur de graphe, on refuse de
          deviner ce que l'auteur voulait borner ;
        - la racine est un entree optionnelle du workflow, ou la feuille manque
          sous une racine resolue : pas de borne, on ne tronque pas ce qui
          n'est pas contraint.
        """
        if node.limit is None:
            return None
        if isinstance(node.limit, int):
            return node.limit

        path = node.limit.strip()[2:-1].split(".")
        current: Any = self._scope(node, inputs, values)
        for depth, part in enumerate(path):
            if not isinstance(current, dict) or part not in current:
                if depth == 0 and not node.limit_optional:
                    raise LoadError(f"noeud {node.id}: reference non resolue: {node.limit}")
                return None
            current = current[part]

        bound = current
        if isinstance(bound, bool) or not isinstance(bound, int) or bound < 1:
            raise GraphError(f"noeud {node.id}: limit resolue invalide: {bound!r}")
        return bound

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
        if self._active_feedback:
            scope["feedback"] = self._active_feedback
        return scope

    async def _exec_agent(
        self, node: Node, agent: dict, scope: dict[str, Any], run_id: str, unit: str
    ) -> Any:
        if str(agent.get("@type", "")).endswith("ImageAgent"):
            return await self._exec_image(node, agent, scope, run_id, unit)
        if str(agent.get("@type", "")).endswith("VideoAgent"):
            return await self._exec_video(node, agent, scope, run_id, unit)
        if str(agent.get("@type", "")).endswith("ToolAgent"):
            return await self._exec_tool(node, agent, scope, run_id, unit)

        resolved = self._resolve_inputs(agent, scope)
        images = self._vision_images(agent, resolved)
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
            self.journal.append(
                "budget_refused", run=run_id, node=unit, cost=cost, budget=float(budget)
            )
            raise BudgetError(
                f"budget depasse pour {node.id}: cout estime {cost} > budget {budget}"
            )

        self.journal.append("node_started", run=run_id, node=unit)
        async with self._global:
            produced = await self.provider.complete(
                agent_id=agent["@id"],
                model=agent.get("model", ""),
                system=self._system(agent),
                prompt=json.dumps(resolved, ensure_ascii=False, sort_keys=True),
                schema=self._output_schema(agent),
                output_key=self._output_key(agent),
                images=images,
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
            self.journal.append(
                "budget_refused", run=run_id, node=unit, cost=cost, budget=float(budget)
            )
            raise BudgetError(
                f"budget depasse pour {node.id}: cout estime {cost} > budget {budget}"
            )

        reference = self._reference(resolved)
        references = self._references(resolved)
        if references and reference is None:
            reference = references[0]
        self.journal.append("node_started", run=run_id, node=unit)
        async with self._global:
            binary = await self.image.generate(
                agent_id=agent["@id"],
                model=agent.get("model", ""),
                system=self._system(agent),
                prompt=self._image_prompt(resolved),
                reference=reference,
                references=references,
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

    async def _exec_video(
        self, node: Node, agent: dict, scope: dict[str, Any], run_id: str, unit: str
    ) -> Any:
        """VideoAgent : une image de plan + un mouvement -> un clip genere.

        Le cout depend de la duree, donc l'estimation et le refus de budget
        arrivent avant tout appel reseau : on ne paie pas un plan qu'on refuse.
        """
        if self.video is None:
            raise LoadError(
                f"noeud {node.id}: un VideoAgent demande un adaptateur video (parametre 'video')"
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

        duration = self._video_duration(resolved)
        budget = agent.get("budget")
        cost = float(self.video.estimate(duration))
        if budget is not None and cost > float(budget):
            self.journal.append(
                "budget_refused", run=run_id, node=unit, cost=cost, budget=float(budget)
            )
            raise BudgetError(
                f"budget depasse pour {node.id}: cout estime {cost} > budget {budget}"
            )

        reference = self._reference(resolved)
        references = self._references(resolved)
        if references and reference is None:
            reference = references[0]
        self.journal.append("node_started", run=run_id, node=unit)
        async with self._global:
            binary = await self.video.generate(
                agent_id=agent["@id"],
                model=agent.get("model", ""),
                system=self._system(agent),
                prompt=self._video_prompt(resolved),
                reference=reference,
                references=references,
                duration=duration,
            )

        name = self._output_key(agent) or "clip"
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

    async def _exec_tool(
        self, node: Node, agent: dict, scope: dict[str, Any], run_id: str, unit: str
    ) -> Any:
        """ToolAgent : le contrat d'un agent, l'impl d'un outil du registre."""
        names = expand_list(agent.get("tools"))
        if len(names) != 1:
            raise LoadError(
                f"noeud {node.id}: un ToolAgent declare exactement un outil, recu: {names}"
            )
        tool_name = names[0]
        entry = self.registry.tool(tool_name)
        kind = entry.get("kind")
        if kind not in ("python", "cli", "mcp"):
            raise LoadError(f"noeud {node.id}: outil {tool_name}: kind invalide: {kind!r}")

        resolved = self._resolve_inputs(agent, scope)
        cache_key = self.store.key(
            [node.id, agent["@id"], agent.get("version", 1), "tool", tool_name,
             entry.get("impl") or entry.get("command"), resolved, scope.get("item")]
        )
        cached = self.store.cached(cache_key)
        if cached is not None:
            self._unit_costs[unit] = 0.0
            self.journal.append(
                "node_skipped", run=run_id, node=unit, artifact=cached, reason="cache", cost=0.0
            )
            return self.store.get(cached)

        cost = float(entry.get("cost", 0.0))
        budget = agent.get("budget")
        if budget is not None and cost > float(budget):
            self.journal.append(
                "budget_refused", run=run_id, node=unit, cost=cost, budget=float(budget)
            )
            raise BudgetError(
                f"budget depasse pour {node.id}: cout estime {cost} > budget {budget}"
            )

        self.journal.append("node_started", run=run_id, node=unit)
        async with self._global:
            value = await run_tool(tool_name, entry, resolved, self._tools)

        name = self._output_key(agent)
        if isinstance(value, Binary):
            consumed = self._consumed(node, resolved)
            binary_key = self.store.put(
                value, parents=consumed, producer=agent["@id"], cost=cost
            )
            produced = {name or "binary": value.ref()}
            self._validate_outputs(node, agent, produced)
            artifact = self.store.put(
                produced,
                parents=(binary_key, *consumed),
                producer=agent["@id"],
                cost=cost,
            )
        else:
            declared = list((agent.get("outputs") or {}).keys())
            if isinstance(value, dict) and any(key in value for key in declared):
                produced = value  # l'outil rend deja des cles conformes au contrat
            else:
                produced = {name or "result": value}
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

    async def _exec_subworkflow(
        self, node: Node, inputs: dict[str, Any], values: dict[str, Any], run_id: str
    ) -> Any:
        """D5 : un noeud peut executer un workflow entier, avec son journal propre."""
        inner = self.registry.workflow(node.workflow_id)
        scope = self._scope(node, inputs, values)
        resolved: dict[str, Any] = {}
        for name, spec in (inner.get("inputs") or {}).items():
            if name in scope:
                resolved[name] = scope[name]
            elif isinstance(spec, dict) and spec.get("required"):
                raise LoadError(
                    f"input requis manquant pour le sous-workflow {node.id}: {name}"
                )

        cache_key = self.store.key(
            [node.id, inner["@id"], inner.get("version", 1), "workflow", resolved,
             scope.get("item")]
        )
        cached = self.store.cached(cache_key)
        if cached is not None:
            self._unit_costs[node.id] = 0.0
            self.journal.append(
                "node_skipped", run=run_id, node=node.id, artifact=cached, reason="cache", cost=0.0
            )
            return self.store.get(cached)

        self.journal.append(
            "node_started", run=run_id, node=node.id, workflow=inner["@id"]
        )
        prefix = f"{node.id}/"
        inner_answers = {
            key[len(prefix):]: answer
            for key, answer in self._merged.items()
            if key.startswith(prefix)
        }
        child = Executor(
            self.registry,
            self.provider,
            self.store,
            self.journal,
            answers=inner_answers,
            max_parallel=self.max_parallel,
            image=self.image,
            video=self.video,
            context=self.context,
        )
        sub_run = f"{run_id}#{node.id}"
        result = await child.run(node.workflow_id, resolved, run_id=sub_run)

        if result.status == "blocked" and result.pending_gate is not None:
            gate = result.pending_gate
            raise _GateBlocked(
                GateRequest(f"{node.id}/{gate.id}", gate.question, gate.options)
            )
        if result.status == "stopped":
            raise _RunStopped(f"sous-workflow {node.id}: arrete par l'humain")
        if result.status != "completed":
            raise RuntimeError(f"sous-workflow {node.id} en echec: {result.error}")

        outputs = dict(result.outputs)
        cost = child.journal.state(sub_run).cost
        artifact = self.store.put(
            outputs,
            parents=self._consumed(node, resolved),
            producer=inner["@id"],
            cost=cost,
        )
        self.store.remember(cache_key, artifact)
        self._artifacts[node.id] = artifact
        self._unit_costs[node.id] = cost
        self.journal.append(
            "node_finished", run=run_id, node=node.id, artifact=artifact, cost=cost
        )
        return outputs

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

    def _vision_images(self, agent: dict, resolved: dict[str, Any]) -> list | None:
        """Charge les binaires des images declarees en entree d'un agent texte.

        Convention D23 : un agent qui declare `images` recoit les pixels, pas
        seulement des references d'artefacts. Les autres agents texte gardent
        un prompt JSON pur.
        """
        if "images" not in (agent.get("inputs") or {}) and "references" not in (
            agent.get("inputs") or {}
        ):
            return None
        values = []
        for key in ("images", "references"):
            if key in (agent.get("inputs") or {}) and resolved.get(key) is not None:
                values.append(resolved[key])
        images = []
        for value in values:
            refs = value if isinstance(value, list) else [value]
            for ref in refs:
                for hash_ in self._embedded(ref):
                    binary = self.store.get(hash_)
                    if str(getattr(binary, "media_type", "")).startswith("image/"):
                        images.append(binary)
        return images or None

    def _references(self, resolved: dict[str, Any]) -> list:
        """Toutes les images binaires referencees par les entrees, dedupliquees."""
        references = []
        seen = set()
        for value in resolved.values():
            for hash_ in self._embedded(value):
                if hash_ in seen:
                    continue
                seen.add(hash_)
                binary = self.store.get(hash_)
                if str(getattr(binary, "media_type", "")).startswith("image/"):
                    references.append(binary)
        return references

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

    @staticmethod
    def _video_duration(resolved: dict[str, Any]) -> float:
        """Duree demandee pour le plan, en secondes. Defaut : 5 s."""
        item = resolved.get("item")
        value = item.get("duration") if isinstance(item, dict) else None
        if value is None:
            value = resolved.get("duration")
        if value is None:
            return 5.0
        try:
            duration = float(value)
        except (TypeError, ValueError) as exc:
            raise LoadError(f"duree video invalide: {value!r}") from exc
        if duration <= 0:
            raise LoadError(f"duree video invalide: {value!r} (doit etre > 0)")
        return duration

    @staticmethod
    def _video_prompt(resolved: dict[str, Any]) -> str:
        """Le prompt du plan, augmente du mouvement demande par l'humain."""
        base = Executor._text_prompt(resolved)
        item = resolved.get("item")
        motion = item.get("motion") if isinstance(item, dict) else None
        if isinstance(motion, str) and motion.strip():
            base = f"{base}. Mouvement: {motion.strip()}"
        feedback = resolved.get("feedback")
        if isinstance(feedback, str) and feedback.strip():
            base = f"{base}. Correction demandee: {feedback.strip()}"
        return base

    @staticmethod
    def _text_prompt(resolved: dict[str, Any]) -> str:
        """Un prompt naturel pour la generation d'images ; le JSON en dernier recours.

        Regle : la premiere cle 'prompt' d'une entree objet, sinon la premiere entree
        textuelle, sinon le JSON complet resolve. Les providers d'images ne veulent pas
        d'un bloc JSON : ils veulent la phrase du plan.
        """
        for value in resolved.values():
            if isinstance(value, dict) and isinstance(value.get("prompt"), str):
                return value["prompt"]
        for value in resolved.values():
            if isinstance(value, str):
                return value
        return json.dumps(resolved, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _image_prompt(resolved: dict[str, Any]) -> str:
        """Prompt image : le plan, plus l'ancre visuelle, le brief et la correction."""
        base = Executor._text_prompt(resolved)
        extras: list[str] = []

        reference = resolved.get("reference")
        if isinstance(reference, str) and reference.strip() and reference.strip() != base:
            extras.append(f"Reference visuelle: {reference.strip()}")

        brief = resolved.get("brief")
        if isinstance(brief, dict):
            constraints = brief.get("constraints") or {}
            freeform = constraints.get("freeform") or []
            if freeform:
                extras.append("Contraintes du brief: " + "; ".join(map(str, freeform)))

        feedback = resolved.get("feedback")
        if isinstance(feedback, str) and feedback.strip():
            extras.append(f"Correction demandee: {feedback.strip()}")

        return ". ".join([base, *extras]) if extras else base

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
            if node.agent_id is None:
                # sous-workflow : la valeur est deja le dictionnaire des sorties
                if isinstance(value, dict):
                    outputs.update(value)
                continue
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
