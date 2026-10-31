#!/usr/bin/env python3
"""Génère les vidéos pour les plans d'une scène.

Usage: python scripts/generate_videos.py <project> <scene>

Étapes par plan :
1. Utilise le shot préprod comme start frame (source de vérité)
2. Génère une end frame (action qui progresse)
3. Upload les deux sur tmpfiles (48h)
4. Appelle wan-fast avec les 2 images + prompt
5. Sauvegarde dans production/video/<scene>/shot_XX.mp4
"""

import json, requests, sys, time
from pathlib import Path
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.utils import upload_to_tmpfiles
from pipeline import slugify

PROJECTS_DIR = Path(__file__).resolve().parent.parent / "projets"
SEED = 42


def read_key():
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("POLLINATION_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


def build_end_prompt(shot, scene_data, project_slug):
    """Construit le prompt pour l'end frame : action qui a progressé."""
    desc = shot.get("description", "")
    intention = shot.get("intention", "")
    prompt = f"END FRAME: {desc}. The action has progressed. {intention}"

    char_details = scene_data.get("character_details", {})
    for char_name, details in char_details.items():
        st = (desc + " " + shot.get("prompt_image", "")).lower().replace("\u2011", "-")
        cn = char_name.lower().replace("\u2011", "-")
        first = cn.split()[0]
        if first not in st:
            continue
        clothing = details.get("clothing", "")
        attitude = details.get("attitude", "")
        if clothing or attitude:
            prompt = f"{char_name}: {clothing}. {attitude}. {prompt}"

    # Ambiance
    style_path = Path(__file__).resolve().parent.parent / "projets" / project_slug / "bible" / "ambiance" / "style-guide.yaml"
    if style_path.exists():
        import yaml
        sd = yaml.safe_load(style_path.read_text())
        ver = sd.get("versions", {}).get(sd.get("active_version", "v1"), {})
        ambiance = ver.get("prompt_image", "")
        if ambiance:
            prompt = f"{prompt}. {ambiance}"
    return prompt


def generate_scene_videos(project, scene):
    KEY = read_key()
    if not KEY:
        print("ERR: Clé API introuvable")
        return

    project_dir = PROJECTS_DIR / project
    sb_path = project_dir / "preprod" / "storyboard" / f"scene_{scene:02d}" / "storyboard.json"
    parsed_path = project_dir / "script" / "parsed" / f"scene_{scene:02d}.yaml"
    if not sb_path.exists() or not parsed_path.exists():
        print(f"ERR: storyboard.json ou scene_{scene:02d}.yaml introuvable")
        return

    import yaml
    scene_data = yaml.safe_load(parsed_path.read_text())
    shots = json.loads(sb_path.read_text())
    storyboard_dir = project_dir / "preprod" / "storyboard" / f"scene_{scene:02d}"
    video_dir = project_dir / "production" / "video" / f"scene_{scene:02d}"
    video_dir.mkdir(parents=True, exist_ok=True)

    # Upload ref sheets pour l'end frame (refs du plan ou toutes)
    bible = project_dir / "bible"
    for shot in shots:
        num = shot["number"]
        # Ne pas regénérer si la vidéo existe déjà
        existing = video_dir / f"shot_{num:02d}.mp4"
        if existing.exists() and existing.stat().st_size > 5000:
            print(f"Shot {num}: ✅ déjà existant, skip")
            continue

        shot_refs = shot.get("refs", [])
        if not shot_refs:
            # Fallback: persos de la scène
            for c in scene_data.get("characters", []):
                shot_refs.append(slugify(c))
            # Lieu
            loc = scene_data.get("location", "").lower()
            known = {
                "boutique de fleurs": "boutique-de-fleurs",
                "bureau d'adèle": "bureau-adele",
                "banque bnp": "bureau-bnp",
                "rue": "rue-banlieue-1985",
                "terrasse de café": "terrasse-cafe",
                "restaurant": "restaurant-chic",
            }
            for k, v in known.items():
                if k in loc:
                    shot_refs.append(v)
                    break

        # Upload refs pour ce plan
        paths = []
        for slug in shot_refs[:4]:
            for etype in ["personnage", "objet", "lieu"]:
                p = bible / etype / slug / "sheet_v1.png"
                if p.exists():
                    paths.append(str(p))
                    break

        end_prompt = build_end_prompt(shot, scene_data, project)

        # Start frame = shot préprod (source de vérité)
        start_path = storyboard_dir / f"shot_{num:02d}.png"
        if not start_path.exists():
            print(f"Shot {num}: pas de shot préprod, skipping")
            continue

        print(f"Shot {num}: upload start frame + refs...", flush=True)
        with ThreadPoolExecutor(4) as pool:
            start_url = upload_to_tmpfiles(str(start_path), expire=172800)
            ref_urls = list(pool.map(lambda p: upload_to_tmpfiles(p, expire=172800), paths))

        # End frame
        print(f"  End frame...", flush=True)
        end_params = f"model=gptimage&width=512&height=512&seed={SEED + num * 2 + 1}&key={KEY}&nologo=true"
        if ref_urls:
            end_params += f"&image={quote('|'.join(ref_urls))}"
        resp = requests.get(
            f"https://gen.pollinations.ai/image/{quote(end_prompt)}?{end_params}", timeout=180)
        if resp.status_code != 200:
            print(f"  ❌ End frame échouée: {resp.status_code}")
            continue
        end_path = video_dir / f"shot_{num:02d}_end.png"
        end_path.write_bytes(resp.content)
        time.sleep(1)

        # Upload end frame
        end_url = upload_to_tmpfiles(str(end_path), expire=172800)
        vrefs = quote(f"{start_url}|{end_url}")

        # Vidéo
        duration = shot.get("duration_seconds", 5)
        vid_prompt = f"{shot.get('description', '')[:200]}. Cinematic, film grain."
        models = ["wan-fast", "grok-video-pro"]

        for model in models:
            print(f"  Vidéo {num} ({model}, {duration}s)...", flush=True)
            resp = requests.get(
                f"https://gen.pollinations.ai/video/{quote(vid_prompt)}"
                f"?model={model}&duration={duration}&key={KEY}&image={vrefs}&seed={SEED}",
                timeout=300)
            vpath = video_dir / f"shot_{num:02d}.mp4"
            if resp.status_code == 200 and len(resp.content) > 5000:
                vpath.write_bytes(resp.content)
                print(f"  ✅ {len(resp.content)} bytes")
                shot["video_path"] = str(vpath)
                break
            print(f"  {model} → {resp.status_code} {resp.text[:100]}")
            if resp.status_code == 402:
                continue
            time.sleep(2)

    sb_path.write_text(json.dumps(shots, ensure_ascii=False, indent=2))
    print(f"\n✅ Scène {scene}: vidéos dans {video_dir}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python scripts/generate_videos.py <project> <scene>")
        sys.exit(1)
    generate_scene_videos(sys.argv[1], int(sys.argv[2]))
