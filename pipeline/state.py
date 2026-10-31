"""État d'avancement par scène et par artefact.

Le checkpoint historique (`.checkpoint.json`) dit *quelle étape a tourné*, pas *si son
résultat a été validé*. Il ne distingue pas « 30 shots générés » de « 30 shots regardés et
validés », et ne sait rien dire d'une scène partielle.

Ce module tient l'autre moitié de l'information : pour chaque scène et chaque étape, le
statut de l'artefact produit. C'est ce qui rend `resume` ciblé (reprendre au plan, pas à
l'étape entière) et l'estimation de coût honnête.

Aucun code de production ne dépend de ce module pour l'instant : il enregistre et expose.
L'application des statuts est progressive et opt-in (`production.enforce_approvals`).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

STATE_FILENAME = "scene_state.json"

# Un artefact est "pending_approval" tant que l'humain ne l'a pas validé explicitement.
PENDING = "pending_approval"
APPROVED = "approved"
REJECTED = "rejected"
GENERATED = "generated"

VALID_STATUSES = (PENDING, APPROVED, REJECTED, GENERATED)


class SceneState:
    """Lit/écrit `scene_state.json` à la racine d'un projet.

    Format :

        {
          "scene_01": {
            "storyboard": {
              "status": "approved",
              "updated": "2026-09-27T14:31:00",
              "note": "rythm validated, 30 plans",
              "artifacts": {"shots": 30, "count": 30}
            }
          }
        }
    """

    def __init__(self, project_root: Path):
        self.root = Path(project_root)
        self.path = self.root / STATE_FILENAME

    # ── I/O ────────────────────────────────────────────────────

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, data: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── Écriture ───────────────────────────────────────────────

    def set(
        self,
        scene: str,
        step: str,
        status: str,
        note: str = "",
        artifacts: Optional[dict] = None,
    ) -> None:
        if status not in VALID_STATUSES:
            raise ValueError(f"statut invalide : {status!r} (attendu {VALID_STATUSES})")
        data = self.load()
        entry = data.setdefault(scene, {})
        current = entry.get(step, {})
        current.update(
            {
                "status": status,
                "updated": datetime.now().isoformat(timespec="seconds"),
            }
        )
        if note:
            current["note"] = note
        if artifacts is not None:
            current["artifacts"] = artifacts
        entry[step] = current
        self.save(data)

    def mark_generated(self, scene: str, step: str, artifacts: Optional[dict] = None, note: str = "") -> None:
        """Enregistre qu'un artefact existe, en attente de validation humaine.

        Un artefact déjà `approved` ou `rejected` n'est pas rétrogradé : régénérer ne
        vaut pas dé-validation.
        """
        if self.get(scene, step).get("status") in (APPROVED, REJECTED):
            return
        self.set(scene, step, GENERATED, note=note, artifacts=artifacts)

    def mark_shot(
        self,
        scene: str,
        step: str,
        shot_number: int,
        status: str = GENERATED,
        note: str = "",
        extra: Optional[dict] = None,
    ) -> None:
        """Enregistre l'avancement d'un plan précis.

        À la différence de `mark_generated` (un artefact par scène), ceci tient un
        journal par plan sous `artifacts.shots[<numéro>]`. C'est ce qui permet de
        reprendre au plan exact après un crash : un plan dont l'artefact existe sur
        disque et qui est noté ici n'est pas régénéré.

        `mark_generated` reste appelé pour le statut global de la scène ; ceci ne le
        remplace pas.
        """
        data = self.load()
        entry = data.setdefault(scene, {}).setdefault(step, {})
        artifacts = entry.setdefault("artifacts", {})
        shots = artifacts.setdefault("shots", {})
        shot = shots.setdefault(str(shot_number), {})
        shot["status"] = status
        shot["updated"] = datetime.now().isoformat(timespec="seconds")
        if note:
            shot["note"] = note
        if extra:
            shot.update(extra)
        self.save(data)

    def shot_done(self, scene: str, step: str, shot_number: int) -> bool:
        """Un plan est-il déjà enregistré comme fait ?"""
        data = self.load()
        shots = data.get(scene, {}).get(step, {}).get("artifacts", {}).get("shots", {})
        return str(shot_number) in shots

    def approve(self, scene: str, step: str, note: str = "") -> None:
        self.set(scene, step, APPROVED, note=note)

    def reject(self, scene: str, step: str, note: str = "") -> None:
        self.set(scene, step, REJECTED, note=note)

    # ── Lecture ────────────────────────────────────────────────

    def get(self, scene: str, step: str) -> dict:
        return self.load().get(scene, {}).get(step, {})

    def status(self, scene: str, step: str) -> Optional[str]:
        return self.get(scene, step).get("status")

    def is_approved(self, scene: str, step: str) -> bool:
        return self.status(scene, step) == APPROVED

    def is_ready(self, scene: str, step: str) -> bool:
        """Un artefact prêt à être consommé en aval : généré ou validé."""
        return self.status(scene, step) in (GENERATED, APPROVED)

    def scenes(self) -> list[str]:
        return sorted(self.load())

    def summary(self) -> dict[str, Any]:
        data = self.load()
        out: dict[str, Any] = {}
        for scene, steps in data.items():
            out[scene] = {s: e.get("status", GENERATED) for s, e in steps.items()}
        return out

    def pending(self) -> dict[str, list[str]]:
        """Scènes dont un artefact attend encore validation."""
        out: dict[str, list[str]] = {}
        for scene, steps in self.summary().items():
            waiting = [s for s, st in steps.items() if st in (GENERATED, PENDING)]
            if waiting:
                out[scene] = waiting
        return out
