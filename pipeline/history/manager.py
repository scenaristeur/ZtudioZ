from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

KIND_EXTENSIONS = {
    "image": ".png",
    "video": ".mp4",
    "audio": ".mp3",
    "prompt": ".json",
    "storyboard": ".json",
    "scenario": ".md",
    "fiche": ".yaml",
    "shot_image": ".png",
}

KIND_DIRS = {
    "image": "images",
    "video": "videos",
    "audio": "audio",
    "prompt": "prompts",
    "storyboard": "storyboards",
    "scenario": "scenarios",
    "fiche": "fiches",
    "shot_image": "shot_images",
}


def _compute_hash(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:12]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class HistoryManager:
    """Version tracker for all generated artifacts in a project.

    Stores every prompt, output, and metadata as immutable snapshots.
    Supports rollback with cascade invalidation of downstream artifacts.

    Directory layout per project:
        {project_root}/
            .history.json          # Version index
            history/
                {kind}s/
                    {hash}.json    # Prompt + metadata
                    {hash}.{ext}   # Actual output file (symlink or copy)
    """

    def __init__(self, project_root: str | Path):
        self.root = Path(project_root)
        self.history_dir = self.root / "history"
        self.index_path = self.root / ".history.json"
        self._index: dict[str, Any] = {"versions": [], "rollbacks": []}
        self._load_index()

    def _load_index(self) -> None:
        if self.index_path.exists():
            try:
                self._index = json.loads(self.index_path.read_text())
            except (json.JSONDecodeError, OSError):
                logger.warning("Corrupt .history.json, resetting")
                self._index = {"versions": [], "rollbacks": []}

    def _save_index(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(
            json.dumps(self._index, indent=2, ensure_ascii=False)
        )

    def _kind_dir(self, kind: str) -> Path:
        dirname = KIND_DIRS.get(kind, f"{kind}s")
        d = self.history_dir / dirname
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _ext(self, kind: str) -> str:
        return KIND_EXTENSIONS.get(kind, ".bin")

    def snapshot(
        self,
        kind: str,
        prompt: str,
        output_path: str | Path | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Record an artifact snapshot.

        Args:
            kind: Type of artifact (image, video, audio, prompt, etc.)
            prompt: The prompt/text that generated this artifact
            output_path: Path to the generated file (will be symlinked)
            metadata: Additional metadata (model, seed, cost, step, scene, entity)

        Returns:
            The hash identifying this snapshot
        """
        meta = metadata or {}
        hash_input = f"{kind}:{prompt}:{json.dumps(meta, sort_keys=True)}"
        artifact_hash = _compute_hash(hash_input)

        snapshot_data = {
            "hash": artifact_hash,
            "kind": kind,
            "prompt": prompt,
            "timestamp": _now_iso(),
            "output": str(output_path) if output_path else None,
            **meta,
        }

        kind_dir = self._kind_dir(kind)

        snapshot_json = kind_dir / f"{artifact_hash}.json"
        snapshot_json.write_text(
            json.dumps(snapshot_data, indent=2, ensure_ascii=False)
        )

        if output_path:
            src = Path(output_path)
            if src.exists():
                ext = src.suffix or self._ext(kind)
                dest = kind_dir / f"{artifact_hash}{ext}"
                if not dest.exists():
                    try:
                        shutil.copy2(src, dest)
                    except OSError:
                        logger.warning("Could not copy %s to history", src)

        self._index["versions"].append(snapshot_data)
        self._save_index()

        logger.info(
            "History snapshot: %s/%s (kind=%s, step=%s)",
            kind_dir.name, artifact_hash, kind, meta.get("step", "?"),
        )
        return artifact_hash

    def get_history(
        self,
        kind: str | None = None,
        entity: str | None = None,
        scene: int | None = None,
        step: str | None = None,
    ) -> list[dict]:
        """Return all versions, optionally filtered."""
        results = self._index.get("versions", [])
        if kind:
            results = [v for v in results if v.get("kind") == kind]
        if entity:
            results = [v for v in results if v.get("entity") == entity]
        if scene is not None:
            results = [v for v in results if v.get("scene") == scene]
        if step:
            results = [v for v in results if v.get("step") == step]
        return results

    def get_version(self, artifact_hash: str) -> dict | None:
        """Return the full snapshot for a given hash."""
        for v in self._index.get("versions", []):
            if v.get("hash") == artifact_hash:
                kind = v.get("kind", "")
                kind_dir = self._kind_dir(kind)
                json_path = kind_dir / f"{artifact_hash}.json"
                if json_path.exists():
                    return json.loads(json_path.read_text())
                return v
        return None

    def get_output_path(self, artifact_hash: str) -> Path | None:
        """Return the path to the stored output file for a snapshot."""
        version = self.get_version(artifact_hash)
        if not version:
            return None
        kind = version.get("kind", "")
        kind_dir = self._kind_dir(kind)
        ext = self._ext(kind)
        output = kind_dir / f"{artifact_hash}{ext}"
        return output if output.exists() else None

    def diff(self, hash_a: str, hash_b: str) -> dict:
        """Compare two versions. Returns differences."""
        va = self.get_version(hash_a)
        vb = self.get_version(hash_b)
        if not va or not vb:
            return {"error": "Version not found"}

        diffs = {}
        all_keys = set(list(va.keys()) + list(vb.keys()))
        for key in sorted(all_keys):
            val_a = va.get(key)
            val_b = vb.get(key)
            if val_a != val_b:
                diffs[key] = {"from": val_a, "to": val_b}
        return diffs

    def rollback(
        self,
        artifact_hash: str,
        reason: str = "",
        restore_to: str | Path | None = None,
    ) -> dict | None:
        """Restore a previous version and invalidate downstream artifacts.

        Args:
            artifact_hash: Hash of the version to restore
            reason: Why this rollback is happening
            restore_to: Where to copy the restored file. If None, uses the
                        original output path from the snapshot.

        Returns:
            The rollback record, or None if the hash was not found.
        """
        version = self.get_version(artifact_hash)
        if not version:
            return None

        kind = version.get("kind", "")
        output = version.get("output")
        if not output:
            return None

        src = self.get_output_path(artifact_hash)
        if not src or not src.exists():
            return None

        dest = Path(restore_to) if restore_to else Path(output)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

        rollback_record = {
            "hash": artifact_hash,
            "kind": kind,
            "output": output,
            "reason": reason,
            "timestamp": _now_iso(),
        }
        self._index.setdefault("rollbacks", []).append(rollback_record)
        self._save_index()

        invalidated = self._cascade_invalidate(kind, output)
        rollback_record["invalidated"] = invalidated

        logger.info(
            "Rollback %s → %s (reason: %s, invalidated: %d)",
            kind, output, reason, len(invalidated),
        )
        return rollback_record

    def _cascade_invalidate(self, kind: str, output_path: str) -> list[str]:
        """Invalidate downstream artifacts when rolling back.

        Cascade rules:
            scenario → parse → fiches, storyboard, audio
            fiches → sheets
            storyboard → shot_images
            shot_image → video
            video → montage
        """
        cascade_map = {
            "scenario": ["fiche", "storyboard", "audio"],
            "fiche": ["shot_image"],
            "storyboard": ["shot_image"],
            "shot_image": ["video"],
            "video": ["scenario"],  # montage
        }

        downstream_kinds = cascade_map.get(kind, [])
        invalidated = []

        if not downstream_kinds:
            return invalidated

        for version in list(self._index.get("versions", [])):
            v_kind = version.get("kind", "")
            v_output = version.get("output", "")
            if v_kind in downstream_kinds and v_output:
                if self._is_downstream(output_path, v_output):
                    invalidated.append(version.get("hash", ""))
                    dest = Path(v_output)
                    if dest.exists():
                        try:
                            dest.unlink()
                        except OSError:
                            pass

        return invalidated

    def _is_downstream(self, parent_path: str, child_path: str) -> bool:
        """Check if child_path is downstream of parent_path based on project structure."""
        try:
            parent = Path(parent_path).parts
            child = Path(child_path).parts
            if len(child) < len(parent):
                return False
            parent_scene = None
            child_scene = None
            for p in parent:
                if p.startswith("scene_"):
                    parent_scene = p
            for c in child:
                if c.startswith("scene_"):
                    child_scene = c
            if parent_scene and child_scene:
                return parent_scene == child_scene
            return True
        except (ValueError, IndexError):
            return False

    def log_run(
        self,
        step: str,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        status: str = "ok",
        error: str = "",
        duration_ms: float = 0.0,
        cost_pollen: float = 0.0,
    ) -> str:
        """Log a pipeline step execution."""
        run_hash = _compute_hash(f"run:{step}:{_now_iso()}")
        run_record = {
            "hash": run_hash,
            "type": "run",
            "step": step,
            "inputs": inputs,
            "outputs": outputs,
            "status": status,
            "error": error,
            "duration_ms": round(duration_ms, 1),
            "cost_pollen": round(cost_pollen, 6),
            "timestamp": _now_iso(),
        }

        runs_dir = self.history_dir / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        run_path = runs_dir / f"{run_hash}.json"
        run_path.write_text(json.dumps(run_record, indent=2, ensure_ascii=False))

        self._index.setdefault("runs", []).append(run_record)
        self._save_index()

        logger.info("Run logged: %s (step=%s, status=%s)", run_hash, step, status)
        return run_hash

    def get_runs(
        self,
        step: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        """Return all logged runs, optionally filtered."""
        results = self._index.get("runs", [])
        if step:
            results = [r for r in results if r.get("step") == step]
        if status:
            results = [r for r in results if r.get("status") == status]
        return results

    def get_total_cost(self) -> float:
        """Sum all cost_pollen from runs."""
        return sum(r.get("cost_pollen", 0) for r in self._index.get("runs", []))

    def summary(self) -> dict:
        """Return a summary of the project history."""
        versions = self._index.get("versions", [])
        runs = self._index.get("runs", [])
        rollbacks = self._index.get("rollbacks", [])

        by_kind: dict[str, int] = {}
        for v in versions:
            k = v.get("kind", "unknown")
            by_kind[k] = by_kind.get(k, 0) + 1

        return {
            "total_versions": len(versions),
            "total_runs": len(runs),
            "total_rollbacks": len(rollbacks),
            "versions_by_kind": by_kind,
            "total_cost_pollen": round(self.get_total_cost(), 4),
            "first_created": versions[0]["timestamp"] if versions else None,
            "last_updated": versions[-1]["timestamp"] if versions else None,
        }
