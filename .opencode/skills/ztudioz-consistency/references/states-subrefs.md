# États visuels et sous-réferences

Détail de référence pour `ztudioz-consistency` et `ztudioz-bible`. Héritage du clip
« Melody's Ghost ».

## 1. Le problème que les états résolvent

La sheet de base d'un personnage le fige dans **une** pose (assis, de face, neutre). Un plan
peut vouloir un personnage debout, de trois quarts, en mouvement. Deux solutions mauvaises :
recadrer l'image (ça rate la pose), ou laisser le générateur inventer (ça casse l'identité).

La bonne solution : la fiche déclare **plusieurs états visuels**, et le pipeline compose la
référence de la bonne pose au moment du plan.

## 2. Syntaxe `states` (YAML anchors)

```yaml
versions:
  v1:
    _base: &base
      attributs: [...]
      prompt_image: "..."
    states:
      assis:          # état AVEC sheet → image statique
        sheet: sheet_v1.assis.png
        prompt_image: "..."
      au-volant:      # état SANS sheet → sous-référence dynamique
        prompt_image: "..."
        changes: {posture: "au volant"}
      marcher:        # sous-référence, plusieurs variations libres
        prompt_image: "..."
        changes: {posture: "marche, pas tendu", regard: "vers l'avant"}
```

Règles :

- Un état **avec** `sheet` → **image statique** (celle déclarée est chargée).
- Un état **sans** `sheet** mais avec `prompt_image` → **sous-référence dynamique** générée
  (compose mono-réf base + ancre `REFERENCE ONLY`) et **mise en cache** dans
  `preprod/clip/v2/_subrefs/`.
- Le **fallback** sur la sheet de base ne s'applique **que** si la sheet de l'état est
  déclarée mais que le fichier manque.

## 3. Sous-réferences (subrefs)

Une sous-référence est une **variation en cache** d'une image de référence, générée à la
demande : pose corrective du personnage, angle du lieu, déterminés par la timeline.

Les sous-références servent de **refs à la composition** (personnage + lieu), **pas** de refs
directes à tout mélanger dans la vidéo.

Fonctions clés (pipeline clip, mais le principe vaut pour le cinéma) :

- `_refs_for_section(sec, beat)` : **beat-aware** — lit `states.perso` / `states.lieu` du
  beat ou, à défaut, de la section. C'est ce qui fait que la position suit le beat et non la
  section entière.
- `_state_sheet(slug, state, kind)` : renvoie la sheet de l'état si elle existe, sinon
  génère la sous-référence en cache.

Les sous-références sont **partagées par cache** entre plans/sections : une même pose = un
seul fichier, réutilisé partout. Un cache bien tenu rend les sous-réf gratuites après le
premier plan.

## 4. Usage cinéma

Au lieu de recadrer un plan autour d'un personnage « assis » alors que le script le veut
« marchant », déclarer l'état `marche` (via `prompt_image`) et composer la référence de
pose. **La position suit la timeline, pas la sheet de base.**

Ordre de résolution d'un état pour un beat donné :

1. `states` du **beat** (override le plus spécifique)
2. `states` de la **section**
3. sheet / prompt_image de la version **active** de la fiche
4. (pour le cinéma) le `character_details` réinjecté par le step `keyframes`

## 5. Pièges

- **Un `states` sur une version non active n'a aucun effet** — la version active décide.
- **Ne pas mettre d'état pour « être dans le décor »** : le lieu a sa propre fiche et son
  propre état (`states` sur la fiche du lieu, clé `lieu`).
- **Les sous-réf ne remplacent pas la double-ancre** : elles fournissent les poses, la
  double-ancre (`references/identity-drift.md`) borne l'identité. Les deux vont ensemble.
- **Un état sans variation de pose ne sert à rien** (`changes: {}`) — c'est la sheet de base
  déguisée, et ça coûte une génération pour rien.
