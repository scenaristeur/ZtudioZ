---
name: ztudioz-studio-v3
description: >-
  Piloter le studio déclaratif v3 depuis une session opencode : créer un projet
  (dossier de runs), enchaîner les paliers m0-conversation (discussion -> brief ->
  traitement), m1-scene (plans + images + gate + verdict) et m2-video (images -> vraies
  vidéos -> montage, ffmpeg, ffprobe), gérer les gates humaines (continue/retry/stop),
  reprendre après Ctrl-C, lire le journal et les coûts, invalider une branche par lignée,
  charger des packs de skills externes et brancher les providers réels (Albert,
  Pollinations). Déclencheur : studio
  v3, nouveau projet v3, m0, m1, m2, vidéo, gate, runs, artifacts, invalidate, albert,
  pollinations.
---

# Studio v3 — assistant de production déclaratif

## Depuis opencode

Commandes disponibles (TUI opencode) :

| Commande | Usage |
|---|---|
| `/studio-status <projet>` | dernier run, coût, gate, palier suivant |
| `/studio-next <projet>` | palier suivant + entrées manquantes |
| `/studio-run <projet> <workflow> [json]` | lance un workflow, s'arrête à la gate |
| `/studio-answer <projet> <run_id> <gate> <choix> [feedback]` | répond à une gate |
| `/studio-journal <projet> [run_id]` | journal filtré |
| `/studio-validate` | valide le catalogue |

L'agent `studio-operator` (mode primary) exécute ces commandes. Il ne répond **jamais** à une
gate sans choix explicite de l'humain.

Un projet n'est pas un dossier de production partagé : **un projet = un dossier de runs**.
Tout l'état est là : `studio.yaml` (config), `journal.jsonl` (histoire append-only) et
`artifacts/` (livrables adressés par hash de contenu, jamais écrasés).

```yaml
# <projet>/studio.yaml — aucun secret ici, seulement des noms de variables
name: feu-follet
context:                 # hérité par tous les workflows, sans le repasser en --input
  brief:
    constraints:
      max_shots: 2
  reference: "Phare blanc et rouge, mer grise, crépuscule froid"
providers:
  llm:   {kind: openai, model: openai/gpt-oss-120b, base_url: ${OPENAI_BASE_URL}}
  image: {kind: pollinations, model: flux, width: 256, height: 256}
  video: {kind: pollinations, model: wan-fast, width: 512, height: 512}
```

Avec `--project <dossier>`, la CLI lit cette config, écrit artefacts et journal dans le
projet, et n'exige plus aucun flag provider. Les flags restent des overrides explicites.
Sans `--project`, `--runs` et les flags fonctionnent comme avant (défaut mock).

Un workflow déclare ce qu'il consomme **une fois** : `context: [brief]` (requis) ou
`context: {brief: {required: false}}` (optionnel). Une entrée explicite en `--input` écrase
le contexte du projet. `m1-scene` ne redemande plus `brief` : il vient du projet.

### Enchaîner les paliers

Après un run **terminé**, le suivant hérite des entrées et sorties du dernier run, filtrées
par ce que son workflow déclare. Exemple M0 → M1 :

```bash
python -m studio.cli --project runs/<slug> run studio:workflow/m0-conversation \
  --input '{"discussion": "<discussion>"}' \
  --answer gate_brief=continue --answer gate_treatment=continue

# treatment + brief viennent du journal ; seule la référence est humaine
python -m studio.cli --project runs/<slug> next
python -m studio.cli --project runs/<slug> run studio:workflow/m1-scene \
  --input '{"reference": "<ancre visuelle>"}' \
  --answer gate_images=continue --answer gate_verdict=continue
```

`next` affiche le palier suivant, ce qui est hérité et ce qui manque. `status` montre le
dernier run, son coût, ses nœuds et la gate en attente. `bible` liste les entités et planches
(`studio --project <dossier> bible [--type lieu]`). Le CLI vérifie les entrées requises
**avant** tout appel : une entrée manquante ou un JSON invalide sort en erreur propre, sans
traceback.

### Bible et références

La bible du projet est lue telle quelle depuis `<projet>/bible/` :
`bible/<type>/<slug>/fiche.yaml` + `sheet_<version>.png`.
`m1-scene` accepte `references: ["lieu/desert", "personnage/mael"]` ; l'agent `sheet-keeper`
résout les planches, les stocke comme artefacts, et les ImageAgents les reçoivent avec la
planche générée. Le contrôleur de continuité reçoit aussi ces images pour comparer les shots
aux planches. La vidéo accepte de même une liste `references` (Pollinations n'en utilise
qu'une ; Agnes local gère le chaînage).

Le CLI affiche `herite du journal: ...`. Un run **bloqué** à une gate ne nourrit pas la
mémoire : seule une validation humaine terminée compte. L'ordre est
`--input` explicite > `context` du projet > journal.

### API HTTP (lecture + gates)

```bash
python -m studio.cli serve --projects runs --port 8000
```

- `GET /api/projects` : projets, dernier statut, gate en attente.
- `GET /api/projects/<slug>` : providers, contexte, runs.
- `GET /api/projects/<slug>/journal[?run=<id>]` : événements.
- `GET /api/projects/<slug>/runs/<run_id>` : résumé, gate, artefacts.
- `GET /api/projects/<slug>/artifacts/<hash>` : fichier (JSON, image, mp4).
- `POST /api/projects/<slug>/runs/<run_id>/gates/<gate_id>` avec `{"answer": "continue"}` :
  relance en arrière-plan (202), puis suivre le journal.

L'API ne lance pas encore de nouveaux runs : elle répond aux gates. Pas d'authentification —
écoute sur `127.0.0.1` uniquement. Un seul run à la fois par projet. `studio serve` charge
automatiquement `.env` (sans écraser une variable déjà exportée) ; une erreur de run est
exposée dans le résumé et affichée dans l'UI.

### UI web

```bash
cd web && npm install && npm run build   # dist committé, inutile en général
cd .. && python -m studio.cli serve --projects runs
# http://127.0.0.1:8000  (si le port est pris, ex. Portainer : --port 8001)
```

La page est **pilotée par le graphe** : projets, runs, colonnes du DAG, cartes de nœuds
(gate / complété / en attente), aperçu image et vidéo des artefacts, panneau de gate,
journal. Elle interroge l'API toutes les 2 s. Pour développer : `npm run dev` (Vite proxifie
`/api` vers `127.0.0.1:8000`).

## Règles permanentes

- Le studio v3 est autonome : ne jamais lire ni écrire hors du projet courant
  (`runs/<slug>`). `runs/` est la seule source d'état.
- Ne jamais lancer un run réel sans demande explicite de l'humain.
- Toute modification du moteur passe par un test rouge d'abord (cf. REFACTOR_V3.md §8).
- L'humain décide aux gates : on ne répond jamais `continue` à sa place.

## Providers réels

```bash
cd /home/smag/dev/ZtudioZ-v3
set -a && . ./.env && set +a   # OPENAI_* (Albert), POLLINATION_API_KEY, AGNES_API_KEY
export STUDIO_IMAGE_WIDTH=256 STUDIO_IMAGE_HEIGHT=256
```
- Texte : `--provider openai` (Albert etalab, modèle via `STUDIO_MODEL`, les placeholders
  `mock/*` des agents cèdent la place au modèle opérateur).
- Images : `--image-provider pollinations` (gratuit : 1 image / 45 s / IP + quota global
  partagé ; l'adaptateur honore les délais annoncés, un run M1 complet prend ~4-5 min).
  **`mock` produit des PNG unis de 8×8** : c'est un placeholder de test, jamais un rendu.
  Le prompt image porte le plan, l'ancre `reference` et les contraintes `freeform` du brief ;
  un `retry` de `gate_images` avec feedback regénère réellement les images.
  Pour l'ancrage visuel, configurer `ref_model: gptimage` et `strict_refs: true` : une
  référence passe alors par `gen.pollinations.ai` avec `image=URL` ; plusieurs références
  passent par le POST JSON en liste. `flux` seul est texte→image et ignore les pixels.
- Vidéos : `--video-provider pollinations` (`wan-fast` défaut, ~0,01 $/s ; `grok-video-pro`
  ~0,05 $/s via `STUDIO_VIDEO_MODEL`). Facturé à la seconde, budget refusé avant l'appel.
- Agnes local (`http://localhost:8765`) : `--image-provider agnes` et
  `--video-provider agnes`, ou `kind: agnes` dans `studio.yaml`. L'image accepte
  `reference_image` (image→image) et compose plusieurs références via apihub
  (`AGNES_API_KEY`). La vidéo accepte 1 image (i2v) ou 2 images (keyframes début/fin,
  `mode=keyframes`). Une file vidéo pleine (`503 video_queue_full`) est retentée puis
  expliquée nettement.
- **Repli Agnes → Pollinations, jamais silencieux** : si Agnes est injoignable ou sature,
  le CLI demande l'accord humain (`--fallback ask`, défaut), bascule avec
  `--fallback pollinations`, ou échoue avec `--fallback fail`. En mode non interactif,
  `ask` n'invente pas la réponse : il faut relancer avec `--fallback pollinations`.
  Le repli utilise `flux` sans référence, puis `gptimage` dès qu'une référence existe
  (`strict_refs`), et `wan-fast` avec image pour la vidéo, à coût bas.
- Keyframes : `studio:workflow/m2-keyframes` génère deux images par plan
  (`start_image`, `end_image`) via `keyframe-maker`, fait valider `gate_keyframes`,
  puis lance les vidéos avec les deux références. Aucune vidéo n'est payée avant
  la validation humaine des keyframes.
- Vision : le `continuity-checker` utilise `gemma-4-31b-it` (Albert) et reçoit les **pixels**
  des images en `image_url` (data URI), pas seulement des références. Il rend une entrée par
  contrainte `freeform` du brief (`text`, `respected`, `evidence`) ; `ok=true` est refusé par
  le schéma si une contrainte est violée.
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
Après le brief, le run s'arrête à `gate_treatment` : même choix (`continue` / `retry` /
`stop`). Pour une correction, `retry` accepte un feedback :
```bash
python -m studio.cli --runs $RUNS run studio:workflow/m0-conversation \
  --input '{"discussion": "<identique>"}' \
  --answer gate_treatment=retry \
  --feedback "gate_treatment=reduis a 3 plans, garde la fin silencieuse"
```
Le feedback est injecté dans l'étape rejouée et journalisé (`gate_retry.feedback`) ; il
produit une **nouvelle version**, pas une correction en place. Dans l'UI, c'est la zone de
texte sous la gate + « Créer une nouvelle version ».

Pour corriger un seul plan d'un foreach (vidéos, images), cibler l'index :
```bash
python -m studio.cli --project <dossier> answer <run_id> gate_videos retry \
  --feedback-item 'gate_videos#1=garder le masque beige et la chemise identiques'
```
Seuls l'item `#1` et les items en échec sont rejoués ; les autres restent en cache.
`--feedback-item` est répétable (`gate#0=...`, `gate#2=...`). Ne pas mélanger avec un
`--feedback` global pour la même gate.

Après un run terminé, l'UI affiche **« Palier suivant »** : entrées héritées, champs
manquants (ex. `reference` pour M1) et bouton de lancement. API : `GET /api/projects/<slug>/next`
et `POST /api/projects/<slug>/runs` (`{"workflow": "...", "inputs": {...}}`). Aucune valeur
visuelle n'est inventée : si `reference` manque, c'est l'humain qui la saisit.
Le `--input` doit être **identique** (le run_id en dérive) ; les nœuds déjà faits ne sont
jamais repayés (cache + journal). Récupère le **brief complet** (`outputs.brief`) : il doit
repartir avec toi jusqu'à M1, ce n'est pas un artefact mort.

### M1 — traitement -> plans -> images -> gate -> verdict -> gate
```bash
python -m studio.cli --runs $RUNS run studio:workflow/m1-scene \
  --provider openai --image-provider pollinations \
  --input '{"treatment": "<sortie du M0>", "reference": "<description visuelle ancre>", "brief": <outputs.brief du M0>}'
python -m studio.cli --runs $RUNS run studio:workflow/m1-scene ... \
  --answer gate_images=continue --answer gate_verdict=continue
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
Attention : le verdict du `continuity-checker` voit désormais **les pixels** (Albert
`gemma-4-31b-it`) et juge **plan par plan** (`shots` : `index` 0-based, `ok`, `notes`,
`issues`), en plus des contraintes `freeform` (`respected`/`evidence`). Un seul plan
fautif interdit `ok=true`. Il reste un avis : c'est l'humain qui tranche à
`gate_verdict`. Le CLI affiche chaque plan et propose une commande ciblée
`--feedback-item gate_verdict#<index>=...` ; ne l'exécute que si l'humain a validé la
correction de ces plans. L'UI expose la même chose : une zone par plan fautif
(« Recommencer les plans fautifs ») et une zone globale.

Sans feedback, un `retry` de foreach ne rejoue que les items en échec : les items déjà
réussis restent en cache et ne sont pas repayés. Avec feedback global (`--feedback
gate=texte`), tout le fan-out est rejoué. Pour ne corriger qu'un item, utiliser
`--feedback-item gate#index=texte` (répétable) : seuls les items visés et les items en
échec sont rejoués, et la correction n'est injectée que dans les items visés. Ne jamais
mélanger `--feedback` et `--feedback-item` pour la même gate (l'API refuse
`feedback` + `feedback_items`).

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
python -m studio.cli --skills-root /chemin/vers/packs validate   # packs externes optionnels
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
