---
description: État du dernier run studio et palier suivant
---

Tu es l'opérateur du studio v3. Projet : **$1**.

Si `$1` est vide, demande le dossier du projet et **arrête-toi** : n'exécute pas la commande
avec une valeur vide.

Exécute dans `/home/smag/dev/ZtudioZ-v3` :

```bash
.venv/bin/python -m studio.cli --project "$1" status
```

Montre le run, son statut, la gate en attente et le palier suivant. Ne réponds à aucune gate.
Termine par un résumé **très court** :
- **Situation** : une phrase.
- **Problème** : ce qui bloque (gate, verdict, erreur), ou « rien ».
- **Action** : la commande exacte à lancer ensuite.
