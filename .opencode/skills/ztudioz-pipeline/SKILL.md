---
name: ztudioz-pipeline
description: >-
  Exécuter ou comprendre le pipeline cinéma de ZtudioZ - les 10 étapes réelles (init, parse, fiches,
  sheets, storyboard, board, visualize, produce, keyframes, audio), la CLI main.py, les 25 outils MCP
  ztudioz, les checkpoints et la reprise (resume), la validation humaine c/r/s/p/q. Utilise pour
  lancer une scène, savoir quelle étape produit quoi, répondre à "quelle étape vient après",
  "comment reprendre", "que fait produce". Déclencheur : pipeline, étape, run, resume, checkpoint,
  main.py, run_pipeline, MCP ztudioz.
---

# Pipeline cinéma ZtudioZ

**Dépendances** : aucune. Point d'entrée du cycle de production.

## Les 10 étapes réelles

Source de vérité : `AVAILABLE_STEPS` dans `pipeline/orchestrator.py:111`.

```
init → parse → fiches → sheets → storyboard → board → visualize → produce | keyframes → audio
```

| Étape | Service | Produit | Fichier |
|---|---|---|---|
| `init` | — | arborescence `projets/<slug>/` | `orchestrator.py:310` |
| `parse` | LLM | `script/parsed/scene_NN.yaml` + champ `timeline` | `steps/parse.py` |
| `fiches` | LLM | `bible/personnage\|lieu\|objet/<slug>/fiche.yaml` | `steps/fiche_step.py` |
| `sheets` | Image | `bible/<type>/<slug>/sheet_v1.png` | `steps/sheets.py` |
| `storyboard` | LLM (2 passes) | `preprod/storyboard/scene_NN/storyboard.json` | `steps/storyboard.py` |
| `board` | Image | `storyboard_board_<n>plans.png` — planche croquis wilderien de validation | `steps/visualize.py` / MCP `storyboard_board` |
| `visualize` | Image | `shot_NN.png` — images photo des plans | `steps/visualize.py` |
| `produce` | Video | `production/video/scene_NN/shot_NN.mp4` (double-ancre 2 refs, repli i2v 1 ref) | `steps/produce.py` |
| `keyframes` | Image+Video | `production/video/scene_NN_kf/` (chaîne d'images) | `steps/keyframes_step.py` |
| `audio` | Audio | `production/audio/scene_NN/shot_NN_<role>.mp3` | `steps/audio_step.py` |

### Stages (recut narratif)

`run --steps move` fonctionne : les noms de stages sont expandés en étapes réelles
(`Orchestrator.STAGES`), les identifiants d'étapes et les checkpoints ne changent pas.

| Stage | Étapes | Sens |
|---|---|---|
| `prepare` | `init`, `parse` | écrire le scénario |
| `identify` | `fiches`, `sheets` | bible + références visuelles |
| `cut` | `storyboard`, `board` | découpage en plans + planche de validation |
| `frame` | `visualize` | plans en images |
| `move` | `produce`, `keyframes` | vidéo (2 branches concurrentes) |
| `sound` | `audio` | voix off + ambiances |
| `assemble` | *(aucune)* | montage = `pipeline.services.montage`, pas une étape |

`produce` injecte le verrou de scène (`SCENE LOCK`) et utilise la **double-ancre** :
`ref_images=[shot_N, shot_{N+1}]` quand le shot suivant existe, sinon i2v simple.
Désactivable par projet via `production.produce.double_anchor: false`.

## Deux erreurs à ne pas reproduire

**`board` EST une étape — et c'est un point de validation.** La planche croquis wilderien
(`storyboard_board_<n>plans.png`) se génère juste après `storyboard.json` et AVANT `visualize`,
pour valider la composition des plans (salle, public, écran, personnages) à moindre coût (1 image
au lieu de N photos). La planche n'est pas le produit final : elle sert de gate. Un storyboard
dessiné doit montrer la salle PLEINE de public et le contenu de l'écran — pas une salle vide.
Voir `ztudioz-parse-storyboard` pour le contenu de la planche et le prompt.

**`produce` et `keyframes` sont deux branches concurrentes** du même moment (la vidéo), pas deux étapes successives. C'est `production.keyframes.enabled` dans `config.yaml` qui décide laquelle est active. Si le LLM a un doute, le dire plutôt que d'enchaîner les deux.

## Modes d'accès

### CLI

```bash
python main.py init <slug> <chemin/script.md>   # arborescence + parse
python main.py run <slug> [--steps parse,fiches] [--force]
python main.py resume <slug>                     # reprise après pause
python main.py status <slug>                     # checkpoint + entités
python main.py switch-version <slug> <entity> <v2>
python main.py clip <slug> --variant <id> --mode images|video|full
```

### MCP ztudioz (25 outils, `pipeline/ztudioz_toolkit.py:44-71`)

`init_project` · `list_projects` · `get_status` · `parse_script` · `generate_fiches` ·
`generate_sheets` · `upload_sheets` · `storyboard_text` · `storyboard_visualize` ·
`storyboard_board` · `produce_video` · `generate_audio` · `run_pipeline` ·
`switch_version` · `generate_image` · `generate_video` · `say_audio` · `llm_chat` ·
`upload_media` · `get_project_files` · `analyze_images` · `analyze_character_sheet` ·
`analyze_animatic` · `generate_clip` · `analyze_clip_audio`

`run_pipeline(project, steps, force)` exécute plusieurs étapes ; `force` régénère malgré le cache.
`upload_sheets` ré-uploade les refs tmpfiles (48 h) avant toute génération avec refs.

Il n'existe **pas** d'étape `montage` : `services/montage.py:assemble_scene` /
`assemble_film` sont des fonctions appelées par `keyframes_step.py` et `clip/steps.py`.
N'appelle jamais `run_pipeline(project, "montage")`.

## Ordre strict et validation

- **Ordre impératif** : `sheets` (références) AVANT `storyboard`, et `storyboard` AVANT `visualize`. Ne jamais lancer les shots sans les sheets de la scène.
- **Une scène à la fois** : on ne passe à la suivante qu'après validation.
- **Validation humaine** à chaque porte. Le comportement est réglé par
  `production.validation_mode` :

  | Mode | Comportement | Quand |
  |---|---|---|
  | `interactive` | Affiche la porte et appelle `input()`. **Exige un TTY** : sans terminal, le run s'arrête avec une erreur explicite au lieu de rester bloqué ou de planter sur `EOFError`. | un humain pilote dans un terminal |
  | `manual` | Journalise la porte et continue sans prompt. | un agent valide entre deux appels d'étapes |
  | `off` | Aucune porte. | production automatique assumée |

  L'ancien booléen `production.human_validation` reste honoré **seulement** si
  `validation_mode` est absent : `true` → `interactive`, `false` → `off`.

  Depuis un agent, le seul mode sûr est `manual` (ou les outils MCP étape par étape, qui ne
  traversent jamais le portail). Voir `ztudioz-video-production` pour le même problème aux
  portails keyframes.
- **Coût** : `production.budget_per_scene: 0` désactive le plafond. Au-dessus de 0, le
  pré-vol (`pipeline/cost_preflight.py`) refuse de démarrer une scène qui dépasse le
  plafond ; `--force` passe outre. L'estimation signale les modèles sans prix connu plutôt
  que d'inventer un montant.
- **Checkpoint** : `.checkpoint.json` écrit après chaque étape ; `resume` s'appuie dessus.

## Connaître l'état d'un projet

```bash
python main.py status <slug>
```

`projets/<slug>/.checkpoint.json` donne les étapes faites, `projets/<slug>/bible/` les
entités, `projets/<slug>/production/video/` les vidéos. Un projet **non suivi par git**
(`.gitignore` contient `projets*`) : c'est le seul endroit où lire l'état, et il n'a aucun
filet de sécurité.

## Corrigé : `produce` était relancé à chaque run

L'orchestrateur écrivait le checkpoint sous la clé `"production"` alors que l'identifiant de
l'étape est `"produce"`. `_get_completed_steps()` ne voyait donc jamais `produce` comme
accompli, et `run` relançait la vidéo à chaque fois ; seul le saut fichier >5 KB dans
`produce` évitait la régénération. Le checkpoint est migré à la lecture, et
`_get_completed_steps()` vérifie désormais la présence réelle des artefacts
(`shot_*.png`, `*.mp4`, `*.mp3`) : un projet interrompu entre l'écriture du fichier et celle
du checkpoint n'est plus rejoué. Voir `ztudioz-troubleshooting`.
