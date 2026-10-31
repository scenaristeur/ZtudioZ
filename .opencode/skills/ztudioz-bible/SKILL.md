---
name: ztudioz-bible
description: >-
  Format et écriture des fiches bible ZtudioZ - dossier bible/personnage, bible/lieu, bible/objet
  (singulier), fichier fiche.yaml, versions v1/v2 avec active_version et switch-version pour l'A/B
  testing, champs seed/mix_alias/attributs/palette/accessoires/tenues/prompt_image, et les états
  visuels states. Utilise pour créer ou déboguer une fiche, faire une A/B de version, comprendre
  pourquoi une sheet dérive. Déclencheur : bible, fiche, personnage, lieu, objet, attributs,
  active_version, switch-version, states, sheet_v1.
---

# La Bible

**Dépendances** : `ztudioz-providers` (tailles et modèles pour la génération des sheets).

## Emplacement — au singulier

```
projets/<slug>/bible/
├── ambiance/
│   ├── style-guide.yaml        # palette, format, grain, composition
│   └── prompt_templates.yaml   # character_sheet / location_sheet / object_sheet
├── personnage/<slug>/fiche.yaml + sheet_v1.png
├── lieu/<slug>/fiche.yaml + sheet_v1.png
├── objet/<slug>/fiche.yaml + sheet_v1.png
└── storyboard_guard.yaml       # surcharge projet du garde-fou (voir ztudioz-parse-storyboard)
```

Les dossiers sont au **singulier** : `bible/personnage/`, `bible/lieu/`, `bible/objet/`
(cf. `models/bible.py:57`). Certaines documente au pluriel — c'est faux, et `FicheStep`
écrit au singulier.

`bible/ambiance/` n'est **pas** créé par `init` : le créer avec `style-guide.yaml` et
`prompt_templates.yaml`, sinon `prompt_builder.py:22,33` échoue. C'est la seule partie
d'initialisation à faire à la main.

## fiche.yaml — personnage

```yaml
name: "Paul-Henri Delorme"
slug: paul-henri
type: personnage
created: 2026-07-09
active_version: v2          # ← la version utilisée

versions:
  v1:
    seed: 42
    mix_alias: "Bacri × Binoche"
    attributs:               # 10 attributs visuels
      - "Silhouette masculine légèrement voûtée"
      - "Manteau en velours côtelé usé"
      # …
    personnalite: "Doux, mélancolique, déphasé, observateur"
    palette: "tons sépia, marron, vert doux, gris chaud"
    accessoires: ["sécateur", "bouquet de roses blanches"]
    prompt_image: "Portrait de Paul-Henri Delorme, fleuriste mélancolique, 42 ans, manteau velours côtelé usé, …"
    tenues:
      jour: "Manteau velours côtelé marron, chemise bleu pâle"
      soir: "Veste en laine grise, pull col roulé noir"
  v2:
    seed: 99
    mix_alias: "Arditi × Deneuve"
    # …
```

## fiche.yaml — lieu

Même structure, avec `atmosphere` (au lieu de `personnalite`) et un `eclairage` :

```yaml
name: "Grand salon high-tech"
slug: grand-salon-high-tech
type: lieu
active_version: v1
versions:
  v1:
    seed: 7
    attributs: ["…"]              # 10 attributs
    atmosphere: "Surannée, mélancolique, hors du temps"
    palette: "vieux rose, bois foncé, gris, zinc"
    eclairage: "Lumière naturelle tamisée par la vitrine"
    prompt_image: "…"
```

## fiche.yaml — objet

`name`, `slug`, `type: objet`, `versions[]` avec `attributs`, `seed`, `prompt_image`.

## `seed` et `mix_alias` ne suffisent pas

Ces champs existaient pour stabiliser l'identité, et l'expérience l'a montré **insuffisant** :
la cohérence vient de **refs + contrainte forte**, pas de la description textuelle. `seed` et
`mix_alias` restent utiles (reproductibilité, orientation artistique) mais ne garantissent
rien. Voir `ztudioz-consistency`.

## A/B testing — basculer de version

```bash
python main.py switch-version <slug> <entity-slug> v2
```

Change `active_version` dans la fiche. Les sheets de la version active sont alors la
référence. Utile pour comparer deux lectures d'un même personnage avant de verrouiller.

## États visuels (`states`)

Une fiche peut déclarer **plusieurs états d'une même entité** (pose, moment, angle, humeur)
sous `versions.v1.states`, avec des YAML anchors :

```yaml
versions:
  v1:
    _base: &base
      attributs: [...]
      prompt_image: "..."
    states:
      assis:                       # sheet dédiée
        sheet: sheet_v1.assis.png
        prompt_image: "..."
      au-volant:                   # sous-référence dynamique, pas de sheet
        prompt_image: "..."
        changes: {posture: "au volant"}
```

**Règle** : un état **avec** `sheet` → image statique ; un état **sans** `sheet` mais avec
`prompt_image` → **sous-référence dynamique** générée et mise en cache. Le fallback sur la
sheet de base ne s'applique que si la sheet de l'état est déclarée mais que le fichier manque.

**Usage cinéma** : au lieu de recadrer un plan sur un personnage « assis » alors que le
script le veut « marchant », déclarer l'état `marche` et composer la référence de pose.
**La position suit la timeline, pas la sheet de base.** Détail des sous-références et de la
double-ancre : `ztudioz-consistency` et sa référence `references/states-subrefs.md`.

## Ordre de production

1. `fiches` → une `fiche.yaml` par entité
2. **relire** les fiches (attributs, `prompt_image`, `voice` pour le TTS)
3. `sheets` → `sheet_v1.png` par entité, **objets → personnages → lieux**
4. vérifier chaque sheet par vision avant d'aller plus loin

Les sheets sont les **références** du reste de la production. Une sheet fausse produit des
vidéos fausses : les corriger avant le storyboard, pas après.
