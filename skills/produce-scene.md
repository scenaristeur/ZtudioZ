# produce-scene.md

## Role
Scene production agent. Produces all assets for a single scene
from parsed script to final montage.

## Inputs
- `project_slug`: string
- `scene_number`: int

## Steps

### 1. Check Status
Call `ztudioz.get_status(project_slug)` to check what's already done.
- If scene is fully complete, ask user to confirm regeneration.

### 2. Parse Script
Call `ztudioz.parse_script(project_slug)`.
- Skip if `scene_{N}.yaml` already exists.

### 3. Generate Fiches
Call `ztudioz.generate_fiches(project_slug)`.
- Skip if bible entities already exist for this scene's characters.

### 4. Generate Sheets
Call `ztudioz.generate_sheets(project_slug)`.
- Skip if `sheet_v1.png` exists for each entity.

### 5. Storyboard Text
Call `ztudioz.storyboard_text(project_slug)`.
- Skip if `storyboard.json` exists for this scene.

### 6. Storyboard Visualize
Call `ztudioz.storyboard_visualize(project_slug, scene_number)`.
- Generates shot images from storyboard.
- Each shot gets max 4 refs from bible sheets.

### 7. Produce Video
Call `ztudioz.produce_video(project_slug)`.
- Generates end frames + video clips.
- Uses preprod shots as start frames.
- Model: wan-fast (fallback: grok-video-pro).

### 8. Generate Audio
Call `ztudioz.generate_audio(project_slug)`.
- TTS for dialogues per character.
- Voice matched from character fiches.

### 9. Assemble Montage
Call `ztudioz.run_pipeline(project_slug, "montage")`.

## Consistance personnages/lieux (CRITIQUE — transféré du clip V4)

Le leçon n°1 apprise en confectionnant le clip « Le Jardinier des Ondes » : générer un
personnage **sans refs** donne un personnage différent à CHAQUE image/vidéo (jeune en
salopette vs quinqua à lunettes, etc.). La description textuelle ne suffit jamais.

**Règle : composer TOUJOURS avec les refs sheets (perso + lieu), même pour un plan à
personnage unique**, en portant dans le prompt la contrainte d'identité forte :

- **Image** : préfixe `REFERENCE ONLY for character identity (same face, same clothes).`
  et `No other people.` / `NO PEOPLE: empty scene only.` pour les plans vides.
- **Vidéo (I2V)** : préfixe `REFERENCE IMAGE for character identity: match exactly
  (same face, same clothes, same hair).`
- Ne JAMAIS retirer les refs « pour éviter les personnages fantômes » — c'était une
  fausse piste. La pièce manquante était la **contrainte explicite** dans le prompt.

Ordre des blocs du prompt image (≤ ~800 chars sinon troncature qui perd les contraintes) :
`contraintes critiques → style guide court → staging → light → mood → character → ratio`.

## Arc / fil conducteur (transféré du clip V4)

Un montage de beaux plans sans progression narrative = « répétitif, pas de fil
conducteur ». Avant de storyboarder une SCÈNE, vérifier qu'elle sert un **arc global**
(intention/scène, cf. METHODE étape 0). Ne pas enchaîner des plans qui ne forment pas
un mouvement dramatique.

## Pitfalls (accumulated)
- `gpt-image-2` rejects `image` param for board templates
- Character names use `\u2011` (non-breaking hyphen) → match by first name
- `flux` never supports refs → fallback: gpt-image-2 → gptimage (mais garder `strict_refs`
  à true : mieux vaut échouer que générer sans refs et produire un personnage incohérent)
- Budget: ~0.01 pollen/image, ~0.15 pollen/video
- Never overwrite existing videos >5KB
- Audio uses Pollinations `/audio/` endpoint, not `/say/`
- **Fallback provider** : si le provider actif renvoie 402/503 (crédits épuisés, serveur
  down), basculer sur un backup (Agnes local/ apihub ⇄ Pollinations) plutôt que de
  s'arrêter. En clip, `_fallback_video_services()` l'automatise ; pour le cinéma, prévoir
  le même ordre [actif, backup].

## États visuels & sous-références (capitalisé du clip Melody's Ghost)

Une fiche peut déclarer des **états visuels** (`versions.v1.states`, YAML anchors) :
- état AVEC `sheet:` → image de référence dédiée (`sheet_v1.<etat>.png`)
- état SANS `sheet` mais avec `prompt_image:` → **sous-référence dynamique** générée en
  cache (compose mono-réf base + ancre `REFERENCE ONLY`), ex. `_subrefs/<slug>_<etat>_<hash>.png`

**Quand l'utiliser** : le script veut un personnage qui « marche » mais la sheet de base le
montre « assis ». Déclarer `states.marche: {prompt_image: ...}` et composer la référence de
pose selon la **timeline** (le beat/pas l'action). Code : `_state_sheet()`, `_refs_for_section(sec, beat)`,
`_build_compose_refs()`. Fallback sheet de base seulement si la sheet d'état est déclarée
mais manquante.

## Double-ancre identité (capitalisé du clip Melody's Ghost)

**Symptôme** : le début de plan montre le bon personnage, mais le milieu « glisse » vers un
autre (i2v simple = 1 seule ancre au début).

**Fix** : mode **keyframes 2 refs** (`VideoService.generate(ref_images=[start, end])`) —
identité bornée des deux côtés :
1. `start` = image composée (dernière frame du plan précédent + sous-réfs pose/lieu).
2. `end` = image composée « pose finale du beat ».
3. Prompt : `REFERENCE IMAGE for character identity: match exactly (same face, same clothes, same hair).`
   + description biblique du personnage.

Repli i2v simple auto si la composition de l'end échoue (502/503 apihub) → vérifier ce plan
par vision (mid-frame !) et le régénérer si dérive.

## Chaînage & format (capitalisé du clip Melody's Ghost)

- **`section_reset: true`** (chaining) : ré-ancrer sur le héro de la nouvelle section à
  chaque frontière (sinon la dernière frame de la section précédente pollue la suivante).
- **Format unifié** : `.last.png` (dernière frame), `.start.png` (départ composé) et vidéo
  de sortie = **exactement le même format**. Extraire `.last.png` en natif (pas de recadrage
  16:9), recadrer le start au ratio de sortie du serveur vidéo. Recadrer, jamais étirer.
- **Validation vision** : vérifier une **mid-frame** par plan (pas seulement le début) pour
  détecter le drift mi-plan.

## Validation
After each step, verify output files exist and are >0 bytes.
Report progress via WebSocket if available.
