"""Artefacts typés : stockage adressé par contenu, cache par clé de noeud."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def canonical(value: Any) -> str:
    """Serialisation stable, utilisee pour les hashs et les cles de cache."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def digest(value: Any) -> str:
    """Empreinte courte et stable d'une valeur."""
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Artifact:
    """Sortie versionnee d'un noeud."""

    hash: str
    value: Any


class ArtifactStore:
    """Stockage sur disque, adresse par le hash du contenu."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._cache_file = self.root / "cache.json"
        if self._cache_file.exists():
            self._cache = json.loads(self._cache_file.read_text(encoding="utf-8"))
        else:
            self._cache = {}

    def put(self, value: Any) -> str:
        """Ecrit un artefact si absent et retourne son hash."""
        key = digest(value)
        path = self.root / f"{key}.json"
        if not path.exists():
            path.write_text(canonical(value), encoding="utf-8")
        return key

    def get(self, key: str) -> Any:
        """Relit un artefact par son hash."""
        return json.loads((self.root / f"{key}.json").read_text(encoding="utf-8"))

    def key(self, payload: Any) -> str:
        """Cle de cache d'un noeud."""
        return digest(payload)

    def cached(self, key: str) -> str | None:
        """Retourne le hash d'artefact en cache, ou None."""
        return self._cache.get(key)

    def remember(self, key: str, artifact_hash: str) -> None:
        """Memorise une correspondance cle de noeud -> artefact."""
        self._cache[key] = artifact_hash
        self._cache_file.write_text(canonical(self._cache), encoding="utf-8")
