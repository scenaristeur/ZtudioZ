#!/usr/bin/env python3
"""Génère des transitions vidéo fluides (keyframes Agnes) entre des plans storyboard.

Usage :
  python keyframes_flow.py <project> <scene> [options]

Exemples :
  # Transition directe shot_01 → shot_03 de la scène 06 (2 images, serveur local)
  python keyframes_flow.py 50eme_rose 6

  # Idem avec 2 images intermédiaires créées automatiquement (3+ refs → apihub direct)
  python keyframes_flow.py 50eme_rose 6 --intermediates 2 --dev

  # Images personnalisées
  python keyframes_flow.py 50eme_rose 6 --start a.png --end b.png --prompt "..."

Options :
  --start PATH         Image de départ (défaut : premier shot de la scène)
  --end PATH           Image de fin (défaut : dernier shot de la scène)
  --intermediates N    Nombre d'images intermédiaires à générer (défaut : 0)
  --prompt TEXT        Prompt de transition vidéo (défaut : template scène)
  --seed INT           Seed de la vidéo
  --duration INT       Durée en secondes (défaut : 5)
  --dev                Résolution réduite (dev)
  --only-video         Ne pas créer les intermédiaires, réutiliser l'existant
  --regen-intermediates  Recréer les images intermédiaires même si présentes
  --force              Régénère la vidéo malgré le cache
  --list               Liste les keyframes existants du projet et sort
"""

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent.parent))

from pipeline.config import get_config
from pipeline.services.image import ImageService
from pipeline.services.video import VideoService

PROJECTS_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "projets"


def scene_shot_dir(project: str, scene: int) -> Path:
    return PROJECTS_DIR / project / "preprod" / "storyboard" / f"scene_{scene:02d}"


def keyframes_dir(project: str, scene: int) -> Path:
    return PROJECTS_DIR / project / "preprod" / "keyframes" / f"scene_{scene:02d}"


def default_shots(project: str, scene: int):
    """Détermine start/end par défaut : premier et dernier shot de la scène."""
    shot_dir = scene_shot_dir(project, scene)
    if not shot_dir.exists():
        raise SystemExit(f"ERR: dossier storyboard introuvable : {shot_dir}")
    shots = sorted(shot_dir.glob("shot_*.png"))
    if not shots:
        raise SystemExit(f"ERR: aucun shot_*.png dans {shot_dir}")
    return shots[0], shots[-1]


def list_keyframes(project: str) -> None:
    base = PROJECTS_DIR / project / "preprod" / "keyframes"
    if not base.exists():
        print(f"Aucun keyframe pour le projet {project}")
        return
    for manifest in sorted(base.glob("*/keyframes_manifest.json")):
        data = json.loads(manifest.read_text())
        print(f"scene_{data['scene']:02d}  video={data.get('video') or '-'}  "
              f"refs={len(data['refs'])}  dur={data.get('duration', '-')}s")


def copy_ref(target_dir: Path, name: str, source: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / name
    if not out.exists() or out.stat().st_size != source.stat().st_size:
        shutil.copyfile(source, out)
    return out


def create_intermediates(start: Path, end: Path, n: int, target_dir: Path,
                         regen: bool, dev: bool) -> list[Path]:
    """Crée n images intermédiaires entre start et end via ImageService.compose."""
    image_cfg = dict(get_config()["services"]["image"]["providers"].get("agnes", {}))
    image_cfg["dev"] = dev
    svc = ImageService(config=image_cfg)
    out: list[Path] = []
    total = n + 1
    for k in range(1, n + 1):
        dest = target_dir / f"kf_{k}_inter.png"
        if dest.exists() and not regen:
            print(f"  intermédiaire présent (cache) : {dest}")
            out.append(dest)
            continue
        ratio = k / total
        prompt = (
            f"Create the intermediate frame {k}/{total} (blend weight {ratio:.2f} toward the "
            f"end image) between these two frames of the same scene. Keep the same characters, "
            f"same location, same cinematic style and lighting. The result must read as a "
            f"natural in-between state, composited coherently from both references."
        )
        if dest.exists() and regen:
            dest.unlink()
        print(f"  création intermédiaire {k}/{n} → {dest}")
        path = svc.compose(
            prompt=prompt,
            ref_images=[str(start), str(end)],
            output_dir=target_dir,
            filename=dest.name,
        )
        out.append(Path(path))
    return out


def run(project: str, scene: int, args) -> None:
    key_dir = keyframes_dir(project, scene)
    key_dir.mkdir(parents=True, exist_ok=True)

    start_src = Path(args.start) if args.start else None
    end_src = Path(args.end) if args.end else None
    if not start_src or not end_src:
        default_start, default_end = default_shots(project, scene)
        start_src = start_src or default_start
        end_src = end_src or default_end
    for p in (start_src, end_src):
        if not p.exists():
            raise SystemExit(f"ERR: image introuvable : {p}")

    start = copy_ref(key_dir, "kf_00_start.png", start_src)
    end = copy_ref(key_dir, f"kf_{args.intermediates + 1}_end.png", end_src)

    refs = [start]
    if args.only_video:
        inters = sorted(key_dir.glob("kf_*_inter.png"))
        if inters:
            print(f"réutilisation de {len(inters)} intermédiaires existants")
            refs += inters
    elif args.intermediates:
        inters = create_intermediates(
            start, end, args.intermediates, key_dir,
            regen=args.regen_intermediates, dev=args.dev,
        )
        refs += inters
    refs.append(end)

    if args.prompt:
        prompt = args.prompt
    else:
        prompt = (
            "Smooth cinematic transition between these keyframes of the same scene, "
            "same characters and location, subtle camera move and gentle action, "
            "keep style and composition consistent, no identity morphing."
        )

    video_cfg = dict(get_config()["services"]["video"]["providers"].get("agnes", {}))
    video_cfg["dev"] = args.dev
    n_refs = len(refs)
    if n_refs > 2:
        video_cfg["direct"] = True
        provider = "apihub (direct)"
    else:
        provider = "serveur local :8765"
    print(f"{n_refs} keyframes → {provider}")

    fname = f"transition_{'_'.join(p.stem for p in refs)}.mp4"
    if args.force:
        fname = fname.replace(".mp4", f"_{int(time.time())}.mp4")

    print(f"génération vidéo ({n_refs} refs, dur={args.duration}s)...")
    path = VideoService(config=video_cfg).generate(
        prompt=prompt,
        ref_images=[str(r) for r in refs],
        seed=args.seed,
        duration=args.duration,
        output_dir=key_dir,
        filename=fname,
    )
    print(f"vidéo : {path}")

    manifest = {
        "scene": scene,
        "start": str(start),
        "end": str(end),
        "intermediates": [str(p) for p in refs[1:-1]],
        "refs": [str(p) for p in refs],
        "video": str(path),
        "prompt": prompt,
        "duration": args.duration,
        "seed": args.seed,
        "provider": provider,
        "dev": args.dev,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (key_dir / "keyframes_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False)
    )
    print(f"manifest : {key_dir / 'keyframes_manifest.json'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Transitions keyframes Agnes entre plans storyboard")
    parser.add_argument("project", help="slug du projet (ex: 50eme_rose)")
    parser.add_argument("scene", type=int, help="numéro de scène (ex: 6)")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--intermediates", type=int, default=0)
    parser.add_argument("--prompt")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--duration", type=int, default=5)
    parser.add_argument("--dev", action="store_true")
    parser.add_argument("--only-video", action="store_true")
    parser.add_argument("--regen-intermediates", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list:
        list_keyframes(args.project)
        return

    run(args.project, args.scene, args)


if __name__ == "__main__":
    main()
