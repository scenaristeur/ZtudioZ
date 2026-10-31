---
name: ztudioz-providers
description: >-
  Référence des services et providers ZtudioZ - LLM, vision, image, vidéo, audio - pour choisir un
  modèle, une taille, une durée, savoir quels modèles acceptent des images de référence, comprendre
  active:/providers de pipeline/config.yaml, le mode dev, strict_refs, no_model_fallback et les
  limites de débit. Utilise pour ajouter ou changer un provider, déboguer un refus de refs,
  comprendre une erreur 402/429. Déclencheur : provider, modèle, config.yaml, active, agnes,
  pollinations, focus, strict_refs, valid_sizes, dev, rate limit.
---

# Services & providers

**Dépendances** : aucune.

## Règle n°1 : ne jamais dupliquer les valeurs de la config

`pipeline/config.yaml` est la **seule** source de vérité. Toute valeur recopiée dans un
document dérive. Pour lire l'état courant :

```bash
grep -n "active:" pipeline/config.yaml
grep -n -A4 "^  image:\|^  video:\|^  llm:\|^  vision:\|^  audio:" pipeline/config.yaml
```

Ce skill ne fige donc **aucun** modèle ni taille. Il documente ce qui ne se lit pas dans le
YAML : **les capacités et les limites**.

## Structure de la config

```yaml
services:
  image:
    active: <provider>        # ← bascule ici, rien d'autre
    providers:
      <provider>:
        model, width, height, size, dev_size, valid_sizes, strict_refs, ...
```

Changer `active:` suffit : aucun code ne connaît les providers en dur. Un service accepte
`config: dict | None = None` en constructeur, ce qui permet un override en test/dev.

## Capacités par provider — la partie non déductible du YAML

### Image

| Provider | Multi-refs | Notes |
|---|---|---|
| `agnes` | oui, via `ImageService.compose()` (apihub, `extra_body.image`) | serveur local `:8765`, ou apihub `direct: true` |
| `pollinations` | **seulement certains modèles** | `no_model_fallback: true` interdit de retomber sur un modèle sans refs |
| `focus` | non | Gradio, `FOOCUS_URL` optionnel |

Sur Pollinations, la chaîne de repli classique `gpt-image-2` → `gptimage` → `flux` **n'est
pas neutre** : `flux` et `sana` ignorent les images de référence et produisent un personnage
différent. Deux garde-fous :

- `strict_refs: true` → le service **refuse** de retomber sur un modèle sans refs plutôt que de générer sans les refs.
- `no_model_fallback: true` → pas de repli modèle automatique en présence de refs.

Un repli vers `flux` est donc **toujours un bug de cohérence**, pas une dégradation
acceptable. Voir `ztudioz-consistency`.

### Vidéo

| Mode | Conditions | Détail |
|---|---|---|
| `t2v` | 0 ref | texte pur |
| `i2v` | **1 ref** | anime une image — ⚠️ dérive l'identité en milieu de plan |
| `keyframes` | **2 refs** | start + end, identité bornée des deux côtés |

- **Serveur local `:8765`** : 2 images max (`reference_image` + `end_frame_image`).
- **Apihub `direct: true`** : N images (`extra_body.image` en liste) + `mode: keyframes`.
- `frame_rate` 24, `num_frames` en 8n+1 (max 441).
- **Quota apihub vidéo ≈ 1 RPM** sur compte gratuit → les 30 plans d'une scène sont longs.
- Retry 429 réglable : `apihub_retries` / `apihub_retry_base_wait` / `apihub_retry_max_wait`.

Le 1-ref d'un i2v qui « glisse » vers un autre personnage à mi-plan est le défaut le plus
fréquent du système. Le correctif est le mode keyframes 2 refs — voir
`ztudioz-video-production` et `ztudioz-consistency`.

### LLM, vision, audio

- **LLM** : `albert` (tool-calling), `agnes` (tool-calling), `pollinations` (chat_completion).
- **Vision** : `OPENAI_API_KEY` + `OPENAI_BASE_URL`, image redimensionnée à 1024×768 pour
  éviter les timeouts. **Aucun fallback de clé en dur** : lève une erreur si la variable manque.
- **Audio** : pollinations uniquement, endpoints `say` / `respond`.

## Tailles et durées

`valid_sizes` (image) et `valid_durations` (vidéo) vivent dans `config.yaml`, plus en dur
dans le code. Si une taille est refusée, lire ces deux listes avant de changer quoi que ce
soit — et les mettre à jour **des deux côtés** (serveur local + config) si la table change.

## Mode dev

`dev: true` bascule le service sur les résolutions réduites (`dev_size` image,
`dev_width`/`dev_height` vidéo) pour tester sans brûler le quota.

**`dev: true` est actif par défaut à plusieurs endroits dans `config.yaml`.** Vérifier et
passer à `false` avant une production réelle. Les outils MCP `generate_image` et
`generate_video` ont un flag `dev` : **respecter ce mécanisme**, ne jamais introduire un
chemin qui l'ignore.

## Repli provider automatique

Quand le provider actif renvoie 402/403/429/5xx ou une erreur réseau, `ImageService` et
`VideoService` basculent seuls sur le provider backup de `config.yaml`, dans l'ordre donné
par `get_service_providers_ordered()` (actif en premier), avec une garde anti-boucle
(`set attempted`). C'est ce mécanisme, hérité du clip V4, qui a débloqué la production quand
un 402 figeait tout.

Un **provider** de secours doit conserver les mêmes attaches de refs que l'actif : basculer
de provider en perdant les refs viole la règle de cohérence.

## Changer de provider

1. Éditer `active:` dans `config.yaml`.
2. Vérifier que le backup garde `strict_refs` et le mode keyframes si l'actif les avait.
3. Tester sur **une** scène (`--steps visualize`) avant de relancer.
4. `dev: false` pour la prod.
