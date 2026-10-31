# WORKFLOW — Pipeline ZtudioZ (Méthode Wilder)

> Document de travail décrivant **comment on produit aujourd'hui** : le pipeline config-driven, ses étapes, ses modes d'accès et ses pièges.
> La **méthode** (cycle, validation, cohérence) est détaillée dans `METHODE.md` (processus canonique) ; l'**inventaire complet** des services/providers/modèles dans `DOC.md` ; la **vue d'ensemble** dans `README.md`.
> *"Une scène qui ne fonctionne pas, c'est la faute de la virgule qui est mal placée." — Wilder*

## Philosophie

1. **Une scène à la fois** — Jamais la suivante sans validation.
2. **Commencer par l'intention** — Chaque scène a un but dramatique.
3. **Chaque élément sert le climax** — Pas de remplissage.
4. **Le piège se resserre** — Escalade constante.
5. **Le sérieux absolu** — Jouer le drame, le rire naît du décalage.
6. **Le cynisme tendre** — Personne n'est parfait.
7. **Le silence est une réplique** — Ce qui n'est pas dit compte autant.

## Ambiance générale

Définie dans `bible/ambiance/style-guide.yaml` avant la première scène.
Contient : palette, format (Cinemascope), grain pellicule, composition. Injective dans tous les prompts.

## Cycle par scène — étapes du pipeline

```
init → parse → fiches → sheets → storyboard → board → visualize → produce → audio
```

| Étape | Entrée → Sortie | Service | Livrable |
|-------|-----------------|---------|----------|
| `init` | script.md + slug → arborescence | — | `script/parsed/`, `bible/`, `preprod/`, `production/` |
| `parse` | script.md → scènes | LLM | `script/parsed/scene_N.yaml` (title, location, characters, ambiance, intention, description, character_details) |
| `fiches` | scènes → fiches | LLM | `bible/<type>/<slug>/fiche.yaml` (name, slug, type, active_version, voice, versions.v1) |
| `sheets` | fiche → image de référence | Image | `bible/<type>/<slug>/sheet_v1.png` (parallélisable via `production.parallel_shots`) |
| `storyboard` | scène → découpage | LLM | `preprod/storyboard/scene_N/storyboard.json` (plans, camera, durée, dialogue, prompt_image) |
| `board` | storyboard → planche | Image | `preprod/storyboard/scene_N/storyboard_board.png` (grille des plans, en-tête projet/scène) |
| `visualize` | storyboard → shots | Image | `preprod/storyboard/scene_N/shot_N.png` (1 image par plan, avec refs sheets via `ImageService`) |
| `produce` | shot → vidéo | Video | `production/video/scene_N/shot_N.mp4` (I2V, seed global) |
| `audio` | dialogue → audio | Audio | `production/audio/scene_N/shot_N_role.mp3` (voix lue dans la fiche) |

**Règles de cycle :**
- Chaque étape produit un **checkpoint** et s'arrête pour validation humaine (`c`/`r`/`s`/`p`/`q`).
- **Une scène à la fois** : on ne passe à la suivante qu'après validation des shots.
- Les **shots préprod sont la source de vérité** — ne jamais régénérer les frames vidéo.
- **Ordre strict des étapes pré-prod** : `sheets` (images de référence) AVANT `storyboard`, et `storyboard` AVANT `board` et `visualize` (shots). Les shots utilisent les sheets comme **refs** (ré-uploadées, `expire=172800`). Ne jamais lancer les shots sans que les sheets de la scène existent.

## Modes d'accès

| Mode | Commande | Usage |
|------|----------|-------|
| **CLI** | `python main.py run <slug> [--steps ...] [--force]` | Pipeline complet avec checkpoints |
| **CLI** | `python main.py init / resume / status / switch-version` | Init, reprise après pause, état, A/B |
| **MCP ztudioz** | `run_mcp_server.py` | Depuis opencode, étape par étape (outils dans `ztudioz_toolkit.py`) |
| **MCP character-designer** | `agents/character_designer.py` | Fiches + sheets entités |

### Outils MCP ztudioz utiles

- `run_pipeline(project, steps, force)` — exécute plusieurs étapes (le flag `force` régénère malgré le cache)
- `storyboard_visualize(project, scene_number)` — board + shots d'une scène
- `produce_video`, `generate_audio` — production plan par plan
- `upload_sheets` — upload des refs tmpfiles (expire 48h) avant génération
- `analyze_images` / `analyze_character_sheet` — validation vision

## Providers actifs (config `pipeline/config.yaml`)

| Couche | Provider actif | Modèle | Changement de provider |
|--------|----------------|--------|------------------------|
| LLM | `albert` | `openai/gpt-oss-120b` | `services.llm.active` |
| Vision | `albert` | `Mistral-Small-3.2-24B` | `services.vision.active` |
| Image | `agnes` | `agnes-image-2.1-flash` (serveur local :8765, ou apihub via `direct`) | `services.image.active` |
| Vidéo | `agnes` | `agnes-video-v2.0` (local :8765, ou apihub `direct`) | `services.video.active` |
| Audio | `pollinations` | TTS `say`/`respond` | `services.audio.active` |

Changer `active:` suffit — le code ne connaît pas les providers en dur.

**Fallback provider automatique** : `ImageService`/`VideoService` retentent sur le provider
backup de `config.yaml` (ordre = `get_service_providers_ordered`, actif en premier) quand
le provider actif renvoie 402/403/429/5xx ou une erreur réseau — garde anti-boucle. Ex. si
`video.active: pollinations` est en 402, la vidéo part sur `agnes` en secours, sans
intervention.

## Pièges connus (production)

- **`.env` à la racine obligatoire** : `config.py` fait `load_dotenv(_ROOT/.env)`, fonctionne quel que soit le cwd (MCP inclus).
- **dev: true est actif par défaut** en config (résolutions réduites : image 768×1152, vidéo 480×480). **À désactiver avant une prod réelle.**
- **Ne jamais écraser une vidéo existante >5KB** — respecter les caches MD5. Utiliser `--force` seulement si nécessaire.
- **strict_refs** (image pollinations) : si `true` + refs, pas de fallback vers un modèle sans refs (`flux`/`sana`) — traduit la règle "fallback interdit".
- **Retry 429 apihub Agnes** : réglable via `apihub_retries` / `apihub_retry_base_wait` / `apihub_retry_max_wait`.
- **Refs tmpfiles** : expirent à 48h max. Ré-uploader avant chaque génération avec refs.
- **Serveur Agnes local** : n'accepte pas de param `model` (il choisit son modèle par défaut) ; tailles/durées valides dans `config.yaml` (`valid_sizes`/`valid_durations`).

## Vérifications

```bash
.venv/bin/python -m py_compile <fichier.py>          # après chaque édition
.venv/bin/python test_system.py                      # smoke test système
.venv/bin/python -c "import json5; json5.load(open('opencode.jsonc'))"
```

## Projets

### 50eme_rose (en cours)

| Info | Valeur |
|------|--------|
| Titre | *La Cinquantième Rose* |
| Scènes | 17 (parsées, fiches, sheets, storyboards, shots) |
| Statut | ✅ Storyboard complété — 🟡 Production vidéo/audio en cours |
