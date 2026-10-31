"""Signature de contenu des images de référence, pour des clés de cache honnêtes.

Le problème
-----------
Les caches image et vidéo cléfient sur le *chemin* des refs (`ref_image`, `ref_images`).
Un chemin est un identifiant stable, pas une description du contenu : régénérer un shot
(la cause première des reprises dans ce système) réécrit `shot_01.png` au même endroit.
Le cache croit alors avoir déjà produit la vidéo de *ce* shot, et sert l'ancienne — la
correction est silencieusement annulée.

L'approche
----------
Au lieu d'un MD5 du fichier (coût à chaque appel, plusieurs Mo à lire), on signe par
`(mtime_ns, taille)`. Deux artéfacts au même chemin ont un mtime différent ; un même
artéfact conserve le sien. C'est déterministe dans le temps pour un même fichier, et
détecte toute régénération. Le coût est un `stat` par ref.

Cas particuliers
----------------
- Réf distante (http/https) : on ne peut pas `stat` le contenu ; on signe l'URL.
  L'expiry d'une URL tmpfiles en fait partie : une ré-upload change l'URL donc la clé,
  ce qui est voulu (l'expiry est une propriété de la référence).
- Réf inexistante : signature vide — le cache la traitera comme absente.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def ref_signature(ref: str) -> str:
    """Signature déterministe d'une référence d'image, pour une clé de cache.

    Args:
        ref: chemin local, chemin `|`-séparé (legacy `ref_image`) ou URL distante.

    Returns:
        Signature compacte (mtime_ns:taille pour un fichier local, l'URL pour une
        distance, chaîne vide si le chemin n'existe pas).
    """
    ref = (ref or "").strip()
    if not ref:
        return ""
    if ref.startswith(("http://", "https://")):
        return f"url:{ref}"
    if "|" in ref:
        return "|".join(ref_signature(r) for r in ref.split("|") if r)
    return _local_ref_signature(ref)


def _local_ref_signature(path: str) -> str:
    try:
        stat = Path(path).stat()
    except OSError:
        return ""
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def refs_signature(refs: Optional[list[str]]) -> str:
    """Signature combinée d'une liste de refs (mode keyframes / multi-refs)."""
    if not refs:
        return ""
    return "|".join(ref_signature(r) for r in refs if r)


def stable_seed(mtime_ns: int, shot_number: int) -> int:
    """Seed stable pour une régénération d'artefact.

    Garde le seed *déterministe pour un même contenu* mais différent entre une image et
    sa régénération, pour ne jamais servir une vidéo d'un shot dont l'image de départ a
    changé (le refs_signature a déjà invalidé la clé ; ici c'est pour la graine).
    """
    return shot_number + (mtime_ns % 100_000)