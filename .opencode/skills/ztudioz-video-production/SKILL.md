---
name: ztudioz-video-production
description: >-
  Produire les vidéos d'une scène dans ZtudioZ - le step keyframes (alternative à produce) avec
  beats calés sur la timeline de chaque plan, image de départ = shot du storyboard, beats évolution
  sur Agnes et multi_ref gpt-image, chaînage, ratio unifié, limite de 2-3 images par appel apihub
  avec fenêtres glissantes, vérification par vision de chaque keyframe et validation humaine avant
  vidéo. Couvre aussi le mode produce i2v. Utilise pour lancer ou déboguer la production vidéo d'un
  projet. Déclencheur : produce, keyframes, kf, beat, vidéo, shot_NN.mp4, i2v, doublon, drift,
  vérifier une keyframe, valider avant vidéo.
---

# Production vidéo

**Dépendances** : `ztudioz-parse-storyboard` (timeline de plan), `ztudioz-consistency`
(règles d'identité), `ztudioz-providers` (modes, quotas, formats).

## Deux modes, une seule vidéo à faire

| Mode | Step | Vidéo par plan | Identité |
|---|---|---|---|
| i2v | `produce` | 1 appel, shot → vidéo | ⚠️ dérive possible en milieu de plan |
| keyframes | `keyframes` | chaîne d'images puis interpolation | ✅ bornée start + end |

Choisir via `production.keyframes.enabled` dans `config.yaml`. Ce sont des **branches
concurrentes**, pas deux étapes successives. En cas de doute, demander plutôt que d'enchaîner.

## `produce` (i2v / double-ancre)

Un plan = un shot (`shot_NN.png`) = image de départ, puis `VideoService.generate()`. Par
défaut la **double-ancre** est active : `ref_images=[shot_N, shot_{N+1}]` (keyframes 2 refs)
quand le shot suivant existe, sinon repli en i2v simple 1 ref. L'identité est bornée des
deux côtés et le plan ne dérive plus en milieu de mouvement. Désactivable par projet via
`production.produce.double_anchor: false`.

Vidéo dans `production/video/scene_NN/shot_NN.mp4`. **Ne jamais écraser un fichier vidéo
> 5 KB** (règle de production, respect du cache) ; `--force` ou un nom différent pour
régénérer. Vérifier la video par vision (notamment mid-frame) — en repli i2v 1 ref,
l'identité peut encore dériver en milieu de plan.

## `keyframes` (mode principal, anti-drift)

Ce que fait le step, par plan du storyboard :

1. **Caler les beats sur la timeline du plan** (`_plan_scene`, une passe LLM par scène). Le
   LLM reçoit la `timeline` du plan (actions/dialogues + timestamps) et ancre chaque beat à
   l'événement réel (`source`) ; `t_seconds` suit le timestamp du dialogue. Les discours
   rythme les keyframes.
2. **Échelonner les timestamps** (`_finalize_beats`) : si le LLM renvoie des beats au même
   `t_seconds` (souvent tous à la durée), les répartir strictement croissant entre 0 et la
   durée du plan.
3. **Image de départ = le shot du storyboard d'origine** (`shot_NN.png`), pas un start
   généré. Le shot a été produit par `visualize` avec les sheets en refs : c'est la référence
   fiable. Ne pas produire un start ad hoc mono-image (gptimage seul) — il dérive. Si le shot
   manque, le créer en multi_ref (gpt-image + sheets en refs) ; si le storyboard a changé
   (ex. 10 → 22 plans), régénérer le start via `VisualizeStep(shot_number=N)`.
4. **Beats = deltas, pas des re-descriptions** : chaque beat est **uniquement le delta
   d'action** entre deux frames (ex. « le facteur tend la lettre »), **en français**. Ne
   jamais réécrire le décor / les vêtements / le style dans un beat : l'identité visuelle
   dérive. Les prompts de scène, de plan, de personnages (bible) et d'ambiance
   (`prompt_image` + `character_details` + `style-guide.yaml`) sont **réinjectés** à la
   génération de chaque image (`_compose_beat_prompt`).
5. **Identité bible prioritaire** : `_character_details` réinjecte **tous** les personnages
   de la scène à **chaque** keyframe, placés **avant** le `prompt_image` du plan — la bible
   l'emporte sur un conflit de tenue du storyboard. Les slugs des personnages sont ajoutés
   aux refs de **chaque** beat `multi_ref` (pas seulement le premier) pour stabiliser
   l'identité.
6. **Image finale dédiée** (`keyframes.shot_end`, défaut `true`) : `_break_down_beats` demande
   un `end_prompt` (état final du plan, français) et ajoute un beat `is_end` après les beats.
7. **Chaînage** : chaque image générée devient la source de la suivante via
   `VideoService.generate(ref_images=[...])`. Un beat « évolution » sur **Agnes** (source =
   image précédente, faible coût) ; un beat « multi_ref » sur **gpt-image** uniquement
   quand une nouvelle référence est nécessaire.
8. **Ratio unifié** (`keyframes.image_size`, ex. `1344x768`) appliqué **à la fois** aux
   évolutions (Agnes) et aux multi_ref (gpt-image), pour éviter la dérive de cadrage.
9. **Durée = maximum** : `duration_seconds` plafonne la vidéo. La durée de chaque segment
   suit la temporisation réelle des keyframes (`t_seconds` des beats), **jamais** `plan ×
   nb segments`.
10. **Fenêtres glissantes** (limite apihub : mode keyframes = 2 à 3 images max) : 4+ keyframes
    sont découpés en fenêtres de 3 avec chevauchement, puis concaténés par ffmpeg
    (`assemble_scene`).

Vidéos au **même** emplacement que `produce` (`production/video/scene_XX/shot_NN.mp4`) + un
snapshot `production.json` (plan + `video_path`, source pour l'audio et le montage). **Ne
jamais écraser `storyboard.json`.**

## Vérifier chaque keyframe avant de continuer

`keyframes.verify_each_keyframe` : avant de passer à la keyframe suivante, la vision LLM
(config `keyframes.verify_model`) vérifie la conformité sur 4 axes : continuité avec l'image
précédente, conformité au prompt de transition (delta), conformité au découpage storyboard
du plan, conformité au script original (`scenario.md`, `_script_scene_text`). Verdict JSON
sauvegardé (`kf_N.verify.json`). Si non conforme, boucle interactive :
`(c)` continuer · `(r)` régénérer avec correction du delta · `(p)` proposer une correction
données/outil (journalisée dans `verification_corrections.md`) · `(s)` skip · `(q)` quit.
`keyframes.verify_max_tries` = nb max de régénérations.

Même défaut que le portail de l'orchestrateur : l'`input()` n'est pas gardé par `isatty()`.
Piloté par un agent (stdin fermé), la boucle de vérification lève `EOFError` et le step
échoue. Poste de travail : **valider avec `ztudioz_analyze_images` entre deux appels**, et
lancer `keyframes` avec la vérification interactive désactivée.

## Validation humaine avant la vidéo

`keyframes.validate_before_video` : affiche les prompts (scène / plan / perso), les deltas de
transition et les keyframes ; choix : `(c)` continuer · `(r)` régénérer avec correction ·
`(s)` skip · `(q)` quit. Comme le portail de l'orchestrateur, cet `input()` est nu : depuis
un agent il lève `EOFError`. C'est le dernier point de contrôle avant d'engager les crédits
vidéo — c'est donc le bon moment pour valider à la main avec `ztudioz_analyze_images`, puis
lancer avec la validation interactive désactivée.

## Cache lié au prompt

Chaque image a son `kf_N.txt` (prompt composé) ; l'image n'est réutilisée que si le prompt
n'a pas changé. Une keyframe en cache **non encore vérifiée** (pas de `kf_N.verify.json`
conforme) est re-vérifiée au prochain run. Purge la sortie (`--force`) pour régénérer.
