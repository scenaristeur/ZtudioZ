#!/usr/bin/env python3
"""Generate Scene 8 assets: sheets + board."""
import sys, requests, urllib.parse, time, yaml
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.utils import upload_to_tmpfiles

KEY = ''
OUT = Path('projets/50eme_rose')

# 1. Fiche Loge de Théâtre
fiche = {"name":"Loge de Théâtre","slug":"loge-theatre","type":"lieu","active_version":"v1","versions":{"v1":{"seed":87,"attributs":["Pièce exiguë","Costumes sur cintres","Perruques sur têtes","Miroir avec ampoules","Affiches de spectacles"],"palette":["rouge","noir","doré"],"prompt_image":"Loge de théâtre avec costumes et miroir"}}}
with open(OUT / "bible/lieux/loge-theatre/fiche.yaml", "w") as f:
    yaml.dump(fiche, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
print("Fiche Loge OK")

# 2. Catherine sheet
url = f"https://gen.pollinations.ai/image/{urllib.parse.quote('photographic character reference sheet, sepia warm tones, character is Catherine: actress 55 years old, red hair, theatrical makeup, theater dressing room, multiple panels, clean beige background, cinematic photography, film grain')}?model=gpt-image-2&width=768&height=768&seed=55&key={KEY}&nologo=true"
resp = requests.get(url, timeout=180)
(OUT / "bible/personnages/catherine/sheet_v1.png").write_bytes(resp.content)
print(f"Catherine: {len(resp.content)}b")
time.sleep(1)

# 3. Loge sheet  
url = f"https://gen.pollinations.ai/image/{urllib.parse.quote('photographic location reference sheet, sepia warm tones, theater dressing room interior, costumes wigs makeup mirror, multiple panels, cinematic photography')}?model=gpt-image-2&width=768&height=768&seed=87&key={KEY}&nologo=true"
resp = requests.get(url, timeout=180)
(OUT / "bible/lieux/loge-theatre/sheet_v1.png").write_bytes(resp.content)
print(f"Loge: {len(resp.content)}b")
time.sleep(1)

# 4. Upload refs
ph = upload_to_tmpfiles(OUT / "bible/personnages/paul-henri/sheet_v1.png", expire=172800)
cath = upload_to_tmpfiles(OUT / "bible/personnages/catherine/sheet_v1.png", expire=172800)
loge = upload_to_tmpfiles(OUT / "bible/lieux/loge-theatre/sheet_v1.png", expire=172800)
s8_refs = urllib.parse.quote(f"{ph}|{cath}|{loge}")

# 5. Board
board = "Storyboard Scène 8. ONLY Paul-Henri (florist suit) and Catherine (redhead actress) in theater dressing room. panel 1: dressing room, Catherine at mirror, Paul-Henri enters. panel 2: they talk, she agrees to help. 2 panels 1 row of 2, black ink drawing, gray shading, French captions, professional storyboard"
url = f"https://gen.pollinations.ai/image/{urllib.parse.quote(board)}?model=gpt-image-2&width=512&height=384&seed=8&key={KEY}&nologo=true&image={s8_refs}"
resp = requests.get(url, timeout=180)
(OUT / "preprod/storyboard/scene_08/storyboard_board.png").write_bytes(resp.content)
print(f"Board S8: {len(resp.content)}b")
