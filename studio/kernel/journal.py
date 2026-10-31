"""Journal append-only : evenements, etat reconstruit, reprise."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class RunState:
    """Etat d'un run reconstruit a partir du seul journal."""

    run_id: str = ""
    completed: set[str] = field(default_factory=set)
    artifacts: dict[str, str] = field(default_factory=dict)
    gate_answers: dict[str, str] = field(default_factory=dict)
    cost: float = 0.0
    status: str = "running"


class Journal:
    """Journal JSONL : on ajoute, on ne reecrit jamais."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self._seq = self._last_seq()

    def _last_seq(self) -> int:
        last = 0
        for event in self.events():
            last = max(last, int(event.get("seq", 0)))
        return last

    def append(self, event_type: str, **fields: object) -> dict:
        """Ajoute un evenement et retourne sa forme complete."""
        self._seq += 1
        event = {
            "seq": self._seq,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "type": event_type,
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def events(self) -> list[dict]:
        """Retourne tous les evenements, dans l'ordre d'ecriture."""
        events: list[dict] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                events.append(json.loads(line))
        return events

    def state(self, run_id: str | None = None) -> RunState:
        """Reconstruit l'etat d'un run (par defaut le dernier demarre)."""
        events = self.events()
        if run_id is None:
            for event in events:
                if event.get("type") == "run_started":
                    run_id = event.get("run")

        state = RunState(run_id=run_id or "")
        for event in events:
            if event.get("run") != state.run_id:
                continue
            kind = event.get("type")
            if kind in ("node_finished", "node_skipped"):
                state.completed.add(event["node"])
                state.artifacts[event["node"]] = event["artifact"]
            if kind == "node_finished" and "#" not in event.get("node", ""):
                # le cout des unites de foreach est porte par l'evenement agrege
                state.cost += float(event.get("cost", 0.0) or 0.0)
            elif kind == "nodes_invalidated":
                # une gate qui demande retry retire l'amont rejoue ; le journal
                # reste append-only, seule la reconstruction d'etat change
                for node_id in event.get("nodes", []):
                    state.completed.discard(node_id)
                    state.artifacts.pop(node_id, None)
                    state.gate_answers.pop(node_id, None)
            elif kind == "gate_answered":
                state.gate_answers[event["node"]] = event["answer"]
            elif kind == "run_finished":
                state.status = event["status"]
        return state
