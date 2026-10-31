# DOC — Inventaire des workflows, méthodes et possibilités de ZtudioZ

> Document vivant. Répertorie **tout** ce qu'on a développé : les workflows de production, les providers/services et leurs alternatives, les modes d'accès, les outils MCP, les skills et les scripts. Complémentaire de `README.md` (vue d'ensemble), de `METHODE.md` (processus détaillé) et de `SCHEMAS.md` (schémas Mermaid des deux pipelines).

## 1. Workflow de production (méthode Wilder)

Une scène à la fois, validée humainement avant de passer à la suivante.

```
0. INTENTION    But dramatique de la scène (script/parsed/scene_N.yaml)
1. EXTRAIRE     Personnages, lieux, objets présents (slugify)
2. FICHER       Fiche canonique par entité (bible/<type>/<slug>/fiche.yaml)
3. SHEETS       Images de référence : objets → personnages → lieux
4. STORYBOARD   Découpage textuel en plans (preprod/storyboard/scene_N/storyboard.json)
5. BOARD        Planche storyboard en grille (storyboard_board.png)
6. SHOTS        Une image par plan (shot_N.png) — source de vérité
7. VIDÉO        I2V depuis les shots (production/video/scene_N/shot_N.mp4)
8. AUDIO        TTS des dialogues (production/audio/scene_N/shot_N_role.mp3)
9. MONTAGE      ffmpeg assemble_scene / assemble_film
```

Chaque étape produit un checkpoint et s'arrête pour validation (`c`ontinue / `r`etry / `s`kip / `p`ause / `q`uit).

### Variante "tout automatique" (pipeline orchestré)

Via `main.py run <slug> [--steps ...]` ou l'outil MCP `run_pipeline` : parse → fiches → sheets → storyboard → visualize → produce → audio, avec checkpoint et resume.

### Détails des étapes (cf. METHODE.md)| Étape | Entrée → Sortie | Service | Notes |
|-------|-----------------|---------|-------|
| `ParseStep` | script.md → scene_*.yaml | LLM | title, location, characters, ambiance, intention, description, character_details (clothing/attitude) |
| `FicheStep` | scènes → fiches YAML | LLM | name, slug, type, active_version, voice (TTS), versions.v1 {seed, attributs, personnalite/atmosphere, palette, accessoires, prompt_image} |
| `SheetsStep` | fiche → sheet_v1.png | Image | prompt template character_sheet/location_sheet/object_sheet ; **parallélisable** via `production.parallel_shots` (ThreadPoolExecutor) |
| `StoryboardStep` | scène → storyboard.json | LLM | shots: number, type (PLAN LARGE/MOYEN/GROS PLAN), camera, description, duration_seconds, prompt_image, dialogue, intention |
| `VisualizeStep` | storyboard → board + shot_N.png | Image | board + shots générés via `ImageService` (provider actif) |
| `ProduceStep` | shot → shot_N.mp4 | Video | I2V, seed global, durée calée sur les dialogues |
| `AudioStep` | dialogue → shot_N_role.mp3 | Audio | voix lue dans la fiche (`voice:`) |

### Règles de cohérence strictes

- **Vêtements** définis dans `character_details.clothing`, identiques sur tous les plans, mentionnés dans chaque `prompt_image`.
- **Lieux** : le fond ne change pas entre les plans d'une scène.
- **Références** : les shots préprod sont la source de vérité ; ne jamais régénérer les frames vidéo.
- **Fallback interdit** si le modèle gère les refs : plutôt ré-uploader les refs (expire=172800) et réessayer.

## 2. Services & providers

Config : `pipeline/config.yaml`, bascule via `active:`. Chaque service expose `generate()` avec cache MD5.

### 2.1 LLM (`services/llm.py`)

| Provider | Modèle | Type | Usage |
|----------|--------|------|-------|
| `albert` *(actif)* | `openai/gpt-oss-120b` | tool_calling_model (Camel) | Parsing, fiches, storyboard, agents |
| `agnes` | `agnes-2.5-flash` | tool_calling_model | Alternative gratuite (base `https://apihub.agnes-ai.com/v1`) |
| `pollinations` | `gemini-fast` | chat_completion | Alternative simple (pas de tool-calling fiable) |

- `ToolCallingModel` (Camel) : intercepte `[TOOL_CALLS]` texte → tool_calls natifs, répare le JSON mal formé.
- `LLMService.vision(images, prompt)` : vision via Albert/Mistral.
- `create_agent(system, tools)` : ChatAgent Camel pour orchestration interne.

### 2.2 Vision (`services/vision.py`)

`VisionAnalysisService` : analyse d'images (base64 direct, pas d'upload). Utilise `OPENAI_API_KEY`/`OPENAI_BASE_URL` + `MODEL_LARGE`, resize à 1024×768.

Méthodes : `analyze`, `analyze_character_sheet`, `analyze_animatic`, `compare_images`. `consistency.py` : `check_character`, `check_location`, `check_scene`.

### 2.3 Image (`services/image.py`)

| Provider | Modèle | Refs | Accès |
|----------|--------|------|-------|
| `agnes` *(actif)* | `agnes-image-2.1-flash` | 1 ref (upload reference_image) | serveur local :8765 `POST /api/image/generate` |
| `agnes` compose | `agnes-image-2.1-flash` | **multi-refs** (`extra_body.image`) | apihub `POST /v1/images/generations` |
| `pollinations` | `gpt-image-2` → `gptimage` → `flux` | oui (sauf flux/sana) | `GET gen.pollinations.ai/image/{prompt}` |
| `focus` | — | non | FOOCUS Gradio `/sdapi/v1/txt2img` |

- Tailles Agnes valides : `valid_sizes` dans `config.yaml` (reflète la table du serveur local : `1024x1024`, `768x1152`, `1152x768`, `768x1344`, `1348x768`, `1792x1024`, `1024x1792`). Dev → `768x1152` (tier 1K).
- **`strict_refs`** (option pollinations, config) : si `true` + refs présentes → **pas de fallback** vers `flux`/`sana` (qui ignorent les refs), le service lève une erreur au lieu de générer sans refs. Traduction config de la règle "fallback interdit".
- `compose()` : combine plusieurs images de référence en une seule (idéal : personnages en plan à deux). En dev → tier `1K` + `ratio`.
- **Coûts Pollinations** : `gpt-image-2` ~0.03 pollen/image (512²), `flux` ~0.00175.

### 2.4 Vidéo (`services/video.py`)

| Provider | Modèle | Refs | Accès |
|----------|--------|------|-------|
| `agnes` *(actif)* | `agnes-video-v2.0` | i2v (1 ref) | serveur local :8765 `/api/tasks/simple` → poll → `/api/video/{id}` |
| `agnes` direct | `agnes-video-v2.0` | t2v / i2v / **keyframes** (2+) | apihub `POST /v1/videos` (rate-limit ~1 RPM) |
| `pollinations` | `grok-video-pro` (défaut), `seedance-2.0`, `wan-fast`, `ltx-2`, `veo` | seedance (2 max : start+end), grok (multi) | `GET gen.pollinations.ai/video/{prompt}` |

- Durées Agnes locales valides : `valid_durations` dans `config.yaml` (défauts 5, 10, 15, 18, 20 s). `frame_rate` 24, `num_frames` 8n+1 (max 441).
- **`direct: true`** dans le provider agnes → apihub. Sans → serveur local (file d'attente, plus fiable).
- **Retry 429 apihub direct** : réglable via `apihub_retries` (3), `apihub_retry_base_wait` (10s), `apihub_retry_max_wait` (60s) ; backoff exponentiel `min(base * 2^attempt, max)`.
- Dev → `480x480`. **Coûts Pollinations** : `wan-fast` ~0.01/s, `grok-video-pro` ~0.05/s.

### 2.5 Audio (`services/audio.py`)

`AudioService.say(text, voice)` → `GET text.pollinations.ai/say/{text}?voice=` ; `respond(prompt, voice)` → `POST /respond`. Voix : `onyx`, `alloy`, `echo`, `fable`, `nova`, `shimmer`.

### 2.6 Montage (`services/montage.py`)

`assemble_scene(video_paths, audio_paths)` et `assemble_film(scene_paths)` via ffmpeg.

### 2.7 Bible

- **YAML** *(actif)* : `bible/<type>/<slug>/fiche.yaml` versionnée (v1/v2…), `active_version`, A/B testing via `switch_version`.
- **Grist** : configuré (`GRIST_SERVER`/`GRIST_DOC_ID`/`GRIST_API_KEY`) mais inutilisé.

## 3. Modes d'accès

| Mode | Outils | Quand |
|------|--------|-------|
| **CLI** | `main.py init/run/resume/status/switch-version` | Pipeline complet, validation humaine |
| **MCP ztudioz** | `run_mcp_server.py` (outils dans `ztudioz_toolkit.py`) | Depuis opencode, étape par étape |
| **MCP character-designer** | `agents/character_designer.py` | Fiches + sheets entités |
| **MCP pollinations** | `npx @pollinations/mcp` | Génération directe image/vidéo/text |
| **Scripts** | `scripts/generate_shot.py`, `generate_shots.py`, `generate_videos.py`, `generate_audio.py` | Production Pollinations "à l'ancienne" (50eme Rose) |
| **API web** | `api/` (FastAPI) + `web/` (Vue.js) | Interface web en cours (cf. PLAN_REFACTOR.md) |

### Outils MCP ztudioz (liste complète)

`init_project`, `list_projects`, `get_status`, `parse_script`, `generate_fiches`, `generate_sheets`, `upload_sheets`, `storyboard_text`, `storyboard_visualize`, `storyboard_board`, `produce_video`, `generate_audio`, `run_pipeline` (option `force` pour régénérer malgré le cache), `switch_version`, `generate_image`, `generate_video`, `say_audio`, `llm_chat`, `upload_media`, `analyze_images`, `analyze_character_sheet`, `analyze_animatic`, `get_project_files`.

### Outils MCP character-designer

`create_character_fiche`, `generate_character_sheet`, `describe_sheet`.

## 4. Skills (orchestration .md)

Dans `skills/` + symlinks `.opencode/skills/` :

| Skill | Rôle |
|-------|------|
| `create-scenario.md` | Assistant scénariste : sources → logline/synopsis/structure → scenario_v{N}.md |
| `new-project.md` | Initialisation de projet |
| `produce-scene.md` | Production complète d'une scène (chaîne d'outils ztudioz). Inclut les leçons consistance/arc/fallback du clip V4 |
| `fix-consistency.md` | Réparation de cohérence visuelle (check cause racine — refs/contraintes — avant régénération) |
| `regenerate.md` | Régénération d'un asset depuis son prompt sauvegardé |

**Skills ztudioz** (`.agents/skills/`, découverts par opencode) :

| Skill | Rôle |
|-------|------|
| `ztudioz-clip` | Production d'un clip musical data-driven (timeline → images → vidéos → montage). Capitalise le REX V4 |
| `ztudioz-keyframes` | Transitions vidéo entre plans storyboard (Agnes keyframes 2+) |

**Skill tiers installé** (`.agents/skills/agnes-ai-skill/SKILL.md`) :

| Skill | Rôle |
|-------|------|
| `agnes-ai-skill` (v1.2.2, [repo](https://github.com/jomeswang/agnes-ai-skill)) | Appels Agnes via le CLI `agnes-ai-cli` (`npx -y agnes-ai-cli@^0.1.0`). Couvre auth, text/image/video, compose multi-refs, keyframes, polling. Confirme : base `/v1`, `videoId` pour le polling (legacy `taskId` compatible), `num_frames` ≤ 441 et 8n+1, `frame_rate` 1-60 |

## 5. Scripts standalone (`scripts/`)

| Script | Usage |
|--------|-------|
| `generate_shot.py <projet> <scene> <shot>` | Génère UN plan avec refs filtrées |
| `generate_shots.py <projet> <scene>` | Tous les plans d'une scène |
| `generate_videos.py <projet> <scene>` | Vidéos depuis les shots préprod |
| `generate_audio.py <projet> <scene>` | TTS dialogues |

→ Ces scripts sont en cours d'absorption dans `pipeline/steps/` (PLAN_REFACTOR.md).

## 6. Providers opencode (`opencode.jsonc`)

- Provider **agnes** (npm `@ai-sdk/openai-compatible`, baseURL apihub/v1, `{env:AGNES_API_KEY}`) avec modèles gratuits :
  - Texte : `agnes-2.0-flash`, `agnes-2.5-flash`
  - Image (modalities output image) : `agnes-image-2.0-flash`, `agnes-image-2.1-flash`
  - Vidéo (modalities output video) : `agnes-video-v2.0`
- MCP : `character-designer`, `pollinations` (clé via `{env:POLLINATION_API_KEY}`), `ztudioz`.

> **Règle** : aucun modèle payant ni clé en dur dans `opencode.jsonc`. Le `opencode.jsonc` réel est `.gitignore`d — `example_opencode.jsonc` est l'exemple versionné.

## 7. Versionnement et historique

- Bible versionnée par entité (fiches + sheets, A/B testing).
- Plan cible : `pipeline/history/manager.py` (HistoryManager : snapshot, rollback, diff) — **non implémenté**, voir PLAN_REFACTOR.md.

## 8. Budgets & limites connues

| Ressource | Coût / limite |
|-----------|---------------|
| Agnes image tiers | 1K=20 RPM → 4K=1 RPM |
| Agnes vidéo (gratuit) | ~1 RPM effectif ; 429 fréquents quand une tâche lourde tourne |
| Pollinations `gpt-image-2` | ~0.03 pollen/image |
| Pollinations vidéo | `wan-fast` 0.01/s, `grok-video-pro` 0.05/s |
| tmpfiles | URLs expirent (max 48h) |
| Vision Albert | resize 1024×768 obligatoire pour éviter timeouts |

## 9. Tests

- `test_system.py` : smoke test système.
- `py_compile` : vérification syntaxe après édition (pas de linter dédié).
- Validation manuelle des assets via vision (sheets ↔ fiches, shots ↔ fiches, animatiques).

## 10. Ressources externes utiles

| Ressource | Lien | Intérêt pour nous |
|-----------|------|-------------------|
| Astuces de prompt vidéo Agnes | https://video.lichuanyang.top/fr/guides/prompt-tips | 5 règles de prompt alignées avec METHODE.md : concret > adjectifs, une action par phrase, lumière/angle explicites, style en première ligne, verrouiller le sujet via refs |
| Agnes Video Generator (open source) | https://github.com/lcy362/agnes-video-generator | Générateur vidéo Agnes gratuit self-hosté ; source d'exemple/comparaison pour notre serveur local et la démo web |
