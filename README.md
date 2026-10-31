# ZtudioZ — Pipeline de Production Audiovisuelle par IA

Système **config-driven** qui transforme un script (ou une idée) en film animé via des agents LLM, génération d'images, vidéo et audio — avec validation humaine à chaque étape (méthode Wilder : _une scène à la fois_).

```
Script.md → LLM → bible YAML versionnée → fiches personnages/lieux/objets
→ sheets (images de référence) → storyboard (découpage + dessins)
→ vidéo (I2V depuis les shots) → audio TTS → montage ffmpeg → film final
```

## Documentation

| Doc           | Contenu                                                                                          |
| ------------- | ------------------------------------------------------------------------------------------------ |
| **`DOC.md`**  | Inventaire complet : workflows, services, providers, modèles, alternatives, MCP, skills, scripts |
| `METHODE.md`  | Processus de production détaillé (cycle en 8 étapes, règles de cohérence, budgets)               |
| `WORKFLOW.md` | Pipeline Wilder (intention → montage), modèles et pièges                                         |
| `SCHEMAS.md`  | Schémas Mermaid des deux pipelines (cinéma + clip) : flux, étapes, points communs                |
| `SPECS.md`    | Spécifications techniques (config YAML, bible, services, agents CAMEL)                           |
| `TODO.md`     | Incohérences, flows à améliorer/clarifier                                                        |

## Stack actuelle

| Couche                 | Provider actif                                               | Alternatives                                                            |
| ---------------------- | ------------------------------------------------------------ | ----------------------------------------------------------------------- |
| LLM pipeline           | **Albert API** (`gpt-oss-120b`, tools)                       | Agnes (`agnes-2.5-flash`), Pollinations (`gemini-fast`)                 |
| LLM vision             | **Albert API** (`Mistral-Small-3.2-24B`)                     | —                                                                       |
| Image gen              | **Agnes** (`agnes-image-2.1-flash`, serveur local :8765)     | Pollinations (`gpt-image-2`/`gptimage`/`flux`), FOOCUS (Gradio local)   |
| Composition multi-refs | **Agnes apihub** (`compose()` avec `extra_body.image`)       | Pollinations multi-ref                                                  |
| Video gen              | **Agnes** (`agnes-video-v2.0`, local :8765 ou apihub direct) | Pollinations (`grok-video-pro`/`seedance-2.0`/`wan-fast`/`ltx-2`/`veo`) |
| Audio TTS              | **Pollinations** (`text.pollinations.ai/say`)                | —                                                                       |
| Bible                  | **YAML versionné** (v1/v2…, switch `active_version`)         | Grist (configuré, inutilisé)                                            |
| Montage                | ffmpeg (`montage.py`)                                        | —                                                                       |

Tout se change dans `pipeline/config.yaml`, sans toucher au code. Chaque service a `active:` + une liste de `providers:`.

## Prérequis

- Python 3.10+, `.venv/` : `pip install -r requirements.txt`
- Fichier `.env` à la racine (chargé automatiquement) :

```env
OPENAI_API_KEY=...              # Albert (LLM + vision)
OPENAI_BASE_URL=https://albert.api.etalab.gouv.fr/v1
POLLINATION_API_KEY=...         # Pollinations (image/vidéo/audio) — optionnel
AGNES_API_KEY=...               # Agnes (image/vidéo/LLM)
FOOCUS_URL=http://localhost:7860 # optionnel, fallback image Gradio
```

> **Clés API** : elles ne sont **jamais** écrites dans `opencode.jsonc` — les providers/MCP référencent `{env:NOM_VAR}` qui résout depuis `.env`.

## Providers Agnes

### Mode dev (basse résolution)

Le flag `dev: true` dans `config.yaml` active les résolutions réduites pour tester sans brûler le quota (tiers de résolution Agnes, cf. [tokenplan](https://www.agnes-ai.com/en/docs/tokenplan)) :

- **Images** : `dev_size: 768x1152` (tier **1K**, 20 RPM) au lieu de `size: 1024x1024`
- **Vidéos** : `dev_width/dev_height: 480x480` au lieu de `768x1152`
- Les outils MCP `generate_image`/`generate_video` acceptent un paramètre `dev`

### Deux accès Agnes

| Accès             | URL                                                    | Usage                                                                                                                                                                                   |
| ----------------- | ------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Serveur local** | `http://localhost:8765` (`npx free-short-video`)       | Défaut. File d'attente intégrée, gère les rate-limits. Image : `/api/image/generate` → poll `/api/image/{id}`. Vidéo : `/api/tasks/simple` → poll `/api/tasks/{id}` → `/api/video/{id}` |
| **Apihub direct** | `https://apihub.agnes-ai.com/v1` (clé `AGNES_API_KEY`) | Composition d'images multi-refs (`compose()`). Vidéo : `POST /v1/videos` (activable via `direct: true`) — **quota vidéo ~1 RPM** sur compte gratuit                                     |

## Utilisation

### CLI (`python main.py`)

```bash
python main.py init <slug> <chemin/script.md>   # arborescence + parse + bible
python main.py run <slug> [--steps parse,fiches,sheets] [--force]
python main.py resume <slug>                     # reprise après pause
python main.py status <slug>                     # checkpoint + entités
python main.py switch-version <slug> <entity> <v2>  # A/B testing
```

Étapes : `init`, `parse`, `fiches`, `sheets`, `storyboard`, `visualize`, `produce`, `audio`

### Serveurs MCP (opencode)

Trois serveurs dans `opencode.jsonc` :

| Serveur              | Commande                                          | Outils                                                                                                                                         |
| -------------------- | ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `ztudioz`            | `.venv/bin/python run_mcp_server.py --mode stdio` | Pipeline complet (init/parse/fiches/sheets/storyboard/produce/audio), vision, upload, `generate_image`/`generate_video`/`say_audio`/`llm_chat` |
| `character-designer` | `.venv/bin/python -m agents.character_designer`   | Fiches + sheets personnages/lieux/objets, description vision                                                                                   |
| `pollinations`       | `npx @pollinations/mcp`                           | Génération directe image/vidéo/text/audio (MCP officiel)                                                                                       |

`example_opencode.jsonc` : modèle minimal sans secrets.

### Pipeline MCP (via les outils ztudioz)

```
ztudioz.init_project → parse_script → generate_fiches → generate_sheets
→ storyboard_text → storyboard_visualize (board + shots) → produce_video
→ generate_audio → montage (ffmpeg)
```

## Arborescence projet

```
projets/<slug>/
├── script/               # scenario.md + parsed/scene_*.yaml
├── bible/                # personnages|lieux|objets/<slug>/fiche.yaml + sheet_v1.png
├── preprod/storyboard/<scene>/   # storyboard.json + board + shot_*.png
├── production/video/<scene>/     # shot_*.mp4
├── production/audio/<scene>/     # dialogues TTS
├── postprod/             # montage + film final
└── logs/ + consumption_<session>.json
```

## Cohérence des personnages

- **Seed fixe** par personnage (bible, versionnée) + `mix_alias` (acteurs connus) pour stabiliser le style
- **Sheets** comme images de référence → upload tmpfiles (48h) → refs dans les prompts images/vidéos
- **Vision LLM** (`VisionAnalysisService`) : décrit/vérifie sheets, shots, animatiques, cohérence inter-scènes
- Les **shots préprod** sont la source de vérité pour les vidéos — on ne régénère pas les frames vidéo

## Résilience

- **Checkpoint** après chaque étape (`.checkpoint.json`) + `resume`
- **Retry** avec backoff exponentiel (3-5 tentatives, gère les 429)
- **Cache MD5** : images/vidéos/audio non regénérés si le hash existe
- **Validation humaine** : pause après chaque étape (`c`ontinue/`r`etry/`s`kip/`p`ause/`q`uit)
- **Fallback modèle** dans Pollinations (ex. `gpt-image-2` → `gptimage` → `flux`), refs retirées si le modèle ne les supporte pas
- **Fallback provider automatique** : si le provider actif (ex. Pollinations) renvoie 402/503/429, `ImageService`/`VideoService` basculent seuls sur le provider backup de `config.yaml` (ordre = `get_service_providers_ordered()`, actif en premier), garde anti-boucle — hérité du clip V4 (un 402 bloquait toute la prod avant bascule manuelle)

## Logs et consommation

`projets/<slug>/logs/` (session) + `consumption_<session>.json` (calls, tokens, coûts par service via `get_tracker()`).

## Projet de démo : 50ème Rose

17 scènes, 39 fiches, 39 sheets, 58 shots, 55 vidéos — terminé sous Pollinations (voir `METHODE.md`).

# tests en cours
