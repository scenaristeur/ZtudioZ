from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_estimates = {
    "openai/gpt-oss-120b": {"input": 0.15, "output": 0.60},
    "mistralai/Mistral-Small-3.2-24B-Instruct-2506": {"input": 0.10, "output": 0.40},
    "image/gptimage": {"unit": 0.002},
    "image/flux": {"unit": 0.001},
    "image/seedream": {"unit": 0.005},
    "video/ltx-2": {"unit": 0.01},
    "video/seedance": {"unit": 0.02},
    "video/veo": {"unit": 0.04},
    "video/wan": {"unit": 0.02},
    "audio/say": {"unit": 0.001},
    "audio/respond": {"unit": 0.002},
}


def _cost_estimate(model_key: str, **kwargs) -> float:
    info = _estimates.get(model_key, {"unit": 0.0})
    if "input" in info:
        tokens_in = kwargs.get("tokens_in", 0)
        tokens_out = kwargs.get("tokens_out", 0)
        return (tokens_in / 1_000_000 * info["input"]
                + tokens_out / 1_000_000 * info["output"])
    return info.get("unit", 0.0)


class ConsumptionTracker:
    def __init__(self):
        self._session_id: str = ""
        self._entries: list[dict] = []
        self._project_dir: Path | None = None

    def start_session(self, label: str = "") -> str:
        now = datetime.now()
        self._session_id = now.strftime("%Y%m%d_%H%M%S")
        self._entries.clear()
        if label:
            self._entries.append({
                "ts": now.isoformat(timespec="seconds"),
                "session": self._session_id,
                "label": label,
                "type": "session_start",
            })
        return self._session_id

    def set_project_dir(self, path: str | Path) -> None:
        self._project_dir = Path(path)

    def track(
        self,
        service: str,
        model: str,
        action: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        duration_ms: float = 0.0,
        status: str = "ok",
        error: str = "",
        **extra,
    ) -> None:
        model_key = f"{service}/{model}"
        cost = _cost_estimate(model_key, tokens_in=tokens_in, tokens_out=tokens_out)
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "session": self._session_id,
            "service": service,
            "model": model,
            "action": action,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "duration_ms": round(duration_ms, 1),
            "cost": round(cost, 6),
            "status": status,
        }
        if error:
            entry["error"] = error
        self._entries.append(entry)
        logger.debug(
            "Conso: %s/%s %s | tokens=%d+%d | cost=%.4f | %.0fms",
            service, model, action, tokens_in, tokens_out, cost, duration_ms,
        )

    def _summarize(self) -> dict:
        total_cost = 0.0
        total_tokens_in = 0
        total_tokens_out = 0
        by_service: dict[str, dict] = {}

        for e in self._entries:
            if e.get("type") == "session_start":
                continue
            total_cost += e["cost"]
            total_tokens_in += e["tokens_in"]
            total_tokens_out += e["tokens_out"]

            svc = e["service"]
            if svc not in by_service:
                by_service[svc] = {"calls": 0, "cost": 0.0, "tokens_in": 0, "tokens_out": 0}
            by_service[svc]["calls"] += 1
            by_service[svc]["cost"] += e["cost"]
            by_service[svc]["tokens_in"] += e["tokens_in"]
            by_service[svc]["tokens_out"] += e["tokens_out"]

        return {
            "total_calls": len([e for e in self._entries if e.get("type") != "session_start"]),
            "total_cost": round(total_cost, 6),
            "total_tokens_in": total_tokens_in,
            "total_tokens_out": total_tokens_out,
            "by_service": by_service,
        }

    def save(self, project_dir: str | Path | None = None) -> Path:
        base = Path(project_dir or self._project_dir or ".")
        base.mkdir(parents=True, exist_ok=True)
        sid = self._session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = base / f"consumption_{sid}.json"
        report = {
            "session": sid,
            "summary": self._summarize(),
            "entries": self._entries,
            "_saved": datetime.now().isoformat(),
        }
        dest.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        return dest

    def print_summary(self) -> None:
        s = self._summarize()
        sid = self._session_id or "(pas de session)"
        print(f"\n{'=' * 60}")
        print(f"  📊 CONSOMMATION API — Session {sid}")
        print(f"{'=' * 60}")
        print(f"  Appels API          : {s['total_calls']}")
        print(f"  Tokens in           : {s['total_tokens_in']:,}")
        print(f"  Tokens out          : {s['total_tokens_out']:,}")
        print(f"  Coût estimé         : {s['total_cost']:.4f} $")
        print(f"{'─' * 60}")
        for svc, data in sorted(s["by_service"].items()):
            print(f"  {svc:12s} : {data['calls']:3d} appels, "
                  f"{data['cost']:.4f} $, "
                  f"{data['tokens_in'] + data['tokens_out']:,} tokens")
        print(f"{'=' * 60}\n")

    def get_session_id(self) -> str:
        return self._session_id


_global_tracker = ConsumptionTracker()


def get_tracker() -> ConsumptionTracker:
    return _global_tracker


def reset_tracker() -> None:
    _global_tracker._entries.clear()
