from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests
import yaml

logger = logging.getLogger(__name__)

PROJECTS_DIR = Path(__file__).resolve().parent.parent.parent / "projets"


def _read_api_key() -> str:
    env = Path(__file__).resolve().parent.parent.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("POLLINATION_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


def _load_voices(project: str) -> dict[str, str]:
    bible = PROJECTS_DIR / project / "bible" / "personnage"
    voices = {}
    if bible.exists():
        for d in bible.iterdir():
            fiche = d / "fiche.yaml"
            if fiche.exists():
                data = yaml.safe_load(fiche.read_text())
                if "voice" in data:
                    voices[data["name"].lower()] = data["voice"]
                    voices[d.name.lower()] = data["voice"]
    return voices


def _parse_dialogue(text: str) -> list[tuple[str, str]]:
    """Extract dialogue lines as (character, text) pairs.

    Handles:
    - Nom: dialogue
    - Nom Composé: dialogue
    - « guillemets »
    - Non-breaking hyphens (U+2011)
    """
    text = text.strip().strip("\u00ab").strip("\u00bb").strip('"')
    pattern = r'([A-Za-zÉÈÊËÀÂÔÛéèêëàâôû][A-Za-zÉÈÊËÀÂÔÛéèêëàâôû\s()\u2011-]+?)\s*:\s*(\u00ab.*?\u00bb|".*?"|[^A-Za-z\u00ab"].*?)(?=\s+[A-Za-zÉÈÊËÀÂÔÛéèêëàâôû]+\s*:|$)'
    lines = []
    for m in re.finditer(pattern, text, re.DOTALL):
        char = m.group(1).strip()
        dialog = m.group(2).strip().strip("\u00ab").strip("\u00bb").strip('"')
        if dialog:
            lines.append((char, dialog))
    if not lines:
        lines.append(("voix-off", text))
    return lines


def _match_voice(char_name: str, voices: dict[str, str]) -> str:
    cl = char_name.lower().replace("\u2011", "-")
    if cl in voices:
        return voices[cl]
    for key, v in voices.items():
        if cl in key or key in cl:
            return v
        first = cl.split()[0]
        if first and first in key:
            return v
    return "onyx"


class AudioStep:
    """Generate audio for each scene: dialogues, voice-over, ambiance.

    For each shot:
    1. Reads dialogue from storyboard.json
    2. Loads voice mappings from character fiches
    3. Generates per-character TTS via Pollinations
    """

    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    def run(self, project: str, scenes: list[dict], storyboard_dir: Path, audio_dir: Path) -> dict:
        results = {}
        api_key = _read_api_key()
        if not api_key:
            logger.error("API key not found")
            return results

        voices = _load_voices(project)
        logger.info("Loaded %d character voices", len(voices))

        for scene in scenes:
            scene_num = scene.get("number", 0)
            scene_key = f"scene_{scene_num:02d}"

            sb_path = storyboard_dir / scene_key / "storyboard.json"
            if not sb_path.exists():
                logger.warning("No storyboard for scene %d", scene_num)
                continue

            shots = json.loads(sb_path.read_text())
            scene_audio_dir = audio_dir / scene_key
            scene_audio_dir.mkdir(parents=True, exist_ok=True)

            logger.info("Scene %d: generating audio", scene_num)

            for shot in shots:
                num = shot["number"]
                dialogue = shot.get("dialogue", "")
                if not dialogue:
                    continue

                repliques = _parse_dialogue(dialogue)
                audio_paths = {}

                for char_name, text in repliques:
                    voice = _match_voice(char_name, voices)
                    safe_name = char_name.lower().replace(" ", "-").replace("\u2011", "-")
                    filename = f"shot_{num:02d}_{safe_name}.mp3"
                    filepath = scene_audio_dir / filename

                    if filepath.exists() and filepath.stat().st_size > 1000:
                        audio_paths[char_name.lower()] = str(filepath)
                        continue

                    encoded = quote(text)
                    url = f"https://gen.pollinations.ai/audio/{encoded}?voice={voice}&key={api_key}"

                    try:
                        resp = requests.get(url, timeout=60)
                        resp.raise_for_status()
                        filepath.write_bytes(resp.content)
                        audio_paths[char_name.lower()] = str(filepath)
                        logger.info("Shot %d/%s: %d bytes [%s]", num, char_name, len(resp.content), voice)
                    except requests.RequestException as e:
                        logger.warning("Shot %d/%s failed: %s", num, char_name, e)
                    time.sleep(0.5)

                shot["audio_paths"] = audio_paths

            sb_path.write_text(json.dumps(shots, ensure_ascii=False, indent=2))
            results[scene_key] = {"shots": shots, "count": len(shots)}

        return results
