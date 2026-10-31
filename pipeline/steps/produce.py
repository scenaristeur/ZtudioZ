from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml

from pipeline.config import get_config
from pipeline.scene_constraints import SceneConstraints
from pipeline.services.video import VideoService

logger = logging.getLogger(__name__)

PROJECTS_DIR = Path(__file__).resolve().parent.parent.parent / "projets"


def _build_motion_prompt(shot: dict, scene_data: dict, project: str) -> str:
    """Construire le prompt de mouvement pour l'I2V depuis le frame de départ.

    Décrit l'action à animer à partir du plan storyboard (description + caméra),
    injecte les vêtements/attitudes des personnages présents, le verrou de scène
    (contraintes déclaratives) et l'ambiance.
    """
    desc = shot.get("description", "")
    camera = shot.get("camera", "")
    prompt = f"{desc}. Caméra : {camera}. Anime la scène avec un mouvement fluide et naturel, cohérent avec ce plan."

    char_details = scene_data.get("character_details", {})
    shot_text = (desc + " " + shot.get("prompt_image", "")).lower().replace("\u2011", "-")
    for char_name, details in char_details.items():
        cl = char_name.lower().replace("\u2011", "-")
        first = cl.split()[0]
        if first not in shot_text and cl not in shot_text:
            continue
        clothing = details.get("clothing", "")
        attitude = details.get("attitude", "")
        if clothing or attitude:
            prompt = f"{char_name}: {clothing}. attitude: {attitude}. {prompt}"

    # Verrou de scène : mêmes contraintes déclaratives que la génération des plans.
    # Sans `bible/scene/scene_NN.yaml`, chaîne vide et comportement inchangé.
    scene_no = scene_data.get("number")
    if scene_no is not None:
        anchor = SceneConstraints(PROJECTS_DIR / project).anchor_text(int(scene_no), shot_text)
        if anchor:
            prompt = f"{anchor} {prompt}"

    style_path = PROJECTS_DIR / project / "bible" / "ambiance" / "style-guide.yaml"
    if style_path.exists():
        sd = yaml.safe_load(style_path.read_text())
        ver = sd.get("versions", {}).get(sd.get("active_version", "v1"), {})
        ambiance = ver.get("prompt_image", "")
        if ambiance:
            prompt = f"{prompt}. {ambiance}"

    return prompt


def _double_anchor_enabled() -> bool:
    """Double-ancre activée ? Défaut : oui (cinéma borné des deux côtés).

    Réglable par projet via `production.produce.double_anchor`. Le i2v simple dérive en
    milieu de plan ; la double-ancre (keyframes 2 refs) borne l'identité. Le repli sur
    1 ref reste automatique quand le plan suivant n'existe pas.
    """
    cfg = get_config().get("production", {}).get("produce", {})
    return bool(cfg.get("double_anchor", True))


def _mark_shot_progress(project: str, scene_key: str, shot_number: int, video_path: str) -> None:
    """Persiste l'avancement d'une vidéo dans `scene_state.json` (P3.4)."""
    try:
        from pipeline.state import SceneState
        state = SceneState(PROJECTS_DIR / project)
        state.mark_shot(scene_key, "produce", shot_number, note=f"video {video_path}")
    except Exception as exc:
        logger.warning("État par plan non enregistré (shot %d) : %s", shot_number, exc)


class ProduceStep:
    """Générer une vidéo par plan à partir du shot préprod (source de vérité).

    Pour chaque plan :
    1. Le shot préprod (shot_N.png) sert de frame de départ (I2V).
    2. VideoService (provider actif config) anime l'action décrite par le plan.
    3. Durée issue du storyboard (arrondie aux durées valides du provider).
    4. Skip des vidéos existantes >5KB (sauvegarde).
    """

    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    def run(self, project: str, scenes: list[dict], storyboard_dir: Path, video_dir: Path) -> dict:
        results = {}
        svc = VideoService()

        for scene in scenes:
            scene_num = scene.get("number", 0)
            scene_key = f"scene_{scene_num:02d}"

            sb_path = storyboard_dir / scene_key / "storyboard.json"
            parsed_path = PROJECTS_DIR / project / "script" / "parsed" / f"scene_{scene_num:02d}.yaml"

            if not sb_path.exists() or not parsed_path.exists():
                logger.warning("Missing storyboard or parsed data for scene %d", scene_num)
                continue

            scene_data = yaml.safe_load(parsed_path.read_text())
            shots = json.loads(sb_path.read_text())

            storyboard_scene_dir = storyboard_dir / scene_key
            scene_video_dir = video_dir / scene_key
            scene_video_dir.mkdir(parents=True, exist_ok=True)

            logger.info("Scene %d: producing %d videos", scene_num, len(shots))

            for shot in shots:
                num = shot["number"]
                out_path = scene_video_dir / f"shot_{num:02d}.mp4"
                if out_path.exists() and out_path.stat().st_size > 5000:
                    logger.info("Shot %d: already exists, skipping", num)
                    continue

                start_path = storyboard_scene_dir / f"shot_{num:02d}.png"
                if not start_path.exists():
                    logger.warning("Shot %d: no preprod image, skipping", num)
                    continue

                raw_duration = shot.get("duration_seconds", 5)
                duration = 5 if raw_duration <= 5 else (10 if raw_duration <= 10 else 15)
                prompt = _build_motion_prompt(shot, scene_data, project)

                # Double-ancre (keyframes 2 refs) : start = shot courant, end = shot suivant.
                # L'identité est bornée des deux côtés et le plan ne peut pas dériver en
                # milieu de mouvement. Sans shot suivant, repli en i2v simple (1 ref).
                refs: list[str] | None = None
                mode = "i2v"
                if _double_anchor_enabled():
                    end_path = storyboard_scene_dir / f"shot_{num + 1:02d}.png"
                    if end_path.exists():
                        refs = [str(start_path), str(end_path)]
                        mode = "keyframes-2refs"

                try:
                    path = svc.generate(
                        prompt=prompt,
                        ref_images=refs,
                        ref_image=str(start_path) if refs is None else None,
                        seed=num,
                        duration=duration,
                        output_dir=str(scene_video_dir),
                        filename=out_path.name,
                    )
                    shot["video_path"] = str(path)
                    shot["video_mode"] = mode
                    logger.info("Shot %d: %s (%s, %d bytes)", num, path, mode, out_path.stat().st_size)
                    _mark_shot_progress(project, scene_key, num, path)
                except Exception as e:
                    logger.error("Shot %d video failed: %s", num, e)

            sb_path.write_text(json.dumps(shots, ensure_ascii=False, indent=2))
            results[scene_key] = {"shots": shots, "count": len(shots)}

        return results
