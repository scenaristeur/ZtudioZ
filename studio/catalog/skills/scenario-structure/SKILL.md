# Pack de connaissance — structure de scenario

Un brief de production tient en six champs, et rien de plus.

## Champs

- `logline` : une phrase, un personnage, un desir, un obstacle.
- `synopsis` : trois a cinq phrases, du debut a la fin.
- `characters` : uniquement ceux qui agissent.
- `locations` : uniquement celles ou l'on tourne.
- `open_questions` : ce que l'humain doit trancher avant la suite.
- `constraints` : les limites que l'auteur a posees, sous forme structurée.

## Constraints

`constraints` n'est pas un depot d'impressions : c'est le champ qui rend la
decision de l'auteur **executoire** aux paliers suivants. Un maximum de plans
ecrit ici borne reellement le decoupage ; perdu ici, il ne revient jamais.

- `max_shots` : entier >= 1 quand l'auteur fixe un nombre de plans (« deux plans
  maximum », « un seul plan sequence »). Ne pas inventer ce nombre.
- `freeform` : les contraintes qui ne tiennent pas dans un champ typé, chacune
  en une phrase imperative et verifiable : « pas de dialogue, seulement des
  sons », « fin ambigue », « meme lumière du debut a la fin ».

Si l'auteur n'a pose aucune contrainte, `constraints` peut etre absent. Ne le
remplis pas avec ce que tu devines : une contrainte imaginee vaut moins que
rien, elle contraindra la production pour de faux.

## Interdits

- Ajouter un personnage absent de la discussion.
- Combler un trou par une invention : c'est une question ouverte.
- Perdre une contrainte chiffrée en la noyant dans le synopsis.
