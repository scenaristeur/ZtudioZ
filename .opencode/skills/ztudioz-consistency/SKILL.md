---
name: ztudioz-consistency
description: >-
  Règles de cohérence visuelle ZtudioZ - vêtements, lieux et références, et surtout la règle que la
  consistance vient des refs avec contrainte forte et pas de la description textuelle. Couvre la
  méthode de réparation (cause racine avant régénération), le drift d'identité en milieu de plan
  i2v, la double-ancre keyframes 2 refs, l'unification de format pour le chaînage, le ré-ancrage par
  frontière de section et l'interdiction du repli vers un modèle sans refs. Utilise quand deux plans
  ou deux vidéos n'ont pas le même personnage ou le même décor. Déclencheur : cohérence,
  consistency, dérive, drift, identité, costume, vêtement, lieu qui change, personnage différent,
  plan raté, fix-consistency.
---

# Cohérence visuelle

**Dépendances** : `ztudioz-bible`, `ztudioz-parse-storyboard`.

Références détaillées :
- `references/states-subrefs.md` — états visuels, sous-références, ré-ancrage
- `references/identity-drift.md` — double-ancre, format unifié, méthode de réparation

## Les trois règles de base

**Vêtements** — définis dans `character_details.clothing` du parsed YAML, identiques pour
tous les plans d'une même scène, et **mentionnés explicitement dans chaque `prompt_image`** :
`Paul-Henri (fleuriste, costume gris, identique à celui de la banque)`. Sans cette
répétition, chaque plan réinvente la tenue.

**Lieux** — le fond ne change pas entre les plans d'une même scène. Si le plan 1 montre le
salon, les plans 2-3 ne peuvent pas montrer le couloir. Nommer le lieu dans chaque
`prompt_image`.

**Références** — les shots préprod sont la source de vérité pour les vidéos. On ne régénère
jamais des frames vidéo pour réparer un problème : si un shot est mauvais, on régénère le
shot, on valide, puis on régénère la vidéo.

## La règle qui compte : refs + contrainte forte

> **La cohérence = refs + contrainte forte, pas la description textuelle.**

Toujours composer **avec** les sheets (personnage + lieu), **même pour un personnage
unique**. En `ImageService` : `ImageService.compose(prompt, ref_images=[...])` (apihub
Agnes, `extra_body.image`). En vidéo : `VideoService.generate(ref_images=[...])` avec 1 ref
→ `i2v`, 2+ refs → mode `keyframes`.

Porter dans le prompt la mention de référence :
- image : `REFERENCE ONLY for character identity (same face, same clothes. No other people.)`
- vidéo : `REFERENCE IMAGE for character identity: match exactly (same face, same clothes, same hair).`

Un `seed` et un `mix_alias` ne remplacent pas une ref. Générer sans refs → un personnage
différent à chaque plan. L'ordre des blocs du prompt image (≤ ~800 caractères) :
**contraintes critiques → style → staging → lumière → mood → personnage → ratio.** Les
contraintes d'abord, sinon la troncature les perd.

## Ne pas valider à l'œil : vérifier par vision

Avant de produire la suite, passer les images par `ztudioz_analyze_images` (ou
`analyze_character_sheet` / `analyze_animatic`) pour compter les personnes, décrire le
personnage et l'ambiance. L'œil humain ne voit pas un changement de visage à 3 % ; la vision
LLM si, et c'est le mécanisme de vérification du `keyframes` (`verify_each_keyframe`).

## Méthode de réparation : la cause racine avant la régénération

Régénérer au hasard coûte des crédits et ne corrige pas la cause. Avant toute
régénération, identifier **à quel étage** la dérive est née :

1. **La fiche est-elle fausse ?** → corriger la bible, régénérer la sheet, re-propager.
2. **La sheet est-elle fausse ?** → régénérer la sheet, re-propager.
3. **Le shot est-il faux ?** → régénérer le shot via `VisualizeStep` avec les sheets en refs, puis la vidéo.
4. **Le prompt ignore-t-il la ref ?** → la ref n'a pas été passée, ou `strict_refs` a laissé retomber sur un modèle sans refs.
5. **La vidéo dérive au milieu ?** → c'est l'i2v 1 ref : passer en mode keyframes 2 refs (double-ancre).
6. **Le décor change ?** → le lieu n'est pas nommé dans le `prompt_image`, ou l'ambiance n'est pas réinjectée.

Le mode keyframes a une boucle de vérification par keyframe (continuité, conformité au
delta, découpage storyboard, script original) avec verdicts JSON et corrections
journalisées dans `verification_corrections.md`. C'est le filet de sécurité de cette
méthode : voir `ztudioz-video-production`.

## Repli interdit vers un modèle sans refs

Si le modèle d'image principal échoue (500, modération, budget), **ne pas** retomber sur
`flux` : `flux` ignore les images de référence et produit un personnage différent. Solution :
ré-uploader les refs (`expire=172800`) et réessayer.

La distinction : ce « repli interdit » vise le passage à un **modèle** sans refs, pas un
changement de **provider** (ex. Pollinations 402 → Agnes), qui est fait et automatique
(`_fallback_generate`) et doit conserver l'attachement aux refs.

## Détail dans les références

- `references/states-subrefs.md` : états visuels, sous-références, ré-ancrage par frontière
- `references/identity-drift.md` : double-ancre identité, format d'image unifié, arbitrage
  i2v vs keyframes, methodes de réparation

Si le problème est « le personnage glisse au milieu du plan », c'est l'i2v 1 ref : voir
`references/identity-drift.md`. Si c'est « il est assis mais le script le veut debout », c'est
une sous-référence d'état : voir `references/states-subrefs.md`.
