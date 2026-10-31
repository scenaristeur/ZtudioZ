from __future__ import annotations

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

PROJECTS_DIR = Path(__file__).resolve().parent.parent.parent / "projets"


class ScenarioStep:
    """Read source materials and summarize them for scenario creation.

    This step doesn't generate the scenario — it prepares the raw materials
    so the AI agent (via skills) can collaborate with the user to create it.
    """

    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    def read_sources(self, project: str) -> dict:
        """Read all source materials from sources/ directory.

        Returns a dict with:
          - texts: list of (filename, content) for text files
          - images: list of paths for reference images
          - summary: combined text of all sources
        """
        sources_dir = PROJECTS_DIR / project / "sources"
        if not sources_dir.exists():
            logger.warning("No sources directory for project %s", project)
            return {"texts": [], "images": [], "summary": ""}

        texts = []
        images = []
        parts = []

        for f in sorted(sources_dir.iterdir()):
            if f.is_dir():
                continue
            if f.suffix in (".md", ".txt"):
                content = f.read_text(encoding="utf-8")
                texts.append((f.name, content))
                parts.append(f"--- {f.name} ---\n{content}\n")
            elif f.suffix in (".png", ".jpg", ".jpeg", ".webp"):
                images.append(str(f))

        inspo_dir = sources_dir / "inspirations"
        if inspo_dir.exists():
            for f in sorted(inspo_dir.iterdir()):
                if f.suffix in (".png", ".jpg", ".jpeg", ".webp"):
                    images.append(str(f))

        return {
            "texts": texts,
            "images": images,
            "summary": "\n".join(parts),
        }

    def summarize_sources(self, project: str) -> str:
        """Produce a text summary of all sources for the LLM."""
        data = self.read_sources(project)
        if not data["summary"]:
            return "No source materials found."

        summary = data["summary"]
        if len(summary) > 8000:
            summary = summary[:8000] + "\n\n[...truncated, see full files in sources/]"

        return summary

    def list_images(self, project: str) -> list[str]:
        """List all reference images in sources/."""
        data = self.read_sources(project)
        return data["images"]

    def get_scenario_version(self, project: str) -> int:
        """Find the next available scenario version number."""
        script_dir = PROJECTS_DIR / project / "script"
        if not script_dir.exists():
            return 1
        versions = []
        for f in script_dir.glob("scenario_v*.md"):
            try:
                v = int(f.stem.split("_v")[1])
                versions.append(v)
            except (IndexError, ValueError):
                pass
        return max(versions, default=0) + 1

    def save_scenario(self, project: str, content: str, version: int | None = None) -> Path:
        """Save a scenario draft to script/scenario_v{N}.md."""
        if version is None:
            version = self.get_scenario_version(project)
        script_dir = PROJECTS_DIR / project / "script"
        script_dir.mkdir(parents=True, exist_ok=True)
        path = script_dir / f"scenario_v{version}.md"
        path.write_text(content, encoding="utf-8")
        logger.info("Scenario saved: %s", path)
        return path

    def run(self, project: str) -> dict:
        """Read sources and return summary for scenario creation."""
        data = self.read_sources(project)
        version = self.get_scenario_version(project)
        return {
            "text_count": len(data["texts"]),
            "image_count": len(data["images"]),
            "summary": data["summary"][:4000],
            "next_version": version,
            "sources": [name for name, _ in data["texts"]],
        }
