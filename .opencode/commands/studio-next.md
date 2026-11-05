---
description: Palier suivant du projet et entrées manquantes
---

Tu es l'opérateur du studio v3. Projet : **$1**.

Si `$1` est vide, demande le dossier du projet et **arrête-toi** : n'exécute pas la commande
avec une valeur vide.

Exécute dans `/home/smag/dev/ZtudioZ-v3` :

```bash
.venv/bin/python -m studio.cli --project "$1" next
```

Montre le palier suivant, ce qui est hérité et ce qui manque. Si une entrée manque, demande-la
à l'humain. Termine par **Action** : la commande exacte à lancer.
