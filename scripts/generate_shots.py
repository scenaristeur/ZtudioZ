#!/usr/bin/env python3
"""Génère les shots storyboard pour une scène.

Usage : python scripts/generate_shots.py <project_slug> <scene_number>
Sauvegarde : preprod/storyboard/scene_XX/shot_XX.png

Lit storyboard.json pour les prompts, upload les sheets de la scène en ref,
appelle gen.pollinations.ai/image/ avec gpt-image-2, sauvegarde.
"""

import json, os, requests, sys, time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.utils import upload_to_tmpfiles
from concurrent.futures import ThreadPoolExecutor

PROJECTS_DIR = Path(__file__).resolve().parent.parent / "projets"


def read_api_key():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("POLLINATION_API_KEY="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("POLLINATION_API_KEY", "")


def get_scene_slugs(project_slug, scene_number):
    """Retourne les slugs des entités pertinentes pour une scène."""
    parsed_path = PROJECTS_DIR / project_slug / "script" / "parsed" / f"scene_{scene_number:02d}.yaml"
    if not parsed_path.exists():
        return []
    import yaml
    data = yaml.safe_load(parsed_path.read_text())
    chars = [c.lower().replace(" ", "-") for c in data.get("characters", [])]
    # Ajouter les slugs dérivés des noms
    from pipeline import slugify
    return [slugify(c) for c in data.get("characters", [])]


def find_sheet(slug):
    for base in ["personnage", "lieu", "objet"]:
        p = PROJECTS_DIR / project_slug / "bible" / base / slug / "sheet_v1.png"
        if p.exists():
            return str(p)
    return None


def generate_shots(project_slug, scene_number):
    KEY = read_api_key()
    if not KEY:
        print("ERR: Clé API introuvable dans .env")
        return

    scene_key = f"scene_{scene_number:02d}"
    out_dir = PROJECTS_DIR / project_slug / "preprod" / "storyboard" / scene_key
    sb_path = out_dir / "storyboard.json"

    if not sb_path.exists():
        print(f"ERR: {sb_path} introuvable")
        return

    shots = json.loads(sb_path.read_text())

    # Upload uniquement les sheets pertinentes pour la scène
    slugs = [__import__("pipeline").slugify(c) for c in sum(
        ([c] for c in __import__("yaml").safe_load(
            (PROJECTS_DIR / project_slug / "script" / "parsed" / f"scene_{scene_number:02d}.yaml"
             ).read_text()).get("characters", [])), []
    )]
    # Fallback: upload tout si parsing échoue
    if not slugs:
        for etype in ["personnage", "objet", "lieu"]:
            edir = PROJECTS_DIR / project_slug / "bible" / etype
            if edir.exists():
                slugs.extend(d.name for d in sorted(edir.iterdir()) if (d / "sheet_v1.png").exists())
        slugs = list(set(slugs))

    sheet_paths = []
    for slug in slugs:
        for base in ["personnage", "lieu", "objet"]:
            p = PROJECTS_DIR / project_slug / "bible" / base / slug / "sheet_v1.png"
            if p.exists():
                sheet_paths.append(str(p))
                break

    print(f"Upload de {len(sheet_paths)} sheets...", flush=True)
    with ThreadPoolExecutor(8) as pool:
        ref_urls = list(pool.map(lambda p: upload_to_tmpfiles(p, expire=172800), sheet_paths))

    all_refs = quote("|".join(ref_urls))

    for shot in shots:
        num = shot["number"]
        prompt = shot["prompt_image"]
        encoded = quote(prompt)
        url = f"https://gen.pollinations.ai/image/{encoded}?model=gpt-image-2&width=512&height=512&seed={num}&key={KEY}&nologo=true&image={all_refs}"

        out_path = out_dir / f"shot_{num:02d}.png"
        print(f"Shot {num}...", end=" ", flush=True)
        resp = requests.get(url, timeout=180)
        out_path.write_bytes(resp.content)
        print(f"{len(resp.content)}b")
        shot["image_path"] = str(out_path)
        time.sleep(1)

    sb_path.write_text(json.dumps(shots, ensure_ascii=False, indent=2))
    print(f"\n✓ {len(shots)} shots → {out_dir}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python scripts/generate_shots.py <project> <scene>")
        sys.exit(1)
    generate_shots(sys.argv[1], int(sys.argv[2]))
