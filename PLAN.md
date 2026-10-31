# ZtudioZ — Plan de Production Audiovisuelle par IA

## Vision

Système générique de production de films animés par IA.
Déposer un script ou une idée dans `projets/` → le pipeline produit un film.
Chaque étape est supervisée par un humain ("assisted pipeline").

## Principes fondateurs

1. **Config-driven** : tout paramètre dans des YAML, jamais dans le code
2. **Services swappables** : LLM/image/video/audio changent via config
3. **Bible versionnée** : personnages/lieux/objets avec versions multiples (A/B testing)
4. **Cohérence par seed + alias mixés** : seed fixe + mix d'acteurs connus brouillés
5. **Résilience** : retry, fallback, checkpoint à chaque étape
6. **Projet auto-structuré** : le système crée lui-même son arborescence

## Cycle de vie d'un projet
1. `python main.py init <slug> <script.md>` → le système crée toute l'arborescence
2. Le script est parsé → scènes extraites → bible initialisée
3. L'humain modifie les fiches YAML (attributs, versions, seeds, alias)
4. `python main.py run <slug>` → pipeline complet avec validation humaine
5. `python main.py switch-version <slug> <entity> <version>` → bascule A/B testing
6. Relancer `run` pour regénérer avec la nouvelle version

## Architecture

```
                    ┌─ CONCIERGE ─────────────────────┐
                    │  Interface humaine + tracking    │
                    └──────────────┬──────────────────┘
                                   │
          ┌────────────────────────┼────────────────────────┐
          ▼                        ▼                        ▼
┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│ PRÉPRODUCTION    │   │ PRODUCTION       │   │ POST-PRODUCTION  │
│                  │   │                  │   │                  │
│ parse_scenario ──┤   │ concept_artist ──┤   │ editor ──────────┤
│ bible → fiches ──┤   │ video_gen ──────┤   │ check ──────────┤
│ character_design─┤   │ audio_design ───┤   │ colorist ───────┤
│ storyboard ──────┤   │ consistency ────┤   │ mix ────────────┤
│ moodboards ──────┤   │                 │   │ export ─────────┤
└──────────────────┘   └──────────────────┘   └──────────────────┘
                                   │
                              ┌────▼────┐
                              │  SQLite  │
                              │ tracking │
                              └─────────┘
```

## Stack technique

| Couche | Technologie | Modèle |
|--------|-------------|--------|
| LLM parsing | Albert API (ToolCallingModel) | `gpt-oss-120b` (tools OK) |
| LLM vision | Albert API | `Mistral-Small-3.2-24B` (vision) |
| Image gen | Pollinations MCP | `flux`, `turbo`, `seedream` |
| Video gen | Pollinations MCP | `seedance`, `veo`, `wan` |
| Audio/TTS | Pollinations MCP | sayText, respondAudio |
| Bible | YAML versionné | v1/v2... |
| Tracking | SQLite | studio.db |
| Montage | ffmpeg | concat + xfade |
| Orchestration | Python + Camel AI | WorkflowEngine |

## Flux pour une scène

```
Script.md
  → [PARSE] LLM → scene.yaml (persos, lieux, ambiances, plans)
  → [BIBLE]  → fiches personnages/lieux/objets avec versions
  → [SHEETS] → Pollinations image → character/location sheets
  → [STORYBOARD] → Pollinations image → frames par plan
  → [VIDEO] → Pollinations seedance (I2V depuis storyboard)
  → [AUDIO] → Pollinations TTS (dialogues + VO + bruitages)
  → [CHECK] → Vision LLM → rapport de cohérence
  → [MONTAGE] → ffmpeg → scène finale
```
