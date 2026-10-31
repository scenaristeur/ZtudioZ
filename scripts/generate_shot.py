#!/usr/bin/env python3
"""Génère un plan storyboard pour une scène/plan donnés.

Usage :
  python scripts/generate_shot.py <project> <scene> <shot>
  python scripts/generate_shot.py 50eme_rose 3 3   # Shot 3 de la Scène 3

Lit script/parsed/scene_XX.yaml pour les détails personnages (vêtements, attitude).
Vérifie si les sheets ref existent, les crée si besoin via generate_sheets.
Génère le plan via gen.pollinations.ai/image/ avec les refs en image param.
"""

import json, os, requests, sys, time, yaml
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import slugify
from pipeline.utils import upload_to_tmpfiles
from pipeline.services.prompt_builder import PromptBuilder

PROJECTS_DIR = Path(__file__).resolve().parent.parent / "projets"


def read_key():
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("POLLINATION_API_KEY="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("POLLINATION_API_KEY", "")


def get_scene_data(project, scene):
    """Lit le YAML parsé de la scène et le storyboard.json."""
    parsed = PROJECTS_DIR / project / "script" / "parsed" / f"scene_{scene:02d}.yaml"
    if not parsed.exists():
        print(f"ERR: {parsed} introuvable")
        return None, None

    data = yaml.safe_load(parsed.read_text())

    sb_path = PROJECTS_DIR / project / "preprod" / "storyboard" / f"scene_{scene:02d}" / "storyboard.json"
    if not sb_path.exists():
        print(f"ERR: {sb_path} introuvable")
        return data, None

    shots = json.loads(sb_path.read_text())
    return data, shots


def ensure_sheets(project, scene_data):
    """Vérifie que les sheets ref existent, les génère si besoin."""
    bible = PROJECTS_DIR / project / "bible"
    slugs_to_check = set()
    for char_name in scene_data.get("characters", []):
        slugs_to_check.add(slugify(char_name))
    # Ajouter le lieu (utiliser le slug direct depuis le lieu connu)
    known_locations = {
        "int. boutique de fleurs": "boutique-de-fleurs",
        "boutique de fleurs": "boutique-de-fleurs",
        "int. maison d'édition — bureau d'adèle": "bureau-adele",
        "int. banque bnp — bureau de madame moreau": "bureau-bnp",
        "int. maison d'édition": "bureau-adele",
    }
    loc_raw = scene_data.get("location", "").lower().strip()
    for key, slug in known_locations.items():
        if key in loc_raw:
            slugs_to_check.add(slug)
            break
    # Ajouter les objets du contrat/carte
    for obj in ["contrat", "carte-de-visite", "enveloppe-bnp"]:
        if (bible / "objet" / obj / "fiche.yaml").exists():
            slugs_to_check.add(obj)

    missing = []
    for slug in slugs_to_check:
        found = False
        for etype in ["personnage", "objet", "lieu"]:
            if (bible / etype / slug / "sheet_v1.png").exists():
                found = True
                break
        if not found:
            missing.append(slug)

    if missing:
        print(f"Sheets manquantes : {missing}")
        # Appeler le script de génération des sheets si besoin
        for slug in missing:
            for etype in ["personnage", "objet", "lieu"]:
                fiche = bible / etype / slug / "fiche.yaml"
                if fiche.exists():
                    print(f"  Génération sheet pour {slug}...")
                    from pipeline.services.image import ImageService
                    from pipeline.services.prompt_builder import PromptBuilder
                    pb = PromptBuilder(bible)
                    entity_data = yaml.safe_load(fiche.read_text())
                    ver = entity_data["versions"][entity_data["active_version"]]
                    prompt = pb.build(
                        {"personnage": "character_sheet", "lieu": "location_sheet", "objet": "object_sheet"}.get(etype, "character_sheet"),
                        name=entity_data["name"], type=entity_data["type"],
                        attributs=ver.get("attributs", []), palette=ver.get("palette", []),
                        accessoires=ver.get("accessoires", []),
                        personnalite=ver.get("personnalite", "") or ver.get("atmosphere", ""),
                        fallback=ver.get("prompt_image", f"portrait de {entity_data['name']}"),
                    )
                    svc = ImageService()
                    svc.generate(prompt=prompt, seed=ver.get("seed", 42), model="gpt-image-2",
                                 width=768, height=768, output_dir=str(fiche.parent), filename="sheet_v1.png")
                    break

    return list(slugs_to_check)


def upload_refs(project, slugs):
    """Upload les sheets des slugs donnés et retourne leur URL combinée."""
    bible = PROJECTS_DIR / project / "bible"
    paths = []
    for slug in slugs:
        for etype in ["personnage", "objet", "lieu"]:
            p = bible / etype / slug / "sheet_v1.png"
            if p.exists():
                paths.append(str(p))
                break
    if not paths:
        return ""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(8) as pool:
        urls = list(pool.map(lambda p: upload_to_tmpfiles(p, expire=172800), paths))
    return "|".join(urls)


def build_prompt(shot, scene_data, project):
    """Construit le prompt final pour le plan avec les détails personnages
    et l'ambiance du film. Ne mentionne que les personnages présents dans le plan."""
    prompt = shot.get("prompt_image", shot.get("description", ""))
    shot_text = (shot.get("description", "") + " " + prompt).lower()
    char_details = scene_data.get("character_details", {})

    detail_parts = []
    for char_name, details in char_details.items():
        # Normaliser tous les tirets (insécable, em, etc.) en tiret standard
        cl = char_name.lower().replace("\u2011", "-").replace("\u2014", "-")
        st = shot_text.replace("\u2011", "-").replace("\u2014", "-")
        first_name = cl.split()[0]
        if first_name not in st and cl not in st:
            continue
        clothing = details.get("clothing", "")
        attitude = details.get("attitude", "")
        if clothing or attitude:
            detail_parts.append(f"{char_name}: {clothing}. attitude: {attitude}")

    if detail_parts:
        details_text = ". ".join(detail_parts)
        prompt = f"{details_text}. {prompt}"

    # Injecter l'ambiance/style-guide du film
    style_path = Path(__file__).resolve().parent.parent / "projets" / project / "bible" / "ambiance" / "style-guide.yaml"
    if style_path.exists():
        import yaml
        style_data = yaml.safe_load(style_path.read_text())
        ver = style_data.get("versions", {}).get(style_data.get("active_version", "v1"), {})
        ambiance = ver.get("prompt_image", "")
        if ambiance:
            prompt = f"{prompt}. {ambiance}"

    return prompt


def generate_shot(project, scene, shot_num):
    KEY = read_key()
    if not KEY:
        print("ERR: Clé API introuvable")
        return

    scene_data, shots = get_scene_data(project, scene)
    if not scene_data or not shots:
        return

    shot = next((s for s in shots if s["number"] == shot_num), None)
    if not shot:
        print(f"ERR: Shot {shot_num} introuvable dans le storyboard")
        return

    out_dir = PROJECTS_DIR / project / "preprod" / "storyboard" / f"scene_{scene:02d}"
    out_path = out_dir / f"shot_{shot_num:02d}.png"

    # Versionner l'ancien fichier s'il existe
    if out_path.exists():
        v = 1
        while (out_dir / f"shot_{shot_num:02d}_v{v}.png").exists():
            v += 1
        old_name = out_dir / f"shot_{shot_num:02d}_v{v}.png"
        out_path.rename(old_name)
        print(f"  ↪ Ancien versionné → {old_name.name}")

    # 1. Utiliser les refs du plan (max 4) ou fallback toutes les sheets
    shot_refs = shot.get("refs", [])
    if shot_refs:
        print(f"Refs du plan: {shot_refs}")
        slugs = shot_refs
    else:
        print("Vérification des sheets (fallback)...")
        slugs = ensure_sheets(project, scene_data)

    # 2. Upload les refs
    print("Upload des refs...")
    refs = upload_refs(project, slugs)
    encoded_refs = quote(refs) if refs else ""

    # 3. Construire le prompt
    print("Construction du prompt...")
    prompt = build_prompt(shot, scene_data, project)

    # 4. Générer avec fallback
    models_to_try = ["gpt-image-2", "gptimage"]
    encoded = quote(prompt)
    url = f"https://gen.pollinations.ai/image/{encoded}"

    last_err = None
    for model in models_to_try:
        params = f"model={model}&width=512&height=512&seed={shot_num}&key={KEY}&nologo=true"
        if encoded_refs:
            params += f"&image={encoded_refs}"

        print(f"Génération shot {shot_num} ({model})...", flush=True)
        resp = requests.get(url + "?" + params, timeout=180)
        if resp.status_code == 200:
            out_path.write_bytes(resp.content)
            print(f"✓ {out_path} ({len(resp.content)} bytes) [{model}]")
            break
        last_err = resp.status_code
        print(f"  {model} → {resp.status_code} {resp.text[:100]}")
        if resp.status_code == 402:
            continue
        resp.raise_for_status()
    else:
        raise Exception(f"Tous les modèles ont échoué: {last_err}")

    # 5. Mettre à jour le storyboard.json
    shot["image_path"] = str(out_path)
    (out_dir / "storyboard.json").write_text(json.dumps(shots, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python scripts/generate_shot.py <project> <scene> <shot>")
        sys.exit(1)
    generate_shot(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]))
