# Modifications — session 2026-09-27 (lot P2 + P3)

Log des changements de **code** faits par cet assistant pendant la session, pour vérifier
ensuite quoi que ce soit de l'autre agent. Snapshot de référence : `/tmp/opencode/baseline/`
(md5 de tous les fichiers pipeline pris avant ce lot).

## P3.1 — Double-ancre cinéma (produce)

`pipeline/steps/produce.py`
- `produce` génère désormais en **keyframes 2 refs** par défaut :
  `ref_images=[shot_N, shot_{N+1}]` quand le shot suivant existe, repli i2v simple sinon.
  Champs `shot["video_mode"] = "keyframes-2refs" | "i2v"`.
- Nouveau `_double_anchor_enabled()` : défaut `true`, désactivable par projet via
  `production.produce.double_anchor: false`.
- `_build_motion_prompt` injecte le **SCENE LOCK** (contraintes déclaratives de scène),
  comme `visualize`.
- Nouveau `_mark_shot_progress` : journal par plan dans `scene_state.json` (avec P3.4).

## P3.4 — Checkpoints par plan

`pipeline/state.py`
- `SceneState.mark_shot(scene, step, shot_number, ...)` : journal d'avancement par plan
  sous `artifacts.shots[<num>]`.
- `SceneState.shot_done(...)` : test d'existence d'un plan fait.

`pipeline/steps/produce.py`, `pipeline/steps/visualize.py`
- Appel `mark_shot` après chaque plan réussi. Un crash laisse une trace exploitable.

## P3.5 — Cache déterministe

`pipeline/services/refsig.py` (NOUVEAU)
- `ref_signature(ref)`, `refs_signature(refs)` : signature de contenu `(mtime_ns:taille)`
  d'une ref locale, URL pour une ref distante. Détecte une ref régénérée au même chemin.

`pipeline/services/image.py`
- Clés de cache agnes (ligne ~175) et pollinations (~359) enrichies de la signature de la
  ref : une ref régénérée ne sert plus l'ancienne image.

`pipeline/services/video.py`
- Clé de cache (~61) enrichie de `refs_signature(refs)` : idem pour les vidéos.

## P3.6 — Storyboard parallèle

`pipeline/steps/storyboard.py`
- `run()` : les scènes sont découpées en parallèle (`ThreadPoolExecutor`), chaque scène
  garde sa séquence de plans et son `storyboard.json`. Worker configurable via
  `production.storyboard.parallel` (défaut 2). `_chat` gère déjà le rate-limit.

## P2 — Couche de stages (recut narratif)

`pipeline/orchestrator.py`
- `Orchestrator.STAGES` : 7 stages (`prepare, identify, cut, frame, move, sound, assemble`)
  mappés vers les étapes existantes. Les identifiants d'étapes, checkpoints et outils MCP
  ne changent pas.
- `Orchestrator.expand_stages(steps)` : expand un nom de stage en étapes réelles.
- `run_pipeline` expand les stages avant la boucle ; `assemble` seul est intercepté avec un
  message (montage = service `pipeline.services.montage`, pas une étape).

## Documentation synchronisée

- `.opencode/skills/ztudioz-pipeline/SKILL.md` : tableau des étapes (produce → double-ancre)
  + nouvelle section « Stages (recut narratif) ».
- `.opencode/skills/ztudioz-video-production/SKILL.md` : section `produce` mise à jour
  (double-ancre au lieu de 1 ref).

## Intégrité des fichiers de l'autre agent

Avant et après ce lot, comparé au snapshot 14:42 :
- `config.yaml`, `config.py`, `vision.py`, `consistency.py`, `ztudioz_toolkit.py` :
  **intacts** (byte-identiques à 14:42).
- `llm.py` : modifié par l'autre agent avant ce lot, **inchangé pendant** ce lot.

Vérification : `py_compile` sur tous les fichiers touchés, `test_system.py` et
`test_doc_drift.py` passent, tests unitaires ciblés (double-ancre, mark_shot, refsig,
parallélisation, stages) passent.
## Session suivante — protocole carte de scène (scène 1 Noostate)

Ajout **additif** dans `projets/Noostate/` (projet intouchable, mais instruction humaine
explicite + sauvegardes dans `/tmp/opencode/noostate-guard/`) :

- `bible/personnage/{maya,antoine,claire,sami}/fiche.yaml` : bloc `tenues.default` déclaré
  (seule ligne `tenues: {}` → remplacée). Reste intact, vérifié par diff.
- `bible/scene/scene_01.yaml` : carte de scène (distribution fermée 4 personnages,
  public en `presence: {min: 4}`, location, must_include/must_not_include).

Code (correctif nécessaire à l'honnêteté du verrou) :

- `pipeline/scene_constraints.py` : `presence` accepte `{min: N}` (scène à public) →
  `SCENE LOCK: AT LEAST N NAMED CHARACTER(S)...; crowd allowed as decor only` ;
  `expected_people` renvoie le dict.
- `pipeline/shot_verify.py` : `_people_text` lit `at least N` dans le prompt de
  vérification (un plan avec public n'est plus jugé faux).

`projects/Noostate/.checkpoint.json` non modifié ; aucune image/vidéo produite.

## Décision utilisateur : fichiers `_alt` (ne pas perturber l'autre agent)

Noostate = projet intouchable + en production. Rien n'est modifié dans les fiches/scène
d'origine. Le travail de déclaration vit dans des fichiers `_alt` (ignorés par le code) :

- `bible/personnage/{maya,antoine,claire,sami}/fiche_alt.yaml` : copie intégrale de la
  fiche + bloc `tenues.default`. Originales `fiche.yaml` intactes (vérifié par diff).
- `bible/scene/scene_01_alt.yaml` : carte de scène (distribution fermée 4 pers., public
  en `presence: {min: 4}`, location, must_include/not_include).
- `scene_01.yaml` n'existe pas : `SceneConstraints` ne lit pas le `_alt` → aucun impact
  sur la production de l'autre agent.

État production : shots 01-05 déjà générés (14:52-14:54, par l'autre agent). Aucun
écrasement, aucune régénération, coût 0.

## Conflit de génération — restauré (16:47)

L'autre agent a régénéré les shots 01-04 (16:27-16:30, v2/v3) pendant que je lançais la
régénération demandée. Mon process (16:31-16:32) a écrasé ses shot_01/02 et laissé shot_03
manquant (process mort).

Décision utilisateur : restaurer les versions de l'autre agent.
- shot_01.png = shot_01_v3.png (16:29) — copie, md5 identique
- shot_02.png = shot_02_v3.png (16:29) — copie, md5 identique
- shot_03.png = shot_03_v3.png (16:30) — restauré depuis v3 (fichier actif manquant)
- shot_04.png (16:30) et shot_05.png (14:54) : inchangés
- `storyboard.json` : référence toujours shot_0N.png (chemins actifs), cohérent.

Leçon : ne jamais lancer de génération pendant que l'autre agent produit les mêmes
fichiers. Vérifier d'abord (timestamps/process) et demander.
