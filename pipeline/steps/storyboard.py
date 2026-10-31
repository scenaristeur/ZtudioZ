from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

import yaml

from pipeline import slugify
from pipeline.config import get_production_config
from pipeline.reporter import step_item, step_warn
from pipeline.services.llm import LLMService
from pipeline.steps.visualize import _active_prompt, _location_aliases

logger = logging.getLogger(__name__)

PROJECTS_DIR = Path(__file__).resolve().parent.parent.parent / "projets"

_REPAIR_SYSTEM_PROMPT = """Tu es un assistant de direction photo. On te donne la description visuelle
d'un plan de cinéma qui contient des éléments interdits par la bible du film (tenue
inventée, technologie futuriste, hologramme...). Tu la RÉÉCRIS pour supprimer ces
éléments, sans rien changer d'autre.

RÈGLES ABSOLUES :
- Tu corriges la RÉALISATION VISUELLE uniquement. L'action, la gestuelle, les
  personnages présents, leur position, leur relation et l'instant narratif sont
  conservés à l'identique.
- Tu n'ajoutes ni personnage, ni dialogue, ni action, ni lieu.
- Tu appliques les attributs de la bible (tenue, couleur, âge, accessoires) : la
  description doit correspondre à la fiche, pas à ton imagination.
- La bible est prioritaire sur la description d'origine en cas de conflit.
- Français naturel et fluide. Aucun mot vide, aucun syntagme tronqué (« vêtue d'une
  veste noire à et d'un » est interdit), aucune redite. Si une information devient
  fausse après correction, supprime-la plutôt que de la laisser bancale.
- N'ÉCRIS JAMAIS les termes interdits, même pour dire qu'ils sont absents. Une phrase
  du type « aucun néon, hologramme ni élément futuriste n'est visible » est INTERDITE :
  elle nomme ce qu'elle prétend nier. Décris directement ce qui est présent, ou supprime.

Réponds UNIQUEMENT avec un JSON: {"description": str}
"""

_PLANS_SYSTEM_PROMPT = """Tu es un storyboarder. À partir du découpage de la scène (sa TIMELINE
d'événements ordonnés : actions, dialogues, transitions), tu détermines la LISTE DES
PLANS (cases) de la scène. Tu ne décris pas le contenu en détail ici : tu fixes les
BORNES de chaque plan sur la timeline (où il commence et où il finit).

Pour chaque plan, renvoie :
- number: numéro du plan (1, 2, 3...)
- start_index: index de l'événement de la timeline où le plan COMMENCE (inclusif)
- end_index: index de l'événement où le plan FINIT (inclusif)
- type: type de plan (PLAN LARGE, PLAN MOYEN, GROS PLAN, PLAN RAPPROCHÉ, PLAN EN COUPLE,
  PLAN SUR-ÉPAULE, PLAN DÉTAIL...)
- camera: mouvement (fixe, travelling avant/arrière/latéral, panoramique, recadrage...)

RÈGLES :
- Les plans se suivent SANS chevauchement et SANS trou : end_index du plan N = start_index
  du plan N+1 - 1. L'ensemble couvre TOUTE la timeline, du premier au dernier événement.
- Chaque plan est une case de storyboard : un fragment visuel/action cohérent. Plusieurs
  micro-actions d'un même bloc (même "groupe" dans la timeline) peuvent être réparties sur
  un ou plusieurs plans selon la logique d'action.
- Un changement de lieu (INT./EXT.), de jour, ou un FONDU/CUT TO marque généralement la
  FIN d'un plan (l'événement transition appartient au plan qui se termine).
- Choisis un nombre de plans raisonnable et justifié par l'action (pas 60 plans pour une
  scène, mais pas 1 seul pour tout).
- Le nombre de plans est piloté par l'ACTION, pas par le nombre de personnes présentes : une
  foule est un fond, pas une raison de multiplier les plans. Vise la dramaticité.

Réponds UNIQUEMENT avec un JSON: {"plans": [{"number": int, "start_index": int,
"end_index": int, "type": str, "camera": str}]}
"""

_PLAN_SYSTEM_PROMPT = """Tu es un storyboarder. Tu décris UN plan (une case de storyboard) d'une scène de
film, à partir de sa portion de TIMELINE (une liste ordonnée d'événements : actions,
dialogues, transitions). Cette portion commence à l'index X de la timeline de la scène.

Tu renvoies pour ce plan :
- number: numéro du plan
- type: type de plan (donné)
- camera: mouvement (donné)
- description: description visuelle détaillée (personnages, position, décor, lumière,
  action), fidèle aux événements de la portion.
- duration_seconds: durée estimée du plan en secondes, cohérente avec les timestamps
  des événements (une réplique = ~2,5 mots/seconde ; une action courte = 2-3 s).
- prompt_image: prompt détaillé pour générateur d'image (style cinématographique),
  reflétant l'action et la dynamique de la portion.
- timeline: la liste ORDONNÉE des événements de la portion, chacun repris tel quel
  ({type, speaker?, text}) PLUS un timestamp dans la scène :
  {"start": <secondes depuis le début de la scène>, "duration": <secondes>}.

RÈGLES :
- Le texte des dialogues et des actions est repris TEL QUEL (pas de reformulation, pas
  de troncature). Chaque événement de la portion doit apparaître dans la timeline du plan.
- start et duration cohérents entre eux et avec duration_seconds ; start croissants dans
  l'ordre des événements.
- REGISTRE VISUEL (prioritaire sur le vocabulaire du script) : le bloc « CONTINUITÉ VISUELLE »
  fait foi. Le script est la vérité NARRATIVE (qui parle, quoi, quand) mais il est écrit dans
  le vocabulaire de son époque et parfois de son auteur. Le décor, la lumière et les costumes
  doivent être rendus dans le registre imposé par ce bloc. Si le script emploie un mot
  « high-tech », « géant », « néon », « hologramme », « futuriste », « cybernétique », tu le
  RENDES selon la description de lieu et le style-guide (ex. un « écran géant » = un grand
  écran plat de projection unique ; « lumière bleue » = la lumière froide que l'écran projette
  sur les sièges). N'introduis JAMAIS un élément que le style-guide ou la fiche de lieu
  interdisent explicitement. En cas de doute, la fiche bible l'emporte.
- Les personnages présents portent EXACTEMENT la tenue décrite dans le bloc « CONTINUITÉ
  VISUELLE » (veste, couleur, accessoires, absence de bijou notable). Ne jamais inventer une
  tenue différente, ni un accessoire qui n'est pas listé.
- Les personnages non listés dans le bloc « CONTINUITÉ VISUELLE » n'ont pas d'existence
  visuelle : s'ils ne sont qu'un fond de foule mentionné, aucun individu ne doit être
  singled out ni rendus reconnaissable.

Réponds UNIQUEMENT avec un JSON: {"shot": {number, type, camera, description,
duration_seconds, prompt_image, timeline: [...]}}
"""


def _guard_config(project: str = "") -> dict:
    """Garde-fou visuel du storyboard, surchargeable par projet.

    Les valeurs par défaut vivent dans `production.storyboard` (config globale). Un
    projet peut les surcharger via `bible/storyboard_guard.yaml` — c'est ce fichier qui
    active le garde-fou, la config globale restant neutre pour ne pas casser un projet
    de science-fiction légitime.
    """
    guard = dict(get_production_config().get("storyboard", {}) or {})
    if project:
        path = PROJECTS_DIR / project / "bible" / "storyboard_guard.yaml"
        if path.exists():
            try:
                guard.update(yaml.safe_load(path.read_text()) or {})
            except yaml.YAMLError as e:
                logger.warning("storyboard_guard.yaml illisible (%s), garde-fou ignoré", e)
    return guard


def _sanitize(text: str, guard: dict) -> str:
    """Neutralise les termes visuels interdits par la bible.

    Le LLM écrit les plans avec le vocabulaire du script, qui dérive vers des
    néons/hologrammes même quand la bible l'interdit explicitement. Le remplacement est
    déterministe : on ne fait pas confiance au LLM pour tenir le registre.
    """
    if not guard.get("enabled", True):
        return text
    out = text
    # Tri par longueur décroissante : « holographic interface » avant « holographic ».
    for term, replacement in sorted((guard.get("banned_terms") or {}).items(),
                                    key=lambda kv: -len(kv[0])):
        if not term:
            continue
        if term.startswith("re:"):
            # Motif : permet de couvrir les flexions (holographique/holographiques)
            # sans énumérer chaque forme.
            pattern = term[3:]
        else:
            pattern = re.escape(term)
        try:
            out = re.sub(pattern, replacement or " ", out, flags=re.IGNORECASE)
        except re.error as e:
            logger.warning("banned_terms motif invalide %r : %s", term, e)

    return _tidy(out)


# Artifacts laissés par la suppression d'un terme au milieu d'un syntagme nominal
# (« veste noire à coutures argentées » -> « veste noire à et d'un »).
_TIDY_RULES = [
    (r"\b(?:de|d'|à|en|et|avec|par|sur)\s+(?=[,.;:])", ""),
    (r"\bà\s+et\b", "et"),
    (r"\bet\s+d'un\b", "et"),
    (r"\bd'\s*,", ","),
    (r"\(\s*\)", ""),
    (r"\s+([,.;:])", r"\1"),
    (r"(?:,\s*){2,}", ", "),
    (r"\s*,\s*(?=,)", ""),
    (r"\s{2,}", " "),
    (r"-\s+", "-"),
]


def _tidy(text: str) -> str:
    """Répare la ponctuation et les débris syntaxiques après substitution."""
    out = text
    for pattern, repl in _TIDY_RULES:
        out = re.sub(pattern, repl, out)
    return out.strip(" ,;:-")


# Phrases méta que le LLM écrit quand on lui demande de supprimer des termes : il
# parrotte la liste d'interdits en négation (« aucun néon, hologramme ou élément
# futuriste n'est visible »). Elles ne portent aucune information visuelle : on les retire.
_SELF_NEGATION = re.compile(
    r"[^.!?\n]*\b(?:aucun|aucune|pas\s+de|sans|ni|jamais)\b[^.!?\n]*"
    r"\b(?:néons?|neons?|hologrammes?|holographiques?|futuristes?|"
    r"cyber\w*|sci-?fi|technolog\w*)\b[^.!?\n]*[.!?]?",
    re.IGNORECASE,
)


def _strip_self_negation(text: str) -> str:
    """Retire les phrases qui se contentent de nier la présence d'éléments interdits."""
    if not text:
        return text
    out = _SELF_NEGATION.sub(" ", text)
    out = re.sub(r"\s{2,}", " ", out)
    return out.strip()


def _violations(text: str, terms: dict) -> list[str]:
    """Termes interdits présents dans un texte (détection seule, sans mutation)."""
    found = []
    for term in terms or {}:
        if not term:
            continue
        pattern = term[3:] if term.startswith("re:") else re.escape(term)
        try:
            if re.search(pattern, text, re.IGNORECASE):
                found.append(term[3:] if term.startswith("re:") else term)
        except re.error as e:
            logger.warning("banned_terms motif invalide %r : %s", term, e)
    return found


def _entity_interdits(bible: Path, slug: str) -> dict:
    """Interdits déclarés dans la fiche bible d'une entité (personnage, objet, lieu)."""
    for etype in ("personnage", "objet", "lieu"):
        fiche = bible / etype / slug / "fiche.yaml"
        if not fiche.exists():
            continue
        try:
            data = yaml.safe_load(fiche.read_text()) or {}
        except yaml.YAMLError:
            return {}
        ver = (data.get("versions") or {}).get(data.get("active_version", "v1")) or {}
        return ver.get("interdits") or {}
    return {}


def _characters_in(text: str, characters: list[str]) -> list[str]:
    """Slugs de bible dont le nom est cité dans un texte.

    Les personnages sans fiche sont ignorés (mentions de foule : « SUIVEURS », «
    FEMME (filme) »…) : ils n'ont pas d'existence visuelle.
    """
    low = text.lower()
    found = []
    for name in characters or []:
        slug = slugify(str(name))
        if not slug:
            continue
        for token in (slug, str(name).lower()):
            if re.search(rf"\b{re.escape(token)}\b", low):
                found.append(slug)
                break
    return found


def _shot_terms(shot: dict, bible: Path | None, named: set, guard: dict,
                text: str | None = None) -> dict:
    """Vocabulaire interdit applicable à un plan : générique + interdits de la bible.

    `text` doit être le texte sur lequel la détection va porter : l'anaphore se calcule
    sur les clauses de ce texte. Utiliser l'ancienne description fausserait la détection
    des violations introduites par la réparation.

    Les `interdits` d'une fiche ne valent que pour les phrases qui parlent de ce
    personnage : sinon « veste noire » (tenue de Maya) serait signalé dès qu'Antoine ou
    Claire sont cités dans la même description.
    """
    generic = dict(guard.get("banned_terms") or {})
    per_character: dict[str, dict] = {}
    for slug in sorted(named):
        interdits = _entity_interdits(bible, slug) if bible else {}
        if interdits:
            per_character[slug] = interdits
    if not per_character:
        return {"all": generic, "per_character": {}}

    desc = text if text is not None else shot.get("description", "")
    clauses = [c for c in re.split(r"(?<=[.;!?])\s+", desc) if c.strip()]
    effective: dict = {}
    for slug, interdits in per_character.items():
        # Une clause qui ne répète pas le nom continue la phrase précédente : sans
        # anaphore, « elle avance vers le petit podium » n'attribuerait jamais le
        # podium à Maya, qui n'est citée qu'une fois.
        current = slug
        for clause in clauses:
            mentioned = [s for s in per_character
                         if re.search(rf"\b{re.escape(s)}\b", clause, re.IGNORECASE)]
            if mentioned:
                current = mentioned[0]
            if current == slug:
                effective.update(interdits)
    if not effective:
        # Aucun segment n'attribue la tenue à un personnage précis : on n'applique rien
        # de spécifique, seul le vocabulaire générique reste actif.
        return {"all": generic, "per_character": {}}
    return {"all": generic, "per_character": effective}


def _detect_violations(text: str, terms: dict) -> list[str]:
    """Termes interdits détectés, sans mutation (vocabulaire générique + par personnage)."""
    return _violations(text, terms.get("all") or {}) + \
        _violations(text, terms.get("per_character") or {})


def _apply_visual_guard(shot: dict, project: str, scene: dict) -> dict:
    """Garde-fou visuel d'un plan : répare la description, nettoie et ré-ancre le prompt.

    La `description` est de la PROSE : la muter par substitution aveugle la dégrade
    ("vêtue d'une veste noire à et d'un"). Elle est donc seulement DÉTECTÉE ici, puis
    réécrite par le LLM (`_repair_description`). Le `prompt_image` est un espace de
    mots-clés : la substitution y est sûre et sert de filet de sécurité.
    """
    guard = _guard_config(project)
    if not guard.get("enabled", True):
        return shot

    bible = PROJECTS_DIR / project / "bible" if project else None
    names = scene.get("characters") or []
    named = set(_characters_in(shot.get("description", ""), names)) | \
        set(_characters_in(shot.get("prompt_image", ""), names))

    terms = _shot_terms(shot, bible, named, guard)
    shot["visual_guard"] = {
        "characters": sorted(named),
        "violations": _detect_violations(shot.get("description", ""), terms),
    }

    if isinstance(shot.get("prompt_image"), str):
        flat = {"enabled": True, "banned_terms": {**(terms.get("all") or {}),
                                                   **(terms.get("per_character") or {})}}
        shot["prompt_image"] = _sanitize(shot["prompt_image"], flat)

    if guard.get("anchor_to_bible", True) and project:
        blocks = []
        style = _active_prompt(bible / "ambiance" / "style-guide.yaml")
        if style:
            blocks.append(style)
        loc_slug = _location_aliases(project, scene.get("location", ""))
        if loc_slug:
            loc_prompt = _active_prompt(bible / "lieu" / loc_slug / "fiche.yaml")
            if loc_prompt:
                blocks.append(loc_prompt)
        for slug in sorted(named):
            costume = ""
            for etype in ("personnage", "objet"):
                f = bible / etype / slug / "fiche.yaml"
                if f.exists():
                    costume = _active_prompt(f)
                    break
            if costume:
                blocks.append(costume)
        if blocks:
            shot["prompt_image"] = " ; ".join(blocks) + " ; " + shot.get("prompt_image", "")

    suffix = (guard.get("negative_suffix") or "").strip()
    if suffix:
        current = shot.get("prompt_image", "")
        if suffix.lower() not in current.lower():
            shot["prompt_image"] = f"{current} ; {suffix}"
    return shot


def _event_to_text(e: dict) -> str:
    """Formate un événement de timeline pour le prompt storyboard."""
    t = e.get("type", "")
    if t == "dialogue":
        return f"{e.get('index', '?')}. DIALOGUE [{e.get('speaker', '')}]: {e.get('text', '')}"
    if t == "action":
        return f"{e.get('index', '?')}. ACTION{'(bloc ' + str(e.get('group')) + '/' + str(e.get('group_total')) + ')' if e.get('group') is not None else ''}: {e.get('text', '')}"
    if t == "transition":
        return f"{e.get('index', '?')}. TRANSITION: {e.get('text', '')}"
    return str(e)


def resolve_refs(project: str, scene_num: int, shots: list[dict]) -> list[dict]:
    """Resolve reference sheet slugs for each shot and add 'refs' field."""
    from pipeline import slugify
    from pipeline.steps.visualize import _ensure_sheets, _get_scene_data

    scene_data, _ = _get_scene_data(project, scene_num)
    if not scene_data:
        return shots

    for shot in shots:
        if "refs" not in shot or not shot["refs"]:
            shot["refs"] = _ensure_sheets(project, scene_data)
    return shots


class StoryboardStep:
    """Storyboard textuel : LLM → découpage en plans (sans génération d'images)."""

    def __init__(self, verbose: bool = False):
        self.llm = LLMService()
        self.verbose = verbose

    def _chat(self, system: str, user: str, schema: dict) -> dict:
        """Appel LLM avec retry sur rate limit 429 et espacement configuré."""
        max_attempts = 5
        delay = float(get_production_config().get("storyboard_delay", 0) or 0)
        for attempt in range(max_attempts):
            try:
                data = self.llm.chat_structured(system=system, user=user, response_schema=schema)
                if delay:
                    time.sleep(delay)
                return data or {}
            except Exception as e:
                msg = str(e)
                if "429" in msg or "RateLimit" in msg or "rate limit" in msg.lower():
                    wait = min(10 * (2 ** attempt), 60)
                    logger.warning("Rate limit LLM (%s), retry dans %ss...", msg[-80:], wait)
                    time.sleep(wait)
                    continue
                logger.error("Échec LLM storyboard: %s", e)
                return {}
        return {}

    def _scene_header(self, scene: dict, scene_num: int) -> str:
        return (
            f"Scène {scene_num}: {scene.get('title', '')}\n"
            f"Lieu: {scene.get('location', '')}\n"
            f"Ambiance: {scene.get('ambiance', '')}\n"
            f"Intention: {scene.get('intention', '')}\n"
            f"Description: {scene.get('description', '')}"
        )

    def _visual_context(self, project: str, scene: dict) -> str:
        """Bloc CONTINUITÉ VISUELLE : style-guide + fiche de lieu + tenues bible.

        Sans ce bloc le storyboard se contente du vocabulaire du script et dérive vers le
        cyberpunk même quand la bible est contemporaine.
        """
        if not project:
            return ""
        bible = PROJECTS_DIR / project / "bible"
        lines = []

        style = _active_prompt(bible / "ambiance" / "style-guide.yaml")
        if style:
            lines.append(f"STYLE-GUIDE DU FILM (impose le rendu de TOUS les plans) :\n{style}")

        loc_slug = _location_aliases(project, scene.get("location", ""))
        if loc_slug:
            loc_prompt = _active_prompt(bible / "lieu" / loc_slug / "fiche.yaml")
            if loc_prompt:
                lines.append(
                    f"LIEU DE LA SCÈNE — DESCRIPTION DE RÉFÉRENCE (fiche bible « {loc_slug} », "
                    f"c'est l'image de référence générée, elle fait foi) :\n{loc_prompt}"
                )

        chars = scene.get("characters") or []
        costume_lines = []
        for c in chars:
            slug = slugify(str(c))
            for etype in ("personnage", "objet"):
                fiche = bible / etype / slug / "fiche.yaml"
                if not fiche.exists():
                    continue
                try:
                    data = yaml.safe_load(fiche.read_text()) or {}
                except yaml.YAMLError:
                    break
                ver = (data.get("versions") or {}).get(data.get("active_version", "v1")) or {}
                desc = "\n".join(f"  - {a}" for a in (ver.get("attributs") or []))
                prompt = ver.get("prompt_image", "") or ""
                costume_lines.append(
                    f"{c} (fiche « {slug} ») :\n{desc}\n  prompt_image : {prompt}"
                )
                break
        if costume_lines:
            lines.append("TENUES ET IDENTITÉ DES PERSONNAGES (bible, source de vérité) :\n"
                         + "\n".join(costume_lines))
        else:
            lines.append(
                "AUCUNE FICHE PERSONNAGE pour cette scène : ne nomme ni ne singularise aucun "
                "individu, la foule reste un fond indifférencié."
            )

        return "\n\n".join(lines)

    def _prompt_user(self, project: str, scene: dict, scene_num: int, body: str) -> str:
        """Assemble l'en-tête de scène + le bloc de continuité visuelle + le corps."""
        ctx = self._visual_context(project, scene)
        parts = [self._scene_header(scene, scene_num)]
        if ctx:
            parts.append("CONTINUITÉ VISUELLE (bible — fait foi sur le rendu) :\n" + ctx)
        parts.append(body)
        return "\n\n".join(parts)

    def _plan_bounds(self, scene: dict, scene_num: int, project: str = "") -> list[dict]:
        """Étape A : déterminer la liste des plans (bornes sur la timeline)."""
        timeline = scene.get("timeline") or []
        timeline_text = "\n".join(_event_to_text(e) for e in timeline) or "(timeline vide)"
        user = self._prompt_user(
            project, scene, scene_num,
            f"TIMELINE DE LA SCÈNE (chaque événement est indexé ; découpe-la en plans) :\n"
            f"{timeline_text}",
        )
        data = self._chat(_PLANS_SYSTEM_PROMPT, user, {"type": "object"})
        plans = data.get("plans", []) if isinstance(data, dict) else []
        # tri et numérotation propre
        plans.sort(key=lambda p: int(p.get("start_index", 0)))
        for i, p in enumerate(plans, 1):
            p["number"] = i
        return plans

    def _describe_plan(self, scene: dict, scene_num: int, plan: dict,
                       timeline: list[dict], project: str = "") -> dict:
        """Étape B : décrire UN plan (type/caméra/description/timeline+timestamps)."""
        start = int(plan.get("start_index", 1))
        end = int(plan.get("end_index", start))
        portion = timeline[start - 1:end]
        portion_text = "\n".join(_event_to_text(e) for e in portion) or "(portion vide)"
        user = self._prompt_user(
            project, scene, scene_num,
            f"PLAN {plan.get('number')} — type: {plan.get('type')}, camera: {plan.get('camera')}\n"
            f"Cette portion de timeline commence à l'index {start} de la scène.\n"
            f"PORTION DE TIMELINE DU PLAN :\n{portion_text}",
        )
        data = self._chat(_PLAN_SYSTEM_PROMPT, user, {"type": "object"})
        shot = data.get("shot", {}) if isinstance(data, dict) else {}
        if not shot:
            shot = {}
        shot.setdefault("number", int(plan.get("number", 1)))
        shot.setdefault("type", plan.get("type", "PLAN LARGE"))
        shot.setdefault("camera", plan.get("camera", "fixe"))
        shot.setdefault("image_path", "")
        shot["start_index"] = start
        shot["end_index"] = end
        return shot

    def _repair_description(self, shot: dict, project: str, scene: dict) -> dict:
        """Réécrit la description d'un plan qui viole le registre de la bible.

        La détection est déterministe (les `interdits` de la bible + le vocabulaire
        générique), la réparation est confiée au LLM : réécrire de la prose par
        substitution donne des phrases agrammaticales. Le sens, l'action et la
        distribution des personnages doivent être conservés — seule la réalisation
        change.
        """
        guard = _guard_config(project)
        if not guard.get("llm_repair", True):
            return shot
        violations = (shot.get("visual_guard") or {}).get("violations") or []
        if not violations:
            return shot

        bible = PROJECTS_DIR / project / "bible"
        named = (shot.get("visual_guard") or {}).get("characters") or []
        lines = []
        style = _active_prompt(bible / "ambiance" / "style-guide.yaml")
        if style:
            lines.append(f"STYLE-GUIDE DU FILM :\n{style}")
        loc_slug = _location_aliases(project, scene.get("location", ""))
        if loc_slug:
            loc_prompt = _active_prompt(bible / "lieu" / loc_slug / "fiche.yaml")
            if loc_prompt:
                lines.append(f"LIEU (fiche bible, image de référence) :\n{loc_prompt}")
        for slug in named:
            for etype in ("personnage", "objet"):
                f = bible / etype / slug / "fiche.yaml"
                if f.exists():
                    attrs = []
                    try:
                        data = yaml.safe_load(f.read_text()) or {}
                        ver = (data.get("versions") or {}).get(data.get("active_version", "v1")) or {}
                    except yaml.YAMLError:
                        ver = {}
                    attrs = ver.get("attributs") or []
                    if attrs:
                        lines.append(f"{slug} (tenue et identité, bible) :\n"
                                     + "\n".join(f"  - {a}" for a in attrs))
                    break

        max_passes = int(guard.get("max_repair_passes", 2) or 0)
        current = shot.get("description", "")
        passes = 0
        while True:
            user = (
                "CONTINUITÉ VISUELLE (bible — fait foi) :\n" + "\n\n".join(lines) + "\n\n"
                f"PLAN {shot.get('number')} — {shot.get('type')}, caméra : {shot.get('camera')}\n\n"
                f"DESCRIPTION ACTUELLE (à corriger) :\n{current}\n\n"
                "ÉLÉMENTS INTERDITS détectés dans cette description :\n"
                + "\n".join(f"  - {v}" for v in violations) + "\n\n"
                "RÉÉCRIS la description pour supprimer ces éléments et respecter strictement la "
                "bible. CONTRAINTE : tu ne corriges QUE la réalisation visuelle. Tu conserves "
                "l'action, la gestuelle, les personnages présents, leur position, leur relation "
                "et l'instant de la scène. Tu n'ajoutes ni dialogue, ni personnage, ni action. "
                "La phrase doit rester en français naturel et fluide — pas de mot vide, pas de "
                "syntagme tronqué, pas de redite."
            )
            data = self._chat(_REPAIR_SYSTEM_PROMPT, user, {"type": "object"})
            fixed = (data or {}).get("description") if isinstance(data, dict) else None
            if not isinstance(fixed, str) or not fixed.strip():
                logger.warning("Plan %s : réparation LLM vide, description conservée",
                               shot.get("number"))
                return shot

            fixed = _strip_self_negation(fixed.strip())
            # L'anaphore et les interdits doivent être recalculés SUR la description réparée.
            named = set(_characters_in(fixed, scene.get("characters") or []))
            terms = _shot_terms(shot, bible, named, guard, text=fixed)
            remaining = _detect_violations(fixed, terms)
            passes += 1
            if not remaining or passes >= max_passes:
                current = fixed
                break
            # La réparation a introduit ou laissé des violations : on retentente en
            # repartant du texte le moins mauvais connu.
            logger.warning("Plan %s : violations après réparation (passe %d) : %s",
                           shot.get("number"), passes, remaining)
            if len(violations) <= len(remaining):
                break
            violations = remaining
            current = fixed

        shot["description"] = current
        shot.setdefault("visual_guard", {})["repaired"] = True
        shot["visual_guard"]["characters"] = sorted(named)
        shot["visual_guard"]["repair_passes"] = passes
        if remaining:
            shot["visual_guard"]["violations"] = remaining
            shot["visual_guard"]["repair_incomplete"] = remaining
        else:
            shot["visual_guard"]["violations"] = []
        return shot

    def _guard_shot(self, shot: dict, project: str, scene: dict) -> dict:
        """Garde-fou complet d'un plan : détection, réparation de la prose, ancrage du prompt."""
        shot = _apply_visual_guard(shot, project, scene)
        if project and (shot.get("visual_guard") or {}).get("violations"):
            shot = self._repair_description(shot, project, scene)
        return shot

    def run(self, scenes: list[dict], output_dir: Path, project: str = "") -> dict:
        results = {}

        # Les scènes sont indépendantes (écriture dans des dossiers distincts) et leur
        # `_chat` gère le rate-limit : on les découpe en parallèle. Chaque scène garde sa
        # séquence interne de plans, préservant l'ordre de ses appels LLM. Le nombre de
        # workers est borné (`storyboard.parallel`) pour ne pas saturer le provider.
        from concurrent.futures import ThreadPoolExecutor, as_completed

        parallel = int(get_production_config().get("storyboard", {}).get("parallel", 2) or 2)

        def one_scene(scene: dict) -> tuple[int, dict]:
            scene_num = scene.get("number", 0)
            scene_title = scene.get("title", "")
            step_item(f"Scène {scene_num}", f"Storyboard textuel de « {scene_title} »")

            if not scene.get("timeline"):
                step_warn(f"Scène {scene_num}", "Pas de timeline (parse manquant)")
                return scene_num, {}

            # Étape A : bornes des plans
            plan_bounds = self._plan_bounds(scene, scene_num, project)
            if not plan_bounds:
                step_warn(f"Scène {scene_num}", "Étape A : aucun plan déterminé")
                return scene_num, {}

            # Étape B : décrire chaque plan
            shots = []
            for pb in plan_bounds:
                shot = self._describe_plan(scene, scene_num, pb, scene.get("timeline", []), project)
                shot = self._guard_shot(shot, project, scene)
                shots.append(shot)

            scene_dir = output_dir / f"scene_{scene_num:02d}"
            scene_dir.mkdir(parents=True, exist_ok=True)

            # Resolve refs from bible (characters + location + objects)
            if project:
                shots = resolve_refs(project, scene_num, shots)

            return scene_num, {"shots": shots, "count": len(shots)}

        with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
            futures = [pool.submit(one_scene, s) for s in scenes]
            for future in as_completed(futures):
                scene_num, data = future.result()
                if not data:
                    continue
                scene_key = f"scene_{scene_num:02d}"
                results[scene_key] = data
                scene_dir = output_dir / scene_key
                json_path = scene_dir / "storyboard.json"
                with open(json_path, "w") as f:
                    json.dump(data["shots"], f, ensure_ascii=False, indent=2)
                step_item(f"  → {data['count']} plans", str(json_path))

        return results
