---
name: ztudioz-clip
description: Produce a full music video clip from a timeline YAML to a final assembled MP4 (hero images → per-beat videos → montage on the MP3 track) via the data-driven clip pipeline. Capitalizes on the V4 'Le Jardinier des Ondes' and V2 Melody's Ghost experiences: character/location consistency from refs + strong constraints + bible **states**/**subrefs**, an explicit narrative arc, per-section coherent location, **double-anchor identity** (keyframes start+end), **format unification**, and automatic provider fallback (Agnes local → pollinations). Trigger words: clip, clip musical, produire un clip, timeline vers clip, générer le clip.
allowed-tools: Bash(python:*) Bash(.venv/bin/python:*) Bash(ffmpeg:*) Bash(ffprobe:*)
---

# ZtudioZ Clip — Pipeline data-driven de clips musicaux

Produire un **clip musical complet** à partir d'une **timeline YAML** : images héro par
section → vidéo par beat → montage final sur la piste MP3. Encapsulé dans `pipeline/clip/`,
piloté par le CLI `python main.py clip`.

Capitalise sur le REX V4 : `projets/KeepTalkingAndListening/preprod/clip/REX_v4.md` et sur
le REX Melody's Ghost (states/subrefs/double-ancre : cf. ce fichier sections « Règles d'or »
6-9 et `projets/Melody'sGhost/TRAVAUX_EN_COURS.md`).

## Vue d'ensemble du flux

```
timeline_v<N>.yaml (sections → beats)
    │  ┌─ images héro par section (compose refs perso+lieu)
    │  └─ vidéos par beat (i2v Agnes local, chaînage last_frame)
    └  montage final (assemble_scene → clip_vN.mp4 sur le MP3)
```

CLI :
```bash
.venv/bin/python main.py clip <projet> --variant v4 --mode images|video|full
   [--max-plans N] [--sections intro,A1] [--force]
```

## RÈGLES D'OR (issus du REX V4 — non négociables)

### 1. Un clip a BESOIN d'un fil conducteur (arc)
Le piège V4 : une timeline qui navigue librement entre pièces/actions sans progression
narrative = « beau mais répétitif, pas de fil conducteur ».
**Avant toute production, formaliser un ARC** (émotionnel / narratif / lumineux) qui
progresse du début à la fin. Calquer le canevas V3 « Le Voyageur » :
`analyse_v3.md` définit « Nuit froide → Lueur → Chaleur → Ambre saturé → Doré apaisé ».

### 2. Le LIEU est pilote de cohérence spatiale (champ par section)
Dans V4, les beats d'une même section (ex. A1 « rituel matinal ») passaient de la cuisine à
la chambre à l'atelier. Pour un clip c'était toléré, mais c'est un défaut.
**Aucun beat ne doit inventer un décor** : chaque section déclare UN lieu cohérent, le beat
décline l'action DANS ce lieu. Associer la sheet lieu en ref.

### 3. La consistance = refs + contrainte forte, PAS la description textuelle
- **Image héro** : composer AVEC les refs (sheet perso + sheet lieu), même pour les scènes à
  personnage unique.
- **Prompt** : contrainte en tête `REFERENCE ONLY for character identity (same face, same
  clothes). No other people.` / `NO PEOPLE: empty scene only.` pour les scènes vides.
- **Vidéo** : préfixe `REFERENCE IMAGE for character identity: match exactly (same face,
  same clothes, same hair).`
- Ne JAMAIS générer un personnage sans refs (dérive : jeune en salopette vs quinqua à
  lunettes).

### 4. Ordre des blocs du prompt image (≤ ~800 chars)
`contraintes critiques (REFERENCE ONLY/NO PEOPLE)` → `style guide court` → `staging` →
`light` → `mood` → `character` → `16:9 cinematic, film grain.`
>800 chars → troncature = perte des contraintes clés. Ne pas y mettre de retours à la ligne.

### 5. Fallback provider automatique
`_fallback_video_services()` ordonne [provider actif, backup]. Si Agnes local est down ou
les crédits Pollinations épuisés (402), la production continue. Testé : Pollinations → 402
en cours de route → bascule manuelle sur Agnes local (`http://127.0.0.1:8765`) sauvée la prod.

### 6. États visuels bibliques (states) + sous-références (subrefs) — Melody's Ghost
Chaque fiche peut déclarer des **states** (`versions.v1.states`) : pose (assis/au-volant/
marchant), moment, angle, humeur. Un état AVEC `sheet:` = image dédiée ; SANS `sheet` mais
avec `prompt_image:` = **sous-référence dynamique** (compose mono-réf base + ancre
`REFERENCE ONLY`) mise en cache dans `preprod/clip/<variant>/_subrefs/`. La **timeline
porte `states` par beat** (`states.perso`/`states.lieu`) → la pose du personnage et l'angle
du lieu **suivent les beats**, pas la sheet de base. Code : `_state_sheet()`,
`_refs_for_section(sec, beat)`, `_build_compose_refs()`.

### 7. Double-ancre identité (keyframes start+end) — Melody's Ghost
**Un générateur i2v simple (1 ref) perd l'identité au milieu du plan** (le héro/start est bon,
mais le personnage dérive). **Fix** : chaque plan = **mode keyframes 2 refs**
(`VideoService.generate(ref_images=[start, end])`) :
- `start` = image composée (dernière frame du plan précédent + sous-réfs pose/lieu du beat)
- `end` = image composée « pose finale du beat »
- prompt vidéo : `REFERENCE IMAGE for character identity: match exactly (same face, same
  clothes, same hair).` + description biblique (`CHARACTER: ...`)
Repli i2v simple auto si compose 503 → vérifier ce plan par **mid-frame** et régénérer seul.

### 8. Format unifié (start = last = vidéo) — Melody's Ghost
`.last.png` (dernière frame), `.start.png` (départ composé) et vidéo = **même format** sinon
rogne/étire d'un plan à l'autre. Extraire `.last.png` en natif (pas de recadrage 16:9) ;
recadrer le start au ratio de sortie du serveur vidéo (`_crop_to_video_ratio`). Recadrer, jamais étirer.

### 9. Ré-ancrage par section (section_reset) — Melody's Ghost
Dans un arc multi-sections au même lieu, sans `section_reset: true`, la dernière frame de la
section précédente pollue la suivante et le **héro validé de la section n'est jamais utilisé**.
Ré-ancrer sur le héro à chaque frontière de section, conserver le chaînage intra-section.

### 10. Validation : héro par section + mid-frame
Avant de produire les vidéos, générer **5 candidats héros par section** (`hero_candidates`)
et **valider la sélection avec l'utilisateur** (consistance + variation caméra/position selon
la timeline). Vérifier ensuite les vidéos sur une **mid-frame**, pas seulement au début.

## Files de la bible (sheets = refs)

```
bible/personnage/<slug>/fiche.yaml + sheet_v1.png   # états visuels (states → subrefs)
bible/lieu/<slug>/fiche.yaml + sheet_v1.png
```

La config variant (`preprod/clip/config.yaml`) liste `refs_bible:` pour mapper perso+lieu.
Les fiches peuvent déclarer des **states** (cold/active/warm/assis/au-volant/…) avec
`sheet_v<N>.<state>.png` ou `prompt_image` (subrefs dynamiques), chargés par plan via
`_load_entity_states()` + `_state_sheet()`.

## Workflow collaboratif (avec l'utilisateur)

1. **Poser les questions structurées AVANT de produire** (le V4 a souffert de l'absence de
   guidage) :
   - Quel est le fil conducteur / l'arc ? (début → fin)
   - Quelle progression dramatique section par section ?
   - Quel(s) lieu(x), de façon COHÉRENTE (pas un lieu par beat) ?
   - Pièces + moments (matin/midi/soir/nuit) pour la variété.
   - Déclarer `states` perso/lieu par beat si une pose/angle spécifique suit la musique.
2. **Générer les images héro** (`--mode images`), **5 candidats/section**, valider la
   consistance par vision LLM (compter les personnes, décrire le perso : "même
   face/vêtements", l'ambiance) et **soumettre les planches de contact à l'utilisateur**
   (`montage -tile 5x1 -label '%f'`) avant de produire.
3. **Tester quelques plans** (`--mode video --max-plans N`) avant la production complète —
   vérifier sur mid-frame que l'identité ne dérive pas (double-ancre).
4. **Produire tout** (`--mode video --max-plans 999`, sans --force pour garder le cache).
5. **Monter** (`--mode full` : réutilise les vidéos en cache, assemble sur le MP3).

## Vérification (vision LLM)

Avant de valider un lot, analyser des frames (`ffmpeg -vf select=eq(n\,60)` ou `fps=1`) :
- **Consistance perso** : « same short dark hair, light shirt/apron » sur tous les plans
  (en **mid-frame**, pas seulement au début).
- **Variété** : pièces/moments/actions/caméras/positions différents (la variation suit la
  timeline).
- **Ambiance** : lumière cohérente avec le style guide.
Robuste dans `pipeline/clip/verify.py` (score 0-1) et `diversity.py` (doublons CLIP).

## Pièges connus

- **Crop 16:9** : `_crop_image_169` doit calculer le crop en Python (pas d'expressions ffmpeg
  `iw:iw*9/16` ambigus qui échouent quand la source est proche de 16:9). Corrigé ; reporter
  via `_crop_169_filter(src_w, src_h)`.
- **Vidéos Agnes local** : sortent à 704x512 (pas 480x270 même en dev) — le serveur local a
  sa propre résolution. Ne pas re-cropper à la volée, utiliser le crop 16:9 filé au montage.
- **Format chaîné** : `.last.png` / `.start.png` / vidéo doivent être le MÊME format
  (start = last = vidéo) sinon rogne/étire d'un plan à l'autre. Extraire `.last.png` en
  natif, recadrer le start au ratio serveur.
- **`--max-plans`** s'applique PAR ARC (à chaque arc, pas global) : pour produire tout, ne
  pas passer une petite valeur.
- **2 refs max** sur le serveur local Agnes ; 3+ refs → `direct: true` (apihub).
- **Drift mi-plan** : i2v simple perd l'identité au milieu → mode **keyframes 2 refs**
  (start+end). Vérifier par mid-frame.
- **Segments vs musique** : le montage découpe à la durée de section mais le rythme des cuts
  n'est pas calé sur les onsets/RMS (boîte à outils `audio_analysis.py` dispo, à brancher).
- **Timeout CLI** : la production des plans peut dépasser 1h (Agnes local ~1min/plan).
  Lancer en arrière-plan ou par blocs de sections.

## Script d'assistance

`pipeline/clip/steps.py` = ClipStep (tout le flux). CLI `python main.py clip`.
Pour l'analyse musicale : `ClipStep().analyze_clip_audio(mp3)` → `analysis.json`.
Pour l'alignement des beats sur la musique : `ClipStep().auto_align_beats(analysis, timeline)`.
