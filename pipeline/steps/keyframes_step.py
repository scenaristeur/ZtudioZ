from __future__ import annotations

import json
import logging
import re
import sys
import time
from pathlib import Path

import yaml

from pipeline.config import get_config, get_production_config
from pipeline.services.image import ImageService
from pipeline.services.llm import LLMService
from pipeline.services.montage import assemble_scene
from pipeline.services.video import VideoService
from pipeline.steps.produce import _build_motion_prompt
from pipeline.utils import upload_to_tmpfiles

logger = logging.getLogger(__name__)

PROJECTS_DIR = Path(__file__).resolve().parent.parent.parent / "projets"


def _provider_cfg(service: str, name: str) -> dict:
    """Config d'un provider sans toucher à la config globale."""
    providers = get_config()["services"][service]["providers"]
    return dict(providers.get(name, {}))


class KeyframesStep:
    """Production vidéo par keyframes : beats → images intermédiaires → chaînage agnes-keyframes.

    Alternative configurable à la stratégie I2V de ProduceStep
    (`production.keyframes.enabled`). Pour chaque plan du storyboard minuté :
    1. Découpage en beats (actions du plan / toutes les N secondes).
    2. Image start = shot storyboard existant (réutilisé par défaut).
    3. Beats "évolution" → agnes local (source = image précédente, faible coût).
       Beats "multi_ref" → gpt-image (multi-références : image précédente + sheets).
    4. Chaînage des images via VideoService.generate(ref_images=...) (agnes-keyframes).

    Résolutions réduites en dev ; écrit les vidéos dans le même dossier que produce
    (`production/video/scene_XX/shot_NN.mp4`) pour ne pas casser audio/montage.
    Génère aussi un snapshot `production.json` (plan + video_path) dans le dossier de
    sortie, source pour audio/montage et pour relancer le step (repartir du snapshot).
    """

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.llm = LLMService()

    def run(self, project: str, scenes: list[dict], storyboard_dir: Path, video_dir: Path,
            output_suffix: str = "", shot_numbers: list[int] | None = None) -> dict:
        """Générer les vidéos keyframes par scène.

        Args:
            project: Slug du projet.
            scenes: Scènes à traiter (dict avec au minimum `number`).
            storyboard_dir: `preprod/storyboard`.
            video_dir: `production/video`.
            output_suffix: Suffixe de dossier de sortie (ex: "_kf" → scene_01_kf).
            shot_numbers: Numéros de plans à traiter (None = tous).
        """
        kf_cfg = get_production_config().get("keyframes", {})
        if not kf_cfg.get("enabled", True):
            logger.info("keyframes désactivé dans la config — on garde le flux i2v de produce")
            return {}

        results = {}
        for scene in scenes:
            scene_num = scene.get("number", 0)
            scene_key = f"scene_{scene_num:02d}"

            scene_video_dir = video_dir / f"{scene_key}{output_suffix}"
            scene_video_dir.mkdir(parents=True, exist_ok=True)

            # Plan source : snapshot production.json du dossier de sortie, sinon storyboard.json.
            # Le snapshot de sortie ne doit JAMAIS écraser le storyboard source.
            snapshot_path = scene_video_dir / "production.json"
            plan_path = snapshot_path if snapshot_path.exists() else storyboard_dir / scene_key / "storyboard.json"
            parsed_path = PROJECTS_DIR / project / "script" / "parsed" / f"scene_{scene_num:02d}.yaml"

            if not plan_path.exists() or not parsed_path.exists():
                logger.warning("Missing storyboard or parsed data for scene %d", scene_num)
                continue

            scene_data = yaml.safe_load(parsed_path.read_text())
            all_shots = json.loads(plan_path.read_text())
            if shot_numbers:
                shots = [s for s in all_shots if s.get("number") in shot_numbers]
            else:
                shots = all_shots

            storyboard_scene_dir = storyboard_dir / scene_key

            # Refonte : planification de TOUTE la scène en une passe LLM (scénario +
            # plans ordonnés + persos + ambiance + dialogues dans le timing). Les beats
            # sont ensuite figés par plan : une bonne keyframe du premier coup, sans
            # vérification+régénération coûteuse.
            scene_plan = self._plan_scene(project, scene_num, scene_data, shots, kf_cfg)

            logger.info("Scene %d: keyframes pour %d plans → %s", scene_num, len(shots), scene_video_dir)

            for shot in shots:
                num = shot["number"]
                out_path = scene_video_dir / f"shot_{num:02d}.mp4"
                if out_path.exists() and out_path.stat().st_size > 5000:
                    logger.info("Shot %d: vidéo keyframes existante, skip", num)
                    shot["video_path"] = str(out_path)
                    continue

                start_path = storyboard_scene_dir / f"shot_{num:02d}.png"
                if not start_path.exists():
                    logger.warning("Shot %d: pas d'image preprod, skip", num)
                    continue

                try:
                    result = self._process_shot(
                        project, shot, start_path, scene_data, scene_video_dir, kf_cfg,
                        beats=scene_plan.get(num),
                    )
                    shot["video_path"] = result["video"]
                    logger.info("Shot %d: %s (%d keyframes)", num, result["video"], len(result["images"]))
                except Exception as e:
                    logger.error("Shot %d keyframes failed: %s", num, e)

            # Snapshot de production : plan complet + vidéos générées (source pour audio/montage)
            snapshot_path.write_text(json.dumps(all_shots, ensure_ascii=False, indent=2))
            results[scene_key] = {"shots": all_shots, "count": len(shots)}

        return results

    # ── Beats ────────────────────────────────────────────────────

    def _break_down_beats(self, shot: dict, scene_data: dict, kf_cfg: dict,
                          feedback: str | None = None) -> list[dict]:
        """Décomposer le plan en images intermédiaires (beats).

        Chaque beat ne contient que le DELTA (action/événement entre l'image
        précédente et celle-ci). Les prompts d'ambiance et de personnages sont
        réinjectés à la génération de l'image (voir _compose_beat_prompt), jamais
        dans le delta, sinon l'identité visuelle dérive d'une frame à l'autre.
        """
        cadence = kf_cfg.get("cadence", "action")
        min_beats = int(kf_cfg.get("min_beats", 1))
        max_beats = int(kf_cfg.get("max_beats", 4))
        interval = int(kf_cfg.get("seconds_interval", 5))
        duration = shot.get("duration_seconds", 5)
        shot_end = bool(kf_cfg.get("shot_end", True))
        # L'image de fin est dédiée : les beats s'arrêtent donc au (max_beats-1)ᵉ.
        max_beats = max(1, max_beats - (1 if shot_end else 0))

        if cadence == "seconds":
            n = max(min_beats, min(max_beats, max(1, int(duration // interval))))
            beats = [
                {"t_seconds": (i + 1) * interval, "prompt": "", "strategy": "evolution", "extra_refs": []}
                for i in range(n)
            ]
            return self._with_end(beats, duration, shot_end)

        available_refs = self._available_refs(shot, scene_data)
        schema = {
            "type": "object",
            "properties": {
                "beats": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "t_seconds": {"type": "number"},
                            "prompt": {"type": "string"},
                            "strategy": {"type": "string", "enum": ["evolution", "multi_ref"]},
                            "extra_refs": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "end_prompt": {"type": "string"},
            },
        }
        system = (
            "You are a storyboard animator. Given one storyboard shot you produce the "
            "intermediate keyframes that will be chained by an AI video model (keyframes interpolation).\n"
            "Rules:\n"
            f"- The START image already exists and matches the shot. Produce ONLY the {min_beats}..{max_beats} "
            f"intermediate beat images (shot duration {duration}s).\n"
            "- Write each beat.prompt IN FRENCH. Each beat.prompt is ONLY the visual DELTA: the action/event/"
            "movement that changes between the previous keyframe and this one (e.g. 'le facteur tend la lettre à "
            "Paul-Henri', 'Paul-Henri regarde la lettre', 'Paul-Henri pose la lettre'). 1-2 short sentences. "
            "NEVER re-describe the setting, the characters' clothing, the lighting or the film style: those base "
            "prompts (scene, characters, ambiance) are appended separately at image generation time. Re-describing "
            "them makes the character identity drift between frames.\n"
            "- strategy 'evolution': the change continues from the previous keyframe (motion, expression, camera "
            "move), no new reference image needed.\n"
            "- strategy 'multi_ref': the beat introduces something NEW that needs a reference image (a character/"
            "object appears, camera reveals a new element). Put ONLY slugs from the provided available list in "
            "extra_refs. ALWAYS include the character slugs present in this shot (they must stay visible).\n"
            "- end_prompt: the FINAL state of the shot (very last frame), written as a delta in French (e.g. "
            "'Paul-Henri lit la lettre, l'expression touchée'). This is the dedicated END image after all beats.\n"
            f"Available reference slugs: {', '.join(available_refs) or 'none'}\n"
            "Return JSON: {\"beats\":[{\"t_seconds\":<float>,\"prompt\":\"<delta en français, action seule>\","
            "\"strategy\":\"evolution|multi_ref\",\"extra_refs\":[<slugs>]}],\"end_prompt\":\"<delta de fin en français>\"}"
        )
        user = json.dumps({
            "shot": {k: shot.get(k) for k in ("number", "type", "camera", "description",
                                              "prompt_image", "dialogue", "duration_seconds")},
            "characters": scene_data.get("characters", []),
            "location": scene_data.get("location", ""),
        }, ensure_ascii=False, indent=2)
        if feedback:
            user = json.dumps({
                "shot": json.loads(user),
                "feedback": f"User correction after validating the keyframes: {feedback}. "
                            "Regenerate the beats accordingly (keep deltas minimal).",
            }, ensure_ascii=False, indent=2)

        beats: list[dict] = []
        end_prompt = ""
        try:
            data = self.llm.chat_structured(system, user, schema)
            beats = (data or {}).get("beats", []) if isinstance(data, dict) else []
            end_prompt = (data or {}).get("end_prompt", "") if isinstance(data, dict) else ""
        except Exception as e:
            logger.error("Beat breakdown LLM failed, fallback cadence seconds: %s", e)

        if not beats:
            n = max(min_beats, min(max_beats, max(1, int(duration // interval))))
            beats = [
                {"t_seconds": (i + 1) * interval, "prompt": "", "strategy": "evolution", "extra_refs": []}
                for i in range(n)
            ]
            return self._with_end(beats, duration, shot_end)

        clean = []
        for b in beats[:max_beats]:
            if not isinstance(b, dict):
                continue
            b.setdefault("strategy", "evolution")
            b.setdefault("prompt", "")
            b.setdefault("extra_refs", [])
            if b["strategy"] not in ("evolution", "multi_ref"):
                b["strategy"] = "evolution"
            b["extra_refs"] = [s for s in b.get("extra_refs", []) if s in available_refs]
            clean.append(b)
        if shot_end and end_prompt.strip():
            clean.append({"t_seconds": duration, "prompt": end_prompt.strip(),
                          "strategy": "evolution", "extra_refs": [], "is_end": True})
        return clean

    def _plan_scene(self, project: str, scene_num: int, scene_data: dict, shots: list[dict],
                    kf_cfg: dict) -> dict[int, list[dict]]:
        """Planifier les keyframes de toute la scène en UNE passe LLM.

        Le LLM reçoit le scénario complet de la scène (source de vérité), les plans
        storyboard dans l'ordre (avec dialogues et durées), les personnages (bible) et
        l'ambiance (film + scène + intention). Pour chaque plan il liste les keyframes
        de transition, chacune ancrée à un événement/réplique du scénario (champ
        `source`) avec un `t_seconds` intégrant le timing des dialogues. La stratégie
        (evolution/multi_ref) est décidée en post-traitement (_finalize_beats), jamais
        par le LLM — c'est une règle déterministe, pas un choix libre.

        Returns:
            dict[int, list[dict]] — beats finaux par numéro de plan.
        """
        script_text = self._script_scene_text(project, scene_num)
        available = sorted({s for shot in shots for s in self._available_refs(shot, scene_data)})
        schema = {
            "type": "object",
            "properties": {
                "shots": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "number": {"type": "integer"},
                            "beats": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "t_seconds": {"type": "number"},
                                        "prompt": {"type": "string"},
                                        "extra_refs": {"type": "array", "items": {"type": "string"}},
                                        "source": {"type": "string"},
                                    },
                                },
                            },
                            "end_prompt": {"type": "string"},
                        },
                    },
                },
            },
        }
        system = (
            "Tu es un monteur storyboarder. Tu détermines les images-clés (keyframes) qui "
            "permettent de passer d'un plan à l'autre d'une scène, en respectant l'ambiance "
            "du film, de la scène et du plan, et en intégrant le timing des dialogues.\n\n"
            "Tu reçois :\n"
            "- le scénario complet de la scène (source de vérité) ;\n"
            "- les plans storyboard dans l'ordre, chacun avec sa TIMELINE : la liste ordonnée "
            "de ses événements (actions, dialogues, transitions) avec leurs timestamps dans la "
            "scène ({start, duration}) ;\n"
            "- les personnages avec vêtements et attitudes (bible) ;\n"
            "- l'ambiance du film (style guide), l'ambiance et l'intention de la scène ;\n"
            "- la liste des slugs de référence disponibles.\n\n"
            "Pour CHAQUE plan, liste les keyframes intermédiaires (beats) qui font avancer "
            "l'action de son image de départ (le shot storyboard, déjà existant) à son image finale.\n\n"
            "RÈGLES STRICTES :\n"
            "1. Chaque beat.prompt est UNIQUEMENT le DELTA visuel en français : l'action/événement "
            "qui change entre la keyframe précédente et celle-ci (ex. « le facteur tend la lettre »). "
            "Jamais de ré-description du décor, des vêtements, de la lumière ou du style : ils sont "
            "réinjectés séparément à la génération. 1-2 phrases courtes.\n"
            "2. Ancre chaque beat à la TIMELINE du plan : `source` = le texte exact de l'événement "
            "(dialogue ou action) dont vient l'action, et `t_seconds` = son timestamp `start` dans "
            "la scène (converti en secondes depuis le début du plan). N'invente RIEN qui ne soit pas "
            "dans la timeline du plan ou le scénario.\n"
            "3. Timing : t_seconds croissants, dans ]0, durée_seconds[. Chaque événement de la "
            "timeline du plan (dialogue important, action) doit correspondre à une keyframe au bon "
            "moment. Une réplique = une keyframe au moment où elle est dite.\n"
            "4. end_prompt = l'état final du plan, en français, écrit comme un delta (dernière frame).\n"
            "5. Ambiance : les actions et expressions doivent refléter l'ambiance de la scène "
            "(ex. mélancolie → gestes lents, regards vides) et servir l'intention.\n"
            "6. extra_refs : slugs UNIQUEMENT de la liste disponible, pour les éléments NOUVEAUX qui "
            "entrent dans le champ (personnage/objet qui apparaît). Liste toujours les personnages "
            "présents dans le plan.\n"
            f"Slugs disponibles : {', '.join(available) or 'aucun'}\n"
            "Réponds UNIQUEMENT en JSON valide : {\"shots\":[{\"number\":<int>,\"beats\":[{\"t_seconds\":"
            "<float>,\"prompt\":\"<delta en français>\",\"extra_refs\":[<slugs>],\"source\":\"<phrase du "
            "scénario>\"}],\"end_prompt\":\"<état final en français>\"}]}"
        )
        def _shot_payload(shot: dict) -> dict:
            base = {k: shot.get(k) for k in ("number", "type", "camera", "description",
                                             "prompt_image", "dialogue", "duration_seconds")}
            # La timeline du plan (événements + timestamps) est la source de vérité
            # du découpage : les beats doivent être calés dessus.
            base["timeline"] = shot.get("timeline") or []
            return base

        user = json.dumps({
            "scene": {k: scene_data.get(k) for k in ("number", "title", "location", "time_of_day",
                                                     "ambiance", "intention", "description")},
            "script": script_text or "(scénario indisponible — ancre-toi aux descriptions des plans)",
            "style_guide": self._ambiance_prompt(project),
            "characters": scene_data.get("character_details", {}),
            "shots": [_shot_payload(s) for s in shots],
        }, ensure_ascii=False, indent=2)

        plan: dict[int, list[dict]] = {}
        try:
            data = self.llm.chat_structured(system, user, schema) or {}
            for entry in data.get("shots", []):
                num = int(entry.get("number", 0))
                shot = next((s for s in shots if int(s.get("number", 0)) == num), None)
                if not shot:
                    continue
                plan[num] = self._finalize_beats(shot, entry.get("beats", []),
                                                 entry.get("end_prompt", ""), scene_data, kf_cfg)
        except Exception as e:
            logger.error("Plan scène %d LLM failed, fallback par plan: %s", scene_num, e)
        return plan

    def _finalize_beats(self, shot: dict, beats: list[dict], end_prompt: str,
                        scene_data: dict, kf_cfg: dict) -> list[dict]:
        """Règles déterministes : stratégie, refs perso obligatoires, image finale.

        - Les slugs des personnages de la scène sont TOUJOURS dans les refs (identité).
        - La stratégie est une règle, pas un choix LLM : `multi_ref` pour le 1er beat
          et pour tout beat qui introduit un nouvel élément (ref pas encore établie),
          sinon `evolution`. Config `keyframes.strategy` = "multi_ref" force tout.
        - L'image finale dédiée (shot_end) est ajoutée après les beats.
        """
        duration = shot.get("duration_seconds", 5)
        shot_end = bool(kf_cfg.get("shot_end", True))
        max_beats = max(1, int(kf_cfg.get("max_beats", 4)) - (1 if shot_end else 0))
        force = kf_cfg.get("strategy", "multi_ref") == "multi_ref"
        available = self._available_refs(shot, scene_data)
        scene_slugs = self._character_slugs(scene_data)

        clean: list[dict] = []
        established: set[str] = set()
        for b in beats[:max_beats]:
            if not isinstance(b, dict):
                continue
            refs = [s for s in (b.get("extra_refs") or []) if s in available]
            refs = list(dict.fromkeys(scene_slugs + refs))
            new_elements = [s for s in refs if s not in established]
            if force or not clean or new_elements:
                strategy = "multi_ref"
            else:
                strategy = "evolution"
            established |= set(refs)
            clean.append({
                "t_seconds": min(max(0.0, float(b.get("t_seconds") or 0)), float(duration)),
                "prompt": (b.get("prompt") or "").strip(),
                "source": (b.get("source") or "").strip(),
                "strategy": strategy,
                "extra_refs": refs,
            })
        clean.sort(key=lambda x: x["t_seconds"])
        # Fix : si plusieurs beats partagent le même timestamp (souvent tous à la durée,
        # car le LLM renvoie t_seconds=0 pour le 1er puis duration), on les répartit de
        # façon croissante et distincte entre 0 et duration pour échelonner le plan.
        if len(clean) > 1:
            seen: dict[float, list[int]] = {}
            for i, b in enumerate(clean):
                seen.setdefault(b["t_seconds"], []).append(i)
            for ts, idxs in seen.items():
                if len(idxs) < 2 or ts == 0.0:
                    continue
                # Répartir les beats à timestamp identique uniformément dans l'ordre.
                lo = clean[idxs[0] - 1]["t_seconds"] if idxs[0] > 0 else 0.0
                hi = float(duration)
                span = hi - lo
                n = len(idxs)
                for k, pos in enumerate(idxs):
                    frac = (k + 1) / (n + 1)
                    clean[pos]["t_seconds"] = round(lo + span * frac, 2)
        clean.sort(key=lambda x: x["t_seconds"])
        if shot_end and end_prompt.strip():
            clean.append({"t_seconds": float(duration), "prompt": end_prompt.strip(),
                          "source": "", "strategy": "evolution", "extra_refs": [], "is_end": True})
        return clean

    @staticmethod
    def _with_end(beats: list[dict], duration: int, shot_end: bool) -> list[dict]:
        """Ajoute l'image finale dédiée (fallback sans LLM) quand shot_end est actif."""
        if not shot_end:
            return beats
        end = {"t_seconds": duration, "prompt": "", "strategy": "evolution",
               "extra_refs": [], "is_end": True}
        return beats + [end]

    @staticmethod
    def _available_refs(shot: dict, scene_data: dict) -> list[str]:
        refs = set()
        for r in shot.get("refs") or []:
            if isinstance(r, str):
                refs.add(r)
            elif isinstance(r, dict):
                refs.add(r.get("slug") or r.get("name") or "")
        for e in scene_data.get("characters") or []:
            refs.add(e.lower().replace(" ", "-"))
        refs.discard("")
        return sorted(refs)

    # ── Prompts ─────────────────────────────────────────────────

    def _compose_beat_prompt(self, shot: dict, scene_data: dict, project: str,
                             beat: dict | None = None) -> str:
        """Prompt final d'image = delta + persos (bible) + base scène/plan + ambiance.

        Le delta (action entre deux frames) est volontairement minimal ; l'identité
        des personnages (vêtements, attitude — source de vérité bible) est réinjectée
        AVANT le prompt du plan pour qu'elle l'emporte sur un éventuel conflit de
        tenue dans le storyboard. La scène et l'ambiance complètent.
        """
        parts: list[str] = []
        delta = (beat or {}).get("prompt", "").strip()
        if delta:
            parts.append(delta)

        chars = self._character_details(shot, scene_data, delta)
        if chars:
            parts.append(chars)

        base = (shot.get("prompt_image") or "").strip()
        if base:
            parts.append(base)

        scene_ambiance = (scene_data.get("ambiance") or "").strip()
        if scene_ambiance:
            parts.append(f"Ambiance de la scène : {scene_ambiance}")

        ambiance = self._ambiance_prompt(project)
        if ambiance:
            parts.append(ambiance)

        return ". ".join(parts) + "."

    def _character_details(self, shot: dict, scene_data: dict, delta_text: str = "") -> str:
        """Détails (vêtements + attitude) de TOUS les personnages de la scène.

        La bible est la source de vérité : tous les personnages de la scène sont
        réinjectés à chaque keyframe (même ceux absents du delta) pour éviter la
        dérive d'identité (ex. uniforme « La Poste » perdu entre deux plans).
        """
        char_details = scene_data.get("character_details", {})
        if not char_details:
            return ""

        out: list[str] = []
        for char_name, details in char_details.items():
            bit = f"{char_name}:"
            if details.get("clothing"):
                bit += f" {details['clothing']}"
            if details.get("attitude"):
                bit += f" attitude: {details['attitude']}"
            if bit != f"{char_name}: ":
                out.append(bit)
        return "; ".join(out)

    @staticmethod
    def _ambiance_prompt(project: str) -> str:
        style_path = PROJECTS_DIR / project / "bible" / "ambiance" / "style-guide.yaml"
        if not style_path.exists():
            return ""
        sd = yaml.safe_load(style_path.read_text())
        ver = sd.get("versions", {}).get(sd.get("active_version", "v1"), {})
        return ver.get("prompt_image", "")

    # ── Images intermédiaires ────────────────────────────────────

    def _generate_images(self, project: str, shot: dict, start_path: Path,
                         beats: list[dict], scene_data: dict, scene_video_dir: Path,
                         kf_cfg: dict) -> tuple[list[str], list[float], dict]:
        """Générer les keyframes : start puis un beat à la fois (chaîne de consistance).

        Chaque keyframe est VÉRIFIÉE avant de passer à la suivante : continuité avec
        l'image précédente, conformité au prompt de transition (delta), au découpage
        storyboard du plan et au script original (extrait de la scène). En cas de
        non-conformité, une boucle de correction interactive propose des corrections
        de données (delta/refs du beat) ou d'outil (composés prompts, config, skills),
        à faire valider par l'utilisateur.

        Returns:
            (images, timestamps, verdicts) — timestamps alignés sur `images`
            (t=0 pour le start, puis t_seconds de chaque beat), verdicts par index.
        """
        key_dir = scene_video_dir / f"kf_shot_{shot['number']:02d}"
        key_dir.mkdir(parents=True, exist_ok=True)
        scene_num = int(scene_data.get("number", 0) or 0)
        verify = bool(kf_cfg.get("verify_each_keyframe", True))
        max_tries = int(kf_cfg.get("verify_max_tries", 3))

        if kf_cfg.get("regen_start") or not start_path.exists():
            start = self._generate_multi_ref(
                project, shot, key_dir / "kf_00_start.png", kf_cfg,
                previous=None, prompt=None, extra_slugs=shot.get("refs_slugs") or [],
                scene_data=scene_data,
            )
        else:
            start = str(start_path)

        images = [start]
        timestamps = [0.0]
        prev = start
        verdicts: dict[int, dict] = {}
        for i, beat in enumerate(beats, 1):
            dest = key_dir / f"kf_{i:02d}.png"
            prompt_file = dest.with_suffix(".txt")
            verify_file = dest.with_suffix(".verify.json")
            prompt = self._compose_beat_prompt(shot, scene_data, project, beat)
            cached = (dest.exists() and dest.stat().st_size > 10000
                      and prompt_file.exists() and prompt_file.read_text() == prompt)

            if cached:
                img = str(dest)
                if verify and not self._verdict_conform(verify_file):
                    verdicts[i] = self._verify_keyframe(
                        project, scene_num, shot, scene_data, beat, prev, img, kf_cfg)
                    self._save_verdict(verify_file, verdicts[i])
                    decision, correction = self._verify_loop(
                        project, scene_num, shot, scene_data, beat, i, verdicts[i], kf_cfg)
                    if decision == "skip":
                        continue
                    if decision == "quit":
                        raise KeyboardInterrupt("Vérification interrompue par l'utilisateur")
                    if decision == "regenerate":
                        if correction:
                            beat["prompt"] = correction
                            prompt = self._compose_beat_prompt(shot, scene_data, project, beat)
                            prompt_file.write_text(prompt)
                            dest.unlink(missing_ok=True)
                        img = self._regenerate_beat(
                            project, shot, scene_data, beat, prev, dest, prompt, kf_cfg)
                images.append(img)
                timestamps.append(float(beat.get("t_seconds") or i))
                prev = img
                continue

            img = None
            correction: str | None = None
            for attempt in range(max_tries + 1):
                if correction:
                    beat["prompt"] = correction
                    prompt = self._compose_beat_prompt(shot, scene_data, project, beat)
                # Le prompt a changé (branche non-cache) : le fichier existant est
                # obsolète. ImageService ne déduplique par hash que si filename est
                # omis ; ici on force la régénération en supprimant l'ancien fichier.
                dest.unlink(missing_ok=True)
                img = self._regenerate_beat(
                    project, shot, scene_data, beat, prev, dest, prompt, kf_cfg)
                prompt_file.write_text(prompt)

                if not verify:
                    break
                verdicts[i] = self._verify_keyframe(
                    project, scene_num, shot, scene_data, beat, prev, img, kf_cfg)
                self._save_verdict(verify_file, verdicts[i])
                if self._verdict_conform(verify_file):
                    break
                decision, correction = self._verify_loop(
                    project, scene_num, shot, scene_data, beat, i, verdicts[i], kf_cfg)
                if decision == "continue":
                    break
                if decision == "skip":
                    img = None
                    break
                if decision == "quit":
                    raise KeyboardInterrupt("Vérification interrompue par l'utilisateur")

            if img is None:
                logger.info("Shot %d: keyframe %d sautée (skip)", shot["number"], i)
                continue
            images.append(img)
            timestamps.append(float(beat.get("t_seconds") or i))
            prev = img

        return images, timestamps, verdicts

    def _regenerate_beat(self, project: str, shot: dict, scene_data: dict, beat: dict,
                         prev: str, dest: Path, prompt: str, kf_cfg: dict) -> str:
        """Génère (ou régénère) une keyframe selon la stratégie du beat."""
        if beat.get("strategy") == "multi_ref" and beat.get("extra_refs"):
            # Fix #3 : les personnages de la scène restent référencés à CHAQUE beat
            # multi_ref (pas seulement le 1er) pour stabiliser leur identité.
            extra_slugs = list(dict.fromkeys(beat["extra_refs"] + self._character_slugs(scene_data)))
            return self._generate_multi_ref(project, shot, dest, kf_cfg,
                                            previous=prev, prompt=prompt,
                                            extra_slugs=extra_slugs,
                                            scene_data=scene_data)
        return self._generate_evolution(prompt, prev, dest, kf_cfg)

    @staticmethod
    def _character_slugs(scene_data: dict) -> list[str]:
        """Slugs des personnages présents dans la scène (pour les refs multi_ref)."""
        slugs = []
        for name in scene_data.get("characters") or []:
            slug = str(name).lower().replace(" ", "-").replace("\u2011", "-")
            if slug:
                slugs.append(slug)
        return slugs

    @staticmethod
    def _verdict_conform(verify_file: Path) -> bool:
        """Vrai si un verdict déjà sauvegardé est conforme."""
        if not verify_file.exists():
            return False
        try:
            v = json.loads(verify_file.read_text())
            return bool(v.get("conforme")) and bool(v.get("raw"))
        except (json.JSONDecodeError, OSError):
            return False

    @staticmethod
    def _save_verdict(verify_file: Path, verdict: dict) -> None:
        try:
            verify_file.write_text(json.dumps(verdict, ensure_ascii=False, indent=2))
        except OSError as e:
            logger.warning("Impossible d'écrire le verdict %s: %s", verify_file, e)

    # ── Vérification d'une keyframe ──────────────────────────────

    @staticmethod
    def _script_scene_text(project: str, scene_num: int) -> str:
        """Extrait le texte de la scène du scénario original (référence de conformité)."""
        script_path = PROJECTS_DIR / project / "script" / "scenario.md"
        if not script_path.exists():
            return ""
        text = script_path.read_text()
        pattern = r"(?:^|\n)##\s*(?:SCÈNE|SCENE)\s+(\d+)"
        matches = list(re.finditer(pattern, text, flags=re.IGNORECASE))
        for i, m in enumerate(matches):
            if int(m.group(1)) == scene_num:
                start = m.start()
                end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
                return text[start:end].strip()
        return ""

    def _verify_keyframe(self, project: str, scene_num: int, shot: dict, scene_data: dict,
                         beat: dict, prev_image: str, new_image: str, kf_cfg: dict) -> dict:
        """Vérifie la conformité d'une keyframe via vision LLM.

        Axes vérifiés : continuité avec l'image précédente, conformité au prompt de
        transition (delta), au découpage storyboard du plan et au script original.
        Propose des corrections (données ou outil) en cas de non-conformité.

        Returns:
            Verdict dict : conforme (bool), niveau, issues, propositions, raw.
        """
        delta = beat.get("prompt", "")
        script_text = self._script_scene_text(project, scene_num)
        prompt = (
            "Tu es un superviseur de cohérence visuelle pour une production de film en "
            "images-clés. Tu reçois DEUX images : l'image PRÉCÉDENTE (déjà validée) et la "
            "NOUVELLE keyframe générée. Vérifie la conformité de la NOUVELLE image sur 4 axes :\n"
            "1. CONTINUITÉ : mêmes personnages, mêmes vêtements/couleurs, même décor, mêmes "
            "objets que l'image précédente ?\n"
            "2. TRANSITION : l'action décrite dans le prompt de transition est-elle bien "
            "représentée ?\n"
            "3. STORYBOARD : le plan (type, caméra, description) est-il respecté ?\n"
            "4. SCRIPT : la scène est-elle fidèle au script original ?\n"
            f"Prompt de transition (delta) : {delta}\n"
            f"Plan (storyboard) : type={shot.get('type')}, caméra={shot.get('camera')}, "
            f"description={shot.get('description')}\n"
            f"Personnages (bible) : {json.dumps(scene_data.get('character_details', {}), ensure_ascii=False)}\n"
            f"Script original de la scène {scene_num} : {script_text[:1500]}\n"
            "Réponds UNIQUEMENT en JSON valide : {\"conforme\": true|false, \"niveau\": "
            "\"continuite\"|\"transition\"|\"storyboard\"|\"script\"|\"global\", \"issues\": [\"...\"], "
            "\"propositions\": [\"correction de données ou d'outil, précise (ex. ajoute le-facteur aux refs "
            "du beat, allège le delta, corrige le prompt du plan, change la config)\"]}"
        )
        raw = self.llm.vision([prev_image, new_image], prompt,
                              model=kf_cfg.get("verify_model") or None)
        return self._parse_verdict(raw)

    @staticmethod
    def _parse_verdict(raw: str) -> dict:
        """Parse le verdict JSON de la vision (avec récupération best-effort)."""
        try:
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(raw[start:end + 1])
        except (json.JSONDecodeError, ValueError):
            pass
        return {
            "conforme": False,
            "niveau": "global",
            "issues": [raw[:500]] if raw else ["Réponse vision non interprétable"],
            "propositions": [],
            "raw": raw,
        }

    def _verify_loop(self, project: str, scene_num: int, shot: dict, scene_data: dict,
                     beat: dict, beat_idx: int, verdict: dict, kf_cfg: dict) -> tuple[str, str | None]:
        """Boucle interactive de correction après une vérification non conforme.

        Returns:
            (decision, correction) — decision: continue | regenerate | skip | quit
        """
        print("\n" + "─" * 64)
        print(f"  🔎 Vérification keyframe {beat_idx} du plan {shot['number']} — NON CONFORME")
        print("─" * 64)
        print(f"  Niveau : {verdict.get('niveau', '?')}")
        for issue in verdict.get("issues") or []:
            print(f"  ✗ {issue}")
        props = verdict.get("propositions") or []
        if props:
            print("  Propositions de correction (données / outil) :")
            for j, p in enumerate(props, 1):
                print(f"    {j}. {p}")

        if not sys.stdin.isatty():
            logger.info("Pas de terminal interactif — keyframe %d acceptée malgré la non-conformité", beat_idx)
            return "continue", None

        while True:
            answer = input("\n  (c)ontinuer, (r)egénérer (correction), (p)ropose correction "
                           "(données/outil), (s)kip keyframe, (q)uit : ").strip().lower()
            if answer == "c":
                return "continue", None
            if answer == "s":
                return "skip", None
            if answer == "q":
                return "quit", None
            if answer in ("r", "p"):
                if props and answer == "p":
                    pick = input(f"  Proposition à appliquer (1-{len(props)}), ou texte libre : ").strip()
                    try:
                        idx = int(pick) - 1
                        correction = props[idx] if 0 <= idx < len(props) else pick
                    except ValueError:
                        correction = pick
                else:
                    correction = input("  Correction à appliquer au delta du beat : ").strip()
                if correction:
                    logger.info("Correction validée pour keyframe %d: %s", beat_idx, correction)
                    self._log_tool_proposal(project, scene_num, shot, beat_idx, correction)
                    return "regenerate", correction
            print("  Choix non reconnu.")

    @staticmethod
    def _log_tool_proposal(project: str, scene_num: int, shot: dict,
                           beat_idx: int, proposal: str) -> None:
        """Enregistre une correction proposée (données/outil) pour apprentissage auto."""
        log_dir = PROJECTS_DIR / project / "production" / "video" / f"scene_{scene_num:02d}_kf"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "verification_corrections.md"
        line = (f"- Plan {shot['number']}, keyframe {beat_idx} "
                f"({time.strftime('%Y-%m-%d %H:%M')}) : {proposal}")
        try:
            content = log_path.read_text() if log_path.exists() else "# Corrections validées (keyframes)\n"
            log_path.write_text(content.rstrip() + "\n" + line + "\n")
        except OSError as e:
            logger.warning("Impossible de journaliser la correction: %s", e)

    def _generate_evolution(self, prompt: str, source: str, dest: Path, kf_cfg: dict) -> str:
        """Beat évolution : agnes local, source = image précédente (faible coût)."""
        cfg = _provider_cfg("image", "agnes")
        if kf_cfg.get("dev") is not None:
            cfg["dev"] = kf_cfg["dev"]
        size = kf_cfg.get("image_size", "")
        if size and "x" in size:
            cfg["size"] = size
            cfg["dev_size"] = size
        svc = ImageService(config=cfg)
        path = svc.generate(
            prompt=prompt,
            ref_image=source,
            output_dir=str(dest.parent),
            filename=dest.name,
        )
        return str(path)

    def _generate_multi_ref(self, project: str, shot: dict, dest: Path, kf_cfg: dict,
                            previous: str | None = None, prompt: str | None = None,
                            extra_slugs: list[str] | None = None,
                            scene_data: dict | None = None) -> str:
        """Beat multi-références : gpt-image (UNIQUEMENT quand plusieurs refs sont nécessaires)."""
        cfg = _provider_cfg("image", "pollinations")
        if kf_cfg.get("dev") is not None:
            cfg["dev"] = kf_cfg["dev"]
        size = kf_cfg.get("image_size", "")
        if size and "x" in size:
            try:
                cfg["width"], cfg["height"] = (int(x) for x in size.split("x", 1))
            except ValueError:
                pass
        model = kf_cfg.get("multi_ref_model", "gptimage")

        refs: list[Path] = []
        if previous:
            refs.append(Path(previous))
        for slug in extra_slugs or []:
            sheet = self._find_sheet(project, slug)
            if sheet:
                refs.append(sheet)
        refs = list(dict.fromkeys(refs))

        if prompt is None:
            prompt = self._compose_beat_prompt(shot, scene_data or {}, project, beat=None)
        if not refs:
            logger.warning("Shot %d: multi_ref sans aucune référence, évolution agnes", shot["number"])
            return self._generate_evolution(prompt, previous or "", dest, kf_cfg)

        urls = [upload_to_tmpfiles(r) for r in refs]
        svc = ImageService(config=cfg)
        return str(svc.generate(
            prompt=prompt,
            model=model,
            ref_image="|".join(urls),
            output_dir=str(dest.parent),
            filename=dest.name,
        ))

    @staticmethod
    def _find_sheet(project: str, slug: str) -> Path | None:
        bible = PROJECTS_DIR / project / "bible"
        for etype in ("personnage", "lieu", "objet"):
            for p in (bible / etype / slug).glob("sheet_*.png"):
                return p
        return None

    # ── Vidéo ────────────────────────────────────────────────────

    def _chain_video(self, project: str, shot: dict, scene_data: dict, images: list[str],
                     timestamps: list[float], scene_video_dir: Path, kf_cfg: dict) -> Path:
        num = shot["number"]
        out_path = scene_video_dir / f"shot_{num:02d}.mp4"
        refs = list(dict.fromkeys(images))

        cfg = _provider_cfg("video", "agnes")
        if kf_cfg.get("dev") is not None:
            cfg["dev"] = kf_cfg["dev"]
        # apihub direct : mode keyframes limité à 2-3 images → fenêtres de 3 chevauchées
        if len(refs) > 2 and kf_cfg.get("use_direct_for_multi", True):
            cfg["direct"] = True
            window = 3
            logger.info("Shot %d: %d keyframes → agnes apihub direct (fenêtres de 3)", num, len(refs))
        else:
            window = 2
            logger.info("Shot %d: %d keyframes → agnes serveur local :8765", num, len(refs))

        # `duration_seconds` est un MAXIMUM : la durée de chaque segment suit la
        # temporisation du plan (t_seconds des beats, événement, expression), jamais
        # au-delà du max. Sans timestamps, on répartit le max entre les segments.
        max_duration = max(1, int(shot.get("duration_seconds", 5) or 5))
        if not timestamps:
            timestamps = [max_duration * (i / max(1, len(refs) - 1)) for i in range(len(refs))]
        if len(timestamps) < len(refs):
            timestamps = timestamps + [timestamps[-1]] * (len(refs) - len(timestamps))

        prompt = _build_motion_prompt(shot, scene_data, project)
        svc = VideoService(config=cfg)

        if len(refs) <= window:
            # Un seul segment : l'écart de temporisation du plan (max = duration_seconds)
            duration = max(1, round(min(max_duration, timestamps[-1] - timestamps[0])))
            path = svc.generate(
                prompt=prompt, ref_images=refs, seed=num, duration=duration,
                output_dir=str(scene_video_dir), filename=out_path.name,
            )
            return Path(path)

        # > window keyframes : segments de window images avec chevauchement, puis concat.
        # Fix #5 : la durée RÉELLE du plan est répartie selon la temporisation des
        # keyframes (ex. 2,5 s + 2,5 s pour un plan de 5 s) au lieu de plan × nb segments.
        windows = [refs[i:i + window] for i in range(0, len(refs) - 1, window - 1)]
        key_dir = scene_video_dir / f"kf_shot_{num:02d}"
        key_dir.mkdir(parents=True, exist_ok=True)

        seg_paths: list[str] = []
        for i, win_refs in enumerate(windows, 1):
            seg = key_dir / f"seg_{i:02d}.mp4"
            if not (seg.exists() and seg.stat().st_size > 5000):
                # durée du segment = temporisation entre la 1ère et la dernière keyframe
                i0 = refs.index(win_refs[0])
                i1 = refs.index(win_refs[-1])
                seg_duration = max(1, round(min(max_duration, timestamps[i1] - timestamps[i0])))
                logger.info("Shot %d: segment %d/%d (%d keyframes, %ds)", num, i, len(windows), len(win_refs), seg_duration)
                svc.generate(
                    prompt=prompt, ref_images=win_refs, seed=num + i, duration=seg_duration,
                    output_dir=str(key_dir), filename=seg.name,
                )
            seg_paths.append(str(seg))

        assemble_scene(seg_paths, output_path=str(out_path))
        return out_path

    # ── Orchestration d'un plan ──────────────────────────────────

    def _process_shot(self, project: str, shot: dict, start_path: Path, scene_data: dict,
                      scene_video_dir: Path, kf_cfg: dict, beats: list[dict] | None = None) -> dict:
        """Beats → keyframes (vérifiées une à une) → validation humaine → vidéo.

        Args:
            beats: Beats prédéfinis par le plan de scène (une bonne keyframe du premier
                coup). None = découpage par plan (fallback `_break_down_beats`).
        """
        validate = bool(kf_cfg.get("validate_before_video", True))

        images: list[str] = []
        timestamps: list[float] = []
        verdicts: dict = {}
        feedback: str | None = None
        attempt = 0
        while True:
            if beats is None:
                beats = self._break_down_beats(shot, scene_data, kf_cfg, feedback=feedback)
            images, timestamps, verdicts = self._generate_images(
                project, shot, start_path, beats, scene_data, scene_video_dir, kf_cfg)
            feedback = None

            if not validate or attempt >= int(kf_cfg.get("max_validation_tries", 3)):
                break
            decision, feedback = self._validate(shot, beats, images, scene_data, project)
            if decision == "continue":
                break
            if decision == "skip":
                return {"video": "", "images": images, "beats": beats, "skipped": True}
            if decision == "quit":
                raise KeyboardInterrupt("Validation interrompue par l'utilisateur")
            attempt += 1

        video = self._chain_video(project, shot, scene_data, images, timestamps, scene_video_dir, kf_cfg)

        key_dir = scene_video_dir / f"kf_shot_{shot['number']:02d}"
        manifest = {
            "shot": shot["number"],
            "duration_seconds": shot.get("duration_seconds", 5),
            "start_image": images[0] if images else "",
            "base_prompt": self._compose_beat_prompt(shot, scene_data, project, beat=None),
            "beats": beats,
            "images": images,
            "timestamps": timestamps,
            "verifications": verdicts,
            "video": str(video),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        (key_dir / "kf_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        return {"video": str(video), "images": images, "beats": beats}

    def _validate(self, shot: dict, beats: list[dict], images: list[str],
                  scene_data: dict, project: str) -> tuple[str, str | None]:
        """Validation humaine : prompts scène/plan/perso, transitions et keyframes.

        Returns:
            (decision, feedback) — decision: continue | retry | skip | quit
        """
        base_prompt = self._compose_beat_prompt(shot, scene_data, project, beat=None)
        print("\n" + "─" * 64)
        print(f"  🔎 Validation plan {shot['number']} — avant génération de la vidéo")
        print("─" * 64)
        print(f"  Base scène/plan/perso/ambiance :\n    {base_prompt}\n")
        for i, b in enumerate(beats, 1):
            delta = self._compose_beat_prompt(shot, scene_data, project, b)
            print(f"  Beat {i} @{b.get('t_seconds', i * 5)}s [{b.get('strategy')}] "
                  f"refs={b.get('extra_refs') or '-'}")
            print(f"    delta: {b.get('prompt', '')}")
            print(f"    prompt complet: {delta}")
        print("\n  Keyframes:")
        for img in images:
            print(f"    - {img}")

        if not sys.stdin.isatty():
            logger.info("Pas de terminal interactif — validation automatiquement validée")
            return "continue", None

        while True:
            answer = input("\n  (c)ontinuer, (r)egénérer beats (correction), "
                           "(s)kip vidéo, (q)uit : ").strip().lower()
            if answer == "c":
                return "continue", None
            if answer == "r":
                feedback = input("  Correction à appliquer aux beats : ").strip()
                return "retry", feedback or "Corrige les deltas pour qu'ils restent des actions minimales."
            if answer == "s":
                return "skip", None
            if answer == "q":
                return "quit", None
