# PLAN_REFACTOR.md — ZtudioZ v2

## Diagnostic

Trois générations de projets coexistent, chacune avec ses forces et faibesses :

| Génération | Position | Ce qu'elle fait bien | Ce qui manque |
|------------|----------|---------------------|---------------|
| HoloAtelier | `/home/smag/dev/HoloAtelier/` | Constitution JSON, governance engine, 13 rôles, 84 tests | Jamais arrivé à la production |
| studio_v0 | `/home/smag/dev/studio_v0/` | Skill learning, YAML workflows, 24 agents | Jamais intégré à opencode |
| ZtudioZ (v1) | `/home/smag/dev/ZtudioZ/` | MCP opencode, a produit 17 scènes | Scripts/duplication, pas d'architecture |

**Tragédie** : HoloAtelier avait la meilleure architecture mais n'a jamais produit de film.
ZtudioZ a produit un film mais n'a pas d'architecture.

## Objectif

Créer ZtudioZ v2 : **une seule architecture fiable** qui combine :
- Le meilleur de chaque génération
- Une interface web (Vue.js) en complément d'opencode
- Versionnement complet de tout (prompts, assets, versions)
- Skills comme cerveau d'orchestration (plus de code Python pour l'orchestration)
- Rollback possible sur chaque étape
- Logs complets de chaque action

---

## Architecture cible

```
ZtudioZ/
├── opencode.jsonc                          # Config MCP (2 serveurs)
├── requirements.txt
├── main.py                                 # CLI (fallback opencode)
│
├── constitution/
│   └── agents.json                         # Définitions rôles + graphe dépendances
│
├── pipeline/                               # Backend Python
│   ├── config.py / config.yaml
│   ├── models/
│   │   ├── bible.py                        # Entity, EntityVersion, BibleDB
│   │   └── scene.py                        # Shot, Scene, Project
│   ├── services/                           # Wrappers API (pas de logique métier)
│   │   ├── image.py
│   │   ├── video.py
│   │   ├── audio.py
│   │   ├── llm.py
│   │   ├── vision.py
│   │   ├── montage.py
│   │   └── prompt_builder.py
│   ├── steps/                              # Logique pipeline (1 fichier par step)
│   │   ├── sources.py                      # Lire/résumer les sources
│   │   ├── scenario.py                     # Sources → scénario (collaboratif)
│   │   ├── parse.py
│   │   ├── fiches.py
│   │   ├── sheets.py
│   │   ├── storyboard.py
│   │   ├── visualize.py                    # Absorbe scripts/generate_shot.py
│   │   ├── produce.py                      # Absorbe scripts/generate_videos.py
│   │   ├── audio.py                        # Absorbe scripts/generate_audio.py
│   │   └── montage.py
│   └── history/                            # Système de versionnage
│       ├── __init__.py
│       ├── manager.py                      # HistoryManager
│       └── index.json                      # Index versions par projet
│
├── api/                                    # FastAPI (pour la web UI)
│   ├── main.py
│   ├── deps.py
│   ├── ws.py                               # Hub WebSocket temps réel
│   └── routes/
│       ├── projects.py
│       ├── pipeline.py
│       ├── bible.py
│       ├── assets.py
│       ├── history.py
│       └── chat.py
│
├── web/                                    # Vue.js SPA (Options API + Vite)
│   ├── package.json
│   ├── vite.config.js
│   └── src/
│       ├── main.js
│       ├── App.vue
│       ├── router.js
│       ├── api.js
│       ├── views/
│       │   ├── Dashboard.vue
│       │   ├── Sources.vue
│       │   ├── Scenario.vue
│       │   ├── Editor.vue
│       │   ├── Assets.vue
│       │   ├── History.vue
│       │   └── Chat.vue
│       ├── components/
│       │   ├── SceneList.vue
│       │   ├── ShotGrid.vue
│       │   ├── VideoPlayer.vue
│       │   ├── FicheEditor.vue
│       │   ├── PromptLog.vue
│       │   ├── VersionDiff.vue
│       │   ├── RollbackButton.vue
│       │   ├── StepProgress.vue
│       │   └── BudgetBar.vue
│       └── composables/
│           ├── useWebSocket.js
│           ├── useApi.js
│           └── useProject.js
│
├── skills/                                 # Le cerveau (fichiers .md)
│   ├── create-scenario.md
│   ├── produce-scene.md
│   ├── fix-consistency.md
│   ├── new-project.md
│   └── regenerate.md
│
├── .opencode/skills/                       # Symlinks pour opencode
│   └── *.md → ../../skills/*.md
│
└── projets/{slug}/
    ├── .checkpoint.json
    ├── .history.json                       # Index versions du projet
    ├── sources/                            # Matières premières
    │   ├── conversation-originale.md
    │   ├── inspirations/
    │   └── notes.md
    ├── script/
    │   ├── scenario_v1.md
    │   └── parsed/
    ├── bible/
    ├── preprod/
    ├── production/
    ├── postprod/
    └── logs/
```

---

## Pipeline complet

```
Phase 0: SOURCES
  Utilisateur upload matières premières → sources/
      │
      ▼
Phase 1: SCENARIO (skill: create-scenario.md)
  IA aide à structurer → scenario_v{N}.md
      │
      ▼
Phase 2: PARSE
  scenario.md → scene_XX.yaml
      │
      ├──► Phase 3a: BIBLE     fiches → sheets → upload refs
      ├──► Phase 3b: STORYBOARD storyboard.json → images shots
      └──► Phase 3c: AUDIO     TTS dialogues + VO
              │
              ▼
Phase 4: PRODUCE
  End frames → clips vidéo
      │
      ▼
Phase 5: MONTAGE
  Vidéos + audio → montages scènes → film final
```

Chaque phase écrit dans `history/` et peut être annulée indépendamment.

---

## Système de versionnage

### HistoryManager (`pipeline/history/manager.py`)

```python
class HistoryManager:
    def snapshot(self, kind, prompt, output_path, metadata):
        """Enregistre un artefact : prompt + sortie + métadonnées."""

    def get_history(self, kind, entity=None):
        """Retourne toutes les versions d'un artefact."""

    def get_version(self, artifact_hash):
        """Retourne le snapshot complet."""

    def rollback(self, artifact_hash):
        """Restaure une version + invalide les artefacts downstream."""

    def diff(self, hash_a, hash_b):
        """Compare deux versions."""
```

### Ce qui est versionné

| Artefact | Méthode | Stockage |
|----------|---------|----------|
| Entités bible | v1, v2, v3... | `bible/*/slug/fiche_v{N}.yaml` + `sheet_v{N}.png` |
| Prompts | Hash immutable | `history/prompts/{hash}.json` |
| Images générées | Lié au hash prompt | `history/images/{hash}.png` |
| Vidéos générées | Lié au hash prompt | `history/videos/{hash}.mp4` |
| Audio généré | Lié au hash prompt | `history/audio/{hash}.mp3` |
| Storyboards | Versionné par scène | `preprod/storyboard/scene_XX/storyboard_v{N}.json` |
| Scénario | Brouillons versionnés | `script/scenario_v{N}.md` |
| Runs pipeline | Audit trail complet | `history/runs/{timestamp}.json` |

### `.history.json` (par projet)

```json
{
  "versions": [
    {
      "hash": "a1b2c3d4",
      "kind": "image",
      "entity": "paul-henri",
      "prompt": "Portrait de Paul-Henri, 45 ans...",
      "model": "gpt-image-2",
      "seed": 42,
      "output": "bible/personnage/paul-henri/sheet_v1.png",
      "timestamp": "2026-07-19T10:30:00",
      "cost_pollen": 0.012,
      "step": "sheets",
      "scene": null
    }
  ],
  "rollbacks": [
    {
      "from_hash": "x1y2z3",
      "to_hash": "a1b2c3d4",
      "reason": "Mauvais vêtements sur le personnage",
      "timestamp": "2026-07-19T12:00:00"
    }
  ]
}
```

### Rollback

1. Copie l'ancienne version dans l'emplacement production
2. Invalide les artefacts downstream (cascade)
3. Log le rollback dans `.history.json`
4. Affiche à l'utilisateur ce qui doit être régénéré

---

## Skills (le cerveau)

### create-scenario.md

Rôle : assistant scénariste. Aide l'utilisateur à transformer des idées brutes en scénario structuré.

Workflow :
1. Lire toutes les sources dans `sources/`
2. Proposer logline + synopsis + structure + personnages
3. Valider avec l'utilisateur
4. Rédiger `script/scenario_v{N}.md`
5. Versionner chaque brouillon

Règles :
- Ne jamais inventer ce que l'utilisateur n'a pas fourni
- Proposer, ne pas décider
- Garder la voix et l'intention de l'utilisateur
- Versionner chaque brouillon, même rejeté

### produce-scene.md

Rôle : agent de production. Produit tous les assets d'une scène.

Workflow :
1. `ztudioz.get_status` → vérifier l'état
2. `ztudioz.parse_script` → scene_XX.yaml
3. `ztudioz.generate_fiches` → fiches bible
4. `ztudioz.generate_sheets` → planches référence
5. `ztudioz.storyboard_text` → storyboard.json
6. `ztudioz.storyboard_visualize` → images shots
7. `ztudioz.produce_video` → clips vidéo
8. `ztudioz.generate_audio` → TTS
9. `ztudioz.run_pipeline("montage")` → assemblage

Pièges accumulés :
- `gpt-image-2` rejette `image` pour les templates board
- Noms personnages utilisent `\u2011` (tiret insécable)
- `flux` ne supporte jamais les refs → fallback: gpt-image-2 → gptimage
- Budget : ~0.01 pollen/image, ~0.15 pollen/video
- Ne jamais écraser les vidéos existantes >5KB

---

## Interface web (Vue.js)

### Pages

| Route | Composant | Description |
|-------|-----------|-------------|
| `/` | Dashboard.vue | Vue d'ensemble projet, budget, progression |
| `/sources` | Sources.vue | Upload/gestion matières premières |
| `/scenario` | Scenario.vue | Éditeur scénario collaboratif |
| `/editor` | Editor.vue | Éditeur script/bible/storyboard |
| `/assets` | Assets.vue | Galerie images/vidéos/audio |
| `/history` | History.vue | Navigateur versions + rollback |
| `/chat` | Chat.vue | Conversation avec le concierge |

### Composants clés

| Composant | Fonction |
|-----------|----------|
| `SceneList.vue` | Sélecteur scènes + badges statut |
| `ShotGrid.vue` | Grille storyboard |
| `VideoPlayer.vue` | Prévisualisation vidéo |
| `FicheEditor.vue` | Éditeur fiches personnages/lieux |
| `PromptLog.vue` | Affiche les prompts utilisés par asset |
| `VersionDiff.vue` | Compare deux versions |
| `RollbackButton.vue` | Revenir à une version précédente |
| `StepProgress.vue` | Barres de progression pipeline |
| `BudgetBar.vue` | Suivi budget pollen |

### Communication

- REST pour le CRUD (projets, fiches, assets)
- WebSocket pour le temps réel (progression steps, notifications)

---

## Fichiers supprimés

| Fichier | Lignes | Remplacé par |
|---------|--------|-------------|
| `scripts/generate_shot.py` | 249 | `pipeline/steps/visualize.py` |
| `scripts/generate_shots.py` | 119 | `pipeline/steps/visualize.py` |
| `scripts/generate_videos.py` | 186 | `pipeline/steps/produce.py` |
| `scripts/generate_audio.py` | 126 | `pipeline/steps/audio.py` |
| `scripts/generate_scene_08.py` | ~100 | — |
| `pipeline/workflows.py` | ~100 | `constitution/agents.json` |
| `pipeline/orchestrator.py` | 478 | `skills/*.md` + `api/` |
| `pipeline/ztudioz_toolkit.py` | 663 | `api/routes/*` |
| `pipeline/reporter.py` | ~80 | `api/ws.py` |
| `pipeline/consumption.py` | ~100 | `pipeline/history/manager.py` |
| `agents/character_designer.py` | ~100 | `api/routes/bible.py` |
| `opencode.jsonc1` | — | archive |

---

## Phases d'implémentation

| Phase | Description | Fichiers | Estimation |
|-------|-------------|----------|------------|
| **1** | Système history/versionnage | `pipeline/history/manager.py` | 1 session |
| **2** | Clean pipeline : absorber scripts/ dans steps/, corriger bugs | `pipeline/steps/visualize.py`, `produce.py`, `audio.py` | 1-2 sessions |
| **3** | Skill création scénario + step | `skills/create-scenario.md`, `pipeline/steps/scenario.py` | 1 session |
| **4** | Constitution + skills restants | `constitution/agents.json`, `skills/*.md` | 1 session |
| **5** | Serveur FastAPI | `api/main.py`, `api/routes/*`, `api/ws.py` | 1-2 sessions |
| **6** | Frontend Vue.js | `web/src/**` | 3-4 sessions |
| **7** | Intégration + tests | End-to-end | 1 session |

**Total : ~9-11 sessions**

---

## Décisions de conception

| Décision | Choix | Rationale |
|----------|-------|-----------|
| Frontend | Vue.js SPA (Options API + Vite) | Dev rapide, hot reload, déploiement simple |
| Backend | FastAPI + WebSocket | Temps réel, REST pour CRUD |
| Versionnage | Tout garder, symlinks pour l'espace | Le disque est pas cher, le rollback est sûr |
| Skills | Fichiers .md, symlinks vers `.opencode/skills/` | Source unique de vérité, fonctionne avec opencode et web |
| Création scénario | IA propose activement | L'utilisateur fournit le brut, l'IA structure |
| Historique | `HistoryManager` + `.history.json` par projet | Chaque prompt, chaque sortie, chaque rollback logué |
| Rollback | Copie ancienne version + invalidation cascade | Force la régénération des étapes downstream |
| Temps réel | Événements WebSocket depuis FastAPI | Barres de progression, complétion d'étapes |
