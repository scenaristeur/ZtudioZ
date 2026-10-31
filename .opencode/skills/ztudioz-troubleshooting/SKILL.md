---
name: ztudioz-troubleshooting
description: >-
  Diagnostic des pannes du pipeline ZtudioZ - .env manquant, clés API absentes, erreurs 402 crédits
  épuisés et 429 rate-limit, refs tmpfiles expirées après 48h, cache MD5 qui masque une
  régénération, size/duration refusées, serveur Agnes local arrêté, vision qui time out, et bugs
  connus non corrigés (checkpoint produce vs production, providers hardcodés, dossier bible/ambiance
  absent). Utilise quand une étape échoue, qu'une image ou une vidéo ne se régénère pas, ou qu'un
  appel provider refuse. Déclencheur : erreur, échoue, 402, 429, 503, timeout, .env, API key, cache,
  pas régénéré, bloqué, planté.
---

# Dépannage

**Dépendances** : `ztudioz-providers`.

## Diagnostic par symptôme

| Symptôme | Cause probable | Action |
|---|---|---|
| `KeyError` / `.env` introuvable | `.env` absent de la racine | le créer ; `config.py:load_dotenv(ROOT/'.env')` fonctionne quel que soit le cwd |
| Erreur clé API absente | variable non définie | `.env` + `${VARIABLE}` dans `config.yaml` ; **jamais de clé en dur** |
| 402 | crédits épuisés | le repli provider automatique prend le relais ; sinon basculer `active:` |
| 429 | rate-limit | storyboard : `production.storyboard_delay` ; apihub vidéo : ~1 RPM ; retry 429 réglable (`apihub_retries`…) |
| 502 / 503 | apihub instable | re-uploader les refs puis réessayer ; le repli provider peut aussi partir |
| Refs ignorées | URLs tmpfiles expirées (48 h) | `upload_sheets` / ré-upload `expire=172800` **avant** la génération |
| « Ça ne régénère pas » | cache MD5 / `--force` insuffisant | le prompt a changé ? purger le fichier cible ; cache = hash du prompt |
| Size refusée | hors `valid_sizes` | lire la liste dans `config.yaml`, **pas** en dur dans le code |
| Durée refusée | hors `valid_durations` | idem `config.yaml` |
| Port 8765 injoignable | serveur Agnes local arrêté | basculer `direct: true` (apihub) ou un autre provider |
| Vision timeout | image trop grande | `vision.py` redimensionne à 1024×768 ; vérifier le chemin |
| Vidéo regénérée à chaque run | bug checkpoint `produce` | voir plus bas |
| `prompt_builder` plante | `bible/ambiance/` absent | le créer (non fait par `init`) |

## Le repli provider automatique est la première chose à savoir

Quand le provider actif renvoie 402/403/429/5xx ou une erreur réseau, `ImageService` et
`VideoService` basculent **seuls** sur le provider backup de `config.yaml`, dans l'ordre de
`get_service_providers_ordered()` (actif en premier), avec garde anti-boucle (`set attempted`).

Donc « le provider actif est mort » n'est **pas** un blocage. Vérifier le repli avant de
paniquer. Un 400 (prompt invalide) ne déclenche **pas** de repli — c'est volontaire.

Un repli qui change de provider doit garder `strict_refs` et l'attachement aux refs. S'il
retombe sur un modèle sans refs (`flux`/`sana`), la cohérence est perdue — c'est un bug, pas
une dégradation.

## Cache : la cause n°1 de « rien ne change »

- Image / audio : hash MD5 du prompt (+ des refs). Prompt identique → fichier renvoyé.
- Compose : cache **par existence de fichier**. Pour vraiment régénérer, **purger le fichier
  cible** ; `--force` seul peut ne pas suffire.
- Keyframes : image réutilisée seulement si le `kf_N.txt` (prompt composé) n'a pas changé.
  Une keyframe en cache **non encore vérifiée** est re-vérifiée au prochain run.
- **Ne jamais écraser une vidéo > 5 KB** (règle de production).

Quand un changement semble sans effet : purger le fichier cible avant de conclure que le
changement n'a pas été pris en compte.

## Bugs connus

**Corrigés depuis** — ne pas « réparer » à nouveau :

- `produce` n'était jamais reconnu comme fait : le checkpoint écrivait `"production"` au
  lieu de `"produce"`, donc `run` relançait la vidéo à chaque fois. Corrigé, checkpoint
  migré à la lecture, et `_get_completed_steps()` se base désormais sur les artefacts
  réellement présents sur disque.
- `bible/ambiance/` n'était jamais créé par `init`, alors que le prompt builder en dépend.
  Corrigé : `ambiance/` et `scene/` sont créés d'emblée, et
  `ProjectInit.verify_structure()` diagnostique les projets existants.

**NON corrigés — documentés, pas fixés dans ce chantier :**

**1. Providers hardcodés dans les scripts keyframes** — `.agents/skills/ztudioz-keyframes/scripts/keyframes_flow.py:88,159`

Le script passe par le CLI agnes et ignore `services.image.active` / `services.video.active`.
Il produit donc sur le mauvais provider si la config a été changée. Le step `keyframes` du
pipeline n'a pas ce problème ; c'est le script autonome qui l'a.

**2. `pipeline/steps/scenario.py` orphelin** — pas dans `AVAILABLE_STEPS`, jamais appelé.

**3. `describe_sheet` inexistant** — `.agents/skills/ztudioz-keyframes/SKILL.md:43` renvoie
vers un outil `describe_sheet` qui n'existe pas dans le MCP. Utiliser `analyze_character_sheet`.

## Règle de diagnostic

Toujours distinguer trois questions, dans cet ordre :

1. **La donnée est-elle juste ?** (prompt, refs, config, fichiers) → pas de regenerated coûtuse.
2. **Le service/provider répond-il ?** (quotas, réseau) → repli automatique, test isolé.
3. **Le pipeline propage-t-il ?** (checkpoint, cache) → c'est là que vivent les bugs de
   reprise : un artefact présent sur disque mais non marqué fait relancer une étape coûteuse.

Un `ImageService().generate(...)` ou `VideoService().generate(...)` isolé, en mode `dev`,
isole le service du pipeline : c'est le test de référence après un changement de provider.
