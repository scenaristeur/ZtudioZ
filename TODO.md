# TODO — Incohérences, flows à améliorer, clarifications

> Inventaire non-exhaustif. Chaque entrée est un problème ou une piste identifiée lors de la revue (août 2026). Ordre = priorité décroissante.

## Bugs confirmés

### B1. ~~`VideoService._num_frames_for_duration` produit des frames non-8n+1~~ ✅ corrigé
`pipeline/services/video.py:197` — la formule `target + (1 - target % 8) % 8` donnait déjà le bon 8n+1, mais `frames += 1` ensuite cassait tout (5s → 122 au lieu de 121).

**Corrigé** : ligne `frames += 1` supprimée. Vérifié : 3s→73, 5s→121, 10s→241 (tous 8n+1).

### B2. ~~Clé API Albert en dur dans `vision.py` (fallback)~~ ✅ corrigé
`pipeline/services/vision.py:39-41` — une clé `sk_eyJ...` était hardcodée en fallback quand `OPENAI_API_KEY` manque. **Viole la règle absolue n°1** (jamais de clé en dur) et risque d'être commitée.

**Corrigé** : fallback supprimé ; `VisionAnalysisService` lève un `RuntimeError` clair si `OPENAI_API_KEY` manque. Vérifié que le working tree ne contient plus la clé (`git grep "sk-eyJ"` → vide).

> ⚠️ **Restant** : la clé est encore dans l'**historique git local** (aucun remote configuré). Si le repo doit un jour être partagé, réécrire l'historique ou tourner la clé.

### B3. ~~`orchestrator.generate_visualize` passe des kwargs invalides à `VisualizeStep`~~ ✅ corrigé
`pipeline/orchestrator.py` — passait `entities=entities` et `sketch_mode=True` à `VisualizeStep.run`/`__init__` qui ne les acceptent pas → `TypeError` latent (non atteint aujourd'hui, le chemin MCP passe par le toolkit).

**Corrigé** : kwargs retirés de l'appel.

## Incohérences config vs code

### C1. ~~`video.py` pollinations : `seedance` n'est plus le défaut~~ ✅ corrigé
Le code `VideoService.generate()` avait `model or cfg.get("model", "seedance")` — incohérent avec `config.yaml` (`grok-video-pro`).

**Corrigé** : défaut code aligné sur `grok-video-pro` (`video.py:40`).

### C2. ~~`image.py` fallback `flux` ignore les refs~~ ✅ corrigé (option `strict_refs`)
`ref_support`/`fallback_models` : `gpt-image-2` → `gptimage` → `flux`. METHODE.md dit "fallback interdit" pour les shots — le fallback automatique devait être optionnel.

**Corrigé** : option `strict_refs` (config `image.providers.pollinations.strict_refs`, défaut `false`). Si `true` + refs présentes → pas de fallback vers un modèle sans refs, le service lève une erreur. Traduction config de la règle "fallback interdit".

### C3. ~~`.env` vs code : `POLLINATION_API_KEY` vs `POLLINATIONS_API_KEY`~~ ✅ corrigé (unifié)
- `.env` et `config.yaml` : `POLLINATION_API_KEY` (singulier) — **canonique**
- `opencode.jsonc` (MCP pollinations) : mappe `{env:POLLINATION_API_KEY}` → `POLLINATIONS_API_KEY` (nom attendu par `@pollinations/mcp`) — conservé

**Corrigé** : suppression des double-tests pluriel/singulier et des fallbacks `os.environ` dans `scripts/generate_shots.py`, `scripts/generate_shot.py`, `pipeline/services/image.py`, `pipeline/ztudioz_toolkit.py`.

### C4. ~~`storyboard_board` appelle Pollinations en dur~~ ✅ corrigé
`pipeline/ztudioz_toolkit.py` — construisait une URL `gen.pollinations.ai/image/...` directement, en contournant `ImageService`.

**Corrigé** : réécrit via `ImageService.generate()` (respecte `image.active`).

### C5. ~~`storyboard_visualize` mélange ImageService et URL Pollinations~~ ✅ corrigé
`pipeline/ztudioz_toolkit.py:296-305` — board forcé sur `gpt-image-2`, shots en URL Pollinations brute.

**Corrigé** :
- plus de `model` forcé sur le board ;
- `image.py.generate()` route les refs multiples agnes (`|`) vers `compose()` ;
- `pipeline/steps/visualize.py` génère les shots via `self.image.generate()` (provider actif), imports `requests`/`quote`/`time` et check `api_key` retirés ;
- `_ensure_sheets` ne force plus `gpt-image-2`.

## Flows à améliorer

### F1. ~~Le mode dev MCP mute la config de l'instance~~ ✅ corrigé
`ztudioz_toolkit.py:513-515` — `cfg["dev"] = dev; svc.config = cfg` était fragile (mutabilité).

**Corrigé** : `generate_image`/`generate_video` construisent désormais le service avec `ImageService(config=cfg)` / `VideoService(config=cfg)` (override propre, pas de mutation).

### F2. ~~`_generate_agnes` (image) n'utilise pas le paramètre `model`~~ ✅ documenté
`image.py:64` — `model` est accepté mais seul `size` est envoyé (le serveur local :8765 choisit son modèle par défaut, vérifié : aucune route `/api/image/generate` n'accepte de param `model`).

**Corrigé** : le modèle est maintenant loggé comme ignoré côté local (au lieu d'être silencieux). Documenté dans DOC.md.

### F3. ~~Agnes vidéo apihub : retry 429 très long~~ ✅ corrigé (configurable)
`video.py:246-258` — 5 tentatives jusqu'à ~10 min d'attente totale.

**Corrigé** : retry réglable via `apihub_retries` (3), `apihub_retry_base_wait` (10s), `apihub_retry_max_wait` (60s), backoff `min(base * 2^attempt, max)` — dans `config.yaml`. `direct: false` par défaut (serveur local) reste le chemin par défaut.

### F4. ~~Pas de parallélisation des générations sheets/shots~~ ✅ corrigé (sheets)
`production.parallel_shots` n'était utilisé nulle part.

**Corrigé** : `SheetsStep.run` parallélise les générations via `ThreadPoolExecutor` quand `parallel_shots` est `true`.

### F5. ~~`run_pipeline` dans le toolkit n'accepte pas `--force`~~ ✅ corrigé
`ztudioz_toolkit.py:453` — pas de flag force.

**Corrigé** : paramètre MCP `force: bool = False` ajouté, transmis à `orch.run_pipeline(steps=..., force=force)`.

### F6. ~~`_AGNES_VALID_SIZES`/durées dupliquées avec le serveur local~~ ✅ corrigé
Tailles hardcodées dans `image.py:50`, durées dans `video.py:121`.

**Corrigé** : extraites dans `config.yaml` — `image.providers.agnes.valid_sizes` et `video.providers.agnes.valid_durations` (défauts 5, 10, 15, 18, 20). Repli sur les constantes si absentes.

## Clarifications / documenter

### D1. ~~Double doc WORKFLOW/METHODE/SPECS~~ ✅ partiellement résolu
Trois docs décrivent des workflows légèrement différents. Rôles clarifiés : `README.md` (état actuel) + `DOC.md` (inventaire) + `METHODE.md` (processus canonique) ; `WORKFLOW.md` et `SPECS.md` à archiver ou aligner.

**Fait** : DOC.md aligné (strict_refs, apihub retries, valid sizes/durations en config, run_pipeline force, parallélisation sheets, étape VisualizeStep via ImageService).
**Restant** : WORKFLOW.md (dit encore "seedance-2.0", "gpt-image-2 fallback flux") et SPECS.md (exemples de config `image.active: pollinations`, `video.model: seedance`) décrivent l'ancien état. À archiver ou aligner — pas critique car DOC.md est l'inventaire vivant.

### D2. `dev: true` par défaut dans config.yaml
`image.dev: true` et `video.dev: true` sont activés par défaut. C'est voulu pour le dev, mais **à désactiver avant une prod réelle**. Documenté dans AGENTS.md/DOC.md ("Modes dev").

### D3. ~~Nommage des répertoires de projets~~ ✅ vérifié, non bloquant
`.gitignore` ignore `projets*` ; `projets copy _planche_references_ok/` (37M) et `projets copy_storyboard_3/` (44M) restent à la racine. **Non bloquant** (ignorés par git, utilisés pendant la production 50eme_rose). À archiver hors repo quand la prod sera terminée.

### D4. `agents/storyboarder.py`, `video_producer.py`, `audio_designer.py` annoncés dans SPECS mais absents
Seul `character_designer.py` existe. Le reste est absorbé par le toolkit ztudioz. À décider : SPECS à jour (recommandé, l'approche toolkit est la bonne) ou création des agents.

### D5. ~~Vérifier la charge `.env` pour le MCP `ztudioz`/`character-designer`~~ ✅ corrigé
`pipeline/config.py` charge désormais `.env` explicitement : `load_dotenv(_ROOT / ".env")` avec `_ROOT = Path(__file__).resolve().parent.parent`. Fonctionne quel que soit le cwd de lancement du MCP. Vérifié : `_ROOT` = racine repo.

## Idées

- [ ] **Utiliser `agnes-ai-cli` comme couche d'exécution Agnes** (skill installé dans `.agents/skills/agnes-ai-skill/`) au lieu des requêtes HTTP directes dans `image.py`/`video.py`. Le CLI gère déjà auth, bridge local→URL publique, polling `videoId` et JSON. À évaluer : garder les wrappers Python mais remplacer les payloads bruts par des appels CLI, ou garder HTTP pour la perf.
- [ ] Benchmark des modèles Agnes (image 2.0 vs 2.1, vidéo v2.0) sur les mêmes prompts — actuellement testés ad-hoc.
- [ ] `ConsistencyService` : brancher réellement `production.consistency_check` dans le pipeline (présent dans config, usage partiel).
- [ ] Export JSON des entités bible pour la web UI (actuellement YAML seul).
- [ ] Tracker de coûts : ajouter le prix par modèle Agnes (pollen) dans `consumption.py` (today `get_tracker().track` sans coût Agnes).
- [ ] Git : le `opencode.jsonc` n'est PAS commité (`.gitignore` → `opencode*`), seul `example_opencode.jsonc` l'est. C'est voulu, mais vérifier que personne ne commit le vrai par erreur.
