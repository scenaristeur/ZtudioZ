---
name: ztudioz-web-api
description: >-
  API web et interface de ZtudioZ - les routes FastAPI de api/, les pages et composants Next.js de
  web/, le flux render-job asynchrone et le suivi de progression, et la façon dont l'interface
  pilote le pipeline par étape. Utilise pour comprendre ou modifier le backend web, brancher un
  écran sur le pipeline, ou comprendre comment une page web déclenche et suit une génération.
  Déclencheur : web, api, route, endpoint, fastapi, nextjs, interface, navigateur, render job, page
  web.
---

# API web & interface

**Dépendances** : `ztudioz-pipeline` (le pipeline que l'interface pilote).

> L'API et l'interface sont **secondaires** : le cœur du projet est le pipeline
> (`pipeline/`) et les skills de production. Consulter ce skill seulement pour une question
> web précise.

## Couche API — `api/`

Routes FastAPI qui enveloppe le pipeline. Une route ne fait pas de production : elle appelle
`Orchestrator` / les services, et **serialize** un état.

Le pipeline est **bloquant et long** (génération d'images et de vidéos, minutes par plan).
Une route ne peut donc pas tout faire en une requête synchrone. Le flux est :

1. une route lance le pipeline et renvoie un **identifiant de job** ;
2. le client **sonde** l'avancement (progression, logs) ;
3. une route récupère le résultat.

Ne pas supposer qu'un `POST /run` renvoie le film fini. Vérifier la forme réelle des réponses
dans `api/` avant d'écrire un appelant.

## Couche interface — `web/`

Application Next.js. Le pattern est : **une action appelle une route API, l'interface
reflète la progression** (étapes cochées, logs, aperçus d'images/vidéos).

Le web est un **pilote** du pipeline, pas une seconde implémentation. Si une action d'interface
réimplémente une logique métier, c'est une divergence : la logique doit être dans
`pipeline/`.

## Points d'attention

- **`.env` à la racine** : `pipeline/config.py` fait `load_dotenv(ROOT/'.env')`, donc le MCP
  et l'API fonctionnent quel que soit le cwd de lancement. Ne pas introduire un second
  mécanisme de chargement de `.env` dans `api/` ou `web/`.
- **Clés API** : jamais en dur, jamais versionnées. `${VARIABLE}` dans `config.yaml`,
  `{env:VAR}` côté config opencode.
- **Dev vs prod** : `dev: true` (par défaut dans `config.yaml`) donne des résolutions
  réduites. L'interface doit refléter le mode réellement actif, sinon l'utilisateur juge une
  image sur un rendu preview.
- **Projets non suivis par git** : une action web destructive sur un projet n'a aucun filet de
  sécurité. Faire confirmer avant toute suppression.
