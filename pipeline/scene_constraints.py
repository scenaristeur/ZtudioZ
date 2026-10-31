"""Contraintes de scène : le verrou déclaratif de cohérence d'une scène.

Le problème
-----------
La cohérence repose aujourd'hui sur `character_details.clothing`, extrait par le LLM dans
le YAML parsé, et sur le fait que le prompt de chaque plan le répète. C'est une consigne
demandée à un générateur de texte : rien ne garantit qu'un plan sur trente n'invente pas une
veste. Or « vêtements et lieux ne changent pas au sein d'une scène » est une contrainte
*structurelle*, pas une instruction de style.

L'approche
----------
Un fichier déclaratif par scène, `bible/scene/scene_NN.yaml`, fait autorité. Il déclare la
liste fermée des personnages présents, la variante de tenue de chacun (résolue dans la
fiche bible via `versions.<active>.tenues.<variante>`), le lieu, le moment, et le nombre de
personnes attendu. Ce bloc est injecté en tête du prompt de **chaque** plan de la scène, et
fournit au vérificateur structure les valeurs attendues.

Opt-in par construction : sans `bible/scene/scene_NN.yaml`, `load()` renvoie `None` et
l'appelant ne change rien au comportement existant.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

SCENE_DIRNAME = "scene"


def _normalize_characters(raw: Any) -> dict[str, dict]:
    """Accepte deux écritures de `characters` dans le YAML de scène.

    dict (forme canonique, tenue résolvable) :

        characters:
          maya: {name: Maya, tenue: soir}

    liste de noms (forme simple, sans tenue) :

        characters: [Maya, Antoine]

    Le YAML parsé utilise la forme liste, mais elle y mélange personnages et figurants
    ("SUIVEURS", "FEMME (filme)") : c'est précisément pourquoi la forme dict, qui porte
    `presence` explicitement, est la seule qui fasse autorité.
    """
    if isinstance(raw, dict):
        return {str(slug): (entry if isinstance(entry, dict) else {}) for slug, entry in raw.items()}
    if isinstance(raw, list):
        return {str(name).strip().lower(): {"name": str(name).strip()} for name in raw if str(name).strip()}
    return {}


class SceneConstraints:
    """Contraintes déclaratives d'une scène, résolues depuis la bible."""

    def __init__(self, project_root: str | Path):
        self.root = Path(project_root)
        self.bible = self.root / "bible"
        self._cache: dict[int, Optional[dict]] = {}

    # ── Chargement ─────────────────────────────────────────────

    def _candidates(self, scene: int) -> list[Path]:
        d = self.bible / SCENE_DIRNAME
        return [d / f"scene_{scene:02d}.yaml", d / f"scene_{scene}.yaml"]

    def load(self, scene: int) -> Optional[dict]:
        """Contraintes brutes de la scène, ou None si la scène n'en déclare pas."""
        if scene in self._cache:
            return self._cache[scene]
        data: Optional[dict] = None
        for path in self._candidates(scene):
            if not path.exists():
                continue
            try:
                loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (yaml.YAMLError, OSError) as exc:
                logger.warning("Contraintes de scène illisibles (%s) : %s", path, exc)
                break
            if isinstance(loaded, dict):
                loaded.setdefault("scene", scene)
                data = loaded
            break
        self._cache[scene] = data
        return data

    def exists(self, scene: int) -> bool:
        return self.load(scene) is not None

    # ── Résolution des tenues depuis la bible ──────────────────

    def _tenue_text(self, slug: str, variant: str | None) -> str:
        """Texte de tenue d'un personnage, pour une variante donnée.

        Priorité à la variante demandée ; à défaut la tenue la plus probable
        (`default`) ; à défaut la tenue named keys de la version active.
        """
        fiche = self.bible / "personnage" / slug / "fiche.yaml"
        if not fiche.exists():
            return ""
        try:
            data = yaml.safe_load(fiche.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError):
            return ""
        versions = data.get("versions", {}) or {}
        ver = versions.get(data.get("active_version", "v1"), {}) or {}
        tenues = ver.get("tenues") or {}
        if not isinstance(tenues, dict) or not tenues:
            return ""
        if variant and variant in tenues:
            return str(tenues[variant] or "").strip()
        if "default" in tenues:
            return str(tenues["default"] or "").strip()
        # Une seule tenue : c'est elle, sans qu'on ait à choisir.
        if len(tenues) == 1:
            return str(next(iter(tenues.values())) or "").strip()
        return ""

    # ── Rendu pour le prompt ────────────────────────────────────

    def anchor_text(self, scene: int, shot_text: str = "") -> str:
        """Bloc de contraintes à injecter en tête du prompt d'un plan.

        Renvoie une chaîne vide si la scène ne déclare pas de contraintes. Le bloc liste
        ladistribution fermée (une liste explicite est plus contraignante qu'une
        consigne), le lieu, le moment, et ferme sur l'interdiction d'en ajouter.
        """
        data = self.load(scene)
        if not data:
            return ""

        parts: list[str] = []
        characters = _normalize_characters(data.get("characters"))
        presence = data.get("presence")
        if isinstance(presence, dict) and isinstance(presence.get("min"), int):
            # Scène à public non borné : au moins N personnages nommés, le reste est du
            # décor vivant déclaré dans `must_include`. Un compte exact ferait juger faux
            # un plan qui montre le public.
            who = ", ".join(
                self._personnage_text(slug, entry)
                for slug, entry in characters.items()
            ) if characters else ""
            if who:
                parts.append(f"AT LEAST {presence['min']} NAMED CHARACTER(S), no one replaces them: {who}; crowd allowed as decor only")
            else:
                parts.append(f"AT LEAST {presence['min']} NAMED CHARACTER(S), no one replaces them; crowd allowed as decor only")
        elif isinstance(presence, int) or characters:
            count = presence if isinstance(presence, int) else len(characters)
            if characters:
                who = ", ".join(
                    self._personnage_text(slug, entry)
                    for slug, entry in characters.items()
                )
                parts.append(f"EXACTLY {count} CHARACTER(S), no one else: {who}")
            else:
                # Scène vide déclarée : une liste vide est moins contraignante qu'un
                # compte explicite, et une scène vide doit vraiment le rester.
                parts.append(f"EXACTLY {count} CHARACTER(S), no one else, empty scene")

        location = data.get("location")
        if location:
            parts.append(f"LOCATION (unchanged for the whole scene): {location}")

        moment = data.get("time_of_day")
        if moment:
            parts.append(f"TIME OF DAY: {moment}")

        extras = data.get("must_include")
        if isinstance(extras, list) and extras:
            parts.append("MUST INCLUDE: " + "; ".join(str(e) for e in extras))

        forbids = data.get("must_not_include")
        if isinstance(forbids, list) and forbids:
            parts.append("MUST NOT INCLUDE: " + "; ".join(str(e) for e in forbids))

        if not parts:
            return ""
        return "SCENE LOCK: " + ". ".join(parts) + "."

    def _personnage_text(self, slug: str, entry: dict) -> str:
        name = entry.get("name") or slug
        tenue = self._tenue_text(slug, entry.get("tenue"))
        if tenue:
            return f"{name} wearing {tenue}"
        desc = entry.get("description")
        return f"{name} ({desc})" if desc else name

    # ── Valeurs attendues, pour la vérification ────────────────

    def expected_people(self, scene: int) -> Optional[int]:
        """Nombre de personnes attendu à l'image, ou None si non déclaré.

        Un `presence: {min: N}` (scène à public) renvoie le dict lui-même, que le
        vérificateur sait lire comme « au moins N ».
        """
        data = self.load(scene)
        if not data:
            return None
        presence = data.get("presence")
        if isinstance(presence, int):
            return presence
        if isinstance(presence, dict) and isinstance(presence.get("min"), int):
            return presence
        characters = _normalize_characters(data.get("characters"))
        if characters:
            return len(characters)
        return None

    def expected_location(self, scene: int) -> Optional[str]:
        data = self.load(scene)
        if not data:
            return None
        loc = data.get("location")
        return str(loc) if loc else None

    def summary(self, scene: int) -> dict[str, Any]:
        data = self.load(scene)
        if not data:
            return {}
        characters = _normalize_characters(data.get("characters"))
        return {
            "scene": data.get("scene", scene),
            "location": data.get("location"),
            "time_of_day": data.get("time_of_day"),
            "people": self.expected_people(scene),
            "characters": {
                slug: {
                    "name": entry.get("name") or slug,
                    "outfit": self._tenue_text(slug, entry.get("tenue")),
                    "variant": entry.get("tenue"),
                }
                for slug, entry in characters.items()
            },
        }
