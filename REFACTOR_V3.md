# REFACTOR_V3 — ZtudioZ Studio (runtime d'agents déclaratif)

> **Ce fichier est la version de travail.** L'original, figé au moment de la décision, reste
> consultable dans le dépôt principal : `/home/smag/dev/ZtudioZ/REFACTOR_V3.md` (branche
> `main`). C'est ici que l'on met à jour l'avancement, les décisions et la dette technique.
>
> **Statut** : **M0 + M1 + M2 réalisés** — noyau complet, artefacts binaires typés et lignés,
> adaptateur image, `ToolAgent` + dispatch (python/cli), montage ffmpeg vérifié par ffprobe,
> échec d'item borné, sous-workflows, retry de gate, SIGINT, 104 tests verts, démos CLI
> M0/M1/M2 fonctionnelles ; runs réels validés de bout en bout (Albert pour le texte,
> Pollinations pour les images, RIA respecte les quotas annoncés).
> **Décideur** : l'humain (super-décideur, cf. `AGENTS.md`).
> **Date de décision** : 2026-10-31.
> **Branche** : `studio-v3`, **orpheline**, worktree `/home/smag/dev/ZtudioZ-v3`.
> **Base de code v1** : `main` @ `4311be5` — gelée, intouchable, sert de référence.
> **Périmètre** : constat, diagnostic, architecture, plan par paliers, reprise, dette connue.

---

## 1. Décision et arbitrages

L'utilisateur a demandé une **refonte totale plutôt qu'une correction incrémentale** : un
assistant de production réellement découpé en agents, configurable par l'utilisateur, capable
de parallélisme, partant indifféremment d'une conversation, d'un scénario importé ou de
quelques images. Quatre arbitrages ont été tranchés :

| # | Question | Arbitrage retenu | Raison |
|---|---|---|---|
| 1 | Comment matérialiser la refonte | **Branche orpheline** dans un worktree séparé | Historique neuf, `main` reste déployable, `projets/` jamais exposé au worktree |
| 2 | Base du moteur | **Noyau asyncio maison** (~800 lignes) | Contrôle total du DAG, du cache, des gates et du budget ; zéro lock-in |
| 3 | Premier palier | **M0 conversation seule** | Valide tout le noyau à coût zéro, avant tout appel média |
| 4 | Rigueur du format | **YAML-LD validé** (`@context` obligatoire) | Documents requêtables et typés, dérive détectée au chargement |

Précision apportée ensuite par l'utilisateur : `pipeline/`, `api/` et `web/` seront
**entièrement revus au palier M3**, pas adaptés à la marge. Ils restent donc hors périmètre
jusque-là, et aucune ligne de la v1 n'est copiée dans la branche neuve avant M3.

`camel-ai` (0.2.90) est déjà installé dans `.venv` mais **n'est utilisé par aucun fichier du
dépôt**. Il reste disponible comme réservoir d'outils, il n'est pas la base du runtime.

---

## 2. Constat de l'existant (audit du 2026-10-31)

Audit **read-only** : aucune écriture, aucun commit n'en a résulté. Vérifications exécutées :
`test_doc_drift.py` ✅, `test_system.py` ✅, `list_projects` ❌, `get_status` par projet.

### 2.1 Trois cerveaux concurrents

Le même cycle de production est décrit trois fois, en trois endroits qui ne s'accordent pas :

| Emplacement | Nature | Exemple de divergence |
|---|---|---|
| `pipeline/orchestrator.py` | orchestration **codée en dur** (`AVAILABLE_STEPS`, `STAGES`, `_save_checkpoint`) | `board` est une étape réelle |
| `.opencode/skills/ztudioz-*` (11 skills) | savoir **procédural** en Markdown | `board` décrit tantôt comme étape, tantôt comme sous-pas |
| `.opencode/agents/*.md` (9 subagents) | rôles **opencode** avec jeux d'outils | `storyboarder` affirme que `board` n'est pas une étape |

Aucun test ne relie ces trois sources. Une correction dans l'une ne se propage pas aux autres :
c'est la cause racine de la dérive documentaire constatée.

### 2.2 Dérives mesurées

- `board` : **trois implémentations** coexistent — `BoardStep` (planches par groupes de 5,
  prompt de salle de conférence codé en dur), l'outil MCP `storyboard_board` (une planche via
  `PromptBuilder`), et `storyboard_visualize` (planche **puis** shots).
- `write_checkpoint` est cité dans la documentation ; le symbole réel est `_save_checkpoint`.
- `describe_sheet` est documenté comme inexistant, alors qu'il existe dans le MCP
  `character-designer`.
- `bible/ambiance/` : documenté comme jamais créé par `init`, alors que le correctif est en
  place et que `verify_structure()` diagnostique.
- Docs web : `ztudioz-web-api` et `ztudioz-n8n` décrivent Next.js et un *render-job* qui
  n'existent pas. Le web réel est **Vue 3 + Vite + Pinia**, l'API **FastAPI** avec les routes
  `projects`, `pipeline`, `bible`, `assets`, `history` + WebSocket.
- 5 skills racine (`skills/*.md` : `create-scenario`, `fix-consistency`, `new-project`,
  `produce-scene`, `regenerate`) ne sont **chargés par personne** et décrivent un cycle
  obsolète.
- Numéros de ligne cités dans la documentation déjà périmés.

### 2.3 Bloquants runtime constatés

| # | Symptôme | Cause identifiée | Impact |
|---|---|---|---|
| B1 | `list_projects` échoue, donc `get_status("KeepTalkingAndListening")` aussi | YAML invalide : `projets/KeepTalkingAndListening/bible/lieu/la-serre-maison/fiche.yaml`, items `attributs` sans `-` (l. 11-20) | Une fiche cassée rend **tous** les projets illistables |
| B2 | `keyframes.enabled: true` ne suffit pas | un `run` complet exécute `produce` **avant** `keyframes`, et `keyframes` saute les mp4 > 5 Ko | La branche keyframes ne peut pas gagner |
| B3 | Route montage de l'API cassée | `api/routes/pipeline.py:74` appelle `assemble_scene` avec une signature qui ne correspond plus (`video_paths: list, audio_paths: list, output_path`) | Assemblage impossible depuis le web |
| B4 | `video_producer` ne peut pas produire | le subagent n'a ni `run_pipeline` ni `bash` dans ses permissions | Le rôle est déclaré mais inopérant |

### 2.4 Ce qui marche et qui est conservé comme référence

- `pipeline/services/*` : wrappers providers (llm, vision, image, video, audio, montage,
  prompt_builder, consistency, refsig) — **la logique provider est saine**, elle sera
  consommée et non réécrite.
- `pipeline/models/` : `BibleDB`, `Scene`, versions d'entités.
- `pipeline/consumption.py` : compteur de coûts.
- `pipeline/scene_constraints.py`, `pipeline/shot_verify.py` : contraintes et vérification.
- `pipeline/config.yaml` : modèle `active:` + `providers[]`, qui inspire directement le
  registre d'outils de la v3.
- Les acquis de production (cohérence par refs, double-ancre, invalidation aval) restent la
  **doctrine** et doivent être exprimables dans le graphe.

---

## 3. Diagnostic — la cause racine

Le problème n'est pas le pipeline : c'est que **l'orchestration est du code, le savoir est de
la documentation, et rien ne les relie**. Trois conséquences :

1. Ajouter une variante de production (keyframes au lieu de produce) demande de modifier du
   Python, donc un accord, donc de la friction.
2. Corriger un comportement demande de corriger la doc *et* le code, sans test qui le vérifie.
3. L'utilisateur ne peut pas voir ni modifier le processus sans lire du Python.

La v3 déplace l'orchestration **hors du code** : un graphe déclaré en YAML-LD, exécuté par un
noyau générique, avec des tests qui valident la config elle-même.

---

## 4. Principe — quatre primitives, pas quarante agents

| Primitive | Rôle | Coût | Code |
|---|---|---|---|
| `LlmAgent` | rôle + modèle + outils + sortie structurée | LLM | générique, un seul fichier |
| `ToolAgent` | déterministe : ffmpeg, script, MCP, service | ~0 | adaptateur par outil |
| `HumanGate` | question bloquante, options `continue / retry / stop` | 0 | générique |
| `Workflow` | nœud composite → **les sous-sous-tâches** | récursif | le moteur |

Un agent **n'est pas du code** : c'est un document. Le code ne sert qu'à ces quatre types et
aux adaptateurs d'outils. C'est le garde-fou anti-sur-abstraction : si un besoin ne rentre pas
dans ces quatre types, c'est le besoin qu'il faut discuter, pas le moteur qu'il faut étendre.

---

## 5. Architecture réalisée (M2)

L'arborescence ci-dessous est **l'état réel du dépôt**, pas une intention.

```
studio/
  kernel/
    context.py     vocabulaire YAML-LD, expansion des IRIs, validation des termes
    registry.py    chargement + validation du catalogue (agents, workflows, tools, skills)
    graph.py       DAG : needs, foreach (+ alias), parallel, gates, sous-workflows, cycles
    dispatch.py    outils du registre : kind python | cli ; mcp refuse net en M2
    executor.py    asyncio, sémaphore, budget, cache, gates (+ retry), lignée, coûts
    journal.py     événements append-only (JSONL), reconstruction d'état et de coût
    artifacts.py   stockage adressé par contenu, binaires, lignée, invalidation aval
    errors.py      StudioError, LoadError, GraphError, GateError, BudgetError, SchemaError
  tools/
    llm.py         Provider (protocole) · MockProvider · OpenAICompatProvider
    images.py      ImageProvider · MockImageProvider · OpenAICompat · Pollinations (quotas respectés)
    media.py       outils livrés : clip (lavfi), montage (concat), probe (ffprobe)
  catalog/
    agents/*.yamlld      déclarations d'agents (LlmAgent, ImageAgent, ToolAgent)
    workflows/*.yamlld   déclarations de graphes (m0, m1, m2)
    skills/<id>/SKILL.md packs de connaissance injectés dans les prompts
    schemas/*.schema.json   contrats de sortie (brief, shots, verdict, probe)
    tools.yamlld         registre des capacités (kind: python | cli | mcp) + table servers:
  cli.py           run | journal | validate | invalidate | --skills-root
tests/             22 fichiers, 104 tests
pyproject.toml     dépendances + config pytest
```

Écarts assumés avec le plan initial :

| Prévu | Réalisé | Raison |
|---|---|---|
| `kernel/gate.py` séparé | gate dans `executor._exec_gate` | une quinzaine de lignes, pas besoin d'un module |
| `agents/llm_agent.py`, `tool_agent.py`, `human_agent.py` | logique d'agent dans `executor.py` | les 4 primitives partagent la résolution d'entrées, le cache et le budget |
| `schemas/` à la racine | `catalog/schemas/` | tout ce qui est déclaré vit sous `catalog/` |
| `agents/*.yamlld` à la racine | `catalog/agents/*.yamlld` | évite la collision avec les modules Python |
| `examples/m0-conversation/` | `catalog/` directement | M0 n'a pas besoin de fixture séparée |

Règles de dépendance : `kernel` ne connaît **aucun** provider ; `tools/llm.py` ne connaît
**aucun** workflow ; `catalog/` ne contient **aucun** exécutable. `pipeline/` est absent de la
branche jusqu'à M3.

---

## 6. Spécifications

### 6.1 Document YAML-LD

Tout document commence par `"@context": "studio:context"` (les clés `@` doivent être
**quotées** en YAML), déclare un `"@id"` unique et un `type`. Le chargeur :

1. résout le sentinel `studio:context` vers le vocabulaire interne (`kernel/context.py`) —
   **aucun accès réseau** ; pyld est appelé avec `document_loader=None` ;
2. **rejette tout terme non déclaré** : JSON-LD ignore silencieusement une clé inconnue,
   donc la validation des clés est faite explicitement avant expansion ;
3. confie l'expansion réelle à pyld, puis conserve la forme compacte (lisibile) tout en
   développant `@id`, `@type` et `agent` en IRIs absolues.

```yaml
"@context": "studio:context"
"@id": "studio:agent/continuity-checker"
type: studio:LlmAgent
name: Contrôleur de continuité
version: 1
model: pollinations/gemini-fast
skills: [scenario-structure]
tools: [vision.describe]
inputs:
  shot:
    type: string
    required: true
outputs:
  verdict:
    type: object
    schema: catalog/schemas/verdict.schema.json
budget: 0.02
```

Base du vocabulaire : `https://ztudioz.dev/ns/studio#`. Termes déclarés : `type` (alias de
`@type`), `name`, `description`, `version`, `model`, `prompt`, `skills`, `tools`, `inputs`,
`outputs`, `budget`, `nodes`, `id`, `agent`, `needs`, `foreach`, `parallel`,
`on_item_failure`, `question`, `options`, `as`, `kind`, `server`, `tool`, `impl`.

Un `@context` inline n'est **pas** accepté : il faut la référence au vocabulaire canonique,
ce qui garantit qu'un seul vocabulaire existe dans le dépôt.

### 6.2 `LlmAgent`

Champs : `model` (`provider/modèle`), `skills` (packs de connaissance injectés),
`tools` (capacités appelables), `inputs` / `outputs` (typés + schéma JSON), `prompt`,
`budget`. La sortie est **validée contre son schéma** ; un agent qui ne respecte pas son
contrat échoue le nœud, il ne rend pas un résultat approximatif.

À M1 s'ajoute `ImageAgent` : même contrat d'entrées/sorties, mais la sortie déclarée porte
la référence `{"artifact", "media_type", "bytes"}` d'un **binaire** stocké par le store.
L'exécuteur le branche sur l'adaptateur image (`Executor(image=...)`) et lui applique les
mêmes garanties : cache par clé de nœud, budget refusé avant appel, coût journalisé.
C'est un type **provisoire**, consigné en dette D16 : son absorption par le dispatch
`ToolAgent` (M2) a buté sur un point réel — les impls `python` du registre ne voient pas
les adaptateurs injectés à l'exécuteur. L'unification est reportée à M3, avec le branchement
des providers (`providers[]` de `pipeline/config.yaml` v1).

### 6.3 `ToolAgent`

Même contrat d'entrée/sortie, sans LLM. Chaque outil est un adaptateur mince déclaré dans
`tools.yamlld` : `kind: python | mcp | cli`, avec ses paramètres. Pas de logique métier dans
l'adaptateur.

Réalité M2 : le dispatch tourne pour `python` et `cli`.

- **python** : `impl: module:function` appelée avec `(params, ctx)` où `ctx` ne donne à voir
  que le store et le registre. Retour : dict/liste JSON-able ou `Binary` (alors stocké en
  binaire, référencé, ligné comme une image).
- **cli** : `command: [argv…]`, substitution `${param}` dans les arguments, stdout capturé →
  `{"stdout", "exit"}` ; un code retour non nul est un échec d'outil net, journalisé.
- **mcp** : client stdio branché (post-M2) sur la table `servers:` du registre, avec
  mapping `arguments:` ; le toolkit v1 reste à exposer (dette D22, M3).

Un `ToolAgent` déclare **exactement un outil** ; son coût (`cost:` de l'entrée de registre)
est estimé avant exécution, refusé avant appel si hors budget. La règle d'enveloppe : si la
valeur rendue porte déjà une clé conforme aux sorties déclarées elle est gardée telle quelle,
sinon elle est enveloppée sous la première sortie nommée.

### 6.4 `HumanGate`

Nœud bloquant. Elle journalise `gate_asked`, puis :

| Réponse | Comportement réalisé |
|---|---|
| `continue` | `gate_answered` + `node_finished` (artefact `{"answer": ...}`), les nœuds aval s'exécutent |
| `stop` | idem, mais le run s'arrête avec `status="stopped"` ; l'aval n'est **pas** exécuté |
| `retry` | **fait (M2)** : `gate_retry` + `nodes_invalidated` — l'artefact consommé et **tout son aval** sont purgés du cache, la gate est reposée ; les nœuds amont directs se rejouent dans la foulée, leur propre amont reste en cache |
| absente | `status="blocked"` + `pending_gate` (question + options) ; jamais de saut silencieux |

Le CLI sort alors avec le code 3 et indique comment relancer. Une gate ne se saute jamais :
c'est la garantie que « l'humain décide ».

### 6.5 `Workflow`

```yaml
"@context": "studio:context"
"@id": "studio:workflow/scene-production"
type: studio:Workflow
inputs:
  scene: { type: int }
nodes:
  - id: fiches
    agent: studio:agent/fiche-designer
    foreach: "${scene.characters}"
    parallel: 4
  - id: storyboard
    agent: studio:agent/storyboarder
    needs: [fiches]
  - id: gate_board
    type: studio:HumanGate
    needs: [storyboard]
    question: La planche est-elle bonne ?
    options: [continue, retry, stop]
  - id: videos
    agent: studio:agent/video-producer
    needs: [gate_board]
    foreach: "${storyboard.shots}"
    parallel: 2
    on_item_failure: continue
  - id: montage
    agent: studio:agent/montage-editor
    needs: [videos]
```

Sémantique réalisée :

| Champ | État |
|---|---|
| `needs` | **fait** — niveaux topologiques (Kahn), ordre déterministe (trié dans chaque niveau) |
| `foreach` | **fait** — expansion en items, portée par un sémaphore propre |
| `as:` | **fait (M2)** — l'item est aussi injecté sous le nom déclaré (`as: shot`), `item` reste l'alias par défaut |
| `parallel` | **fait** — borne de concurrence par nœud + sémaphore global d'executor |
| `on_item_failure: continue` | **fait (M2)** — un item en panne est journalisé (`item_failed`) et retiré du résultat groupé ; le nœud échoue seulement si **tous** les items tombent |
| sous-workflow (`agent: studio:workflow/...`) | **fait (M2)** — exécuté par un `Executor` enfant, journal propre (`run#node`), gates exposées préfixées (`node/gate`), sorties fusionnées, cache au niveau du nœud parent |
| un nœud qui ne dépend d'aucune gate voit son exécution ordonnée par nom, la gate étant traitée avant les nœuds du même niveau | **fait** |

Note `foreach` : la liste référencée est cherchée dans la **portée fusionnée** — donc
`${shots}` (clé de sortie fusionnée) et non `${id_du_noeud.shots}`. L'exemple du §6.5
pré-M2 (`${storyboard.shots}`) serait refusé : référence non résolue. À aligner dans les
futurs exemples.

### 6.6 Registre d'outils

`catalog/tools.yamlld` déclare chaque capacité et **où** elle est implémentée. Un outil
référencé par un agent mais absent du registre casse au **chargement** : c'est le point
d'accroche unique des skills, des MCP et des services, et le garde-fou anti-dérive.

```yaml
tools:
  vision.describe:
    kind: mcp
    server: character-designer
    tool: describe_sheet
```

Réalité post-M2 : le dispatch tourne pour `python`, `cli` **et `mcp`**. Les outils MCP
déclarent leur serveur dans une table `servers:` du même `tools.yamlld` (commande stdio +
args + env) et un mapping `arguments:` traduit les entrées du nœud en arguments de l'outil
(`text: "${message}"`). Les résultats texte sont rendus `{"text": ...}`, ceux qui sont du
JSON sont parsés. Testé contre un serveur jouet (`mcp` 2.x, extra optionnel du pyproject).

Reste à faire : brancher le **toolkit v1** (`ztudioz_toolkit`, ~25 outils, serveur camel) —
c'est de l'exposition, pas du mécanisme (dette D22). `vision.describe` reste une déclaration
dont le serveur n'existe pas encore sur cette branche.

### 6.7 Artefacts

Réalité M1 : `ArtifactStore` écrit chaque valeur JSON sous `artifacts/<hash>.json` et chaque
binaire sous `artifacts/<hash>.<ext>` (png, jpg, webp, mp4, mov ; l'empreinte porte les octets
bruts). Un troisième fichier, `lineage.json`, enregistre par hash : `media_type`, `parents`,
`producer`, `cost`. Un `Binary` ne circule jamais en base64 dans le JSON : il est référencé
`{"artifact", "media_type", "bytes"}`.

L'exécuteur alimente la lignée à chaque `put` : `parents` = hash des artefacts des `needs`
+ hash des références embarquées dans les entrées résolues (détection récursive). Ainsi la
chaîne `ref → image → verdict` est matérialisée dans le store, pas seulement dans le graphe.

- `store.lineage(hash)` retourne un `Artifact` typé complet.
- `store.dependents(hash)` remonte la lignée transitivement vers l'aval.
- `store.invalidate(hash)` purge de `cache.json` les entrées de cet artefact **et de tout son
  aval** ; l'amont et les branches parallèles restent en cache. Les fichiers, adressés par
  contenu, ne sont jamais supprimés : ils restent réutilisables.

L'invalidation **passive par clé de cache** (entrée modifiée → nouvelle clé → réexécution) est
toujours le premier mécanisme à l'œuvre ; `invalidate()` est le mécanisme actif, testé
(`test_invalidate_clears_downstream_cache_only`), non encore exposé par le CLI (dette D17).

### 6.8 Journal, reprise, replay

Journal JSONL append-only. Événements réellement émis :

`run_started` · `node_started` · `node_finished` · `node_skipped` (avec `reason: cache`) ·
`node_failed` · `item_failed` · `budget_refused` · `gate_asked` · `gate_answered` ·
`gate_retry` · `nodes_invalidated` · `run_finished`

Chaque événement porte `seq` (croissant, global au fichier), `ts` (UTC ISO) et `run`.
Depuis M1, chaque `node_finished` porte un champ `cost` (flottant, crédits) : coût unitaire
par nœud, somme des unités pour les agrégats de `foreach`, `0.0` pour une gate et un
`node_skipped`. `Journal.state(run_id)` cumule ces coûts (unités `#i` non comptées deux fois)
dans `RunState.cost`, et le CLI affiche « cout journalise ».
`run_id = hash(workflow + inputs)` : mêmes entrées ⇒ même run ⇒ reprise.

**Plus attendu depuis M2** : `node_started` et `budget_refused` sont émis (le refus de budget
a son événement propre, en plus du `node_failed`). **Non réalisé** : un vrai `replay`
reconstruisant les sorties sans relire le store — le store le fait déjà (dette D10, reportée).

### 6.9 Cache et budget

Clé de cache = `hash(id du nœud + @id agent + version + modèle + prompt + entrées déclarées
résolues + item)`. Cache hit = zéro appel, zéro coût, événement `node_skipped`.

Piège découvert en M0 et **conservé tel quel** : la clé ne contient que les entrées **déclarées**
par l'agent. Un agent qui ne déclare aucun `inputs` ne voit donc pas sa clé changer quand une
entrée du run change — il est recalculé comme identique. C'est exact (il ne consomme rien de
l'entrée) et c'est ce que vérifie `test_changed_input_invalidates_downstream`.

Budget vérifié **avant** l'appel : si `provider.estimate() > agent.budget`, on lève
`BudgetError` et le compteur d'appels reste à 0. Le test
`test_budget_is_refused_before_any_call` le garantit. Le dépassement est un refus journalisé en
`node_failed`, pas une facture surprise.

### 6.10 Skills comme packs de connaissance

Un `skills:` référence un pack existant ; son corps est injecté dans le prompt système avec un
entête `# Pack de connaissance : <nom>`. Une référence morte casse au **chargement**
(`test_unknown_skill_reference_is_rejected`). Les skills cessent d'être des orchestrateurs
concurrents pour devenir de la **connaissance attachée à un agent** : c'est ce qui supprime la
dérive à la racine, une seule description du processus vivant dans le graphe.

Réalité M0 : les packs vivent dans `studio/catalog/skills/`. Le branchement sur les 11 skills
v1 de `.opencode/skills/` se fera en passant `extra_skill_roots=[...]` à `load_registry` (M3).

---

## 7. Plan par paliers

Chaque palier est démontrable et se termine par une validation humaine.

| Palier | Contenu | Critères d'acceptation | Coût | État |
|---|---|---|---|---|
| **M0** conversation | noyau complet, 3 agents LLM, 1 gate, 1 workflow, provider mock | YAML-LD validé ; DAG exécuté ; gate bloquante ; reprise après gate ; parallélisme prouvé par test ; **0 appel réseau** | 0 | **réalisé, 29 tests verts** |
| **M1** scénario + photos | artefacts typés + binaires, adaptateur image, vérif vision, gate sur images, invalidation par lignée | 3 shots + 3 images avec refs ; verdict vision ; coût journalisé ; 2ᵉ run = cache hit ; changement de ref → invalidation aval **uniquement** | tests 0 / run réel sur demande | **réalisé, 56 tests verts** |
| **M2** assets + montage | `ToolAgent` (ffmpeg), dispatch des outils, `on_item_failure`, sous-workflows, `retry` de gate | `ffprobe` conforme ; montage reproductible ; échec d'un item n'arrête pas le run | 0 | **réalisé, 80 tests verts** |
| **M3** intégration v1 | **refonte complète** de `pipeline/`, `api/`, `web/` : adaptateurs providers, routes, UI pilotée par le graphe | un projet réel avance d'un palier à l'autre depuis la CLI et depuis le web | selon run | à faire |
| **M4** production | reprise des acquis : cohérence refs, double-ancre, keyframes, budget, rapports | un plan produit et vérifié par vision, sans régression de cohérence | selon run | à faire |

M0 à M2 sont **autonomes** : ils ne dépendent d'aucun fichier de la v1.

Écart assumé sur M0 : le critère « `Ctrl-C` + `resume` » a été validé **au niveau du moteur**
(relancer `run()` avec les mêmes entrées + la réponse de gate reprend aux nœuds restants,
`test_resume_after_gate_only_runs_remaining_nodes`), pas au niveau du signal `SIGINT` du CLI.
Le rattrapage du signal est reporté en M2, quand des runs longs existeront vraiment — **clos
en M2** : `Ctrl-C` sort 130, le journal permet de reprendre, test subprocess à l'appui.

---

## 8. Méthode — TDD, sans exception

1. Écrire le test **rouge** qui décrit la garantie.
2. Écrire le minimum d'implémentation qui le fait passer.
3. Lancer la démo du palier et la faire valider par l'humain.
4. Ne jamais passer au palier suivant sur un artefact non validé.

Tests de M0 (écrits avant toute implémentation) — **29 tests, tous verts** :

| Test | Tests | Garantie |
|---|---|---|
| `test_yamld.py` | 7 | `@context` résolu et expansé, `@id` unique, terme / outil / skill inconnu rejeté, catalogue livré chargeable |
| `test_graph.py` | 6 | niveaux parallèles, cycle détecté, `needs` et `agent` inconnus rejetés, `id` dupliqué rejeté, attributs `foreach` / `parallel` / gate lus |
| `test_executor.py` | 4 | 3 nœuds indépendants vraiment concurrents, `foreach` borné à `parallel`, budget refusé **avec 0 appel**, entrée requise manquante **avec 0 appel** |
| `test_gate.py` | 3 | gate bloquante et question remontée, `continue` exécute l'aval, `stop` ne l'exécute pas |
| `test_journal.py` | 3 | `seq` strictement croissant, fichier append-only (le préfixe ne change pas), état reconstruit == état du run |
| `test_resume.py` | 3 | 2ᵉ run identique = 0 appel, reprise après gate = uniquement les nœuds restants, entrée modifiée re-exécute selon les entrées **déclarées** |
| `test_m0_e2e.py` | 3 | workflow complet mocké, brief valide contre son schéma, blocage sans réponse de gate, `studio validate` sort 0 |

Tests ajoutés à M1 (écrits rouges avant implémentation) — **+24 tests** ; +3 pour le run réel
et le CLI (`test_providers.py`, `--image-provider`) — **56 verts au total** :

| Test | Tests | Garantie |
|---|---|---|
| `test_artifacts_binary.py` | 5 | binaire stocké en fichier brut `<hash>.<ext>`, dédupliqué par contenu, hash sur les octets, référence JSON (jamais de base64) |
| `test_lineage.py` | 5 | `parents`/`producer`/`cost`/`media_type` enregistrés, dépendants transitifs, `invalidate` ne purge **que** l'aval, lignée reprise sur disque |
| `test_images.py` | 7 | ImageAgent écrit un PNG, coût journalisé par nœud et agrégé sur `foreach`, 2ᵉ run = cache, budget refusé à 0 appel, refus clair sans adaptateur, lignée ref → aval |
| `test_m1_e2e.py` | 7 | 3 shots + 3 images + verdict validé schéma, gate sur images, 2ᵉ run identique sans appel, **changement de ref = réexamen de l'aval seul**, coût total du run, CLI bout en bout |

Tests ajoutés à M2 (rouges d'abord) — **+24 tests, 80 verts** :

| Test | Tests | Garantie |
|---|---|---|
| `test_tools.py` | 7 | ToolAgent python (clip mp4 ffprobe-conforme), montage concat + probe, dispatch cli, refus net du kind mcp, kind invalide rejeté au chargement, budget d'outil refusé à 0 appel, sortie d'outil validée schéma |
| `test_item_failure.py` | 3 | `on_item_failure: continue` : item en panne journalisé et retiré, le run aboutit ; sans le flag, l'item tue le nœud |
| `test_subworkflow.py` | 3 | nœud = workflow interne, 2ᵉ run = cache, gate interne bloque avec id préfixé `node/gate` |
| `test_gate_retry.py` | 2 | `retry` = purge amont direct + `nodes_invalidated` + repose ; l'aïeul en cache n'est pas repayé |
| `test_events.py` | 3 | `node_started` émis, `budget_refused` émis avec 0 appel, `as:` nomme l'item du foreach |
| `test_m2_e2e.py` | 4 | catalogue livré : gate montage, mp4 sur disque (3 clips + 1), probe conforme, 2ᵉ run sans production, item maudit n'arrête pas le run |
| `test_cli_ops.py` | 2 | **SIGINT → code 130, relance reprend** ; `studio invalidate <hash>` purgne l'aval |

Tests ajoutés après M2 (run réel & branchements) — **+24 tests, 104 verts** :

| Test | Tests | Garantie |
|---|---|---|
| `test_mcp_dispatch.py` | 4 | serveur stdio réel (mcp 2.x), texte et JSON parsés, cache, serveur inconnu = échec net |
| `test_pollinations.py` | 14 | URL déterministe (seed = prompt+ref), sniff media-type, délais annoncés honorés (402/503, FREE_LIMIT comme SERVICE_UNAVAILABLE), sériage 45 s, backoff, élévation d'erreur nette |
| `test_skills_roots.py` | 4 | `extra_skill_roots` charge des packs externes, `--skills-root` au CLI, et les 11 skills v1 sont des packs valides (skip si dépôt v1 absent) |
| `test_images.py` (+2) | 2 | `_text_prompt` : phrase naturelle du plan, pas de bloc JSON dans le prompt d'image |
| `test_m1_e2e.py` (+1) | 1 | `notes` du verdict : chaîne ou liste, selon le schéma élargi |

---

## 9. Garde-fous

**Hors périmètre, explicitement :**
- Aucune écriture, migration ou lecture destructive dans `projets/`.
- Aucune modification de `main` au-delà de ce document.
- Pas d'API web, pas de base de données, pas de système de plugins avant M2.
- Pas de réécriture des providers avant M3.
- Pas de dépendance à `camel-ai` dans le noyau.

**Risques et parades :**

| Risque | Parade |
|---|---|
| Sur-abstraction | plafond dur : 4 types de nœuds, 1 format, pas de plugins |
| Divergence config ↔ skills ↔ outils | validation au chargement + test anti-dérive (extension de `test_doc_drift.py`) |
| Le noyau grossit sans fin | chaque garantie a son test ; une garantie sans test n'entre pas |
| Le worktree divergerait de la v1 | `main` reste la référence ; la v3 ne copie rien avant M3 |
| Coût involontaire | provider mock par défaut en test, budget par nœud, run réel uniquement sur demande explicite |

---

## 10. Reprise et délégation

### 10.1 Où est quoi

| Élément | Emplacement |
|---|---|
| **Ce document (version de travail)** | `/home/smag/dev/ZtudioZ-v3/REFACTOR_V3.md`, branche `studio-v3` |
| Version historique figée à la décision | `/home/smag/dev/ZtudioZ/REFACTOR_V3.md`, branche `main` |
| Code v3 | `/home/smag/dev/ZtudioZ-v3/studio/` + `tests/` |
| Référence v1 (gelée) | `/home/smag/dev/ZtudioZ`, branche `main` @ `4311be5` |
| Projets de production | `/home/smag/dev/ZtudioZ/projets/` — **jamais** dans le worktree |
| Environnement v3 | `/home/smag/dev/ZtudioZ-v3/.venv` (isolé : PyYAML, jsonschema, pyld, pytest) |

### 10.2 Procédure de création — **déjà exécutée**

```bash
git worktree add --detach /home/smag/dev/ZtudioZ-v3 main
cd /home/smag/dev/ZtudioZ-v3
git checkout --orphan studio-v3
git rm -rf .                        # supprime les fichiers DANS le worktree uniquement
git commit --allow-empty -m "chore: branche orpheline studio v3"
python3 -m venv .venv && .venv/bin/pip install pytest pyyaml jsonschema pyld
```

`git rm -rf .` n'affecte que le worktree : `main` et `projets/` restent intacts, et
`projets*` est gitignoré donc jamais copié.

### 10.3 Reprendre dans une nouvelle session

1. Lire ce document **en entier** (il est dans la branche, à côté du code).
2. Lire `AGENTS.md` (rôle production) et `AGENTS_CODE.md` (règles de code) dans
   `/home/smag/dev/ZtudioZ`.
3. Vérifier l'état :

```bash
cd /home/smag/dev/ZtudioZ-v3
git status --porcelain                 # doit être vide
git log --oneline -5
.venv/bin/python -m pytest tests/      # 104 passed
.venv/bin/python -m studio.cli validate
```

4. Identifier le palier en cours via la section 7 (colonne **État**) et le journal (section 11).
5. Continuer **par un test rouge**, jamais par du code.
6. Ne jamais toucher à `/home/smag/dev/ZtudioZ/projets/`.

### 10.4 Déléguer

Le document est autosuffisant : il contient le constat, les arbitrages, les spécifications,
l'état réel et les critères d'acceptation. Un sous-agent peut prendre un palier entier à
condition de :

- ne pas franchir le portier code sans accord humain ;
- livrer **tests d'abord**, puis implémentation, puis démo ;
- ne rien écrire dans `projets/` ni dans `main` ;
- mettre à jour la section 7 (colonne **État**), la section 12 (dette) et la section 11
  (journal) à la fin de son palier — l'écart entre le plan et le code est le vrai risque.

---

## 11. Journal de décisions (à compléter)

| Date | Palier | Décision / état | Par |
|---|---|---|---|
| 2026-10-31 | M0 | Audit v1 terminé, aucune écriture | assistant |
| 2026-10-31 | — | Arbitrages 1-4 tranchés (branche orpheline, asyncio, M0, YAML-LD validé) | humain |
| 2026-10-31 | — | `pipeline/`, `api/`, `web/` seront entièrement revus en M3 | humain |
| 2026-10-31 | — | Ce document rédigé dans `main` comme point de reprise | assistant |
| 2026-10-31 | M0 | Branche `studio-v3` créée, noyau asyncio implémenté, 29 tests verts, démo M0 fonctionnelle | assistant |
| 2026-10-31 | M0 | Ce document déplacé **dans la branche `studio-v3`** comme version de travail ; la copie de `main` reste figée à la décision | humain (demande) + assistant |
| 2026-10-31 | M0 | Section 5 recalée sur l'arborescence réelle (`catalog/`, pas de `gate.py` ni de `agents/` Python) ; section 12 ouverte avec 15 dettes consignées (D1–D15) | assistant |
| 2026-10-31 | M0 | Écart acté : `Ctrl-C`/SIGINT non rattrapé, la reprise est validée au niveau moteur (D13) | assistant |
| 2026-10-31 | M1 | Dette attaquée dans l'ordre D1→D2→D3 puis adaptateur image, workflow et gate ; 24 tests écrits rouges d'abord, 53 verts à la fin | assistant |
| 2026-10-31 | M1 | **`ImageAgent` retenu comme type provisoire** pour l'adaptateur image (le dispatch `ToolAgent` reste en M2) ; consigné en dette D16 | assistant |
| 2026-10-31 | M1 | Un binaire ne circule **jamais** en base64 dans le JSON : il circule référencé `{"artifact", "media_type", "bytes"}` ; l'empreinte porte les octets bruts | assistant |
| 2026-10-31 | M1 | Coût journalisé : chaque `node_finished` porte `cost`, `RunState.cost` cumule hors unités `#i`, le CLI l'affiche ; `MockImageProvider` produit de vrais PNG (aucun réseau en test) | assistant |
| 2026-10-31 | M1 | Démo validée : `m1-scene` bloque à la gate (exit 3), reprend avec `--answer`, 2ᵉ run identique sans production, changement de ref = réexamen de l'aval seul (`shots` en cache) | assistant |
| 2026-10-31 | M1 | **Run réel Albert (etalab)** sur demande : M0 complet (brief + traitement conformes aux schémas) et M1 (5 shots réels). Découverte : le verdict du `continuity-checker` réel est `ok=false` parce qu'il ne reçoit que des **références JSON**, pas les plans ni les pixels — la vérification vision multimodale reste à brancher (M4) | assistant + albert |
| 2026-10-31 | M1 | Deux garde-fous ajoutés pour le run réel : `--image-provider` distinct du provider LLM (mock image quand le provider texte n'a pas d'endpoint images) et `_resolve_model` (un agent qui déclare `model: mock/echo` cède la place au modèle de l'opérateur ; un modèle réel déclaré est respecté) | assistant |
| 2026-10-31 | M2 | 24 tests écrits rouges d'abord, implémentation, 80 verts : dispatch d'outils (python + cli, mcp refusé net), `ToolAgent`, `on_item_failure`, sous-workflows, `retry` de gate, `node_started`/`budget_refused`, SIGINT (code 130), `studio invalidate`, `as:` | assistant |
| 2026-10-31 | M2 | **Retry de gate = invalidation par lignée** : la réponse `retry` purge l'artefact consommé et tout son aval (`nodes_invalidated` dans le journal, append-only respecté), rejoue l'amont direct, repose la question. Un montage régénéré identique (bitexact) se déduplique tout seul sur le disque | assistant |
| 2026-10-31 | M2 | **Sous-workflow par `Executor` enfant** : même store/journal, sous-run `run#node`, gates exposées préfixées (`render/gate_x`) ; le cache du nœud parent évite de rejouer l'intier | assistant |
| 2026-10-31 | M2 | D16 **reportée en M3** (honnêteté) : l'absorption d'`ImageAgent` par le dispatch exige que les impls du registre voient les adaptateurs providers — branchement M3 (`config.yaml` v1 `providers[]`) | assistant |
| 2026-10-31 | M2 | Démo validée en attente humaine : `m2-montage` bloque à la gate, retry, `continue` → probe `{duration: 1.2, codec: h264}` sur 4 mp4 (3 clips + 1 montage) | assistant |
| 2026-10-31 | M2+ | **Dispatch MCP branché** (D19 clos) : client stdio mcp 2.x, table `servers:`, mapping `arguments:`, résultats JSON parsés — mécanisme prouvé contre serveur jouet ; exposition du toolkit v1 reportée (D22, M3) | assistant |
| 2026-10-31 | M2+ | **Skills v1 joignables** (D15 clos) : CLI `--skills-root`, les 11 packs `.opencode/skills/ztudioz-*` chargés à côté des packs v3 — lecture seule stricte du dépôt v1 | assistant |
| 2026-10-31 | M2+ | **Run réel M1 complet** (D14 clos, texte + images) : Albert pour les plans, Pollinations pour 4 JPEG réels. Découverte API : quota double (par IP `FREE_LIMIT` 45 s, global `SERVICE_UNAVAILABLE`), rendu en 402 comme en 503 — l'adaptateur honore le délai annoncé et se série lui-même | assistant + providers |
| 2026-10-31 | M2+ | Le contrat de sortie a fait son travail : gpt-oss a rendu `notes` en liste, refusé par le schéma, puis accepté — `verdict.schema.json` élargi à `oneOf` chaîne/liste. Le verdict réel reste `ok=false` : le contrôleur ne voit pas les pixels (dette D23, multimodal = M4) | assistant |
| 2026-10-31 | M2+ | Prompt d'image naturel : `_text_prompt` extrait la phrase du plan au lieu d'un bloc JSON — les providers d'images ne veulent pas de JSON dans le prompt | assistant |

---

## 12. Dette technique — ce qui est déclaré mais non réalisé

Liste volontairement exhaustive : c'est le carnet de charges du palier suivant, et ce qui
empêche de croire que le noyau est fini. Clos : D1–D3 (M1), D4–D9, D13, D17, D18 (M2),
D14 (run réel, texte + images), D15 (`--skills-root`), D19 (dispatch mcp).
Restent ouverts : D10, D11, D12, D16, D20, D21, D22, D23.

| # | Écart | Onde le voir | À traiter |
|---|---|---|---|
| ~~D1~~ | ~~Artefacts JSON uniquement~~ | **clos en M1** : `Binary` + `<hash>.<ext>`, empreinte sur les octets bruts | — |
| ~~D2~~ | ~~Pas de lignée~~ | **clos en M1** : `lineage.json`, `Artifact(hash, value, media_type, parents, producer, cost)` | — |
| ~~D3~~ | ~~Pas d'invalidation aval~~ | **clos en M1** : `dependents()` + `invalidate()`, testés ; l'invalidation passive par clé de cache reste le chemin principal | — |
| ~~D4~~ | ~~`on_item_failure` parsé mais ignoré~~ | **clos en M2** : items en panne journalisés (`item_failed`) et retirés du groupé ; le nœud ne tombe que si tout tombe | — |
| ~~D5~~ | ~~Sous-workflows non implémentés~~ | **clos en M2** : `Executor` enfant, sous-journal `run#node`, gates préfixées, cache du nœud parent | — |
| ~~D6~~ | ~~`ToolAgent` inexistant~~ | **clos en M2** : troisième branche de l'exécuteur, un outil par agent, contrat d'entrées/sorties partagé | — |
| ~~D7~~ | ~~Dispatch d'outils non câblé~~ | **clos en M2** pour `python` et `cli` ; `mcp` refusé net → D19 | — |
| ~~D8~~ | ~~`retry` de gate = repose~~ | **clos en M2** : purge l'amont direct et son aval (`nodes_invalidated`), rejoue l'amont, repose la question | — |
| ~~D9~~ | ~~`node_started`/`budget_refused` non émis~~ | **clos en M2** : les deux sont journalisés | — |
| D10 | **`replay`** : `Journal.state()` reconstruit l'état, pas les sorties sans relire le store | `journal.py` | M3 (le store le fait déjà ; reporté sans dette de garantie) |
| D11 | **Budget = coût fixe par appel** (`provider.estimate()`, `cost` de l'entrée d'outil), pas de coût réel token/image | `tools/llm.py`, `tools/images.py`, `tools.yamlld` | M3 |
| D12 | **Un seul agent par nœud** : pas de fan-out multi-agent ni de vote/consensus entre agents | `graph.Node` | M3 |
| ~~D13~~ | ~~`SIGINT` non rattrapé dans le CLI~~ | **clos en M2** : code 130, message de reprise, journal intact — reprise prouvée par test subprocess | — |
| D14 | **Clos en run réel (2026-10-31, deux fournisseurs)** : `OpenAICompatProvider` exercé contre Albert (etalab) sur M0+M1 ; `PollinationsImageProvider` exercé sur M1 complet (4 JPEG réels, 9-21 Ko, seeds deterministes) — quota gratuit respecte les delais annonces par l'API (`Retry after Ns`), seriage 1 image / 45 s. `OpenAICompatImageProvider` ecrit, non exerce (faute d'endpoint chez Albert) | `tools/llm.py`, `tools/images.py` | — |
| ~~D15~~ | ~~Les 11 skills v1 ne sont pas joignables~~ | **clos** : CLI `--skills-root` (repetable) ; les 11 packs `.opencode/skills/ztudioz-*` se chargent en plus des 2 packs v3 (13 au total), lecture seule sur v1 | — |
| D16 | **`ImageAgent` n'est pas encore absorbé par le dispatch** : les impls `python` du registre ne voient pas les adaptateurs injectés à l'exécuteur (`image=`) ; l'unification attend le branchement providers du M3 (`config.yaml` v1 `providers[]`) | `executor._exec_image`, `dispatch.run_tool` | M3 |
| ~~D17~~ | ~~`invalidate()` non exposé~~ | **clos en M2** : `studio invalidate <hash>` | — |
| ~~D18~~ | ~~`foreach` impose le nom `item`~~ | **clos en M2** : `as: shot` injecte l'item sous le nom déclaré (`item` reste disponible) | — |
| ~~D19~~ | ~~Dispatch `kind: mcp` refusé~~ | **clos** : client stdio + table `servers:` + mapping `arguments:` ; testé contre serveur jouet mcp 2.x | — |
| D20 | **Un seul outil par `ToolAgent`** : la convention « exactement un outil déclaré » évite le routage multi-outils ; un agent qui compose deux outils n'existe pas | `executor._exec_tool` | M3 si le besoin apparaît |
| D21 | **Déterminisme des media réels** : les clips synthétiques sont `bitexact` (retry = déduplication parfaite), un mp4/JPEG réel ne l'est pas — la déduplication par contenu n'y suffira pas | `tools/media.py`, `tools/images.py` | M4 |
| D22 | **Le toolkit v1 (~25 outils MCP) n'est pas exposé** : le mécanisme marche, il manque le serveur (camel, context `projets/`) et ses fiches d'outils dans le registre | `pipeline/ztudioz_toolkit.py` v1, `tools.yamlld` | M3 — exposition, pas duplication |
| D23 | **Le verdict vision est un proxy texte** : le noeud recoit des references d'artefacts, pas les pixels ; un reel `continuity-checker` refuse donc a juste titre (`ok=false`) | `catalog/agents/continuity-checker.yamlld`, `_exec_agent` | M4 : entrees multimodales des providers LLM |

Ce qui, au contraire, est **solide et testé** : validation stricte du YAML-LD (terme, `@id`,
outil, skill, schéma), graphe (cycles, dépendances et agents morts), niveaux parallèles
réellement concurrents, borne `parallel`, gate bloquante qui ne se saute pas, refus de budget
avant appel, cache au deuxième run, journal append-only et état reconstruit ; M1 : binaires
adressés par contenu, lignée et invalidation aval, coûts journalisés, gate sur images ; M2 :
dispatch d'outils python/cli, `ToolAgent`, items en panne bornés, sous-workflows, retry de
gate, SIGINT relançable, purge active au CLI, alias `as:`.

---

## 13. Annexes

### 13.1 Correspondance v1 → v3

| v1 | v3 | Devenir |
|---|---|---|
| `pipeline/orchestrator.py` | `studio/kernel/graph.py` + `studio/catalog/workflows/*.yamlld` | remplacé en M3 |
| `pipeline/steps/*` | nœuds de workflow + `ToolAgent` (D6) | remplacé en M3 |
| `pipeline/services/*` | `studio/tools/` (un adaptateur par service) | réutilisé, encapsulé en M3 |
| `pipeline/ztudioz_toolkit.py` (25 outils MCP) | `studio/catalog/tools.yamlld` (`kind: mcp`) | exposé, plus dupliqué |
| `.opencode/skills/ztudioz-*` (11) | champ `skills:` des agents, via `extra_skill_roots` (D15) | **conservé tel quel** |
| `.opencode/agents/*.md` (9 subagents) | agents déclarés dans `studio/catalog/agents/` | remplacé en M3 |
| `constitution/agents.json` | `studio/catalog/workflows/*.yamlld` | absorbé |
| `skills/*.md` (5 orphelins) | — | supprimés en M3 |
| `pipeline/config.yaml` (`active:` + `providers[]`) | `studio/catalog/tools.yamlld` + modèle des agents | inspire le registre, inchangé côté v1 |
| `api/`, `web/` | revus intégralement | M3 |

### 13.2 Glossaire

- **Nœud** : unité exécutable d'un workflow — agent, gate, ou sous-workflow (D5, non implémenté).
- **Artefact** : sortie hashée et versionnée ; typée, lignée (parents, producteur, coût),
  binaires compris depuis M1 ; l'invalidation aval active est dans le store, pas encore au CLI (D17).
- **Gate** : nœud de validation humaine bloquant ; une réponse absente bloque, jamais ne saute.
- **Pack de connaissance** : `SKILL.md` injecté dans le prompt système d'un agent.
- **Palier** : étape de la refonte, démontrable et validable (M0…M4).
- **Dette D1–D15** : écarts consignés en section 12, chacun rattaché à un palier.
