# AGENTS_CODE — règles de code ZtudioZ

Fichier **chargé à la demande** depuis `AGENTS.md`. Ne pas le lire pour produire un film :
il ne sert que si on modifie le code du dépôt.

Le contexte principal (`AGENTS.md`) est deliberately orienté **production**. Ce fichier est
son complément technique.

---

## 1. Portier code — la règle qui prime

`AGENTS.md` est un rôle de **production cinématographique**. Le code n'est pas la mission
par défaut. Avant **toute** modification de code :

1. **Diagnostiquer** — lire le contexte, identifier la cause racine, ne pas deviner.
2. **Proposer** — périmètre, cause probable, risque, coût (crédits/temps si génération).
3. **Demander** — présenter la proposition et **attendre l'accord explicite de l'utilisateur**.

**Aucune écriture de code sans ce feu vert**, même pour un bug évident, même « trivial ».
Ce n'est pas de la lenteur : c'est le respect du rôle. Un changement de code peut casser une
production en cours, et les projets ne sont pas sous git.

Le signal explicite est l'inverse de l'accord : « continue », « go », « fais-le ».

## 2. Règles absolues

1. **Jamais de clé API en dur** — ni dans le code, ni dans la config. Tout passe par `.env`
   (`.gitignore`d) + `config.yaml` avec `${VARIABLE}`. `opencode.jsonc` référence
   `{env:VAR}`. Ne jamais écrire un secret réel dans un fichier versionné.
2. **Config-driven** — tout paramètre modifiable (modèles, tailles, seeds, URLs) va dans
   `pipeline/config.yaml`, pas en dur. Si un paramètre manque, l'ajouter à la config.
3. **Service activé ≠ code** — `config.yaml` a `active: <provider>` + `providers[]`. Changer
   `active:` doit suffire ; ne pas coder une logique provider sans branche sur `self.config`.
4. **Respecter le cache MD5** — les services renvoient le fichier en cache si le hash existe.
   Ne pas forcer la régénération sans raison : `--force`, un filename différent, ou invalider
   le hash.
5. **Ne jamais écraser** une vidéo existante > 5 KB.
6. **Projets intouchables** — `projets/` est ignoré par git, aucun filet. Jamais `rm`, jamais
   de glob large, jamais `git clean`. En cas de doute, demander.

## 3. Conventions

- Docstrings **en français**, style existant (triples guillemets, `Args:` / `Returns:`).
- Imports en début de fichier ; imports locaux dans les méthodes quand c'est déjà le pattern
  du fichier.
- Suivre le style du fichier environnant (`Optional` vs `| None` — le fichier donne le ton).
- Les services acceptent `config: dict | None = None` en constructeur (override dev/test).
- Pas de commentaires inutiles : les docstrings suffisent. **Ne pas ajouter de commentaires**
  dans le code.
- Ne pas introduire de chemin qui ignore le flag `dev`.

## 4. Vérifications

Pas de linter ni de typecheck dédié. **`py_compile` sur les fichiers modifiés** est la
vérification attendue.

```bash
.venv/bin/python -m py_compile <fichier.py>                       # syntaxe, après CHAQUE édition
.venv/bin/python test_system.py                                  # smoke test système
.venv/bin/python -c "import json5; json5.load(open('opencode.jsonc'))"   # config opencode
```

Après un changement de service, tester par un **petit appel isolé** en mode dev :

```python
ImageService().generate("...", dev=True)
VideoService().generate("...", dev=True)
```

C'est le test de référence : il isole le service du pipeline.

## 5. Bugs connus — documentés, NON corrigés

Ce chantier était documentaire à l'origine. Les bugs **corrigés** sont marqués ; les autres
restent **consignés, pas corrigés**. Ne pas les « réparer » au passage : chacun demande un
accord.

| # | Symptôme | Emplacement | État |
|---|---|---|---|
| 1 | `produce` jamais reconnu fait → la vidéo se régénère à chaque run | `orchestrator.py` écrivait `"production"` au lieu de `"produce"` | **corrigé** — le checkpoint est migré à la lecture |
| 2 | Le script keyframes ignore `services.*.active` (providers hardcodés) | `.agents/skills/ztudioz-keyframes/scripts/keyframes_flow.py:88,159` | consigné |
| 3 | `bible/ambiance/` jamais créé par `init`, alors que `prompt_builder` en dépend | `orchestrator.py:ProjectInit.STRUCTURE` ; lecture dans `pipeline/services/prompt_builder.py:22,33` | **corrigé** — `ambiance/` et `scene/` créés d'emblée ; `ProjectInit.verify_structure()` diagnostique |
| 4 | `pipeline/steps/scenario.py` orphelin (pas dans `AVAILABLE_STEPS`) | `pipeline/steps/scenario.py` | consigné |
| 5 | La doc renvoie vers un outil `describe_sheet` inexistant | `.agents/skills/ztudioz-keyframes/SKILL.md:43` | consigné |
| 6 | Résolution `1348x768` mentionnée à tort dans la doc | docs racines | consigné |
| 7 | Un modèle vidéo renommé sort de la table de coûts → coût vidéo aveugle | `pipeline/consumption.py:_estimates` | consigné — voir §7 |

## 6. Emplacement du code

Voir le skill **`ztudioz-architecture`** pour l'arborescence et les responsabilités de
chaque module. Ne pas dupliquer cette carte ici.

Points qui reviennent souvent :

| Question | Fichier |
|---|---|
| Liste et ordre des étapes ? | `pipeline/orchestrator.py:84` (`AVAILABLE_STEPS`) |
| Quel provider est actif ? | `pipeline/config.yaml` (`services.*.active`) |
| Format d'une fiche bible ? | `pipeline/models/bible.py` |
| Comportement d'un service ? | `pipeline/services/<nom>.py` |
| Coût d'un run ? | `pipeline/consumption.py:get_tracker()` |
| Progression affichée ? | `pipeline/reporter.py` |

## 7. Pièges de code

- **`.env` racine obligatoire** — `pipeline/config.py` fait `load_dotenv(ROOT/'.env')`, donc
  le MCP fonctionne quel que soit le cwd. Ne pas introduire un second chargement.
- **Pas d'étape `board`** — la planche est une sous-pas de `visualize`.
- **Pas d'étape `montage`** — `assemble_scene` / `assemble_film` sont des fonctions.
- **`dev: true` par défaut** à plusieurs endroits de `config.yaml`. Vérifier et passer à
  `false` avant une production réelle.
- **Tailles et durées Agnes** vivent dans `config.yaml` (`valid_sizes`,
  `valid_durations`). Si la table change, mettre à jour **les deux côtés** (serveur local +
  config).
- **`reporter.py`** convertit les erreurs en avertissements : un exit 0 ne garantit pas que
  toutes les images ont été produites.
- **Deux sessions peuvent travailler sur le dépôt.** Avant d'écrire un fichier, vérifier
  qu'un autre agent ne l'a pas modifié (`git status`, `git diff`).

## 8. Fichiers versionnés et non versionnés

- Versionnés : `pipeline/`, `api/`, `web/`, `.opencode/`, `constitution/`, docs racine.
- **Non versionnés** : `.env`, `opencode.jsonc` (`opencode*` est ignoré), `projets/`,
  `outputs/`. Sauvegarder avant édition, et vérifier avec la commande json5 ci-dessus.
- `projets/**` : aucune sécurité git. Ne jamais y toucher sans accord.
