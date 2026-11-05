---
name: ztudioz-studio-v3
description: >-
  Piloter le studio déclaratif v3 depuis une session opencode : créer un projet
  (dossier de runs), enchaîner les paliers m0-conversation (discussion -> brief ->
  traitement), m1-scene (plans + images + gate + verdict) et m2-video (images -> vraies
  vidéos -> montage, ffmpeg, ffprobe), gérer les gates humaines (continue/retry/stop),
  reprendre après Ctrl-C, lire le journal et les coûts, invalider une branche par lignée,
  brancher les skills v1 et les providers réels (Albert, Pollinations). Déclencheur : studio
  v3, nouveau projet v3, m0, m1, m2, vidéo, gate, runs, artifacts, invalidate, albert,
  pollinations.
---

# Studio v3 — assistant de production déclaratif

Le projet n'est pas un dossier `projets/` (v1) : **un projet = un dossier de runs**.
Tout l'état est là : `runs/<slug>/journal.jsonl` (histoire append-only) et
`runs/<slug>/artifacts/` (livrables adressés par hash de contenu, jamais écrasés).

## Règles permanentes

- Ne jamais écrire dans `/home/smag/dev/ZtudioZ/projets/` ni modifier `main`.
- Ne jamais lancer un run réel sans demande explicite de l'humain.
- Toute modification du moteur passe par un test rouge d'abord (cf. REFACTOR_V3.md §8).
- L'humain décide aux gates : on ne répond jamais `continue` à sa place.

## Providers réels

```bash
cd /home/smag/dev/ZtudioZ-v3
set -a && . /home/smag/dev/ZtudioZ/.env && set +a   # OPENAI_* (Albert), POLLINATION_API_KEY
export STUDIO_IMAGE_WIDTH=256 STUDIO_IMAGE_HEIGHT=256
```
- Texte : `--provider openai` (Albert etalab, modèle via `STUDIO_MODEL`, les placeholders
  `mock/*` des agents cèdent la place au modèle opérateur).
- Images : `--image-provider pollinations` (gratuit : 1 image / 45 s / IP + quota global
  partagé ; l'adaptateur honore les délais annoncés, un run M1 complet prend ~4-5 min).
- Vidéos : `--video-provider pollinations` (`wan-fast` défaut, ~0,01 $/s ; `grok-video-pro`
  ~0,05 $/s via `STUDIO_VIDEO_MODEL`). Facturé à la seconde, budget refusé avant l'appel.
- Démo sans réseau : omit les flags, tout est mock (0 coût, 0 réseau).

## Créer un projet et dérouler les paliers

### M0 — discussion -> brief -> traitement
```bash
RUNS=runs/<slug>
python -m studio.cli --runs $RUNS run studio:workflow/m0-conversation \
  --provider openai --input '{"discussion": "<la discussion brute>"}'
```
Sortie attendue : statut `blocked`, gate `gate_brief` avec la question. Montre le brief à
l'humain (artefact JSON sous `runs/<slug>/artifacts/`). Puis, sur SON avis :
```bash
python -m studio.cli --runs $RUNS run studio:workflow/m0-conversation \
  --provider openai --input '{"discussion": "<identique>"}' --answer gate_brief=continue
# retry = refabriquer le brief ; stop = arrêter le run
```
Le `--input` doit être **identique** (le run_id en dérive) ; les nœuds déjà faits ne sont
jamais repayés (cache + journal). Récupère le **brief complet** (`outputs.brief`) : il doit
repartir avec toi jusqu'à M1, ce n'est pas un artefact mort.

### M1 — traitement -> plans -> images -> gate -> verdict
```bash
python -m studio.cli --runs $RUNS run studio:workflow/m1-scene \
  --provider openai --image-provider pollinations \
  --input '{"treatment": "<sortie du M0>", "reference": "<description visuelle ancre>", "brief": <outputs.brief du M0>}'
python -m studio.cli --runs $RUNS run studio:workflow/m1-scene ... --answer gate_images=continue
```
**Passe toujours le `brief`.** Sans lui, `brief.constraints.max_shots` est absent et le
découpage n'est plus borné : un LLM qui surcompte les plans te les fera tous payer (c'est
exactement la panne D24, fermée par cette entrée). Le nœud `images` porte
`limit: "${brief.constraints.max_shots}"` — la borne vient du brief, pas d'un nombre tapé à la
main dans un shell.

Si le plan demandé dépasse la borne, le journal écrit `items_truncated {produced, kept}` : ce
n'est **pas** une erreur ni un bug, c'est la contrainte de l'humain qui a tenu. Signale le
chiffre à l'humain à la gate (« le découpage proposait 6 plans, 2 gardés ») — ne le tais pas.

Livrables : `outputs.image[]` = références d'artefacts ; les vraies images sont dans
`runs/<slug>/artifacts/<hash>.jpg` — ouvres-les, c'est ce que l'humain doit voir à la gate.
Attention : le verdict du `continuity-checker` compare **le texte** — plans, brief, contraintes
— et il le dit. Il **ne voit pas les pixels** (dette D23) : ne le présente jamais comme une
validation visuelle. Les contraintes non chiffrées (`constraints.freeform`, « pas de dialogue »,
« fin ambiguë ») ne sont vérifiées que comme « non contredites par les prompts » (dette D26).
L'humain a vu les images, c'est lui qui a jugé à la gate.

### M2 — plans -> vidéos -> gate -> montage -> gate -> ffprobe

Deux workflows :
- **`m2-video`** : de vraies vidéos (image + mouvement -> mp4 généré). Une gate **avant** la
  génération, une gate sur les clips, puis le montage. C'est le chemin normal.
- `m2-montage` : repli explicite sans génération (clips de couleur, ou images tenues fixes).
  Il a lui aussi une gate **avant** la fabrication des clips (`gate_clips`), puis la gate du
  montage (`gate_montage`). Sans réponse, aucun mp4 n'est produit.

```bash
python -m studio.cli --runs $RUNS run studio:workflow/m2-video \
  --provider mock --video-provider pollinations \
  --input '{"plans": [{"image": {"artifact": "<hash image M1>"}, "prompt": "<plan>", "motion": "<mouvement>", "duration": 5}]}'
```

Le workflow s'arrête d'abord à `gate_storyboard` : **aucun appel payant n'est parti**. Montre
les fiches (image, prompt, mouvement, durée) et attends l'accord. Le mouvement est une décision
visuelle : propose-le, ne l'impose pas.

Après `--answer gate_storyboard=continue`, la génération part. Compte ~4-5 min par clip.
Puis `gate_videos` : donne les chemins `runs/<slug>/artifacts/<hash>.mp4`, l'humain regarde —
**ne prétends jamais voir les vidéos**. Ensuite `gate_montage`, puis `probe`.

- L'image de référence doit être publique : `tools/video.py` la téléverse sur tmpfiles.org
  (URL 48 h). C'est un export vers un tiers, dis-le à l'humain (dette D30).
- `m2-video` ne devine pas les fiches : construis `plans` depuis `shots` + `image` de M1.
- Un clip en panne n'arrête pas le montage des autres ; le journal écrit `item_failed`.
- `outputs.probe = {duration, codec, streams}` — conformité mesurée, pas déclarée.

## Inspecter, reprendre, corriger

```bash
python -m studio.cli --runs $RUNS journal              # tout l'historique horodaté
python -m studio.cli --runs $RUNS validate             # le catalogue entier est-il sain ?
python -m studio.cli --runs $RUNS invalidate <hash>    # purge un artefact + SON aval, rien d'autre
python -m studio.cli --skills-root /home/smag/dev/ZtudioZ/.opencode/skills validate
```
- `Ctrl-C` en plein run (code 130) : relancer la même commande, ça reprend.
- Les coûts : chaque run affiche « cout journalise » ; budget par agent dans
  `studio/catalog/agents/*.yamlld` (refusé **avant** appel si dépassé).

## Catalogue

Agents : `studio/catalog/agents/*.yamlld` (LlmAgent texte, ImageAgent, VideoAgent, ToolAgent ffmpeg).
Workflows : `studio/catalog/workflows/*.yamlld`. Sorties typées : `catalog/schemas/`.

Champs d'un nœud, pour écrire un workflow :

| Champ | Effet |
|---|---|
| `agent:` | `studio:agent/...` **ou** `studio:workflow/...` (sous-workflow, gates exposées préfixées) |
| `needs: [..]` | dépendances ; les sorties des nœuds cités entrent dans la portée |
| `foreach: "${cles}"` | un item = une exécution ; `${...}` se résout dans la portée fusionnée |
| `as: shot` | injecte l'item aussi sous ce nom (`item` reste l'alias par défaut) |
| `limit: 2` ou `"${brief.constraints.max_shots}"` | borne le fan-out ; troncature journalisée `items_truncated` |
| `parallel: 3` | concurrence max sur ce nœud |
| `on_item_failure: continue` | un item en panne est retiré et journalisé, le nœud ne tombe que si tout tombe |
| `type: studio:HumanGate` | nœud de validation humaine ; sans réponse, le run `blocked` |
| `question:` | la question posée à l'humain (obligatoire pour une gate) |
| `options:` | `[continue, retry, stop]` ; jamais de saut silencieux |

Règle de portage des contraintes : une contrainte de l'humain qui n'est ni dans le schéma du
brief ni déclarée en entrée d'un nœud **n'existe pas** pour le moteur (dette D25 — pas encore de
contexte projet hérité automatiquement).

Piège D29 : `inputs:` / `outputs:` sur un **nœud** sont acceptés au chargement puis **ignorés**.
Ils ne renomment rien. Et la sortie `reference` de `ref_image` écrase l'entrée texte `reference`
dans la portée : un nœud qui dépend de `ref_image` ne voit plus l'ancre textuelle de l'humain.

Modifier un agent = toucher prompts/skills/budget uniquement ; le moteur, lui, ne se
change qu'avec tests. L'état du chantier et la dette : `REFACTOR_V3.md` (§7 paliers, §12).
