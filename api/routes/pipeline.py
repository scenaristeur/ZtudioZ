from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.deps import get_history_manager, get_project_root
from api.ws import emit

logger = logging.getLogger(__name__)
router = APIRouter()


class StepRequest(BaseModel):
    scene_number: int | None = None
    shot_number: int | None = None


def _run_step_background(project: str, step: str, scene_number: int | None, shot_number: int | None = None):
    """Run a pipeline step in a background thread, emitting WS events."""
    try:
        emit(project, {"type": "step_start", "step": step, "scene": scene_number, "shot": shot_number})

        if step == "visualize":
            from pipeline.steps.visualize import VisualizeStep
            root = get_project_root(project)
            sb_dir = root / "preprod" / "storyboard"
            scenes = [{"number": scene_number}] if scene_number else _list_scenes(root)
            step_obj = VisualizeStep()
            result = step_obj.run(project, scenes, sb_dir, shot_number=shot_number)

        elif step == "produce":
            from pipeline.steps.produce import ProduceStep
            root = get_project_root(project)
            sb_dir = root / "preprod" / "storyboard"
            vid_dir = root / "production" / "video"
            scenes = [{"number": scene_number}] if scene_number else _list_scenes(root)
            step_obj = ProduceStep()
            result = step_obj.run(project, scenes, sb_dir, vid_dir)

        elif step == "audio":
            from pipeline.steps.audio_step import AudioStep
            root = get_project_root(project)
            sb_dir = root / "preprod" / "storyboard"
            audio_dir = root / "production" / "audio"
            scenes = [{"number": scene_number}] if scene_number else _list_scenes(root)
            step_obj = AudioStep()
            result = step_obj.run(project, scenes, sb_dir, audio_dir)

        elif step == "montage":
            import re
            from pipeline.services.montage import assemble_scene
            root = get_project_root(project)
            sb_dir = root / "preprod" / "storyboard"
            scenes = [scene_number] if scene_number else [
                int(re.match(r"^scene_(\d+)$", d.name).group(1))
                for d in sb_dir.iterdir()
                if d.is_dir() and re.match(r"^scene_\d+$", d.name)
            ]
            result = {}
            for sn in scenes:
                scene_key = f"scene_{sn:02d}"
                vid_dir = root / "production" / "video" / scene_key
                audio_dir = root / "production" / "audio" / scene_key
                out_dir = root / "postprod" / "montage"
                out_dir.mkdir(parents=True, exist_ok=True)
                out = out_dir / f"{scene_key}.mp4"
                try:
                    assemble_scene(str(vid_dir), str(out))
                    result[scene_key] = str(out)
                except Exception as e:
                    result[scene_key] = f"ERROR: {e}"
        else:
            raise ValueError(f"Unknown step: {step}")

        hm = get_history_manager(project)
        hm.log_run(step=step, inputs={"scene": scene_number}, outputs=result or {})

        emit(project, {"type": "step_complete", "step": step, "scene": scene_number, "result": str(result)})

    except Exception as e:
        logger.exception("Step %s failed", step)
        emit(project, {"type": "step_error", "step": step, "scene": scene_number, "error": str(e)})


def _list_scenes(root: Path) -> list[dict]:
    import re
    sb_dir = root / "preprod" / "storyboard"
    scenes = []
    if sb_dir.exists():
        for d in sorted(sb_dir.iterdir()):
            m = re.match(r"^scene_(\d+)$", d.name)
            if d.is_dir() and m:
                scenes.append({"number": int(m.group(1))})
    return scenes


@router.post("/{project}/run/{step}")
def run_step(project: str, step: str, req: StepRequest | None = None):
    root = get_project_root(project)
    if not root.exists():
        raise HTTPException(404, f"Project {project} not found")

    scene_number = req.scene_number if req else None
    shot_number = req.shot_number if req else None

    t = threading.Thread(target=_run_step_background, args=(project, step, scene_number, shot_number), daemon=True)
    t.start()

    return {"status": "started", "step": step, "scene": scene_number, "shot": shot_number}


@router.get("/{project}/status")
def get_status(project: str):
    root = get_project_root(project)
    if not root.exists():
        raise HTTPException(404, f"Project {project} not found")

    checkpoint = root / ".checkpoint.json"
    status = {}
    if checkpoint.exists():
        status = json.loads(checkpoint.read_text())

    scenes = _list_scenes(root)
    video_count = 0
    audio_count = 0
    for sn in scenes:
        sk = f"scene_{sn['number']:02d}"
        vid = root / "production" / "video" / sk
        aud = root / "production" / "audio" / sk
        if vid.exists():
            video_count += len(list(vid.glob("*.mp4")))
        if aud.exists():
            audio_count += len(list(aud.glob("*.mp3")))

    hm = get_history_manager(project)
    summary = hm.summary()

    return {
        "checkpoint": status,
        "scenes": scenes,
        "video_count": video_count,
        "audio_count": audio_count,
        "history": summary,
    }
