from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml

from pipeline.reporter import step_item, step_warn
from pipeline.services.llm import LLMService

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Tu es un assistant de parsing de scénario.
Analyse le script fourni et extrait pour chaque scène :
- number: numéro de la scène
- title: titre ou résumé court
- location: INT./EXT. + lieu
- time_of_day: moment de la journée
- characters: liste des personnages présents
- ambiance: humeur générale (mélancolique, tendue, lumineuse...)
- description: résumé de l'action
- shots: découpage en plans
  - number, type (PLAN LARGE, GROS PLAN, etc.)
  - description, camera, dialogue
  - duration_seconds estimée

Réponds UNIQUEMENT avec un tableau JSON valide.
Utilise le format: [{"number": 1, "title": "...", ...}]
"""


class ParseStep:
    def __init__(self, verbose: bool = False):
        self.llm = LLMService()
        self.verbose = verbose

    def run(self, script_path: str, output_dir: str | Path) -> list[dict]:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        with open(script_path) as f:
            script_text = f.read()

        scenes_text = self._split_scenes(script_text)
        parsed_scenes = []

        for i, scene_text in enumerate(scenes_text):
            scene_num = i + 1

            scene_data = self._parse_single_scene(scene_text, scene_num)

            if scene_data:
                parsed_scenes.append(scene_data)
                out_path = output_dir / f"scene_{scene_num:02d}.yaml"
                with open(out_path, "w") as f:
                    yaml.dump(scene_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
                loc = scene_data.get("location", "?")
                chars = scene_data.get("characters", [])
                desc = scene_data.get("description", "")[:120]
                details = f"{loc} — {len(chars)} pers. — {desc}…"
                step_item(f"Scène {scene_num}", f"{scene_data.get('title', '')} | {details} → {out_path}")

        return parsed_scenes

    def _split_scenes(self, script_text: str) -> list[str]:
        pattern = r"(?:^|\n)##\s*(SCÈNE|SCENE)\s+\d+"
        splits = re.split(pattern, script_text, flags=re.IGNORECASE | re.MULTILINE)
        scenes = []
        current = ""
        found_first = False
        for part in splits:
            part = part.strip()
            if re.match(r"^SCÈNE|SCENE", part, re.IGNORECASE):
                if current.strip():
                    scenes.append(current.strip())
                current = part
                found_first = True
            elif found_first:
                current += "\n" + part
        if current.strip() and found_first:
            scenes.append(current.strip())
        return scenes

    def _extract_timeline(self, scene_text: str) -> list[dict[str, Any]]:
        """Extrait la timeline chronologique de la scène (actions + dialogues entrelacés).

        Le scénario alterne des blocs d'action en italique (`_..._`) et des répliques
        (`**LOCUTEUR**` suivi du texte). On parse séquentiellement, dans l'ordre, pour
        produire une liste ordonnée d'événements de la forme :
            {"type": "action",    "text": "..."}
            {"type": "dialogue",  "speaker": "LOCUTEUR", "text": "..."}
            {"type": "transition","text": "FONDU|CUT TO|INT. ..."}   (mise en scène)
        Les timestamps sont calculés ensuite (durée des actions + répliques).

        Returns:
            list[dict] — timeline ordonnée de la scène.
        """
        timeline: list[dict[str, Any]] = []
        lines = scene_text.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            # Bloc d'action en italique : `_..._` (parfois multiligne)
            if stripped.startswith("_") and stripped.endswith("_") and len(stripped) > 1:
                action_text = stripped[1:-1].strip()
                # Pré-découper en micro-actions (une action visuelle par phrase) pour
                # que le storyboarder puisse regrouper ces fragments en plans (cases).
                micros = re.split(r"(?<=[.;])\s+(?=[A-ZÀ-Ü«\"(])", action_text)
                micros = [s.strip() for s in micros if s.strip()]
                if len(micros) <= 1:
                    timeline.append({"type": "action", "text": action_text})
                else:
                    for k, frag in enumerate(micros):
                        timeline.append({"type": "action", "text": frag, "sub": k})
                i += 1
                continue
            # Marqueur de mise en scène en gras : **FONDU**, **CUT TO**, **INT. ...**, **EXT. ...**
            m = re.match(r"^\*\*(?P<head>[^*\n]+)\*\*$", stripped)
            if m:
                head = m.group("head").strip()
                upper = head.upper()
                if re.match(r"^(FONDU|CUT TO|RETOUR|INT\.|EXT\.)", upper):
                    timeline.append({"type": "transition", "text": head})
                else:
                    # Locuteur de réplique : collecter le texte qui suit jusqu'au prochain
                    # marqueur (`**...**` en gras) ou bloc d'action `_..._` sans parenthèses.
                    # Les didascalies d'aparté `_(...)_` sous le locuteur font partie du dialogue.
                    speaker = head
                    parts: list[str] = []
                    j = i + 1
                    while j < len(lines):
                        nxt = lines[j].strip()
                        if re.match(r"^\*\*[^*\n]+\*\*$", nxt):
                            break
                        if nxt.startswith("_") and nxt.endswith("_") and len(nxt) > 1:
                            # Aparté `_(...)_` → rattaché au dialogue ; sinon action → fin de réplique.
                            inner = nxt[1:-1].strip()
                            if inner.startswith("(") and inner.endswith(")"):
                                parts.append(nxt)
                                j += 1
                                continue
                            break
                        if nxt and not nxt.startswith("#"):
                            parts.append(nxt)
                        j += 1
                    text = " ".join(parts).strip()
                    if text:
                        timeline.append({"type": "dialogue", "speaker": speaker, "text": text})
                    i = j
                    continue
            # Ligne de texte isolée non reconnue : l'ignorer (en-têtes, etc.)
            i += 1
        return timeline

    @staticmethod
    def _annotate_timeline(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Ajoute index (position), group/group_total (bloc d'origine des micro-actions).

        Les micro-actions issues d'un même bloc d'action portent un `sub` croissant ;
        on les regroupe sous un même `group` pour indiquer au découpage qu'elles font
        partie du même bloc et peuvent être regroupées en un ou plusieurs plans.

        Returns:
            timeline annotée (nouvelle liste, entrées enrichies in place).
        """
        group = 0
        sub_seq: list[int] = []  # indices du groupe courant
        annotated: list[dict[str, Any]] = []
        for i, e in enumerate(timeline):
            e = dict(e)
            e["index"] = i + 1
            if e.get("type") == "action" and e.get("sub") is not None:
                if e["sub"] == 0:
                    if sub_seq:
                        for gi in sub_seq:
                            annotated[gi]["group_total"] = len(sub_seq)
                    group += 1
                    sub_seq = []
                e["group"] = group
                sub_seq.append(len(annotated))
            else:
                if sub_seq:
                    for gi in sub_seq:
                        annotated[gi]["group_total"] = len(sub_seq)
                    sub_seq = []
            annotated.append(e)
        if sub_seq:
            for gi in sub_seq:
                annotated[gi]["group_total"] = len(sub_seq)
        return annotated

    def _parse_single_scene(self, scene_text: str, scene_num: int) -> dict | None:
        timeline = self._extract_timeline(scene_text)
        timeline = self._annotate_timeline(timeline)
        try:
            result = self.llm.chat_structured(
                system=_SYSTEM_PROMPT,
                user=f"Scène {scene_num}:\n\n{scene_text[:12000]}",
                response_schema={"type": "array", "items": {"type": "object"}},
            )
            if isinstance(result, list) and result:
                scene = result[0]
                if not isinstance(scene, dict):
                    scene = {}
            elif isinstance(result, dict) and "error" not in result:
                scene = result
            else:
                logger.warning(f"Failed to parse scene {scene_num}: {result}")
                scene = self._fallback_parse(scene_text, scene_num)
            scene["timeline"] = timeline
            return scene
        except Exception as e:
            logger.error(f"Error parsing scene {scene_num}: {e}")
            scene = self._fallback_parse(scene_text, scene_num)
            scene["timeline"] = timeline
            return scene

    def _fallback_parse(self, scene_text: str, scene_num: int) -> dict:
        lines = scene_text.split("\n")
        title = lines[0] if lines else f"Scene {scene_num}"
        location = ""
        for line in lines[:5]:
            if "INT." in line or "EXT." in line:
                location = line.strip()
                break
        return {
            "number": scene_num,
            "title": title.replace("##", "").strip(),
            "location": location,
            "characters": [],
            "ambiance": "",
            "description": scene_text[:500],
            "shots": [],
        }
