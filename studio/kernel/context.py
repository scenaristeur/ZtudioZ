"""Vocabulaire YAML-LD du studio : validation des termes, expansion des IRIs.

Le moteur étend les capacités de pyld mais ne l'utilise pas pour la résolution
de @context : nos références (studio:) sont résolues en local, ce qui rend le
chargement plus rapide et insensible à un réseau absent ou indisponible.
"""

from __future__ import annotations

from typing import Any, Iterable

from pyld import jsonld

from .errors import LoadError

BASE = "https://ztudioz.dev/ns/studio#"
CONTEXT_IRI = "studio:context"

# Vocabulaire declare : chaque clé autorisee est mappee a son IRI expandee.
CONTEXT: dict[str, Any] = {
    "@version": 1.1,
    "studio": BASE,
    "type": "@type",
    "name": "studio:name",
    "description": "studio:description",
    "version": "studio:version",
    "model": "studio:model",
    "prompt": "studio:prompt",
    "skills": {"@id": "studio:skills", "@container": "@set"},
    "tools": {"@id": "studio:tools", "@container": "@set"},
    "inputs": "studio:inputs",
    "outputs": "studio:outputs",
    "budget": "studio:budget",
    "nodes": {"@id": "studio:nodes", "@container": "@set"},
    "id": "studio:nodeId",
    "agent": {"@id": "studio:agent", "@type": "@id"},
    "needs": {"@id": "studio:needs", "@container": "@set"},
    "foreach": "studio:foreach",
    "parallel": "studio:parallel",
    "on_item_failure": "studio:on_item_failure",
    "question": "studio:question",
    "options": {"@id": "studio:options", "@container": "@set"},
    "as": "studio:as",
    "kind": "studio:kind",
    "server": "studio:server",
    "tool": "studio:tool",
    "impl": "studio:impl",
    "servers": "studio:servers",
}

TERMS = {key for key in CONTEXT if not key.startswith("@") and key != "studio"}


def expand_iri(term: str) -> str:
    """Developpe un terme court en IRI absolue."""
    if term.startswith("http://") or term.startswith("https://"):
        return term
    if term.startswith("studio:"):
        return BASE + term[len("studio:") :]
    return BASE + term


def short_iri(iri: str) -> str:
    """Compacte une IRI du vocabulaire studio."""
    return iri[len(BASE) :] if iri.startswith(BASE) else iri


def _reject_unknown_keys(doc: dict, allowed: Iterable[str], where: str) -> None:
    unknown = sorted(set(doc) - set(allowed))
    if unknown:
        raise LoadError(f"{where}: terme(s) non declare(s) dans @context: {unknown}")


def _resolve_context(raw: dict) -> dict:
    """Retourne le contexte resolu sans essayer de fetcher un document externe.

    pyld 3.x refuse les references locales comme 'studio:context'. On resout
    donc le contexte nous-memes et on passe la carte directe a pyld pour
    l'expansion des termes.
    """
    ctx_ref = raw.get("@context")
    if ctx_ref != CONTEXT_IRI:
        raise LoadError(f"@context doit valoir '{CONTEXT_IRI}', recu: {ctx_ref!r}")
    return CONTEXT


def expand_document(raw: dict, allowed: Iterable[str], where: str) -> dict:
    """Valide un document YAML-LD et retourne sa forme compacte normalisee.

    Le contexte est resolu en local, puis l'expansion de pyld est appliquee
    sur le resultat avec document_loader desactive pour eviter tout acces reseau.
    """
    if not isinstance(raw, dict):
        raise LoadError(f"{where}: un document doit etre un mapping")

    resolved_ctx = _resolve_context(raw)
    doc = {key: value for key, value in raw.items() if key != "@context"}
    _reject_unknown_keys(doc, allowed, where)

    expanded = jsonld.expand(
        {"@context": resolved_ctx, **doc},
        options={"document_loader": None},
    )
    if not expanded:
        raise LoadError(f"{where}: document vide apres expansion")
    node = expanded[0]

    out = dict(doc)
    out["@id"] = node.get("@id", "")
    types = node.get("@type") or []
    if types:
        out["@type"] = types[0]
    if "agent" in out:
        out["agent"] = expand_iri(str(out["agent"]))
    return out


def expand_list(value: Any) -> list:
    """Normalise une valeur optionnelle en liste."""
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [value]
