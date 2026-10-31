---
name: ztudioz-parse-storyboard
description: >-
  Contrat timeline de ZtudioZ - pourquoi le parse extrait une timeline ordonnée
  d'actions/dialogues/transitions, et comment le storyboard la découpe en 2 passes LLM (bornes puis
  description) avec une timeline par plan. Couvre le garde-fou visuel storyboard_guard.yaml
  (banned_terms, anchor_to_bible), le rate-limit storyboard_delay, et la planche board
  (storyboard_board_<n>plans.png) : croquis wilderien de validation entre storyboard.json et
  visualize. Utilise pour modifier ou déboguer steps/parse.py ou steps/storyboard.py, comprendre
  pourquoi un plan ne suit pas le script, valider une planche, ou lutter contre la dérive de
  vocabulaire visuel. Déclencheur : parse, timeline, storyboard, découpage, plan, board,
  planche, storyboard_board, storyboard_guard, registre, ancre bible.
---

# Parse & Storyboard — le contrat timeline

**Dépendances** : aucune.

## Principe : la timeline est la source de vérité du scénario

Ce n'est **pas** un résumé LLM. `steps/parse.py` extrait de `scenario.md` une **timeline
chronologique ordonnée** par scène, stockée dans le champ `timeline` de
`script/parsed/scene_NN.yaml`. Chaque événement :

```yaml
{type: action | dialogue | transition, speaker?: str, text: str, index: int, sub?: int, group?: int, group_total?: int}
```

- **Actions** = didascalies `_..._` : la mise en scène dynamique (caméra, mouvements, changements de temps) qu'un résumé perdait.
- **Dialogues** = répliques `**LOCUTEUR**` + texte, **repris tels quels** — jamais reformulés, jamais tronqués.
- **Transitions** = `FONDU`, `CUT TO`, `INT./EXT.` : ce sont les bornes de plans.
- **Pré-découpage** : un bloc d'action est découpé en micro-actions (une par phrase), annotées `sub`/`group`/`group_total`, pour permettre un regroupement en plans.
- La troncature du script avant envoi au LLM est généreuse (12000 car.) pour ne pas perdre les dialogues longs.

**Les actions et les dialogues sont entrelacés** dans cet ordre unique. Ne jamais les
reséparer en deux listes parallèles : c'est cette séquence qui rythme le découpage.

**Cohérence** : tous les dialogues et actions de la scène doivent se retrouver dans la
timeline, et le storyboard doit la couvrir intégralement (vérifiable par ré-échantillonnage).
Un plan qui saute une réplique est un bug de découpage, pas une licence créative.

## Storyboard : 2 passes, jamais la timeline d'un coup

### Passe A — `_plan_bounds` (`storyboard.py:135`)

Un appel LLM détermine la **liste des plans** de la scène :
`{number, start_index, end_index, type, camera}`. Les bornes sont **contiguës, sans trou ni
chevauchement**. Un changement INT./EXT., de jour ou un FONDU marque la fin d'un plan.

### Passe B — `_describe_plan` (`storyboard.py:152`)

Pour **chaque** plan, un appel LLM décrit la case : `type, camera, description,
duration_seconds, prompt_image` **plus sa propre `timeline`** = la portion ordonnée des
événements du plan, chacun avec `{start, duration}` (timestamps dans la scène).

Chaque plan de `storyboard.json` porte donc sa `timeline`. Les dialogues et actions y sont
repris tels quels. C'est cette timeline de plan que le step `keyframes` consomme pour caler
ses beats — voir `ztudioz-video-production`.

### Rate limit

`production.storyboard_delay` (secondes entre appels LLM, un par plan) gère les 429.
**Ne pas supprimer ce délai.** Sans lui, une scène de 30 plans déclenche le rate-limit et
la production s'arrête au milieu.

## storyboard_guard.yaml — le garde-fou visuel (non documenté ailleurs)

Le LLM écrit les plans dans le vocabulaire du script, qui dérive naturellement vers un
registre que la bible interdit (néons, hologrammes sur un film contemporain). On ne lui
fait pas confiance : le nettoyage est **déterministe**, appliqué après le LLM.

`config.yaml` porte les listes, **désactivées par défaut** (`production.storyboard.enabled:
false`) parce qu'elles présupposent un film contemporain et casseraient un projet de SF
légitime :

```yaml
production:
  storyboard:
    enabled: false
    banned_terms:
      "re:\\bholographiques?\\b": "projections plates imprimées"   # préfixe re: = regex (flexions FR)
      "re:\\bfuturistes?\\b": "contemporains"
    negative_suffix: "..."      # queue négative ferme
    anchor_to_bible: true        # ré-ancre le prompt sur la bible
```

**Pour activer sur un projet**, créer `bible/storyboard_guard.yaml` dans le projet :

```yaml
enabled: true
anchor_to_bible: true
```

`storyboard.py:_guard_config()` fusionne la config globale puis la surcharge projet. Les
listes restent modifiables projet par projet. Un remplacement vide (`""`) supprime le terme.
Appliqué sur `description` **et** `prompt_image`.

**Exemple réel** : `projets/Noostate/bible/storyboard_guard.yaml` est activé, avec le
raisonnement écrit dans le fichier — le script emploie « grand salon high-tech » et « écrans
géants », le LLM partait en cyberpunk alors que la bible est contemporaine.

Sans cette surcharge, un projet SF légitime verrait ses termes propres censurés. **Vérifier
avant d'activer** que la liste correspond bien au registre visé.

## La planche `board` — validation avant les photos

Après `storyboard.json`, on génère une **planche croquis** (`storyboard_board_<n>plans.png`)
avec `storyboard_board` / `storyboard_visualize` (MCP) ou via le client image actif. C'est un
**point de validation** : 1 image pour valider la composition de N plans avant de dépenser N
photos. Voir le cycle dans `ztudioz-pipeline`.

### Contenu attendu (pièges constatés)

- **Salle pleine** : la planche doit montrer le PUBLIC dans les rangées (silhouettes variées,
  postures différentes), jamais une salle vide. Si la planche sort vide, le prompt n'a pas
  porté la foule — la régénérer, ne pas valider.
- **Écran avec contenu** : quand l'écran est visible, il doit montrer son contenu (la
  Noosphère = sphère de points, ou autre) — pas un écran blanc.
- **Personnages présents** : la case de chaque plan porte son personnage (ex. la présentatrice
  sur scène), en cohérence avec la `timeline`.
- Style : croquis noir et blanc, dessin au trait, composition wilderienne (frontale,
  symétrique), cases en grille 3x2 avec légendes françaises.

### Méthode

1. Prendre les N premiers plans (ou toute la scène) de `storyboard.json`.
2. Construire le prompt via `PromptBuilder.build_storyboard_board` (grille auto) ou à la main
   sur le modèle du template `storyboard_board`.
3. Passer la sheet du lieu en référence + contrainte forte.
4. Vérifier par vision : salle pleine, écran avec contenu, personnages, cohérence salle.

## Quand les plans ne suivent pas le script

1. Comparer la `timeline` de la scène (`script/parsed/scene_NN.yaml`) et celle du plan
   fautif dans `storyboard.json` — un événement présent dans l'un et absent de l'autre.
2. Si l'événement est absent des deux → le **parse** l'a perdu (script tronqué, format de
   dialogue non reconnu).
3. S'il est dans la timeline de scène mais pas dans celle du plan → le **découpage**
   (passe A) a mal placé les bornes.
4. S'il est dans les deux mais le `prompt_image` l'ignore → le **prompt** : relance la passe
   B sur ce plan seul, ou vérifie `storyboard_guard` (un terme a pu être censuré).
