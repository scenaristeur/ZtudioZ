"""Outil bible : resout des entites en planches de reference stockees."""

from __future__ import annotations

from ..bible import Bible, media_type_for
from ..kernel.artifacts import Binary
from ..kernel.errors import LoadError


def sheets(params: dict, ctx) -> dict:
    """`references`: liste de slugs / type-slug / {type,slug} -> refs d'artefacts."""
    if ctx.bible_root is None:
        if params.get("references"):
            raise LoadError("bible.sheets: aucune bible montee pour ce projet")
        return {"references": []}
    paths = Bible(ctx.bible_root).resolve(params.get("references") or [])
    references = []
    for path in paths:
        binary = Binary(path.read_bytes(), media_type_for(path))
        ctx.store.put(binary)
        references.append(binary.ref())
    return {"references": references}


__all__ = ["sheets"]
