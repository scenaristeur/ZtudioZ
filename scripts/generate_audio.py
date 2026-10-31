#!/usr/bin/env python3
"""Génère l'audio des dialogues pour tous les plans d'une scène.

Lit storyboard.json pour les dialogues.
Lit chaque fiche personnage pour la voix TTS associée.
Génère les fichiers audio dans production/audio/<scene>/shot_XX_<role>.mp3

Usage: python scripts/generate_audio.py <project> <scene>
"""

import json, re, requests, sys, time, yaml
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROJECTS_DIR = Path(__file__).resolve().parent.parent / "projets"
CHARACTER_VOICES = {}  # rempli automatiquement depuis les fiches


def load_voices(project):
    """Charge les voix depuis les fiches personnages."""
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


def read_key():
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("POLLINATION_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


def parse_dialogue(text):
    """Extrait les répliques sous forme (personnage, texte).
    Gère : Nom: texte ou Nom Composé: texte."""
    text = text.strip().strip('\u00ab').strip('\u00bb').strip('"')
    pattern = r'([A-Za-zÉÈÊËÀÂÔÛéèêëàâôû][A-Za-zÉÈÊËÀÂÔÛéèêëàâôû\s\u2011-]+?)\s*:\s*(\u00ab.*?\u00bb|".*?"|[^A-Za-z\u00ab"].*?)(?=\s+[A-Za-zÉÈÊËÀÂÔÛéèêëàâôû]+\s*:|$)'
    lines = []
    for m in re.finditer(pattern, text, re.DOTALL):
        char = m.group(1).strip()
        dialog = m.group(2).strip().strip('\u00ab').strip('\u00bb').strip('"')
        if dialog:
            lines.append((char, dialog))
    if not lines:
        lines.append(("voix-off", text))
    return lines


def generate_scene_audio(project, scene):
    KEY = read_key()
    if not KEY:
        print("ERR: Clé API introuvable")
        return

    voices = load_voices(project)
    print(f"Voix chargées: {len(voices)} personnages")

    sb_path = PROJECTS_DIR / project / "preprod" / "storyboard" / f"scene_{scene:02d}" / "storyboard.json"
    if not sb_path.exists():
        print(f"ERR: {sb_path} introuvable")
        return

    shots = json.loads(sb_path.read_text())
    audio_dir = PROJECTS_DIR / project / "production" / "audio" / f"scene_{scene:02d}"
    audio_dir.mkdir(parents=True, exist_ok=True)

    for shot in shots:
        num = shot["number"]
        dialogue = shot.get("dialogue", "")
        if not dialogue:
            print(f"Shot {num}: pas de dialogue")
            continue

        repliques = parse_dialogue(dialogue)
        audio_paths = {}

        for i, (char_name, text) in enumerate(repliques):
            # Trouver la voix
            cl = char_name.lower()
            voice = voices.get(cl)
            if not voice:
                for key, v in voices.items():
                    if cl in key or key in cl:
                        voice = v
                        break
            if not voice:
                voice = voices.get("paul-henri", "onyx")

            filename = f"shot_{num:02d}_{char_name.lower().replace(' ','-')}.mp3"
            filepath = audio_dir / filename

            print(f"Shot {num}/{char_name}: génération ({voice})...", end=" ", flush=True)
            url = f"https://gen.pollinations.ai/audio/{quote(text)}?voice={voice}&key={KEY}"
            try:
                resp = requests.get(url, timeout=60)
                filepath.write_bytes(resp.content)
                print(f"{len(resp.content)}b")
                audio_paths[char_name.lower()] = str(filepath)
            except Exception as e:
                print(f"ERR: {e}")
            time.sleep(0.5)

        shot["audio_paths"] = audio_paths

    # Sauvegarder
    sb_path.write_text(json.dumps(shots, ensure_ascii=False, indent=2))
    print(f"\n✅ Scène {scene}: audio dans {audio_dir}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python scripts/generate_audio.py <project> <scene>")
        sys.exit(1)
    generate_scene_audio(sys.argv[1], int(sys.argv[2]))
