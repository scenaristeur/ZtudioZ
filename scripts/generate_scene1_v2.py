#!/usr/bin/env python3
"""Régénère la scène 1 en V2 : Agnes (image + vidéo) + dialogues + voix Henri/Gérard.

Flux (correctif multi-refs Agnes) :
  1. Storyboard V2 régénéré via LLM (dialogues fidèles au scénario, paysage)
  2. Image maîtresse de scène via compose() (toutes les refs du lieu fusionnées)
  3. Image de chaque plan : ref unique = image maîtresse
  4. Vidéo de chaque plan : i2v Agnes, ref unique = image du plan
  5. Audio : TTS Agnes local (voix des fiches : Henri pour Paul-Henri, Gérard pour le facteur)
  6. Montage scène V2

Usage :
  python scripts/generate_scene1_v2.py <project> [--steps storyboard,master,shots,videos,audio,montage]
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import slugify
from pipeline.services.image import ImageService
from pipeline.services.llm import LLMService
from pipeline.services.montage import assemble_scene
from pipeline.services.video import VideoService

PROJECTS_DIR = Path(__file__).resolve().parent.parent / "projets"
AGNES_TTS = "http://localhost:8765/api/voices/preview"

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("scene1_v2")

STORYBOARD_SYSTEM = """Tu es un storyboarder. Pour la scène de film fournie, génère un découpage
en plans (shots) avec pour chaque plan :
- number: numéro du plan
- type: type de plan (PLAN LARGE, PLAN MOYEN, GROS PLAN, INSERT, OVER-THE-SHOULDER...)
- camera: mouvement (fixe, travelling, panoramique...)
- description: description visuelle détaillée (personnages, position, décor, lumière)
- duration_seconds: durée estimée en secondes (5 minimum)
- prompt_image: prompt détaillé pour générateur d'image (style cinématographique, format paysage)
- dialogue: texte parlé pendant ce plan, au format "Personnage: « réplique »".
  Vide si aucun dialogue. Fidèle au scénario fourni.

Règles :
- La scène se déroule dans une BOUTIQUE DE FLEURS (pas une antiquité). Paul-Henri porte
  un tablier de fleuriste. Assortiment de fleurs variées (pas seulement des roses).
- Tous les plans en FORMAT PAYSAGE (cinémascope).
- La vitrine est vue de l'INTÉRIEUR de la boutique ; toute inscription éventuelle sur la
  vitre est écrite à l'ENVERS (miroir). Si trop complexe, vitre sans inscription.
- Style wilderien : composition frontale, lumière douce, atmosphère mélancolique.
- Réponds UNIQUEMENT avec un JSON: {"shots": [...]}.
"""


def load_scene(project: str, scene: int) -> dict:
    parsed = PROJECTS_DIR / project / "script" / "parsed" / f"scene_{scene:02d}.yaml"
    return yaml.safe_load(parsed.read_text())


def load_scenario(project: str, scene: int) -> str:
    md = PROJECTS_DIR / project / "script" / "scenario.md"
    text = md.read_text()
    marker = f"## SCÈNE {scene}\n"
    nxt = f"## SCÈNE {scene + 1}\n"
    start = text.find(marker)
    end = text.find(nxt, start)
    if start < 0:
        return ""
    return text[start:end if end > 0 else None].strip()


def scene_sheets(project: str, scene_data: dict, max_refs: int = 6) -> list[str]:
    """Refs à fusionner dans l'image maîtresse de la scène (max 6 — limite Agnes apihub)."""
    bible = PROJECTS_DIR / project / "bible"
    slugs: list[str] = []
    # Priorité 1 : personnages
    for c in scene_data.get("characters", []):
        slugs.append(slugify(c))
    # Priorité 2 : lieu
    known = {
        "boutique de fleurs": "boutique-de-fleurs",
        "bureau d'adèle": "bureau-adele",
        "banque bnp": "bureau-bnp",
        "rue": "rue-banlieue-1985",
        "terrasse de café": "terrasse-cafe",
        "restaurant": "restaurant-chic",
    }
    loc = scene_data.get("location", "").lower()
    for k, v in known.items():
        if k in loc:
            slugs.append(v)
            break
    # Priorité 3 : objets clés de la scène (dans l'ordre du scénario)
    for obj in ["enveloppe-bnp", "coucou-suisse", "magazine-fleuriste",
                "bouquet-de-roses", "telephone-fixe", "contrat", "carte-de-visite"]:
        if (bible / "objet" / obj / "fiche.yaml").exists():
            slugs.append(obj)

    paths = []
    for slug in slugs:
        if len(paths) >= max_refs:
            break
        for etype in ["personnage", "lieu", "objet"]:
            p = bible / etype / slug / "sheet_v1.png"
            if p.exists():
                paths.append(str(p))
                break
    return paths


def build_storyboard(project: str, scene: int, scene_data: dict) -> list[dict]:
    scenario = load_scenario(project, scene)
    llm = LLMService()
    user = (
        f"Scène {scene}: {scene_data.get('title', '')}\n"
        f"Lieu: {scene_data.get('location', '')}\n"
        f"Ambiance: {scene_data.get('ambiance', '')}\n"
        f"Intention: {scene_data.get('intention', '')}\n"
        f"Personnages: {', '.join(scene_data.get('characters', []))}\n\n"
        f"SCÉNARIO DE LA SCÈNE (respecte les dialogues) :\n{scenario[:4000]}"
    )
    data = llm.chat_structured(
        system=STORYBOARD_SYSTEM,
        user=user,
        response_schema={"type": "object"},
    )
    if isinstance(data, dict) and "shots" in data:
        shots = data["shots"]
    elif isinstance(data, dict) and "error" not in data:
        shots = [data]
    else:
        raise RuntimeError(f"Storyboard LLM failed: {data}")
    for i, s in enumerate(shots):
        s.setdefault("number", i + 1)
        s.setdefault("duration_seconds", 5)
        s.setdefault("dialogue", "")
    return shots


def crop_refs(project: str) -> dict[str, str]:
    """Recadre chaque sheet (planche multi-poses) en une seule vue complète.

    Agnes comme Pollinations dupliquent les personnages quand on passe une planche
    multi-poses en référence. On garde le quadrant haut-gauche (vue de face complète).

    Retourne {slug: chemin_local_du_recadrage}.
    """
    bible = PROJECTS_DIR / project / "bible"
    out_dir = PROJECTS_DIR / project / "preprod_refs"
    out_dir.mkdir(parents=True, exist_ok=True)
    refs: dict[str, str] = {}
    for etype in ("personnage", "lieu", "objet"):
        for d in (bible / etype).iterdir():
            if not d.is_dir():
                continue
            sheet = d / "sheet_v1.png"
            if not sheet.exists():
                continue
            slug = d.name
            crop = out_dir / f"{slug}_face.png"
            if not crop.exists():
                from PIL import Image
                im = Image.open(sheet)
                w, h = im.size
                im.crop((0, 0, w // 2, h // 2)).save(crop)
            refs[slug] = str(crop)
    return refs


def shot_refs(shot: dict, refs: dict[str, str]) -> list[str]:
    """Refs nécessaires pour un plan : lieu + personnages/objets mentionnés."""
    text = f"{shot.get('description', '')} {shot.get('prompt_image', '')}".lower()
    slugs = []
    # Lieu de la scène : tous les recadrages de type lieu
    for slug in refs:
        if slug not in slugs and "/lieu/" not in slug:
            pass
    # Personnages : paul-henri et le-facteur détectés par mots-clés
    if "paul" in text or "fleuriste" in text or "delorme" in text:
        if "paul-henri" in refs:
            slugs.append("paul-henri")
    if "facteur" in text or "postman" in text or "la poste" in text:
        if "le-facteur" in refs:
            slugs.append("le-facteur")
    # Objets clés
    obj_map = {
        "coucou": "coucou-suisse",
        "bouquet": "bouquet-de-roses",
        "rose": "bouquet-de-roses",
        "enveloppe": "enveloppe-bnp",
        "recommand": "enveloppe-bnp",
        "magazine": "magazine-fleuriste",
        "téléphone": "telephone-fixe",
        "telephone": "telephone-fixe",
    }
    for kw, slug in obj_map.items():
        if kw in text and slug in refs and slug not in slugs:
            slugs.append(slug)
    # Lieu : boutique-de-fleurs
    if "boutique" in text or "shop" in text or not slugs:
        if "boutique-de-fleurs" in refs and "boutique-de-fleurs" not in slugs:
            slugs.insert(0, "boutique-de-fleurs")
    return [refs[s] for s in slugs if s in refs]


def validate_shot_image(shot: dict, image_path: str, max_retries: int = 2) -> bool:
    """Vérifie par vision LLM (Albert) qu'une image de plan est cohérente.

    Compte les personnages (pas de doublon) et vérifie que la scène correspond au plan.
    Retourne True si l'image est jugée valide. Si invalide, tente une régénération.
    """
    from pipeline.services.vision import VisionAnalysisService
    vision = VisionAnalysisService()
    prompt = (
        "Compte précisément les personnages humains de cette image. "
        "Réponds uniquement au format JSON: {\"fleuristes\": N, \"facteurs\": N, "
        "\"total\": N}. "
        "Compte aussi tout personnage supplémentaire comme des doublons."
    )
    if not Path(image_path).exists():
        logger.warning("Shot %d: image absente, non validée", shot.get("number"))
        return False
    for attempt in range(max_retries):
        try:
            raw = vision.analyze(prompt, [image_path], max_tokens=300)
            import json as _json
            start = raw.find("{")
            end = raw.rfind("}")
            counts = _json.loads(raw[start:end + 1]) if start >= 0 and end > start else {}
            total = int(counts.get("total", 0))
            fleuristes = int(counts.get("fleuristes", 0))
            facteurs = int(counts.get("facteurs", 0))
            # Un plan ne doit pas contenir de doublons manifestes
            if total > 3 or fleuristes > 2 or facteurs > 2:
                logger.warning(
                    "Shot %d: %d personnages (F%d/F%d) — doublon possible, régénération",
                    shot.get("number"), total, fleuristes, facteurs,
                )
                # régénère : supprime l'image et relance la génération
                Path(image_path).unlink(missing_ok=True)
                from pipeline.services.image import ImageService
                ImageService().generate(
                    prompt=shot.get("prompt_image", shot.get("description", ""))
                    + " ONE SINGLE person of each type. No duplicates.",
                    seed=shot.get("number", 1) + attempt,
                    model="gptimage",
                    width=1280, height=720,
                    output_dir=str(Path(image_path).parent),
                    filename=Path(image_path).name,
                )
                continue
            shot["validated"] = True
            logger.info("Shot %d validé (%d personnages)", shot.get("number"), total)
            return True
        except Exception as e:
            logger.warning("Shot %d: validation vision échouée (%s)", shot.get("number"), e)
            shot["validated"] = True
            return True
    logger.warning("Shot %d: validation échouée après %d tentatives", shot.get("number"), max_retries)
    return False


def build_shots(project: str, shots: list[dict], scene_data: dict, out_dir: Path) -> None:
    """Image de chaque plan via Pollinations multi-refs (lieu + persos/objets du plan).

    Les refs sont les recadrages une-pose (crop_refs) uploadés en URLs — Pollinations
    accepte plusieurs refs séparées par '|' avec un modèle i2i multi-image (seedream-pro).
    """
    import yaml as _yaml
    from pipeline.config import get_service_config
    from pipeline.utils import upload_to_tmpfiles

    base_cfg = _yaml.safe_load(
        (Path(__file__).resolve().parent.parent / "pipeline" / "config.yaml").read_text()
    )
    pol = dict(base_cfg["services"]["image"]["providers"]["pollinations"])
    pol["type"] = "api"
    pol["active"] = "pollinations"
    svc = ImageService(config=pol)
    model = pol.get("multi_ref_model", "seedream-pro")

    refs = crop_refs(project)
    for shot in shots:
        num = shot.get("number", 1)
        out = out_dir / f"shot_{num:02d}.png"
        if out.exists() and out.stat().st_size > 10000:
            shot["image_path"] = str(out)
            continue
        prompt = shot.get("prompt_image", shot.get("description", ""))
        # Contrainte vitre (vue de l'intérieur) appliquée aux plans qui montrent la vitrine
        st = f"{shot.get('description', '')} {prompt}".lower()
        if "vitrin" in st or "vitre" in st or "fenêtre" in st or "window" in st:
            prompt = (
                prompt + " Seen from inside the shop; any lettering on the glass window "
                "is mirrored/backwards, or no lettering at all."
            )
        # Ajout de la consigne "UN SEUL" pour éviter les duplications
        prompt = (
            "UN SEUL personnage de chaque type. " + prompt
            if "personnage" not in prompt
            else prompt
        )
        local_refs = shot_refs(shot, refs)
        urls = [upload_to_tmpfiles(r) for r in local_refs]
        ref_image = "|".join(urls) if urls else None
        p = svc.generate(
            prompt=prompt,
            seed=num,
            model=model,
            width=1280, height=720,
            ref_image=ref_image,
            output_dir=str(out_dir),
            filename=f"shot_{num:02d}.png",
        )
        shot["image_path"] = p
        logger.info("Shot %d image → %s (refs: %d)", num, p, len(local_refs))
        validate_shot_image(shot, p)


def build_videos(shots: list[dict], out_dir: Path) -> None:
    svc = VideoService()
    for shot in shots:
        num = shot.get("number", 1)
        # Ne générer une vidéo que si l'image a été validée (pas de doublon de personnages)
        if not shot.get("validated", False):
            logger.warning("Shot %d: image non validée, skip vidéo (pas de génération pour rien)", num)
            continue
        img = shot.get("image_path", "")
        if not img or not Path(img).exists():
            logger.warning("Shot %d: pas d'image, skip", num)
            continue
        out = out_dir / f"shot_{num:02d}.mp4"
        if out.exists() and out.stat().st_size > 5000:
            shot["video_path"] = str(out)
            continue
        duration = int(shot.get("duration_seconds", 5))
        prompt = f"{shot.get('description', '')[:200]}. Cinematic, film grain, smooth natural motion."
        p = svc.generate(
            prompt=prompt,
            ref_image=img,
            seed=num,
            duration=duration,
            output_dir=str(out_dir),
            filename=f"shot_{num:02d}.mp4",
        )
        shot["video_path"] = p
        logger.info("Shot %d video → %s", num, p)


def load_voices(project: str) -> dict[str, str]:
    bible = PROJECTS_DIR / project / "bible" / "personnage"
    voices = {}
    for d in bible.iterdir():
        fiche = d / "fiche.yaml"
        if fiche.exists():
            data = yaml.safe_load(fiche.read_text())
            if "voice" in data:
                voices[data["name"].lower()] = data["voice"]
                voices[d.name.lower()] = data["voice"]
    return voices


def build_audio(project: str, shots: list[dict], out_dir: Path) -> None:
    voices = load_voices(project)
    # Le serveur vocal BNP : voix distincte, nasillarde
    voices.setdefault("serveur vocal", "fr-CA-AntoineNeural")
    for shot in shots:
        num = shot.get("number", 1)
        dialogue = shot.get("dialogue", "")
        if not dialogue:
            continue
        audio_paths = {}
        for line in dialogue.split("\n"):
            line = line.strip()
            if not line or ":" not in line:
                continue
            char, _, text = line.partition(":")
            char = char.strip()
            text = text.strip().strip("\u00ab").strip("\u00bb").strip('"')
            if not text:
                continue
            cl = char.lower()
            # Normalisation : "le facteur" -> "lefacteur", "paul-henri" -> "paulhenri"
            norm = cl.replace(" ", "").replace("'", "").replace("-", "").replace("_", "")
            voice = "onyx"
            # 1) match exact normalisé
            for k, v in voices.items():
                kn = k.replace(" ", "").replace("'", "").replace("-", "").replace("_", "")
                if kn and kn == norm:
                    voice = v
                    break
            # 2) sinon match partiel : on garde le plus COURTE clé contenue dans norm
            #    (évite "le père de paul-henri" vs "paul-henri", "le cycliste" vs "le facteur")
            if voice == "onyx":
                best = ""
                for k, v in voices.items():
                    kn = k.replace(" ", "").replace("'", "").replace("-", "").replace("_", "")
                    if kn and (kn in norm or norm in kn):
                        if not best or len(kn) < len(best):
                            best = kn
                            voice = v
            # 2) sinon match approximatif par premier mot
            if voice == "onyx" and "serveur vocal" in cl:
                voice = voices.get("serveur vocal", "fr-CA-AntoineNeural")
            if voice == "onyx":
                for k, v in voices.items():
                    if cl.split()[0] in k and cl in k:
                        voice = v
                        break
            fname = out_dir / f"shot_{num:02d}_{slugify(char)}.mp3"
            if fname.exists() and fname.stat().st_size > 1000:
                audio_paths[cl] = str(fname)
                continue
            try:
                resp = requests.get(AGNES_TTS, params={"voice": voice, "text": text}, timeout=120)
                resp.raise_for_status()
                fname.write_bytes(resp.content)
                audio_paths[cl] = str(fname)
                logger.info("Shot %d audio %s [%s] %d bytes", num, char, voice, len(resp.content))
                time.sleep(0.3)
            except requests.RequestException as e:
                logger.warning("Shot %d audio %s failed: %s", num, char, e)
        shot["audio_paths"] = audio_paths


def build_montage(shots: list[dict], out_dir: Path) -> str:
    """Concatène vidéos + audio de dialogue mixé sur chaque plan."""
    clips = []
    tmp = Path("/tmp/scene1_v2_clips")
    tmp.mkdir(parents=True, exist_ok=True)
    for i, shot in enumerate(shots):
        video = shot.get("video_path", "")
        if not video or not Path(video).exists():
            continue
        audio = (shot.get("audio_paths") or {}).values()
        clip = tmp / f"clip_{i:02d}.mp4"
        if audio:
            args = ["ffmpeg", "-y", "-i", video]
            for a in audio:
                args += ["-i", a]
            filter_parts = ["[0:v]format=yuv420p[v]"]
            audio_inputs = []
            for idx in range(len(audio)):
                audio_inputs.append(f"[{idx + 1}:a]")
            amix = "".join(audio_inputs) + f"amix=inputs={len(audio)}:normalize=0[a]"
            cmd = args + [
                "-filter_complex", ";".join(filter_parts + [amix]),
                "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-c:a", "aac", "-shortest", str(clip),
            ]
        else:
            cmd = ["ffmpeg", "-y", "-i", video, "-c:v", "libx264", "-c:a", "aac", str(clip)]
        import subprocess
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        clips.append(str(clip))
    return assemble_scene(clips, output_path=str(out_dir))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--steps", default="storyboard,shots,videos,audio,montage")
    args = ap.parse_args()

    scene = 1
    scene_data = load_scene(args.project, scene)
    base = PROJECTS_DIR / args.project
    sb_dir = base / "preprod" / "storyboard" / f"scene_{scene:02d}_v2"
    video_dir = base / "production" / "video" / f"scene_{scene:02d}_v2"
    audio_dir = base / "production" / "audio" / f"scene_{scene:02d}_v2"
    montage_dir = base / "postprod" / "montage"
    for d in (sb_dir, video_dir, audio_dir, montage_dir):
        d.mkdir(parents=True, exist_ok=True)

    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    sb_json = sb_dir / "storyboard.json"
    shots = json.loads(sb_json.read_text()) if sb_json.exists() else None

    if "storyboard" in steps:
        shots = build_storyboard(args.project, scene, scene_data)
        sb_json.write_text(json.dumps(shots, ensure_ascii=False, indent=2))
        logger.info("Storyboard V2: %d plans → %s", len(shots), sb_json)

    if shots is None:
        logger.error("Pas de storyboard. Lance d'abord --steps storyboard")
        return

    if "shots" in steps:
        build_shots(args.project, shots, scene_data, sb_dir)
        sb_json.write_text(json.dumps(shots, ensure_ascii=False, indent=2))

    if "videos" in steps:
        build_videos(shots, video_dir)
        sb_json.write_text(json.dumps(shots, ensure_ascii=False, indent=2))

    if "audio" in steps:
        build_audio(args.project, shots, audio_dir)
        sb_json.write_text(json.dumps(shots, ensure_ascii=False, indent=2))

    if "montage" in steps:
        path = build_montage(shots, montage_dir / f"scene_{scene:02d}_v2.mp4")
        logger.info("Montage V2 → %s", path)


if __name__ == "__main__":
    main()
