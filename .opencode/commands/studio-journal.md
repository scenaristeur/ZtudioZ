---
description: Affiche le journal d'un projet ou d'un run
---

Tu es l'opérateur du studio v3. Projet : **$1**, run : **$2**.

Si `$1` est vide, demande le dossier du projet et **arrête-toi**.

Exécute dans `/home/smag/dev/ZtudioZ-v3` :

```bash
.venv/bin/python -m studio.cli --project "$1" journal --run "$2"
```

Si `$2` est vide, exécute sans `--run`. Résume les événements importants (run, gates, échecs,
coûts), sans noyer l'humain sous le JSON brut.
