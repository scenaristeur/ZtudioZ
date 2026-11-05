---
description: Lance un workflow studio et s'arrête à la gate
---

Tu es l'opérateur du studio v3. Projet : **$1**, workflow : **$2**, entrées : `$3`.

Exécute dans `/home/smag/dev/ZtudioZ-v3` :

```bash
.venv/bin/python -m studio.cli --project "$1" run "$2" --input '$3'
```

Si `$3` est vide, exécute sans `--input`. Montre le `run_id`, le statut, la gate en attente et
les chemins d'artefacts. **Ne réponds pas à la gate** : attends le choix de l'humain.

Avant un run réel, vérifie la config du projet et annonce le coût/temps. Si `$1` ou `$2` est
vide, demande-les.
