---
name: ztudioz-n8n
description: >-
  Piloter ZtudioZ depuis n8n - nœuds et credentials à utiliser, le fait que l'API n'expose pas de
  production synchrone mais un flux render-job asynchrone à sonder, et le pattern de retry/backoff
  pour les quotas de génération. Utilise pour automatiser une production, brancher n8n sur le
  pipeline, ou comprendre les limites d'une automatisation. Déclencheur : n8n, automatisation,
  workflow n8n, webhook, cron, orchestration externe.
---

# n8n

**Dépendances** : `ztudioz-pipeline` (étapes et CLI), `ztudioz-web-api` (routes et polling).

> Sujet **secondaire**. Le cœur du projet est la production en console. n8n est un moyen
> d'automatiser une étape, pas un substitut au pipeline.

## Ce que n8n pilote

Les mêmes étapes que la CLI, via les routes de `api/` (HTTP) — pas d'accès direct aux
services Python. Voir `ztudioz-web-api` pour la forme exacte des réponses.

## Point bloquant : pas de production synchrone

Le pipeline est long (génération d'images puis de vidéos, minutes par plan). Une route ne
peut pas produire un film entier en une requête. Le flux n8n correct est donc :

1. **HTTP Request** → route de lancement → récupère un **identifiant de job**.
2. **Loop** (Wait + HTTP Request) → route de statut, jusqu'à état terminal.
3. **Switch / IF** sur l'état final.
4. Le cas échéant, une route de recovery pour reprendre (`resume`).

Un workflow qui fait un seul `POST /run` et lit la réponse comme un résultat fini est
faux. Vérifier la forme réelle de la réponse avant d'écrire le premier nœud.

## Credentials

- Les secrets n8n sont stockés côté n8n, **jamais** dans le dépôt ni dans `config.yaml` en
  clair. `config.yaml` référence `${VARIABLE}`, alimenté par le `.env` de la racine.
- Si n8n tourne dans un contexte différent, vérifier que le `.env` est bien lu (il est chargé
  depuis la racine du dépôt, quel que soit le cwd) et que `FOOCUS_URL` / `GRIST_*` sont
  présents si ces services optionnels sont utilisés.

## Limites à respecter dans l'automatisation

- **Quotas** : vidéo apihub ≈ 1 RPM ; rate-limit LLM pendant `storyboard`. Un workflow
  n8n qui boucle vite va déclencher des 429. Introduire de l'attente entre les appels.
- **Repli automatique** : un 402/503 déclenche le repli provider sans intervention
  (voir `ztudioz-providers`) — ne pas construire une logique de retry par-dessus.
- **Validation humaine** : le pipeline s'arrête aux portes `c/r/s/p/q` quand
  `production.human_validation` est actif. **Un workflow n8n sans TTY fait planter le run** :
  `input()` sans garde `isatty()` lève `EOFError`, ou bloque indefinitely. Il peut
  donc valider à la place de l'utilisateur des plans non revus. À savoir avant d'automatiser
  une scène, et à signaler.
- **Pas de suppression** : aucun projet n'est suivi par git. Un workflow ne doit pas appeler
  une action destructive sans confirmation humaine explicite.

## Conseil

Automatiser ce qui est **déterministe et réversible** (lancer le parse, lister l'état,
récupérer des fichiers). Garder la main pour la validation visuelle et les choix de
référence, qui sont le cœur de la valeur.
