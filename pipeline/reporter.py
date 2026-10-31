import sys
from datetime import datetime
from typing import Any


def timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def step_start(name: str) -> None:
    print(f"\n{'═' * 60}", file=sys.stderr)
    print(f"  🎬 [{timestamp()}] Lancement de l'étape : {name}", file=sys.stderr)
    print(f"{'═' * 60}", file=sys.stderr)


def step_item(label: str, details: str = "") -> None:
    print(f"  ✓ {label}", file=sys.stderr)
    if details:
        for line in details.split("\n"):
            print(f"    {line}", file=sys.stderr)


def step_warn(label: str, details: str = "") -> None:
    print(f"  ⚠ {label}", file=sys.stderr)
    if details:
        for line in details.split("\n"):
            print(f"    {line}", file=sys.stderr)


def step_end(name: str, summary: str = "") -> None:
    print(f"{'─' * 60}", file=sys.stderr)
    print(f"  ✅ [{timestamp()}] Fin Étape : {name}", file=sys.stderr)
    if summary:
        print(f"    {summary}", file=sys.stderr)
    print(f"{'═' * 60}\n", file=sys.stderr)
