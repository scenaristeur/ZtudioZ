---
name: ztudioz-keyframes
description: Use to smooth/fluidify existing storyboard shots of a scene into animated video transitions via Agnes keyframes (2+ reference images), including creating intermediate keyframe images and managing them on disk (naming, manifest, cache). Also the recommended identity-stabilization mode to prevent mid-plan character drift in i2v (double-anchor start+end). Trigger words: keyframes, transition entre plans, fluidifier, morph entre shots, vidéo de transition, double-ancre, drift identité.
allowed-tools: Bash(python:*) Bash(.venv/bin/python:*) Bash(ffmpeg:*)
---

# ZtudioZ Keyframes — transitions vidéo entre plans storyboard

Transformer des **plans storyboard existants** (shots d'une même scène) en **vidéos
de transition animées**, grâce au mode `keyframes` d'Agnes (2+ images de référence).
Valide sur scène 6 (50eme_rose) : transition shot_01 → shot_03 avec son + mouvement
fluide, mêmes personnages, même lieu.

> **Anti-drift identité** : ce mode est AUSSI le correctif recommandé quand un **i2v simple
> (1 ref)** dérive l'identité d'un personnage au milieu du plan (capitalisé du clip
> Melody's Ghost). Voir section « Double-ancre identité » en fin de fichier.

## Principe clé

`VideoService.generate()` accepte plusieurs images de référence :

- **1 ref** → mode `i2v` (animer une image)
- **2 refs** → mode `keyframes` (transition start → end)
- **2+ refs** → mode `keyframes` multi (transition sur N points)
  - **serveur local `:8765`** : **2 images max** (`reference_image` start + `end_frame_image` end)
  - **apihub direct** (`direct: true`) : N images via `extra_body.image` (liste) + `mode: keyframes`

`ref_image` accepte aussi la convention ImageService `"a.png|b.png"` ; une liste
explicite `ref_images=[...]` prime. Le **cache MD5** inclut toutes les refs.

## Workflow (à suivre dans l'ordre)

### 1. Choisir les images start/end

- **Shots d'une même scène** : `projets/<slug>/preprod/storyboard/scene_<NN>/shot_*.png`
  (défaut du script : premier et dernier shot).
- Le start et l'end doivent être **visuellement liés** : même lieu, mêmes personnages,
  même style. Sinon le modèle ignore l'end (testé : 2 planches de personnages différents
  → résultat sans intérêt).

### 2. Valider la compatibilité (vision LLM)

Avant de lancer, analyser les images avec `ztudioz_analyze_images` (ou `describe_sheet`) :
- même lieu / même personnage / même échelle ?
- plans composites (character sheets à 5 panneaux) = **mauvais matériau**. Préférer des
  images uniques (ex. `preprod_refs/<entity>_face.png` ou les shots).

### 3. Créer les images intermédiaires (si besoin)

Quand le saut entre start et end est trop grand, générer **1..N images intermédiaires**
via `ImageService.compose` (composition multi-refs Agnes, `extra_body.image`, base64,
apihub → nécessite `AGNES_API_KEY`) :

```python
ImageService(config=cfg_agnes).compose(
    prompt=f"Intermediate frame {k}/{n} between these two frames of the same scene, "
           f"same characters, same location, same cinematic style, natural in-between state",
    ref_images=[start, end],          # chemins locaux (encodés en base64, pas d'upload)
    output_dir=key_dir, filename=f"kf_{k}_inter.png",
)
```

- Pondérer le prompt par le ratio `k/(n+1)` (proche du début pour k=1, proche de la fin
  pour k=n).
- 2 intermédiaires (3 refs au total) suffisent généralement ; au-delà de 3-4, la qualité
  plafonne et le coût/le temps explosent.

### 4. Générer la vidéo de transition

```python
VideoService(config=cfg).generate(
    prompt="Smooth cinematic transition between these keyframes of the same scene, "
           "same characters and location, subtle camera move and gentle action, "
           "keep style and composition consistent, no identity morphing.",
    ref_images=[start, inter_1, inter_2, end],  # ou [start, end] pour 2 refs
    duration=5, seed=None,
    output_dir=key_dir, filename="transition_<scene>.mp4",
)
```

- 2 refs → serveur local (pas de clé API). **3+ refs → `direct: true`** (apihub,
  `AGNES_API_KEY`, ~1 RPM compte gratuit : retry configuré dans `config.yaml`).
- Mode dev (`dev: true`) = résolutions réduites, à utiliser pour les tests.

### 5. Vérifier le résultat (vision LLM)

Extraire des frames (`ffmpeg -vf fps=1`), les analyser :
- continuité (même lieu/personnages), pas de déformations, respect de l'end frame.
- Si le résultat n'est pas pertinent, corriger le matériau (intermédiaires, meilleures
  images start/end) — pas le mode.

## Script d'assistance

`.agents/skills/ztudioz-keyframes/scripts/keyframes_flow.py` encapsule tout le flux :

```bash
.venv/bin/python .agents/skills/ztudioz-keyframes/scripts/keyframes_flow.py 50eme_rose 6 --dev
.venv/bin/python .agents/skills/ztudioz-keyframes/scripts/keyframes_flow.py 50eme_rose 6 --dev --intermediates 2
.venv/bin/python .agents/skills/ztudioz-keyframes/scripts/keyframes_flow.py 50eme_rose 6 --start a.png --end b.png
.venv/bin/python .agents/skills/ztudioz-keyframes/scripts/keyframes_flow.py 50eme_rose 6 --list
```

Le script : copie start/end dans le dossier keyframes, crée les intermédiaires (avec
cache), choisit automatiquement local (`2 refs`) ou apihub direct (`3+ refs`), génère la
vidéo et écrit un manifest. Lancer de préférence avec `--dev` pour les itérations rapides.

## Gestion des keyframes (structure + cache)```
projets/<slug>/preprod/keyframes/scene_<NN>/
├── kf_00_start.png          # copie du shot de départ
├── kf_1_inter.png …         # images intermédiaires générées
├── kf_0N_end.png            # copie du shot de fin
├── transition_<refs>.mp4    # vidéo (nom = hash MD5 contenu, cache automatique)
└── keyframes_manifest.json  # scène, refs, prompt, seed, provider, date
```

- **Cache** : ne pas "forcer" la régénération sans raison. `--force` = nom de fichier
  unique (timestamp) ; `--regen-intermediates` = supprime les intermédiaires existants.
- **Reproductibilité** : le manifest garde le prompt + seed ; relancer avec les mêmes
  paramètres = cache hit.
- **Versioning** : les fichiers sont immuables (nom = contenu). Pour un A/B, varier le
  `--prompt`/`--seed` ou `--start`/`--end` — chaque variante produit un fichier distinct.

## Pièges connus

- **Planches composites** (character sheets multi-panneaux) : inutilisables en keyframes
  direct. Utiliser des images uniques.
- **2 images max sur le serveur local** : pour plus de 2 refs, `direct: true` obligatoire.
- **Refs URLs tmpfiles** : expirent (48h par défaut). Le service ré-uploade automatiquement
  les chemins locaux ; si l'URL est morte, ré-uploader.
- **Keyframes apihub** : ~1 RPM sur compte gratuit — ne pas lancer 10 générations en parallèle.
- **Durées** : `valid_durations` (5/10/15/18/20s) côté local ; `num_frames` 8n+1 (≤441) côté
  apihub. Ne pas sortir de ces plages.
- **`--list`** avant de travailler sur un projet pour voir ce qui existe déjà.

## Double-ancre identité (anti-drift mi-plan) — capitalisé du clip Melody's Ghost

**Symptôme** : un plan vidéo démarre avec le bon personnage mais « glisse » vers un autre au
milieu. Cause : **i2v simple = 1 seule ancre (le start)** — le modèle réinterprète le visage
en mouvement.

**Fix (mode keyframes 2 refs)** — l'identité est **bornée des deux côtés** :
```python
VideoService(config=cfg).generate(
    prompt="REFERENCE IMAGE for character identity: match exactly (same face, same clothes, same hair). <beat> ...",
    ref_images=[start, end],   # 2 refs → serveur local (pas de clé API)
    duration=5, ...
)
```
- `start` = image composée (dernière frame plan précédent + sous-réfs perso/lieu du beat)
- `end` = image composée « pose finale du beat »
- Vérifier le résultat sur une **mid-frame** (`ffmpeg -ss <dur/2>`), pas seulement au début.
- Repli i2v simple si la composition de l'end échoue (502/503 apihub) → vérifier le plan et
  le régénérer seul si dérive.

Intégré nativement dans `pipeline/clip/steps.py` (`_gen_video` → `ref_images=[start, end]`) ;
le mécanisme est réutilisable pour toute génération cinéma.
