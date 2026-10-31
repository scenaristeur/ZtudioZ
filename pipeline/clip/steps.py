from __future__ import annotations

import logging
import os
import hashlib
from pathlib import Path

import yaml

from pipeline.config import get_clip_config, get_service_config
from pipeline.reporter import step_end, step_item, step_start, step_warn
from pipeline.services.image import ImageService
from pipeline.services.montage import assemble_scene
from pipeline.services.video import VideoService
from pipeline.utils import upload_to_tmpfiles

# Nouveaux modules
from pipeline.clip.audio_analysis import analyze_audio, beats_to_timeline, save_analysis
from pipeline.clip.verify import verify_plan, should_regenerate, build_correction_prompt
from pipeline.clip.diversity import compute_clip_embeddings, analyze_diversity, save_diversity_report
from pipeline.clip.ab_testing import ab_test_plan, save_ab_report
from pipeline.clip.multipass import multipass_plan, multipass_section
from pipeline.clip.colorgrading import color_grade_clip

logger = logging.getLogger(__name__)

PROJECTS_DIR = Path(__file__).resolve().parent.parent.parent / "projets"


def _clip_dir(project: str) -> Path:
    return PROJECTS_DIR / project / "preprod" / "clip"


def _resolve_env(value):
    """Résoudre les variables ${VAR} d'une config clip (même logique que config.py)."""
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    return value


def _load_clip_config(project: str) -> dict:
    cfg_path = _clip_dir(project) / "config.yaml"
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"Config clip introuvable: {cfg_path}. "
            "Créer preprod/clip/config.yaml (audio, variants, timeline)."
        )
    return _resolve_env(yaml.safe_load(cfg_path.read_text()))


def _load_variant(project: str, variant_id: str) -> tuple[dict, dict]:
    """Retourner (config_clip, variant) pour la variante demandée."""
    cfg = _load_clip_config(project)
    for v in cfg.get("variants", []):
        if v.get("id") == variant_id:
            return cfg, v
    raise KeyError(f"Variante clip '{variant_id}' introuvable dans preprod/clip/config.yaml")


def _load_timeline(project: str, cfg: dict, variant: dict) -> dict:
    tl = variant.get("timeline") or cfg.get("timeline")
    if not tl:
        raise FileNotFoundError("Aucune timeline définie pour cette variante")
    tl_path = _clip_dir(project) / tl
    if not tl_path.exists():
        raise FileNotFoundError(f"Timeline introuvable: {tl_path}")
    return yaml.safe_load(tl_path.read_text())


def _section_duration(sec: dict) -> float:
    return round(float(sec.get("end", 0)) - float(sec.get("start", 0)), 2)


def _safe_id(sec_id: str) -> str:
    """Nom de fichier sûr pour un id de section (sans accents/espaces, compat noms de fichiers)."""
    import re
    import unicodedata
    norm = unicodedata.normalize("NFKD", sec_id)
    ascii_only = norm.encode("ascii", "ignore").decode("ascii")
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_only).strip("_")
    return safe or "section"


class ClipStep:
    """Production d'un clip musical, générique et data-driven.

    Le pipeline est CLIP-AGNOSTIQUE : toutes les données (config, timeline, style guide,
    bible, audio) sont lues dans le dossier projet `preprod/clip/`. Rien n'est codé en dur
    pour un clip particulier → adaptable à tout type de clip (narratif, abstrait, etc.).

    Étapes (par variante) :
      1. Lire config + timeline + style guide + refs bible de la variante.
      2. Générer une image héro par section (prompt = style guide + staging/light/emotion).
      3. Générer une vidéo i2v par section (image héro en référence, durée = section).
      4. Assembler le tout sur la piste audio (montage).

    Modes : "images" (héros, validation) | "video" | "full".
    """

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.image = ImageService()
        # Vidéo : Agnes local par défaut (Pollinations peut être en 402)
        video_config = get_service_config("video")
        if video_config.get("type") != "agnes":
            video_config = {
                "type": "agnes",
                "base_url": "http://127.0.0.1:8765",
                "dev": video_config.get("dev", True),
                "dev_width": video_config.get("dev_width", 480),
                "dev_height": video_config.get("dev_height", 270),
                "model": "agnes-video-v2.0",
            }
        self.video = VideoService(config=video_config)
        self._ref_slug_map: dict[str, str] = {}
        self._ref_urls: list[str] = []
        self._char_descriptions: dict[str, str] = {}
        # États visuels des entités : {slug: {state_name: sheet_path}}
        self._entity_states: dict[str, dict[str, str]] = {}
        # Prompts des états visuels (bible states `prompt_image`) : {slug: {state: prompt}}
        self._entity_state_prompts: dict[str, dict[str, str]] = {}
        # Type de chaque entité (slug → personnage|lieu|objet) pour l'ancre d'identité des subrefs.
        self._slug_type: dict[str, str] = {}
        # Dossier des sous-références (états dynamiques) — sous le dossier de variante.
        self._subref_dir: Path | None = None
        # Dernière frame par arc narratif (pour chaînage arc-aware)
        self._arc_last_frame: dict[str, str | None] = {}
        # Canvas cible (w,h) du clip, dérivé de la config clip du PROJET (ex. 768x1344=9:16
        # vertical). Vide → fallback sur la config clip globale `image_size` (16:9).
        self._canvas: tuple[int, int] | None = None
        # Mode de chaînage des plans (config clip `chaining.mode`) :
        #   - "raw"     (défaut) : start_ref = dernière frame brute du plan précédent. Évite la
        #     dérive féminine que la composition multi-refs (ImageService.compose apihub)
        #     réintroduisait en ré-composant la frame avec les sheets.
        #   - "compose" : comportement historique — re-compose la frame avec les sheets
        #     (personnage + lieu) avant le prochain i2v.
        self._chain_mode: str = "raw"
        # Ré-ancrer sur le héro de la section à chaque changement de section (même arc).
        # Sans ça, les sections suivantes d'un même arc démarrent de la dernière frame de la
        # section précédente et le héro validé de la section n'est jamais utilisé.
        self._section_reset: bool = True

    # ── Point d'entrée ──────────────────────────────────────────

    def run(
        self,
        project: str,
        variant_id: str,
        audio_path: str | Path | None = None,
        mode: str = "full",
        sections: list[str] | None = None,
        force: bool = False,
        max_plans: int | None = None,
    ) -> dict:
        self._project = project
        self._variant = variant_id
        root = PROJECTS_DIR / project
        cfg, variant = _load_variant(project, variant_id)
        timeline = _load_timeline(project, cfg, variant)
        vdir = _clip_dir(project) / variant_id
        self._vdir = vdir
        self._subref_dir = vdir / "_subrefs"
        outdir = root / "production" / "clip" / variant_id
        vdir.mkdir(parents=True, exist_ok=True)
        outdir.mkdir(parents=True, exist_ok=True)

        if audio_path is None:
            audio_path = self._resolve_audio(root, cfg)
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio introuvable: {audio_path}")

        style_guide = self._load_style_guide(root, variant)
        refs = self._variant_refs(root, variant)
        self._char_descriptions = self._load_character_descriptions(root)
        self._load_entity_states(root)
        self.video = self._video_service(cfg)
        # Canvas cible du clip (ratio) défini dans la config clip du projet.
        self._canvas = self._parse_canvas(cfg.get("image_size") or variant.get("image_size"))
        # Mode de chaînage des plans (raw par défaut — cf. __init__).
        self._chain_mode = str(cfg.get("chaining", {}).get("mode", "raw"))
        self._section_reset = bool(cfg.get("chaining", {}).get("section_reset", True))

        all_sections = timeline.get("sections", [])
        if not all_sections:
            raise RuntimeError("Timeline vide (aucune section)")
        if sections:
            all_sections = [s for s in all_sections if s.get("id") in sections]
        else:
            # Le MP3 est la source de vérité temporelle : la timeline doit couvrir
            # exactement la durée réelle de l'audio, en continu, sans trou ni chevauchement.
            self._validate_timeline(all_sections, audio_path)

        step_start(f"Clip [{variant_id}] — images héro par section")
        images: dict[str, str] = {}
        candidates: dict[str, list[str]] = {}
        # `hero_candidates` (>1) = générer N candidats direction par section (v2). Sinon 1 seul
        # héro `{section}.png` (comportement v1).
        n_cand = int(variant.get("hero_candidates", 1) or 1)
        heroes_sel = self._load_hero_selection(variant_id)
        for sec in all_sections:
            sid = _safe_id(sec["id"])
            # 1. Direction déjà validée (heroes.yaml) → on l'utilise telle quelle.
            chosen = heroes_sel.get(sec["id"])
            chosen_path = (vdir / chosen) if chosen else None
            if chosen_path and chosen_path.exists():
                step_item(f"héro {sec['id']}", f"sélection {chosen}")
                images[sec["id"]] = str(chosen_path)
                continue
            # 2. Générer (ou recharger) les candidats de la section.
            sec_candidates: list[str] = []
            for n in range(1, n_cand + 1):
                if n_cand <= 1:
                    out = vdir / f"{sid}.png"
                else:
                    out = vdir / f"{sid}_c{n:02d}.png"
                if out.exists() and not force:
                    step_item(f"héro {sec['id']} c{n:02d}", "cache")
                else:
                    scene, cam, hbeat = self._hero_hint(sec, n_cand, n)
                    prompt = self._build_section_prompt(sec, style_guide, timeline, variant, hint=scene, camera=cam)
                    path = self._gen_image(prompt, variant, sec, out, cand_idx=n, force=force, beat=hbeat)
                    if path:
                        step_item(f"héro {sec['id']} c{n:02d}", "généré")
                    else:
                        step_warn(f"héro {sec['id']} c{n:02d}", "échec")
                if out.exists():
                    sec_candidates.append(str(out))
            candidates[sec["id"]] = sec_candidates
            if sec["id"] not in images and sec_candidates:
                # Héro par défaut : 1er candidat (la sélection finale se fait dans heroes.yaml).
                images[sec["id"]] = sec_candidates[0]
        n_total = sum(len(v) for v in candidates.values())
        step_end("Clip images", f"{len(images)}/{len(all_sections)} héros, {n_total} candidats")

        if mode == "images":
            return {"images": images, "candidates": candidates, "sections": all_sections,
                    "dir": str(vdir)}

        max_plan = self._max_plan_duration(cfg)

        step_start(f"Clip [{variant_id}] — vidéos par plan (max {max_plan}s)")
        # ORCHESTRATEUR D'ARCS : chaque arc = une équipe de tournage autonome.
        # Les plans d'un même arc se chaînent (continuité last_frame).
        # Les arcs sont produits séquentiellement (ou en parallèle si multi-worker).
        arc_tasks = self._build_arc_tasks(all_sections, max_plan)
        plans: list[dict] = []
        for arc in arc_tasks:
            arc_id = arc["id"]
            n_beats = arc["total_beats"]
            step_item(f"arc {arc_id}", f"{n_beats} beats, {len(arc['sections'])} sections")
            arc_plans = self._produce_arc(
                arc, images, variant, vdir, max_plan,
                force=force, max_plans=max_plans, produced=len(plans),
            )
            plans.extend(arc_plans)
            if max_plans is not None and len(plans) >= max_plans:
                break
        step_end("Clip vidéos", f"{len(plans)} plans générés ({len(arc_tasks)} arcs)")

        if mode == "video":
            return {"plans": plans, "videos": {p["id"]: p["video"] for p in plans},
                    "images": images, "dir": str(vdir)}

        step_start(f"Clip [{variant_id}] — montage final")
        final = self._assemble(plans, audio_path, outdir, variant_id)
        step_end("Clip montage", f"Clip final : {final}")
        return {"images": images, "plans": plans, "final": final, "dir": str(vdir)}

    def _validate_timeline(self, sections: list[dict], audio: Path) -> None:
        """Valider que la timeline couvre exactement la durée réelle du MP3, en continu.

        Le MP3 est la source de vérité temporelle : on lit sa durée réelle (ffprobe) et on
        vérifie que les sections sont contiguës de 0 à cette durée (sans trou ni chevauchement).
        """
        import subprocess
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(audio)],
            capture_output=True, text=True, timeout=30,
        )
        import json as _json
        data = _json.loads(result.stdout)
        duration = float(data.get("format", {}).get("duration", 0))
        if duration <= 0:
            logger.warning("Impossible de lire la durée du MP3, validation timeline sautée")
            return
        ordered = sorted(sections, key=lambda s: float(s.get("start", 0)))
        if abs(float(ordered[0].get("start", 0))) > 0.5:
            raise RuntimeError(
                f"Timeline : la 1ère section doit commencer à 0 (actuel {ordered[0].get('start')})"
            )
        for prev, nxt in zip(ordered, ordered[1:]):
            if abs(float(prev.get("end", 0)) - float(nxt.get("start", 0))) > 0.5:
                raise RuntimeError(
                    f"Timeline : trou entre '{prev['id']}' (fin {prev.get('end')}) et "
                    f"'{nxt['id']}' (début {nxt.get('start')})"
                )
        if abs(float(ordered[-1].get("end", 0)) - duration) > 0.5:
            raise RuntimeError(
                f"Timeline : la dernière section doit finir à la durée du MP3 "
                f"({duration:.1f}s), actuel {ordered[-1].get('end')}"
            )
        logger.info("Timeline validée : %d sections, 0 → %.1fs (durée MP3)", len(ordered), duration)

    # ── Génération image ────────────────────────────────────────

    def _gen_image(self, prompt: str, variant: dict, sec: dict, out: Path,
                   cand_idx: int = 0, force: bool = False, beat: dict | None = None) -> str | None:
        seed = int(variant.get("seed", 42)) + len(sec["id"]) + cand_idx
        size = self._image_size()
        # Le cache des services (compose/generate) est par EXISTENCE du fichier : sans
        # suppression préalable, --force au niveau run ne régénèrerait rien (l'ancien
        # candidat à l'ancien prompt serait renvoyé tel quel). On purge l'output d'abord.
        if force and out.exists():
            out.unlink()
        # TOUJOURS utiliser les refs (character + location, sous-références de beat) pour la
        # consistance visuelle. La contrainte "REFERENCE ONLY" dans le prompt empêche les
        # personnages fantômes.
        refs = self._refs_for_section(sec, beat=beat)
        try:
            if refs:
                return self.image.compose(
                    prompt=prompt,
                    ref_images=refs,
                    width=size[0], height=size[1],
                    output_dir=str(out.parent),
                    filename=f"{out.name}",
                )
            return self.image.generate(
                prompt=prompt,
                seed=seed,
                width=size[0], height=size[1],
                output_dir=str(out.parent),
                filename=f"{out.name}",
            )
        except Exception as e:
            logger.error("Image %s failed: %s", sec["id"], e)
            return None

    def _section_character_slugs(self, sec: dict) -> list[str]:
        """Slugs des personnages présents dans une section.

        Source principale : `sec.characters` (explicite dans le timeline, cf. Melody's
        Ghost où l'homme est présent même quand le focus de la section est un LIEU comme
        la-voiture ou le-supermarche). En rétro-compat, s'il n'y a pas de clé `characters`,
        retombe sur le `focus` UNIQUEMENT si c'est un personnage connu de la bible.
        """
        chars = sec.get("characters")
        if chars:
            return [c.strip() for c in chars if c.strip()]
        focus = (sec.get("focus") or "").lower()
        if focus in ("les-enfants", "les-autres-enfants"):
            return ["la-fille-27", "fils-22-ans", "fils-19-ans", "le-plus-jeune-15"]
        slugs = [s.strip() for s in focus.split("+") if s.strip()]
        if self._char_descriptions:
            # Rétro-compat : un focus n'est un personnage que s'il existe dans la bible perso.
            return [s for s in slugs if s in self._char_descriptions]
        return slugs

    def _section_character_prompts(self, sec: dict) -> str:
        """Descriptions des personnages de la section (depuis la bible) à injecter dans le prompt.

        Pour une scène à personnage unique, on injecte la description textuelle (plus fiable
        que la composition multi-refs qui tend à ajouter des personnages fantômes).
        """
        if not self._char_descriptions:
            return ""
        parts = []
        for s in self._section_character_slugs(sec):
            desc = self._char_descriptions.get(s)
            if desc:
                parts.append(f"{desc}.")
        return " ".join(parts)

    def _refs_for_section(self, sec: dict, beat: dict | None = None) -> list[str]:
        """Références image d'une section : sheets personnages (`characters`) + sheets lieux.

        IMPORTANT : on passe TOUJOURS la (les) sheet(s) personnage EN PLUS du lieu pour
        verrouiller l'identité (visage, tenue), même quand le `focus` de la section est un
        LIEU (ex. couplet2=la-voiture, pont=le-supermarche). Sans la sheet de l'homme, chaque
        plan/candidat inventait un homme différent. La contrainte "SOLO" est dans le PROMPT.

        `beat` (optionnel) : si fourni, les `states.perso`/`states.lieu` du beat (ou de la
        section en défaut) déclenchent des SOUS-RÉFÉRENCES — variation de l'image de
        référence déterminée par la timeline (pose du perso, angle du lieu). Cf. `_state_sheet`.
        """
        all_refs = self._last_refs()
        if not all_refs or not self._ref_slug_map:
            return all_refs
        section_states = sec.get("states", {}) or {}
        beat_states = (beat or {}).get("states", {}) or {}
        perso_state = beat_states.get("perso") or section_states.get("perso", "")
        lieu_state = beat_states.get("lieu") or section_states.get("lieu", "")
        refs: list[str] = []
        seen: set[str] = set()
        for slug in self._section_character_slugs(sec):
            sheet = self._state_sheet(slug, perso_state, "personnage") if perso_state and perso_state != "absent" else None
            sheet = sheet or self._ref_slug_map.get(slug)
            if sheet and sheet not in seen:
                refs.append(sheet)
                seen.add(sheet)
        for loc in (sec.get("locations") or []):
            sheet = self._state_sheet(loc, lieu_state, "lieu") if lieu_state else None
            sheet = sheet or self._ref_slug_map.get(loc)
            if sheet and sheet not in seen:
                refs.append(sheet)
                seen.add(sheet)
        return refs if refs else all_refs

    def _build_section_prompt(self, sec: dict, style_guide: str, timeline: dict,
                              variant: dict, hint: str = "", camera: str = "") -> str:
        """Construire le prompt image pour une section.

        Structure condensée : contraintes critiques EN PREMIER, style guide court.
        Max ~800 chars pour éviter la troncature par le générateur.

        `hint` (v2, candidats héros) : UNE direction précise proposée pour cette section
        (décrit la scène). S'il est fourni, il REMPLACE le staging générique — chaque
        candidat est ainsi une interprétation réellement différente.

        `camera` (v2, candidats héros) : directive de CADRAGE en anglais, dérivée du champ
        `camera` du beat échantillonné de la timeline (cf. `_hero_hint`). Largement suivie
        par le générateur → les candidats d'une section varient en plans/positions au lieu
        de montrer la même vue du lieu. Placée juste après les contraintes (prominence).
        """
        parts = []

        # 1. CONTRAINTES CRITIQUES EN PREMIER (ne pas déplacer)
        focus = (sec.get("focus") or "").lower()
        staging = (sec.get("staging") or "").lower()
        has_char = bool(self._section_character_slugs(sec))
        if has_char or "alone" in staging or "seul" in staging or "absent" in staging:
            parts.append(
                "REFERENCE ONLY for character identity (same face, same clothes). "
                "Exactly ONE person. No other people, no silhouettes, no ghosts."
            )
        else:
            parts.append(
                "NO PEOPLE: empty scene only. No characters, no human figures."
            )

        # 1bis. Cadrage (v2, candidats héros) — juste après les contraintes, bien visible.
        if camera:
            parts.append(f"CAMERA: {camera}")

        # 2. Style guide court
        if style_guide:
            parts.append(style_guide.strip())

        # 3. Mise en scène : staging générique SANS contrainte si hint fourni
        staging_text = hint or sec.get('staging', '')
        if staging_text:
            parts.append(f"SCENE: {staging_text}.")

        # 4. Lumière + émotion
        light = sec.get('light', '')
        emotion = sec.get('emotion', '')
        if light:
            parts.append(f"LIGHT: {light}.")
        if emotion:
            parts.append(f"MOOD: {emotion}.")

        # 5. Description personnage (si la section a des personnages)
        char_desc = self._section_character_prompts(sec)
        if char_desc and has_char:
            parts.append(f"CHARACTER: {char_desc}")

        # 6. Ratio et style
        parts.append("16:9 cinematic, film grain.")

        prompt = " ".join(p for p in parts if p)

        # Troncature intelligente si > 800 chars
        if len(prompt) > 800:
            # Garder les 600 premiers chars (contraintes + style) + fin
            prompt = prompt[:600].rsplit(" ", 1)[0] + " 16:9 cinematic, film grain."

        return prompt

    # Cycle caméra EN par défaut (quand le beat n'a pas de `camera` exploitable).
    _HERO_CAMERA_CYCLE_EN = [
        "Wide establishing shot, eye level, medium height.",
        "Medium shot, eye level, subject centered, 35mm.",
        "Close-up shot, shallow depth of field, subject fills the frame.",
        "High angle overhead shot, looking down, wide composition.",
        "Low angle shot looking up, subject larger, cinematic depth.",
        "Medium-wide shot, three-quarter view, dolly perspective.",
        "Extreme close-up, macro details, soft bokeh.",
        "Wide shot with strong foreground framing and deep perspective lines.",
    ]

    def _camera_to_english(self, camera: str, index: int = 0) -> str:
        """Convertir un champ `camera` FR de la timeline en directive cinéma EN.

        Le générateur d'images suit bien mieux les instructions de cadrage en anglais
        ("Close-up shot") que les descriptions françaises ("gros plan lent, bokeh doré").
        Retombe sur un cycle de caméras variées par index si aucune règle ne matche.
        """
        c = (camera or "").lower()
        if "plan fixe" in c or "plan stable" in c:
            out = "Static (locked-off) shot"
        elif "travelling circulaire" in c:
            out = "Slow circular tracking shot, orbiting the subject"
        elif "travelling latéral" in c or "travelling lateral" in c:
            out = "Slow lateral dolly (tracking) shot"
        elif "travelling avant" in c or "travelling" in c:
            out = "Slow dolly tracking shot, moving forward"
        elif "grand angle" in c:
            out = "Wide-angle lens shot, deep perspective"
        elif "vue plongeante" in c:
            out = "High-angle overhead shot, looking down"
        elif "plan depuis l'extérieur" in c:
            out = "Exterior shot, seen from outside, framing through the window"
        elif "plan depuis le bas" in c or "bas de l'escalier" in c:
            out = "Low-angle shot looking up"
        elif "gros plan" in c or "macro" in c:
            out = "Extreme close-up, macro detail, shallow depth of field"
        elif "close-up" in c:
            out = "Close-up shot, shallow depth of field"
        elif "plan moyen large" in c:
            out = "Medium-wide shot"
        elif "plan large" in c or "plan d'ensemble" in c:
            out = "Wide establishing shot"
        elif "plan serré" in c:
            out = "Close shot"
        elif "plan moyen" in c:
            out = "Medium shot, eye level"
        else:
            out = self._HERO_CAMERA_CYCLE_EN[index % len(self._HERO_CAMERA_CYCLE_EN)]
        # Compléter avec la persistance de mouvement si mentionnée (zoom/push-in).
        if "zoom avant" in c and "push-in" not in out:
            out += ", slow push-in (subtle zoom-in)"
        elif "zoom" in c and "push-in" not in out:
            out += ", subtle zoom"
        if "oscille" in c or "oscillant" in c:
            out += ", lights oscillating between harmonious and dissonant"
        return out

    def _hero_hint(self, sec: dict, n_cand: int, n: int) -> tuple[str, str, dict | None]:
        """Contenu + directive caméra + BEAT du candidat héros n°n (suit la TIMELINE).

        - Sections à plusieurs beats : on ÉCHANTILLONNE un beat à travers la timeline
          (début → milieu → fin) → le contenu ET la caméra du beat sont repris, et le dict
          beat est renvoyé pour que les refs du héros utilisent ses `states` (sous-références
          pose/angle, cf. `_state_sheet`).
        - Section à un seul beat (ex. intro) : hero_variants (axes visuels variés) enrichis
          d'un cycle de caméras EN distinct par candidat ; beat = le beat unique (states).

        Returns: (scene_text, camera_en, beat_dict_ou_None)
        """
        beats = sec.get("beats") or []
        variants = sec.get("hero_variants") or []
        if len(beats) >= 2:
            if len(beats) <= n_cand:
                idx = n - 1
            else:
                idx = int(round((n - 1) * (len(beats) - 1) / max(1, n_cand - 1)))
            b = beats[min(idx, len(beats) - 1)]
            scene = b.get("beat", "")
            camera_en = self._camera_to_english(b.get("camera", ""), n - 1)
            return scene, camera_en, b
        # Section à beat unique (ex. intro) : hero_variants (axes variés) + UNE caméra EN
        # par candidat (le beat unique a une seule caméra → on varie le cadrage via le cycle).
        if variants:
            scene = variants[(n - 1) % len(variants)]
        else:
            scene = sec.get("staging", "")
        camera_en = self._HERO_CAMERA_CYCLE_EN[(n - 1) % len(self._HERO_CAMERA_CYCLE_EN)]
        b0 = beats[0] if beats else None
        return scene, camera_en, b0

    def _load_hero_selection(self, variant_id: str) -> dict:
        """Charger la sélection de direction validée → {section_id: candidate_filename}.

        Fichier : `preprod/clip/{variant}_heroes.yaml`. Ex :
            intro: intro_c03.png
            couplet1: couplet1_c02.png
        Les candidats sont lus depuis `preprod/clip/{variant}/`.
        """
        path = _clip_dir(self._project) / f"{variant_id}_heroes.yaml"
        if not path.exists():
            return {}
        try:
            data = yaml.safe_load(path.read_text())
            return data if isinstance(data, dict) else {}
        except Exception:
            logger.warning("heroes.yaml non lisible: %s", path)
            return {}

    # ── Génération vidéo ────────────────────────────────────────

    def _gen_video(self, sec: dict, image: str, variant: dict, out: Path,
                   duration: float, plan_index: int = 0, start_ref: str | None = None,
                   end_ref: str | None = None,
                   beat: str = "", camera: str = "") -> str | None:
        # CHAQUE plan doit être DIFFÉRENT : beat (micro-action) + caméra + seed varient.
        if not camera:
            camera = self._plan_camera(plan_index)

        # Contrainte d'identité : le personnage dans la vidéo DOIT matcher la ref image
        has_char = bool(self._section_character_slugs(sec))
        identity = ""
        if has_char:
            identity = "REFERENCE IMAGE for character identity: match exactly (same face, same clothes, same hair). "
            char_prompt = self._section_character_prompts(sec)
            if char_prompt:
                identity += f"CHARACTER: {char_prompt} "

        if beat:
            prompt = (
                f"{identity}"
                f"{sec.get('light', '')}, {sec.get('emotion', '')}. "
                f"{beat} {camera} Plan continu, léger mouvement, grain cinéma."
            )
        else:
            prompt = (
                f"{identity}"
                f"Caméra à l'épaule, {sec.get('light', '')}, {sec.get('emotion', '')}. "
                f"{sec.get('staging', '')}. {camera} Plan continu, léger mouvement, grain cinéma."
            )

        # Synthèse : respecter la limite de caractères du provider
        provider = self.video.config.get("provider", "default")
        negative = self._build_negative_prompt(sec)
        prompt, negative = self._synthesize_prompt(prompt, provider, negative)

        seed = int(variant.get("seed", 42)) + len(sec["id"]) * 1000 + plan_index
        # Fallback provider automatique : si le provider actif échoue (réseau/402/503),
        # on retente avec l'autre provider dispo (agnes local ⇄ pollinations). Permet de
        # continuer la production si le serveur principal tombe ou si les crédits expirent.
        providers = self._fallback_video_services()
        last_error: Exception | None = None
        # Prédécoupage au ratio de sortie du serveur (évite l'étirement ref 16:9 → out 1.375)
        video_ref = start_ref or image
        if video_ref:
            video_ref = self._crop_to_video_ratio(video_ref)
        # DOUBLE-ANCRE (mode keyframes) : start + end composé (pose finale du beat) bornent
        # l'identité des deux côtés — corrige le drift de personnage en milieu de plan de l'i2v
        # simple. On retombe sur i2v simple si l'end vaut le start (compose échoué/absent).
        refs = [video_ref] if video_ref else []
        if end_ref:
            end_ref2 = self._crop_to_video_ratio(end_ref)
            if str(end_ref2) != str(video_ref):
                refs.append(end_ref2)
        if not refs:
            refs = [image]
        for name, vservice in providers:
            try:
                return vservice.generate(
                    prompt=prompt,
                    ref_image=refs[0] if len(refs) == 1 else None,
                    ref_images=refs if len(refs) > 1 else None,
                    seed=seed,
                    duration=int(duration),
                    output_dir=str(out.parent),
                    filename=f"{out.name}",
                )
            except Exception as e:
                last_error = e
                if len(providers) > 1:
                    logger.warning(
                        "Video %s via %s failed, fallback: %s", sec["id"], name, e
                    )
        logger.error("Video %s failed: %s", sec["id"], last_error)
        return None

    # ── Synthèse de prompts (limites de caractères) ─────────────

    # Limite de caractères par provider (conservateur)
    PROMPT_LIMITS = {
        "agnes": 500,
        "pollinations": 2000,
        "default": 1500,
    }

    def _synthesize_prompt(self, prompt: str, provider: str = "default",
                           negative: str = "") -> tuple[str, str]:
        """Synthétiser un prompt pour respecter la limite de caractères du provider.

        Priorité de préservation : beat (micro-action) > identité > lumière > camera.
        Si le prompt dépasse la limite, on tronque intelligemment :
        1. Supprimer les instructions génériques ("Plan continu, léger mouvement, grain cinéma")
        2. Raccourcir le beat si nécessaire
        3. Dernier recours : tronquer au dernier mot complet

        Returns: (prompt_synthesise, negative_prompt)
        """
        limit = self.PROMPT_LIMITS.get(provider, self.PROMPT_LIMITS["default"])
        full = prompt.strip()
        neg = negative.strip()

        if len(full) <= limit:
            return full, neg

        # Étape 1 : supprimer les instructions génériques
        fillers = [
            "Plan continu, léger mouvement, grain cinéma.",
            "Plan continu, léger mouvement, grain cinéma",
            "léger mouvement, grain cinéma.",
            "léger mouvement, grain cinéma",
            "grain cinéma.",
            "grain cinéma",
        ]
        compressed = full
        for filler in fillers:
            compressed = compressed.replace(filler, "").strip()
        if len(compressed) <= limit:
            return compressed, neg

        # Étape 2 : raccourcir les descriptions longues (staging, etc.)
        # Garder les 2/3 du prompt, en préservant le début (beat) et la fin (camera)
        if len(compressed) > limit:
            # Garder le premier 60% (beat + identité) et le dernier 20% (camera)
            head_len = int(limit * 0.6)
            tail_len = int(limit * 0.2)
            head = compressed[:head_len].rsplit(" ", 1)[0]
            tail = compressed[-tail_len:].split(" ", 1)[-1] if tail_len > 0 else ""
            compressed = f"{head} ... {tail}" if tail else head

        # Étape 3 : troncature de dernier recours
        if len(compressed) > limit:
            compressed = compressed[:limit].rsplit(" ", 1)[0]

        if len(compressed) != len(full):
            logger.info("Prompt compressé: %d → %d chars (limite %d)",
                        len(full), len(compressed), limit)

        return compressed, neg

    # ── Prompts négatifs ────────────────────────────────────────

    NEGATIVE_DEFAULT = (
        "other people, faces, text, watermark, logo, modern objects, "
        "smartphones, computers, cars, blurry, low quality, deformed, "
        "second person, doppelganger, mirror reflection duplicate, "
        "second red scarf, extra red scarf, additional scarf, woman woman figure"
    )

    def _build_negative_prompt(self, sec: dict) -> str:
        """Construire le prompt négatif pour un plan.

        Base = interdictions génériques + écharpe UNIQUE (pas de doublon/reflet qui en
        crée une seconde) + interdictions spécifiques à la section.
        """
        parts = [self.NEGATIVE_DEFAULT]
        staging = (sec.get("staging") or "").lower()
        if "alone" in staging or "seul" in staging or "no other" in staging:
            parts.append("other persons, silhouettes, extra people, crowd")
        return ", ".join(parts)

    # ── Orchestrateur d'arcs (task lists indépendantes) ─────────

    def _build_arc_tasks(self, all_sections: list[dict], max_plan: int
                         ) -> list[dict]:
        """Grouper les sections par arc narratif et construire des task lists indépendantes.

        Chaque arc = une équipe de tournage autonome avec sa propre chaîne de continuité.
        Retourne une liste d'arcs, chacun contenant ses sections et plans à produire.

        Pour un clip multi-lieux (ex: Paris + mer + maison), chaque arc aurait ses
        propres characters, locations, et state sheets — production indépendante.
        """
        arcs: dict[str, dict] = {}
        for sec in all_sections:
            arc_id = sec.get("arc", sec["id"])
            if arc_id not in arcs:
                arcs[arc_id] = {
                    "id": arc_id,
                    "sections": [],
                    "total_beats": 0,
                }
            arcs[arc_id]["sections"].append(sec)
            beats = sec.get("beats", [])
            arcs[arc_id]["total_beats"] += len(beats) if beats else max(
                1, int((float(sec.get("end", 0)) - float(sec.get("start", 0))) / max_plan)
            )
        return list(arcs.values())

    def _produce_arc(self, arc: dict, images: dict[str, str], variant: dict,
                     vdir: Path, max_plan: int, force: bool = False,
                     max_plans: int | None = None, produced: int = 0
                     ) -> list[dict]:
        """Produire tous les plans d'un arc narratif (équipe de tournage autonome).

        Chaînage interne : last_frame du plan N = start_ref du plan N+1 (même arc).
        Pas de chaînage avec les autres arcs (indépendance de production).
        """
        arc_id = arc["id"]
        arc_last_frame: str | None = None
        plans: list[dict] = []

        for sec in arc["sections"]:
            # Frontière de section : ré-ancrage sur le héro de la section plutôt que la
            # dernière frame de la section précédente (chaînage interne conservé). Configurable
            # via chaining.section_reset dans le timeline YAML.
            if self._section_reset:
                arc_last_frame = None
            img = images.get(sec["id"])
            if not img:
                step_warn(f"vidéo {sec['id']}", "pas d'image héro")
                continue
            segments = self._plan_segments(sec, max_plan)
            hero169 = self._crop_image_169(img)

            for i, seg in enumerate(segments):
                if max_plans is not None and produced + len(plans) >= max_plans:
                    return plans
                pid = f"{_safe_id(sec['id'])}_p{i+1:02d}"
                out = vdir / f"{pid}.mp4"

                if out.exists() and not force:
                    step_item(f"plan {pid}", "cache")
                    plans.append({"id": pid, "section": sec["id"], "video": str(out),
                                  "start": seg["start"], "end": seg["end"],
                                  "arc": arc_id})
                    prev = self._extract_last_frame(out)
                    if prev:
                        arc_last_frame = self._chain_start_frame(prev, sec, seg)
                    continue

                start_ref = arc_last_frame or hero169
                # End ancré (pose finale du beat) : recompose le start avec les subréfs du beat.
                # Utilisé en mode keyframes (2 refs) pour borner l'identité des deux côtés.
                end_ref = self._compose_start_image(start_ref if start_ref else img,
                                                    sec, seg)
                path = self._gen_video(
                    sec, img, variant, out, seg["duration"],
                    plan_index=i, start_ref=start_ref, end_ref=end_ref,
                    beat=seg.get("beat", ""), camera=seg.get("camera", ""),
                )
                if path:
                    plans.append({"id": pid, "section": sec["id"], "video": str(path),
                                  "start": seg["start"], "end": seg["end"],
                                  "arc": arc_id})
                    prev = self._extract_last_frame(path)
                    if prev:
                        arc_last_frame = self._chain_start_frame(prev, sec, seg)

        return plans

    # ── Montage ─────────────────────────────────────────────────

    def _assemble(self, plans: list[dict], audio: Path, outdir: Path, variant_id: str) -> str:
        ordered = sorted(plans, key=lambda p: (float(p.get("start", 0)), p.get("id", "")))
        trimmed: list[str] = []
        for p in ordered:
            seg_dur = round(float(p.get("end", 0)) - float(p.get("start", 0)), 2)
            trimmed.append(self._trim_to(p["video"], seg_dur))
        if not trimmed:
            raise RuntimeError("Aucune vidéo de plan à assembler")
        final = outdir / f"clip_{variant_id}.mp4"
        return assemble_scene(trimmed, audio_paths=[str(audio)], output_path=str(final))

    # ── Découpage en plans ──────────────────────────────────────

    def _max_plan_duration(self, cfg: dict) -> int:
        """Durée max d'un plan vidéo (config clip, défaut 5s)."""
        return int(cfg.get("audio", {}).get("max_plan_duration", 5) or 5)

    def _plan_segments(self, sec: dict, max_plan: int) -> list[dict]:
        """Découper une section en segments de ≤ max_plan secondes.

        Si la section a des `beats` définis dans le timeline YAML, on les utilise directement
        (chaque beat = un plan avec micro-action + camera précise). Sinon, découpage
        automatique en tranches de ≤ max_plan secondes (rétro-compatible).
        """
        beats = sec.get("beats")
        if beats:
            # Beats définis dans le timeline → chaque beat est un plan
            segments = []
            for b in beats:
                start = float(b.get("start", 0))
                end = float(b.get("end", 0))
                segments.append({
                    "start": round(start, 2),
                    "end": round(end, 2),
                    "duration": round(end - start, 2),
                    "beat": b.get("beat", ""),
                    "camera": b.get("camera", ""),
                })
            return segments
        # Pas de beats → découpage automatique rétro-compatible
        start = float(sec.get("start", 0))
        end = float(sec.get("end", 0))
        dur = end - start
        if dur <= 0:
            return []
        segments = []
        t = start
        while t < end:
            seg_end = min(t + max_plan, end)
            segments.append({
                "start": round(t, 2),
                "end": round(seg_end, 2),
                "duration": round(seg_end - t, 2),
                "beat": "",
                "camera": "",
            })
            t = seg_end
        return segments

    def _plan_camera(self, index: int) -> str:
        """Mouvement de caméra distinct par plan, pour que les plans d'une même section
        ne soient pas identiques. Cycle de mouvements variés."""
        moves = [
            "Slow push-in toward the traveller.",
            "Slow lateral dolly pan left to right.",
            "Gentle tilt up from his hands to his face.",
            "Slow pull-back revealing the workshop around him.",
            "Subtle handheld drift with shallow focus, breathing motion.",
            "Slow orbit around the traveller, keeping him centered.",
            "Push-in on his hands on the console.",
            "Slow pan across the machine details, then settle on him.",
        ]
        return moves[index % len(moves)]

    def _extract_last_frame(self, video: str | Path) -> str | None:
        """Extraire la DERNIÈRE frame d'un plan vidéo (ffmpeg) → image de départ du plan suivant.

        C'est ce qui garantit le chaînage : chaque plan continue visuellement là où le précédent
        s'arrête (même lumière, même position, même décor).
        """
        src = Path(video)
        if not src.exists():
            return None
        out = src.with_suffix(f".last.png")
        import subprocess
        try:
            subprocess.run(
                # Extraction NATIVE (même taille que la vidéo, ex. 704x512) : pas de recadrage
                # 16:9 qui rogne l'image et fait diverger le format d'avec le start composé.
                ["ffmpeg", "-y", "-v", "error", "-sseof", "-0.1",
                 "-i", str(src), "-frames:v", "1", str(out)],
                check=True, capture_output=True, timeout=60,
            )
            return str(out) if out.exists() else None
        except Exception as e:
            logger.warning("Extraction dernière frame %s échouée: %s", src, e)
            return None

    def _chain_start_frame(self, frame: str, sec: dict, beat: dict | None = None) -> str:
        """Choisir l'image de départ du plan suivant (continuité de chaînage).

        Mode "raw" (défaut) : on réutilise directement la dernière frame brute du plan
        précédent (`.last.png`, déjà recadrée 16:9). Le chaînage est alors un simple i2v
        image→image : aucune ré-composition, donc aucun risque que la composition multi-refs
        (ImageService.compose apihub) réintroduise une figure féminine fantôme (P1).

        Mode "compose" (historique) : re-compose la frame avec les sheets perso/lieu via
        _compose_start_image (ancien comportement, suspect d'ajouter la femme).
        """
        if self._chain_mode != "compose":
            return frame
        return self._compose_start_image(frame, sec, beat)

    def _compose_start_image(self, frame: str, sec: dict, beat: dict | None = None,
                              out_w: int = 1024, out_h: int = 576) -> str:
        """Composer la derniere frame du plan precedent AVEC les sheets de ref.

        Refs : last_frame + sheet_perso etat + sheet_lieu etat (max 4 images).
        La composition via ImageService.compose (multi-refs apihub) fusionne tout
        en UNE image de depart pour le plan suivant (i2v).
        """
        refs = self._build_compose_refs(frame, sec, beat)
        if len(refs) == 1:
            return frame
        from pathlib import Path as _P
        out = _P(frame).with_suffix(".start.png")
        # Prompt adapte selon les refs
        ref_types = []
        if len(refs) > 1:
            ref_types.append("the character portrait")
        if len(refs) > 2:
            ref_types.append("the location")
        ref_desc = " and ".join(ref_types) if ref_types else "the reference"
        prompt = (
            "Recompose this exact frame keeping the same scene, same light, same framing, "
            f"and re-introduce CLEARLY the element(s) shown in {ref_desc} "
            "(same face, same clothes, same setting), in the same position as in the frame. "
            "16:9 cinematic, continuous."
        )
        try:
            composed = self.image.compose(
                prompt=prompt,
                ref_images=refs,
                width=out_w, height=out_h,
                output_dir=str(out.parent),
                filename=f"{out.name}",
            )
            # Recadrer au ratio de sortie vidéo (704x512) : unifier le format du start composé
            # avec le last frame extrait (sinon étirement/rogne d'un plan à l'autre).
            return self._crop_to_video_ratio(composed)
        except Exception as e:
            logger.warning("Composition image de depart %s echouee: %s", out, e)
            return frame

    def _section_focus_slugs(self, sec: dict) -> list[str]:
        """Slugs des personnages focus d'une section (pour verrouiller l'identite)."""
        focus = (sec.get("focus") or "").lower()
        if not focus or focus in ("les-enfants", "les-autres-enfants"):
            return []
        return [s.strip() for s in focus.split("+") if s.strip()]

    # ── Gestion des etats visuels (bible states) ────────────────

    def _load_entity_states(self, root: Path) -> None:
        """Charger les etats visuels de chaque entite bible (personnage, lieu, objet).

        Lit les fiches YAML, trouve les `states`, et mappe chaque etat vers le chemin
        de sa sheet image. Resultat dans self._entity_states = {slug: {state: sheet_path}}.
        """
        bible_dir = root / "bible"
        if not bible_dir.exists():
            return
        for etype in ("personnage", "lieu", "objet"):
            type_dir = bible_dir / etype
            if not type_dir.exists():
                continue
            for entity_dir in type_dir.iterdir():
                if not entity_dir.is_dir():
                    continue
                fiche = entity_dir / "fiche.yaml"
                if not fiche.exists():
                    continue
                try:
                    data = yaml.safe_load(fiche.read_text())
                except Exception:
                    continue
                slug = data.get("slug", entity_dir.name)
                self._slug_type[slug] = etype
                states_map: dict[str, str] = {}
                prompts_map: dict[str, str] = {}
                # Chercher les states dans les versions
                for ver in data.get("versions", {}).values():
                    states = ver.get("states", {})
                    for state_name, state_data in states.items():
                        if isinstance(state_data, str):
                            prompts_map[state_name] = state_data
                            continue
                        sheet_name = state_data.get("sheet", "")
                        if sheet_name:
                            # Sheet fournie dans la fiche → l'utiliser. Si le fichier manque,
                            # repli sur la sheet de base (v1). Un état SANS `sheet` (prompt_image
                            # seul) reste DYNAMIQUE : généré en sous-référence par `_state_sheet`.
                            candidate = entity_dir / sheet_name
                            if candidate.exists():
                                states_map[state_name] = str(candidate)
                            else:
                                default_sheet = entity_dir / "sheet_v1.png"
                                if default_sheet.exists():
                                    states_map[state_name] = str(default_sheet)
                        prompt_image = state_data.get("prompt_image", "")
                        if prompt_image:
                            prompts_map[state_name] = prompt_image
                if states_map:
                    self._entity_states[slug] = states_map
                    logger.info("Etats charges pour %s: %s", slug, list(states_map.keys()))
                if prompts_map:
                    self._entity_state_prompts[slug] = prompts_map

    def _get_state_sheet(self, slug: str, state: str) -> str | None:
        """Retourner le chemin de la sheet pour un etat donne d'une entite."""
        states = self._entity_states.get(slug, {})
        return states.get(state)

    def _state_sheet(self, slug: str, state: str, kind: str = "lieu") -> str | None:
        """Sheet résolue d'un état visuel — statique (bible) OU sous-référence dynamique.

        1. Si l'état a une sheet fournie dans la fiche bible (`states.<etat>.sheet`), on
           l'utilise telle quelle.
        2. Sinon, si l'état a un `prompt_image` (fiche bible), on GÉNÈRE la sous-référence :
           une variation de l'image de référence de l'entité DÉTERMINÉE PAR LA TIMELINE
           (pose du personnage, angle du lieu), mise en cache dans `_subref_dir`. Le héros
           de la section est ensuite composé AVEC cette sous-référence au lieu de la sheet
           de base → le lieu n'est plus toujours pris sous le même angle, et la position
           du personnage suit les beats (ex. l'homme MARCHE dans l'allée au lieu d'être
           rendu assis).
        3. Aucun état exploitable → None (l'appelant retombe sur la sheet de base).

        Génère via un compose mono-réf (source = sheet de base, pour verrouiller
        l'identité) + une ancre "REFERENCE ONLY" selon le type d'entité.
        """
        if not state:
            return None
        existing = self._get_state_sheet(slug, state)
        if existing:
            return existing
        prompt = (self._entity_state_prompts.get(slug) or {}).get(state)
        if not prompt or not self._subref_dir:
            return None
        base = self._ref_slug_map.get(slug)
        if not base or not Path(base).exists():
            return None
        etype = self._slug_type.get(slug, kind)
        anchor = {
            "personnage": "REFERENCE ONLY for character identity (same face, same clothes). No other people.",
            "lieu": "Same exact location, only the framing, angle and light change here. No people.",
            "objet": "Same exact object, only the framing and angle change.",
        }.get(etype, "REFERENCE ONLY for the same identity. No other people.")
        full = f"{prompt.strip()} {anchor}"
        size = self._image_size()
        key = hashlib.md5(f"{slug}:{state}:{full}:{size}".encode()).hexdigest()[:10]
        self._subref_dir.mkdir(parents=True, exist_ok=True)
        out = self._subref_dir / f"{slug}_{state}_{key}.png"
        if not out.exists():
            logger.info("Sous-référence %s[%s] (%s): génération", slug, state, etype)
            try:
                self.image.compose(
                    prompt=full, ref_images=[base],
                    width=size[0], height=size[1],
                    output_dir=str(self._subref_dir), filename=out.name,
                )
            except Exception as e:
                logger.error("Sous-référence %s[%s] échouée: %s", slug, state, e)
                return base
        return str(out) if out.exists() else base

    def _build_compose_refs(self, frame: str, sec: dict, beat: dict | None = None) -> list[str]:
        """Construire la liste des refs pour composer l'image de depart d un plan.

        Refs : last_frame + sheet_perso etat + sheet_lieu etat (max 4).
        Priorite : frame > personnage > lieu. Si >4 refs, tronque intelligemment.
        """
        refs = [frame]
        # Etat du personnage (de la section ou du beat)
        section_states = sec.get("states", {})
        beat_states = (beat or {}).get("states", {})
        perso_state = beat_states.get("perso") or section_states.get("perso", "")
        lieu_state = beat_states.get("lieu") or section_states.get("lieu", "")

        # Sheet personnage
        for slug in self._section_character_slugs(sec):
            sheet = None
            if perso_state:
                sheet = self._state_sheet(slug, perso_state, "personnage")
            if not sheet:
                sheet = self._ref_slug_map.get(slug)
            if sheet:
                refs.append(sheet)

        # Sheet lieu (si definie dans la section)
        locations = sec.get("locations") or []
        for loc_slug in locations:
            sheet = None
            if lieu_state:
                sheet = self._state_sheet(loc_slug, lieu_state, "lieu")
            if not sheet:
                sheet = self._ref_slug_map.get(loc_slug)
            if sheet:
                refs.append(sheet)

        # Dedupliquer et respecter la limite 4 refs
        refs = list(dict.fromkeys(refs))
        if len(refs) > 4:
            logger.warning("Trop de refs (%d), tronque a 4: %s", len(refs), refs[:4])
            refs = refs[:4]
        return refs

    def _crop_image_169(self, image: str | Path, out_w: int | None = None, out_h: int | None = None) -> str:
        """Recadrer une image de départ au ratio de la variante (par défaut 16:9).

        Crop centre pour un ratio uniforme (continuité de cadre entre héro + frames de
        chaînage). Le ratio cible est celui de `clip.image_size` (ex. 768x1344 = 9:16
        vertical pour les courts IG/TikTok/Shorts), défaut 1344x768 (16:9).
        """
        out_w = out_w or self._image_size()[0]
        out_h = out_h or self._image_size()[1]
        src = Path(image)
        if not src.exists():
            return str(src)
        out = src.with_suffix(f".169.png")
        import subprocess
        try:
            # Lire les dimensions source (ffprobe) pour choisir le bon filtre de crop
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0", str(src)],
                capture_output=True, timeout=30, check=True,
            )
            wh = probe.stdout.decode().strip().strip('"').split(",")
            if len(wh) >= 2:
                src_w, src_h = int(wh[0]), int(wh[1])
            else:
                src_w, src_h = None, None
            vf = self._crop_169_filter(out_w, out_h, src_w, src_h)
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", vf, str(out)],
                check=True, capture_output=True, timeout=60,
            )
            return str(out) if out.exists() else str(src)
        except Exception as e:
            logger.warning("Crop 16:9 image %s échoué: %s", src, e)
            return str(src)

    def _crop_169_filter(self, out_w: int | None = None, out_h: int | None = None,
                         src_w: int | None = None, src_h: int | None = None) -> str:
        """Filtre ffmpeg pour recadrer une source au ratio cible (défaut 16:9, crop centre).

        Ratio cible = `clip.image_size` (ex. 768x1344 = 9:16 vertical pour les courts
        IG/TikTok/Shorts), défaut 1344x768 (16:9). Calcule en Python le crop AVANT le
        scale, fiable pour toute taille source (contrairement aux expressions ffmpeg
        ambiguës type iw:iw*9/16 qui échouent quand la hauteur source est déjà proche).

        - Source plus étroite que le ratio cible : crop la HAUTEUR.
        - Source plus large que le ratio cible : crop la LARGEUR.
        Puis scale à out_w x out_h.
        """
        out_w = out_w or self._image_size()[0]
        out_h = out_h or self._image_size()[1]
        if not (src_w and src_h) or (src_w <= 0 or src_h <= 0):
            # Inconnu → fallback simple (scale+pad, sans crop)
            return (f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
                    f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2")
        ratio = out_w / out_h
        src_ratio = src_w / src_h
        if abs(src_ratio - ratio) < 0.02:
            # Déjà ~16:9 → scale direct, pas de crop
            return f"scale={out_w}:{out_h}"
        if src_ratio > ratio:
            # Source plus large → crop la LARGEUR
            cw = int(src_h * ratio)
            ch = src_h
            x = (src_w - cw) // 2
            return f"crop={cw}:{ch}:{x}:0,scale={out_w}:{out_h}"
        # Source plus étroite → crop la HAUTEUR
        ch = int(src_w / ratio)
        cw = src_w
        y = (src_h - ch) // 2
        return f"crop={cw}:{ch}:0:{y},scale={out_w}:{out_h}"

    def _video_output_ratio(self) -> float:
        """Ratio réel de sortie du serveur vidéo local (704x512 = 1.375:1).

        Le serveur Agnes local ignore la taille demandée et renvoie des plans 704x512.
        Si on lui fournit une ref 16:9 (1.78) il l'écrase en 1.375 → étirement en début
        de séquence. On prédécoupe donc la ref au ratio de sortie AVANT l'i2v, puis le
        montage (_trim_to) recadre en 16:9 final. Évite toute distorsion.
        """
        return 704.0 / 512.0

    def _crop_to_video_ratio(self, image: str | Path, out_w: int = 704, out_h: int = 512) -> str:
        """Recadrer une ref image au ratio de sortie du serveur vidéo (1.375:1, centre)."""
        from pathlib import Path as _P
        src = _P(image)
        if not src.exists():
            return str(src)
        out = src.with_suffix(".vratio.png")
        import subprocess
        ratio = self._video_output_ratio()
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0", str(src)],
                capture_output=True, timeout=30, check=True,
            )
            sw, sh = [int(x) for x in probe.stdout.decode().strip().strip('"').split(",")]
            if sw <= 0 or sh <= 0:
                return str(src)
            if abs(sw / sh - ratio) < 0.02:
                return str(src)
            if sw / sh > ratio:
                cw = int(sh * ratio); x = (sw - cw) // 2
                vf = f"crop={cw}:{sh}:{x}:0,scale={out_w}:{out_h}"
            else:
                ch = int(sw / ratio); y = (sh - ch) // 2
                vf = f"crop={sw}:{ch}:0:{y},scale={out_w}:{out_h}"
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", vf, str(out)],
                check=True, capture_output=True, timeout=60,
            )
            return str(out) if out.exists() else str(src)
        except Exception as e:
            logger.warning("Crop ratio vidéo %s échoué: %s", src, e)
            return str(src)

    def _trim_to(self, video: str, duration: float) -> str:
        """Découper une vidéo de plan à sa durée de segment exacte (ffmpeg).

        Le générateur produit des plans de 5s min ; le dernier segment d'une section peut
        être plus court. On tronque chaque plan à la durée exacte de son segment pour que
        le montage concaténé colle à la timeline audio.
        """
        if duration <= 0:
            return video
        src = Path(video)
        out = src.with_suffix(f".trim{src.suffix}")
        import subprocess
        try:
            # -t duration : tronque à la durée exacte du segment. -an : retire la piste audio
            # des plans (le MP3 est la SEULE source audio, ajoutée au montage final).
            # -vf crop 16:9 : uniformise le cadrage de tous les plans (ré-encodage requis).
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(src),
                 "-t", str(duration), "-an", "-vf", self._crop_169_filter(),
                 "-c:v", "libx264", "-preset", "fast", "-crf", "20", str(out)],
                check=True, capture_output=True, timeout=180,
            )
            return str(out)
        except Exception:
            # fallback : retourner l'original non tronqué
            return str(src)

    # ── Helpers ─────────────────────────────────────────────────

    def _ordered_sections(self) -> list[dict]:
        """Sections triées par start (ordre temporel), relues depuis la timeline."""
        root = PROJECTS_DIR / self._project
        cfg, variant = _load_variant(self._project, self._variant)
        timeline = _load_timeline(self._project, cfg, variant)
        return sorted(timeline.get("sections", []), key=lambda s: float(s.get("start", 0)))

    def _resolve_audio(self, root: Path, cfg: dict) -> Path:
        mix = cfg.get("audio", {}).get("mix", "")
        if mix:
            p = root / mix
            if p.exists():
                return p
        # fallback : premier mp3/wav dans sources
        sources = root / "sources"
        for ext in ("*.mp3", "*.wav"):
            found = sorted(sources.glob(ext))
            if found:
                for f in found:
                    if f.suffix == ".mp3":
                        return f
                return found[0]
        raise FileNotFoundError(f"Aucun fichier audio dans {sources}")

    def _load_character_descriptions(self, root: Path) -> dict[str, str]:
        """Charger le prompt_image (description) de chaque personnage de la bible."""
        out: dict[str, str] = {}
        perso = root / "bible" / "personnage"
        if not perso.exists():
            return out
        for d in sorted(perso.glob("*")):
            fiche = d / "fiche.yaml"
            if not fiche.exists():
                continue
            data = yaml.safe_load(fiche.read_text())
            ver = data.get("versions", {}).get(data.get("active_version", "v1"), {})
            desc = ver.get("prompt_image") or ver.get("fallback") or data.get("name", "")
            out[d.name] = desc
        return out

    def _load_style_guide(self, root: Path, variant: dict) -> str:
        rel = variant.get("style_guide", "")
        if rel:
            # Résolu depuis la racine preprod du projet (ex: prototypes/style_guide_v3.md)
            p = root / "preprod" / rel
            if not p.exists():
                p = root / rel
            if p.exists():
                return p.read_text().strip()
        # fallback : tout style_guide*.md de la variante dans le dossier clip
        for cand in sorted(_clip_dir(self._project).glob(f"{self._variant}_*.md")):
            return cand.read_text().strip()
        return ""

    def _variant_refs(self, root: Path, variant: dict) -> list[str]:
        """Uploader les sheets bible de la variante comme références.

        `refs_bible` accepte :
          - `personnage` / `lieu` / `objet` : toutes les sheets de ce type.
          - `personnage:le-pere` : UNIQUEMENT la sheet de ce slug (contrôle fin : on peut
            référencer le père sans jamais référencer les enfants).
        """
        refs_bible = variant.get("refs_bible", [])
        if not refs_bible:
            self._ref_urls = []
            return []
        paths = []
        bible = root / "bible"
        slug_map: dict[str, str] = {}
        for spec in refs_bible:
            spec = str(spec)
            if ":" in spec:
                etype, slug = spec.split(":", 1)
                d = bible / etype / slug
                sheet = d / "sheet_v1.png"
                if sheet.exists():
                    paths.append(str(sheet))
                    slug_map[slug] = str(sheet)
            else:
                etype = spec
                for d in sorted((bible / etype).glob("*")):
                    sheet = d / "sheet_v1.png"
                    if sheet.exists():
                        paths.append(str(sheet))
                        slug_map[d.name] = str(sheet)
        self._ref_slug_map = slug_map
        self._ref_urls = [upload_to_tmpfiles(p, expire=172800) for p in paths]
        return self._ref_urls

    def _last_refs(self) -> list[str]:
        return getattr(self, "_ref_urls", [])

    def _parse_canvas(self, size: str | None) -> tuple[int, int] | None:
        """Parser un `image_size` (ex. "768x1344") → (w, h). None si invalide."""
        if not size:
            return None
        try:
            w, h = str(size).lower().split("x")
            return int(w), int(h)
        except Exception:
            return None

    def _image_size(self) -> tuple[int, int]:
        if self._canvas is not None:
            return self._canvas
        size = get_clip_config().get("image_size", "1344x768")
        try:
            w, h = size.lower().split("x")
            return int(w), int(h)
        except Exception:
            return 1344, 768

    def _video_service(self, cfg: dict) -> VideoService:
        """VideoService pour le clip, depuis la config video du clip (pas la globale).

        Si la config clip définit `video`, on l'utilise ; sinon fallback sur la config
        service video globale. Le provider actif (agnes direct par défaut) gère le i2v.
        """
        vcfg = cfg.get("video")
        if vcfg:
            providers = vcfg.get("providers", {})
            active = vcfg.get("active", list(providers.keys())[0] if providers else "agnes")
            return VideoService(config=providers.get(active, {}))
        return VideoService()

    def _fallback_video_services(self) -> list[tuple[str, VideoService]]:
        """Providers vidéo ordonnés pour le fallback (actif d'abord, puis backup).

        Retourne [(nom, service)] : le provider actif de la config clip video + au plus
        un backup (autre provider dispo). Permet à _gen_video de basculer automatiquement
        si le provider principal échoue (réseau/402/503).
        """
        cfg, _ = _load_variant(self._project, self._variant)
        vcfg = cfg.get("video")
        if not vcfg:
            return [("video", self.video)]
        providers = vcfg.get("providers", {})
        if not providers:
            return [("video", self.video)]
        active = vcfg.get("active") or list(providers.keys())[0]
        named: list[tuple[str, VideoService]] = []
        # Provider actif
        if active in providers:
            named.append((active, VideoService(config=providers[active])))
        # Un backup = un autre provider
        for name, pconf in providers.items():
            if name != active:
                named.append((name, VideoService(config=pconf)))
                break
        if not named:
            named.append(("video", self.video))
        return named

    # ── Analyse musicale ────────────────────────────────────────

    def analyze_clip_audio(self, audio_path: str | Path) -> dict:
        """Analyser le MP3 du clip : onsets, RMS, BPM, sections.

        Retourne l'analyse complète et la sauvegarde dans preprod/clip/analysis.json.
        Utilisée pour caler les beats sur la musique réelle.
        """
        audio_path = Path(audio_path)
        analysis = analyze_audio(audio_path)
        # Sauvegarder à côté du MP3
        out_path = audio_path.parent / "analysis.json"
        save_analysis(analysis, out_path)
        return analysis

    def auto_align_beats(self, analysis: dict, timeline: dict) -> list[dict]:
        """Aligner les beats du timeline sur l'analyse musicale.

        Pour chaque section, enrichit les beats avec les onsets musicaux réels.
        Retourne les beats corrigés avec timestamps musicaux.
        """
        sections = timeline.get("sections", [])
        return beats_to_timeline(analysis, sections)

    # ── Vérification shot par shot ──────────────────────────────

    def verify_plan_result(self, plan_video: str | Path, sec: dict,
                           beat: str = "", camera: str = "") -> dict:
        """Vérifier un plan généré via vision LLM.

        Vérifie l'identité, la conformité au beat, la lumière, les éléments interdits.
        Retourne un score 0-1 et des suggestions.
        """
        try:
            from pipeline.services.vision import VisionAnalysisService
            vision = VisionAnalysisService()
        except Exception:
            vision = None

        section_light = sec.get("light", "")
        section_emotion = sec.get("emotion", "")
        char_desc = self._section_character_prompts(sec)

        return verify_plan(
            video_path=plan_video,
            beat=beat or sec.get("staging", ""),
            section_light=section_light,
            section_emotion=section_emotion,
            character_desc=char_desc,
            location_desc="",
            vision_service=vision,
        )

    def verify_all_plans(self, plans: list[dict]) -> dict:
        """Vérifier tous les plans d'un clip et retourner un rapport.

        Returns:
            {
                "total": int,
                "passed": int,
                "failed": int,
                "results": [{plan_id: verification_result}],
                "to_regenerate": [plan_ids],
            }
        """
        results = []
        to_regenerate = []

        for plan in plans:
            plan_id = plan.get("id", "")
            video = plan.get("video", "")
            if not video:
                continue

            sec_id = plan.get("section", "")
            beat = plan.get("beat", "")
            # Reconstituer le dict sec minimal pour la vérification
            sec = {"light": "", "emotion": "", "staging": beat}

            verification = self.verify_plan_result(video, sec, beat)
            results.append({"plan_id": plan_id, **verification})

            if should_regenerate(verification):
                to_regenerate.append(plan_id)

        return {
            "total": len(results),
            "passed": len(results) - len(to_regenerate),
            "failed": len(to_regenerate),
            "results": results,
            "to_regenerate": to_regenerate,
        }

    # ── Métriques de diversité ──────────────────────────────────

    def check_diversity(self, plans: list[dict]) -> dict:
        """Analyser la diversité visuelle de tous les plans.

        Calcule les embeddings CLIP et détecte les doublons.
        """
        video_paths = {p["id"]: p.get("video", "") for p in plans if p.get("video")}
        if len(video_paths) < 2:
            return {"diversity_score": 1.0, "duplicates": []}

        embeddings = compute_clip_embeddings(list(video_paths.values()))
        emb_dict = {pid: emb for pid, emb in zip(video_paths.keys(), embeddings) if emb}

        report = analyze_diversity(emb_dict)

        # Sauvegarder le rapport
        if self._vdir:
            save_diversity_report(report, self._vdir / "diversity_report.json")

        return report

    # ── Color grading ───────────────────────────────────────────

    def color_grade(self, input_path: str | Path, output_path: str | Path,
                    sections: list[dict] = None, arc: str = None,
                    mode: str = "section") -> str:
        """Appliquer le color grading sur un clip assemblé.

        Modes : "section" (palette par section), "arc" (palette par arc), "global" (look uniforme).
        """
        return color_grade_clip(
            input_path=input_path,
            output_path=output_path,
            palette=mode,
            sections=sections,
            arc=arc,
        )

    # ── Génération multi-passe ──────────────────────────────────

    def multipass_plan_video(self, sec: dict, beat: str, camera: str,
                             variant: dict, out_dir: Path, plan_id: str,
                             ref_image: str, force_hq: bool = False) -> dict:
        """Pipeline multi-passe pour un plan : preview → validation → HQ."""
        try:
            from pipeline.services.vision import VisionAnalysisService
            vision = VisionAnalysisService()
        except Exception:
            vision = None

        provider = self.video.config.get("provider", "default")
        prompt = self._build_video_prompt(sec, beat, camera)
        prompt, _ = self._synthesize_prompt(prompt, provider)
        seed = int(variant.get("seed", 42)) + hash(plan_id) % 10000

        return multipass_plan(
            video_service=self.video,
            prompt=prompt,
            ref_image=ref_image,
            seed=seed,
            plan_id=plan_id,
            output_dir=out_dir,
            preview_size="512x288",
            hq_size="1024x576",
            preview_duration=3,
            hq_duration=5,
            vision_service=vision,
            beat=beat,
            mode="auto",
            force_hq=force_hq,
        )

    # ── A/B Testing ─────────────────────────────────────────────

    def ab_test_plan_video(self, sec: dict, beat: str, camera: str,
                           variant: dict, out_dir: Path, plan_id: str,
                           ref_image: str, n_variants: int = 3,
                           mode: str = "suggest") -> dict | None:
        """Pipeline A/B testing pour un plan : générer N variantes, analyser, choisir."""
        try:
            from pipeline.services.vision import VisionAnalysisService
            vision = VisionAnalysisService()
        except Exception:
            vision = None

        provider = self.video.config.get("provider", "default")
        prompt = self._build_video_prompt(sec, beat, camera)
        prompt, _ = self._synthesize_prompt(prompt, provider)
        seed = int(variant.get("seed", 42)) + hash(plan_id) % 10000

        return ab_test_plan(
            video_service=self.video,
            prompt=prompt,
            ref_image=ref_image,
            seed=seed,
            plan_id=plan_id,
            beat=beat,
            n_variants=n_variants,
            output_dir=out_dir,
            vision_service=vision,
            mode=mode,
        )

    def _build_video_prompt(self, sec: dict, beat: str = "",
                            camera: str = "") -> str:
        """Construire le prompt vidéo pour un plan donné."""
        if not camera:
            camera = self._plan_camera(0)
        if beat:
            prompt = (
                f"{sec.get('light', '')}, {sec.get('emotion', '')}. "
                f"{beat} {camera} Plan continu, léger mouvement, grain cinéma."
            )
        else:
            prompt = (
                f"Caméra à l'épaule, {sec.get('light', '')}, {sec.get('emotion', '')}. "
                f"{sec.get('staging', '')}. {camera} Plan continu, léger mouvement, grain cinéma."
            )
        return prompt

    # ── Montage avec color grading ──────────────────────────────

    def _assemble_with_colorgrading(self, plans: list[dict], audio: Path,
                                    outdir: Path, variant_id: str,
                                    sections: list[dict] = None,
                                    enable_colorgrading: bool = True) -> str:
        """Assembler le clip avec color grading optionnel."""
        # Assembler d'abord
        final = self._assemble(plans, audio, outdir, variant_id)

        if enable_colorgrading and final:
            graded_path = outdir / f"clip_{variant_id}_graded.mp4"
            result = color_grade_clip(
                input_path=final,
                output_path=graded_path,
                palette="section",
                sections=sections,
            )
            if result != str(final):
                logger.info("Color grading appliqué: %s", graded_path)
                return result

        return final
