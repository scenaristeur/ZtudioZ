from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.deps import get_history_manager

router = APIRouter()


class RollbackRequest(BaseModel):
    reason: str = ""


@router.get("/{slug}/versions")
def list_versions(
    slug: str,
    kind: str | None = None,
    entity: str | None = None,
    scene: int | None = None,
    step: str | None = None,
):
    hm = get_history_manager(slug)
    versions = hm.get_history(kind=kind, entity=entity, scene=scene, step=step)
    return {"versions": versions, "count": len(versions)}


@router.get("/{slug}/version/{artifact_hash}")
def get_version(slug: str, artifact_hash: str):
    hm = get_history_manager(slug)
    version = hm.get_version(artifact_hash)
    if not version:
        raise HTTPException(404, "Version not found")
    return version


@router.get("/{slug}/diff/{hash_a}/{hash_b}")
def diff_versions(slug: str, hash_a: str, hash_b: str):
    hm = get_history_manager(slug)
    return hm.diff(hash_a, hash_b)


@router.post("/{slug}/rollback/{artifact_hash}")
def rollback(slug: str, artifact_hash: str, req: RollbackRequest | None = None):
    hm = get_history_manager(slug)
    reason = req.reason if req else ""
    result = hm.rollback(artifact_hash, reason=reason)
    if not result:
        raise HTTPException(404, "Version not found or rollback failed")
    return result


@router.get("/{slug}/runs")
def list_runs(slug: str, step: str | None = None, status: str | None = None):
    hm = get_history_manager(slug)
    runs = hm.get_runs(step=step, status=status)
    return {"runs": runs, "count": len(runs)}


@router.get("/{slug}/summary")
def history_summary(slug: str):
    hm = get_history_manager(slug)
    return hm.summary()
