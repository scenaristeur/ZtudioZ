from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

from pipeline import slugify
from pipeline.services.image import ImageService
from pipeline.services.prompt_builder import PromptBuilder
from pipeline.scene_constraints import SceneConstraints
from pipeline.utils import upload_to_tmpfiles

from pipeline.config import get_config


def _shot_size() -> tuple[int, int]:
    """Dimensions de génération des plans, depuis la config image active (fallback 512x512)."""
    img_cfg = get_config().get("services", {}).get("image", {})
    prov = img_cfg.get("providers", {}).get(img_cfg.get("active", ""), {})
    if prov.get("dev") and prov.get("dev_width") and prov.get("dev_height"):
        return int(prov["dev_width"]), int(prov["dev_height"])
    width = int(prov.get("width", 512) or 512)
    height = int(prov.get("height", 512) or 512)
    return width, height

logger = logging.getLogger(__name__)

_DEFAULT_CONTINUITY = (
    "CONTINUITY: identical set, identical lighting and identical characters as the "
    "previous shot; keep the same decor, props, wardrobe and screen content."
)


def _continuity_prompt() -> str:
    """Phrase de continuité entre plans, paramétrable par projet dans `config.yaml`.

    Returns:
        str — la phrase à concatener au prompt, ou chaîne vide si désactivée.
    """
    cfg = get_config().get("production", {}).get("visualize", {})
    return cfg.get("continuity_prompt", _DEFAULT_CONTINUITY)

PROJECTS_DIR = Path(__file__).resolve().parent.parent.parent / "projets"


def _location_aliases(project: str, location: str) -> str:
    """Résout le slug bible du lieu d'une scène.

    Les alias sont propres à chaque projet et déclarés dans
    `bible/location_aliases.yaml` (le plus spécifique d'abord). Un fallback global peut
    être fourni via `production.visualize.location_aliases` dans la config. Sans alias,
    on retire le préfixe INT./EXT. puis on slugifie le reste.
    """
    loc_raw = (location or "").lower().strip()
    if not loc_raw:
        return ""

    merged: dict[str, str] = {}
    merged.update(get_config().get("production", {}).get("visualize", {}).get("location_aliases", {}) or {})
    alias_file = PROJECTS_DIR / project / "bible" / "location_aliases.yaml"
    if alias_file.exists():
        project_aliases = yaml.safe_load(alias_file.read_text()) or {}
        merged.update(project_aliases)

    for key, slug in merged.items():
        if str(key).lower().strip() in loc_raw:
            return slug

    stripped = re.sub(r"^\s*(int|ext|int/ext|ext/int)\s*[.—-]?\s*", "", loc_raw).strip()
    return slugify(stripped) if stripped else ""


def _active_prompt(fiche_path: Path) -> str:
    """Retourne le prompt_image de la version active d'une fiche bible."""
    if not fiche_path.exists():
        return ""
    try:
        data = yaml.safe_load(fiche_path.read_text()) or {}
    except yaml.YAMLError:
        return ""
    ver = (data.get("versions") or {}).get(data.get("active_version", "v1")) or {}
    return ver.get("prompt_image", "") or ""


def _get_scene_data(project: str, scene: int) -> tuple[dict | None, list | None]:
    parsed = PROJECTS_DIR / project / "script" / "parsed" / f"scene_{scene:02d}.yaml"
    if not parsed.exists():
        logger.error("Scene YAML not found: %s", parsed)
        return None, None
    data = yaml.safe_load(parsed.read_text())

    sb_path = PROJECTS_DIR / project / "preprod" / "storyboard" / f"scene_{scene:02d}" / "storyboard.json"
    if not sb_path.exists():
        logger.error("Storyboard not found: %s", sb_path)
        return data, None
    shots = json.loads(sb_path.read_text())
    return data, shots


def _ensure_sheets(project: str, scene_data: dict) -> list[str]:
    bible = PROJECTS_DIR / project / "bible"
    slugs_to_check = set()

    for char_name in scene_data.get("characters", []):
        slugs_to_check.add(slugify(char_name))

    location_aliases = _location_aliases(project, scene_data.get("location", ""))
    loc_slug = location_aliases
    if loc_slug:
        slugs_to_check.add(loc_slug)
    else:
        # Résolution générique : on retire le préfixe INT./EXT. et on slugifie le reste.
        loc_raw = scene_data.get("location", "").lower().strip()
        loc_stripped = re.sub(r"^\s*(int|ext)\s*[.—-]?\s*", "", loc_raw).strip()
        if loc_stripped:
            slugs_to_check.add(slugify(loc_stripped))


    for obj in get_config().get("production", {}).get("visualize", {}).get("objects", []):
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
        logger.info("Missing sheets: %s", missing)
        for slug in missing:
            for etype in ["personnage", "objet", "lieu"]:
                fiche = bible / etype / slug / "fiche.yaml"
                if fiche.exists():
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
                    svc.generate(
                        prompt=prompt, seed=ver.get("seed", 42),
                        width=768, height=768,
                        output_dir=str(fiche.parent), filename="sheet_v1.png",
                    )
                    break

    return list(slugs_to_check)


def _upload_refs(project: str, slugs: list[str]) -> str:
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
    with ThreadPoolExecutor(8) as pool:
        urls = list(pool.map(lambda p: upload_to_tmpfiles(p, expire=172800), paths))
    return "|".join(urls)


def _build_shot_prompt(shot: dict, scene_data: dict, project: str) -> str:
    prompt = shot.get("prompt_image", shot.get("description", ""))
    shot_text = (shot.get("description", "") + " " + prompt).lower()
    char_details = scene_data.get("character_details", {})

    detail_parts = []
    for char_name, details in char_details.items():
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
        prompt = f"{'. '.join(detail_parts)}. {prompt}"

    # Verrou de scène : la distribution fermée (personnages, tenue, lieu) déclarée dans
    # `bible/scene/scene_NN.yaml` passe avant tout le reste. C'est la seule partie du
    # prompt qui soit autoritaire — le reste est généré. Sans fichier de scène, chaîne
    # vide et comportement inchangé.
    scene_no = scene_data.get("number")
    if scene_no is None:
        scene_no = scene_data.get("scene")
    if scene_no is not None:
        anchor = SceneConstraints(PROJECTS_DIR / project).anchor_text(int(scene_no), shot_text)
        if anchor:
            prompt = f"{anchor} {prompt}"

    style_path = PROJECTS_DIR / project / "bible" / "ambiance" / "style-guide.yaml"
    if style_path.exists():
        style_data = yaml.safe_load(style_path.read_text())
        ver = style_data.get("versions", {}).get(style_data.get("active_version", "v1"), {})
        ambiance = ver.get("prompt_image", "")
        if ambiance:
            prompt = f"{prompt}. {ambiance}"

    return prompt


def _mark_shot_progress(project: str, scene_key: str, shot_number: int, image_path: str) -> None:
    """Persiste l'avancement d'un plan dans `scene_state.json` (P3.4).

    Écrit après chaque plan réussi pour qu'un crash laisse une trace exploitable :
    `artifacts.shots[<num>]`. Coût négligeable (un petit JSON), aucune conséquence sur
    le comportement de génération.
    """
    try:
        from pipeline.state import SceneState
        state = SceneState(PROJECTS_DIR / project)
        state.mark_shot(scene_key, "visualize", shot_number, note=f"image {image_path}")
    except Exception as exc:
        logger.warning("État par plan non enregistré (shot %d) : %s", shot_number, exc)


def _verify_shot_if_enabled(project: str, scene_num: int, shot: dict,
                            image_path: str, scene_dir: Path) -> None:
    """Vérifie le plan contre les contraintes de scène, si l'option est active.

    Désactivée par défaut : la vérification déclenche un appel vision par plan, donc un
    coût. Sans clé `production.visualize.verify_shots`, ou sans `bible/scene/scene_NN.yaml`,
    rien n'est appelé et rien n'est écrit. Les résultats sont juxtapositionnés dans
    `shot_verification.json` pour relecture humaine, jamais pour boucler une régénération.
    """
    cfg = get_config().get("production", {}).get("visualize", {})
    if not cfg.get("verify_shots"):
        return
    constraints = SceneConstraints(PROJECTS_DIR / project)
    if not constraints.exists(scene_num):
        return  # rien de déclaré : pas de note à faire

    try:
        from pipeline.services.vision import VisionAnalysisService
        from pipeline.shot_verify import verify_shot
    except Exception as exc:
        logger.warning("Vérification de plan indisponible : %s", exc)
        return

    result = verify_shot(image_path, shot, constraints, vision=VisionAnalysisService())
    out = scene_dir / "shot_verification.json"
    data = json.loads(out.read_text()) if out.exists() else {}
    data[str(shot.get("number", 1))] = result
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    if result["status"] == "verified":
        logger.info("Shot %d vérifié : score %.2f%s", shot.get("number", 1), result["score"],
                    f" (échecs: {', '.join(result['failed_axes'])})" if result["failed_axes"] else "")


class VisualizeStep:
    """Generate storyboard images from textual shots.

    For each shot:
    1. Reads character details from parsed YAML (filters per shot)
    2. Injects style-guide ambiance
    3. Uses refs from shot["refs"] (max 4) or falls back to all sheets
    4. Uploads refs to tmpfiles for the image API
    5. Generates via ImageService (provider actif config)
    6. Versions old files before overwriting
    """

    def __init__(self, verbose: bool = False):
        self.image = ImageService()
        self.verbose = verbose

    def run(self, project: str, scenes: list[dict], storyboard_dir: Path, shot_number: int | None = None) -> dict:
        results = {}

        for scene in scenes:
            scene_num = scene.get("number", 0)
            scene_key = f"scene_{scene_num:02d}"
            sb_path = storyboard_dir / scene_key / "storyboard.json"

            if not sb_path.exists():
                logger.warning("No storyboard for scene %d", scene_num)
                continue

            scene_data, shots = _get_scene_data(project, scene_num)
            if not scene_data or not shots:
                continue

            # Filter to single shot if shot_number specified
            if shot_number is not None:
                shots = [s for s in shots if s.get("number") == shot_number]
                if not shots:
                    logger.warning("Shot %d not found in scene %d", shot_number, scene_num)
                    continue

            scene_dir = storyboard_dir / scene_key
            scene_dir.mkdir(parents=True, exist_ok=True)

            logger.info("Scene %d: visualizing %d shots", scene_num, len(shots))

            def _gen(shot: dict, prev_image: str | None) -> tuple[int, str, str]:
                sn = shot.get("number", 1)

                shot_refs = shot.get("refs", [])
                if shot_refs:
                    slugs = shot_refs
                else:
                    slugs = _ensure_sheets(project, scene_data)

                refs = _upload_refs(project, slugs)
                # Continuité : l'image du shot précédent sert aussi de référence pour
                # que le miroir, le décor et les accessoires restent identiques.
                if prev_image and Path(prev_image).exists():
                    prev_url = upload_to_tmpfiles(prev_image, expire=172800)
                    refs = (refs + "|" + prev_url) if refs else prev_url

                prompt = _build_shot_prompt(shot, scene_data, project)
                continuity = _continuity_prompt()
                if prev_image and continuity:
                    prompt = f"{prompt} {continuity}"

                out_path = scene_dir / f"shot_{sn:02d}.png"

                if out_path.exists():
                    v = 1
                    while (scene_dir / f"shot_{sn:02d}_v{v}.png").exists():
                        v += 1
                    out_path.rename(scene_dir / f"shot_{sn:02d}_v{v}.png")
                    logger.info("Versioned old shot %d → v%d", sn, v)

                width, height = _shot_size()
                try:
                    path = self.image.generate(
                        prompt=prompt, seed=sn,
                        width=width, height=height,
                        ref_image=refs or None,
                        output_dir=str(scene_dir), filename=f"shot_{sn:02d}.png",
                    )
                    return sn, str(path), "active-provider"
                except Exception as e:
                    logger.error("Shot %d failed: %s", sn, e)
                    return sn, f"ERROR:{e}", ""

            img_cfg = get_config().get("services", {}).get("image", {})
            prov = img_cfg.get("providers", {}).get(img_cfg.get("active", ""), {})
            workers = int(prov.get("parallel", 1) or 1)
            # Séquentiel obligatoire pour chaîner la continuité (chaque shot dépend du précédent).
            prev_image: str | None = None
            for shot in sorted(shots, key=lambda s: s.get("number", 0)):
                if prev_image is None:
                    # En régénération individuelle : partir du shot précédent existant sur disque.
                    prev_num = int(shot.get("number", 1)) - 1
                    prev_file = scene_dir / f"shot_{prev_num:02d}.png"
                    if prev_file.exists():
                        prev_image = str(prev_file)
                sn, result, model = _gen(shot, prev_image)
                if result.startswith("ERROR:"):
                    logger.error("Shot %d failed: %s", sn, result[6:])
                    shot["image_path"] = ""
                else:
                    shot["image_path"] = result
                    shot["model"] = model
                    prev_image = result
                    logger.info("Shot %d → %s [%s]", sn, Path(result).name, model)
                    _verify_shot_if_enabled(project, scene_num, shot, result, scene_dir)
                    _mark_shot_progress(project, scene_key, sn, result)

            results[scene_key] = {"shots": shots, "count": len(shots)}

            # Update storyboard.json: merge generated shots into full list
            if shot_number is not None:
                # Single shot regeneration: read full storyboard, update only this shot
                all_shots = json.loads(sb_path.read_text()) if sb_path.exists() else []
                for gen_shot in shots:
                    for i, existing in enumerate(all_shots):
                        if existing.get("number") == gen_shot.get("number"):
                            all_shots[i].update({k: v for k, v in gen_shot.items() if v})
                            break
                with open(sb_path, "w") as f:
                    json.dump(all_shots, f, ensure_ascii=False, indent=2)
            else:
                with open(sb_path, "w") as f:
                    json.dump(shots, f, ensure_ascii=False, indent=2)

        return results
