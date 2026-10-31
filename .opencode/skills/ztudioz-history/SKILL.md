---
name: ztudioz-history
description: >-
  Historique et décisions du projet ZtudioZ - ce que les clips déjà produits (Le Jardinier des
  Ondes, Melody's Ghost) ont appris, pourquoi les règles actuelles existent, et comment versionner
  ou revenir en arrière sur une fiche bible ou une version d'entité. Utilise pour comprendre
  pourquoi une règle est en place, éviter de refaire une erreur déjà payée, ou faire un rollback
  d'une fiche. Déclencheur : history, historique, pourquoi, lesson, leçon, rollback, revenir en
  arrière, versionner, switch-version, changelog,Archive, clip V3 V4.
---

# Historique & décisions

**Dépendances** : `ztudioz-bible` (versions d'entité).

## Pourquoi ces règles existent

Les règles actuelles ne sont pas des préférences : chacune vient d'un échec observé sur une
production réelle.

**Le clip « Le Jardinier des Ondes » (V3 → V4)** a appris que :
1. La cohérence = **refs + contrainte forte**, pas la description textuelle. Générer sans
   refs → un personnage différent à chaque plan.
2. **Prompt ≤ ~800 caractères**, contraintes critiques **en premier** (sinon la troncature
   les perd).
3. Un **arc / fil conducteur** est indispensable : une suite de beaux plans sans progression
   = « répétitif, pas de fil conducteur ». V3 en avait un, V4 l'avait perdu.
4. **Lieu cohérent par section** : ne pas laisser chaque plan inventer un décor.
5. Le **repli provider** doit être automatique (actif → backup) sur 402/503.

**« Melody's Ghost »** a ajouté la leçon **vidéo / chaînage** :
6. **États visuels (`states`) + sous-références** : la position suit la timeline, pas la sheet
   de base.
7. **Double-ancre identité** : un i2v simple dérive l'identité en milieu de plan. Passer en
   keyframes 2 refs (start + end) et vérifier par **mid-frame**, pas seulement au début.
8. **Format unifié** pour le chaînage : `.last.png`, `.start.png` et la vidéo doivent avoir
   le même format. Extraire en natif, recadrer (jamais étirer).
9. **Ré-ancrage par frontière** (`chaining.section_reset`) dans un arc multi-sections, sinon
   le héros de la nouvelle section n'est jamais utilisé.
10. **Hero candidates + planches de contact** : générer des candidats, les soumettre à
    validation, verrouiller avant de produire.
11. **Cache compose = par existence de fichier** : pour vraiment régénérer, purger le
    fichier cible ; `--force` seul ne suffit pas.

Le cinéma a hérité de ces leçons. Voir `ztudioz-consistency` et
`ztudioz-video-production`, qui les encodent en règles opérationnelles.

## Versionner une entité

Chaque entité a un `versions` (v1, v2, …) et un `active_version`. L'A/B sert à comparer
deux lectures d'un même personnage ou lieu avant de verrouiller :

```bash
python main.py switch-version <slug> <entity-slug> v2
```

Pour revenir en arrière : rebasculer sur l'ancienne version. Les sheets sont alors
rechargées comme références au storyboard suivant. La version inactive est **conservée** dans
le `fiche.yaml` : rien n'est perdu en changeant `active_version`.

Points d'attention :

- Une version inactive n'est **pas** utilisée, même si sa sheet existe.
- Changer d'entité après le storyboard impose de **re-propager** : les `prompt_image` des
  plans ont été écrits contre l'ancienne version. Régénérer les shots, puis les vidéos.
- Les coût successifs d'une scène sont dans `consumption.py:get_tracker()` — utile pour
  savoir si une régénération est abordable avant de la lancer.

## Docs historiques

`PLAN.md`, `PLAN_REFACTOR.md`, `SPECS.md`, `METHODE.md`, `WORKFLOW.md`, `SCHEMAS.md`,
`experimental.md`, `N8N.md` et le compte rendu `projets/*/.../REX_v4.md` documentent les
étapes et lesVersions successives. **Ces documents sont la référence d'archive** : s'ils
contredisent le code ou la config, c'est le code qui fait foi. Le cycle courant est dans
`ztudioz-pipeline`.
