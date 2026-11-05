"""Artefacts typés : stockage adressé par contenu, binaires, lignée, invalidation avale."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Extension de fichier par type de media ; les inconnus tombent sur .bin.
MEDIA_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
}


def _json_default(value: Any) -> Any:
    if isinstance(value, Binary):
        return value.ref()
    return str(value)


def canonical(value: Any) -> str:
    """Serialisation stable, utilisee pour les hashs et les cles de cache."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def digest(value: Any) -> str:
    """Empreinte courte et stable d'une valeur ; les binaires comptent bruts."""
    if isinstance(value, Binary):
        return value.hash
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Binary:
    """Contenu binaire (image, video) adresse par ses octets bruts."""

    data: bytes
    media_type: str

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.data).hexdigest()[:16]

    def ref(self) -> dict:
        """Forme JSON-serialisable : une reference a l'artefact, pas du base64."""
        return {"artifact": self.hash, "media_type": self.media_type, "bytes": len(self.data)}


@dataclass(frozen=True)
class Artifact:
    """Sortie versionnee d'un noeud, avec type de media, producteur et lignee."""

    hash: str
    value: Any
    media_type: str = "application/json"
    parents: tuple[str, ...] = ()
    producer: str = ""
    cost: float = 0.0


class ArtifactStore:
    """Stockage sur disque, adresse par le hash du contenu.

    Trois tables cohabitent dans le meme repertoire :
    - <hash>.json / <hash>.<ext> : le contenu lui-meme, immuable et deduplique ;
    - cache.json : cle de noeud -> hash, le cache d'execution ;
    - lineage.json : hash -> {media_type, parents, producer, cost}, la lignee.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._cache_file = self.root / "cache.json"
        self._lineage_file = self.root / "lineage.json"
        self._cache = self._read_json(self._cache_file)
        self._lineage = self._read_json(self._lineage_file)

    @staticmethod
    def _read_json(path: Path) -> dict:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {}

    def put(self, value: Any, parents: tuple[str, ...] = (), producer: str = "", cost: float = 0.0) -> str:
        """Ecrit un artefact s'il est absent, enregistre sa lignee, retourne son hash."""
        key = digest(value)
        if isinstance(value, Binary):
            path = self.root / f"{key}{MEDIA_EXTENSIONS.get(value.media_type, '.bin')}"
            if not path.exists():
                path.write_bytes(value.data)
            media_type = value.media_type
        else:
            path = self.root / f"{key}.json"
            if not path.exists():
                path.write_text(canonical(value), encoding="utf-8")
            media_type = "application/json"
        self._record(key, media_type, parents, producer, cost)
        return key

    def _record(self, key: str, media_type: str, parents: tuple[str, ...], producer: str, cost: float) -> None:
        entry = self._lineage.get(key, {})
        merged = list(dict.fromkeys([*entry.get("parents", []), *parents]))
        self._lineage[key] = {
            "media_type": media_type,
            "parents": merged,
            "producer": producer or entry.get("producer", ""),
            "cost": cost or entry.get("cost", 0.0),
        }
        self._lineage_file.write_text(canonical(self._lineage), encoding="utf-8")

    def get(self, key: str) -> Any:
        """Relit un artefact par son hash : dict JSON ou Binary, selon son media."""
        path = self.path_for(key)
        if path.suffix == ".json":
            return json.loads(path.read_text(encoding="utf-8"))
        media = self._lineage.get(key, {}).get("media_type", "application/octet-stream")
        return Binary(path.read_bytes(), media)

    def media_type(self, key: str) -> str:
        """Type de media declare pour un artefact (sans charger son contenu)."""
        return self._lineage.get(key, {}).get("media_type", "application/octet-stream")

    def path_for(self, key: str) -> Path:
        """Le chemin du fichier de contenu (JSON ou binaire) d'un artefact."""
        json_path = self.root / f"{key}.json"
        if json_path.exists():
            return json_path
        entry = self._lineage.get(key, {})
        ext = MEDIA_EXTENSIONS.get(entry.get("media_type", ""), ".bin")
        raw_path = self.root / f"{key}{ext}"
        if raw_path.exists():
            return raw_path
        raise KeyError(f"artefact introuvable: {key}")

    def lineage(self, key: str) -> Artifact:
        """Retourne l'artefact typ complet : valeur, media, parents, producteur, cout."""
        entry = self._lineage.get(key, {})
        return Artifact(
            hash=key,
            value=self.get(key),
            media_type=entry.get("media_type", "application/json"),
            parents=tuple(entry.get("parents", ())),
            producer=entry.get("producer", ""),
            cost=float(entry.get("cost", 0.0)),
        )

    def dependents(self, key: str) -> set[str]:
        """Hashs des artefacts qui dependent de celui-ci, transitivement."""
        found: set[str] = set()
        frontier = [key]
        while frontier:
            current = frontier.pop()
            for artifact_hash, entry in self._lineage.items():
                if current in entry.get("parents", []) and artifact_hash not in found:
                    found.add(artifact_hash)
                    frontier.append(artifact_hash)
        return found

    def invalidate(self, key: str) -> set[str]:
        """Retire du cache l'artefact vise et tout son aval ; l'amont est preserve.

        Les fichiers restent sur le disque (adressage par contenu, reutilisables) :
        seules les entrees de cache sont purgees, donc les noeuds concerns se
        reexecuteront au prochain run. Retourne le set des hash's devalues.
        """
        targets = {key} | self.dependents(key)
        stale = {node_key for node_key, artifact in self._cache.items() if artifact in targets}
        for node_key in stale:
            self._cache.pop(node_key)
        if stale:
            self._cache_file.write_text(canonical(self._cache), encoding="utf-8")
        return targets

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


__all__ = ["Artifact", "ArtifactStore", "Binary", "canonical", "digest", "MEDIA_EXTENSIONS"]
