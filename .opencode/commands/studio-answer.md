---
description: Répond à une gate d'un run bloqué (choix humain explicite)
---

Tu es l'opérateur du studio v3. L'humain a explicitement choisi **$4** pour la gate **$3**
du run **$2** du projet **$1**.

Exécute dans `/home/smag/dev/ZtudioZ-v3` :

```bash
.venv/bin/python -m studio.cli --project "$1" answer "$2" "$3" "$4" --feedback "$5"
```

Si `$5` est vide, omets `--feedback`. N'invente jamais la réponse ni la correction : recopie
exactement ce que l'humain a donné. Montre le nouveau statut et la gate suivante.
