from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Shot:
    number: int
    type: str
    description: str
    camera: str = ""
    dialogue: list[dict] = field(default_factory=list)
    action: str = ""
    duration_seconds: float = 5.0
    ref_image: str = ""
    video_path: str = ""
    audio_paths: dict[str, str] = field(default_factory=dict)


@dataclass
class Scene:
    number: int
    title: str
    location: str
    time_of_day: str
    characters: list[str]
    ambiance: str
    description: str
    raw_text: str
    shots: list[Shot] = field(default_factory=list)
    status: str = "pending"
    output_dir: Path | None = None

    @property
    def slug(self) -> str:
        return f"scene_{self.number:02d}"


@dataclass
class Project:
    slug: str
    title: str
    logline: str
    synopsis: str
    scenes: list[Scene] = field(default_factory=list)
    root: Path | None = None


def parse_scene_number(scene_marker: str) -> int:
    return int(scene_marker.replace("SCÈNE", "").replace("SCENE", "").strip().split()[0])
