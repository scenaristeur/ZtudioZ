from __future__ import annotations

from pathlib import Path

from pipeline.history.manager import HistoryManager
from pipeline.models.bible import BibleDB

PROJECTS_DIR = Path(__file__).resolve().parent.parent / "projets"


def get_project_root(slug: str) -> Path:
    return PROJECTS_DIR / slug


def get_history_manager(slug: str) -> HistoryManager:
    return HistoryManager(get_project_root(slug))


def get_bible(slug: str) -> BibleDB:
    return BibleDB(get_project_root(slug) / "bible")
