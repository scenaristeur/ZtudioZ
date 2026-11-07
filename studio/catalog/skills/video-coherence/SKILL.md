# Pack de connaissance — coherence physique video

Une video de plan doit rester physiquement lisible. Ces regles sont non
negociables, meme si le plan ou la correction les contredit.

## Trajectoire et collisions

- Chaque vehicule, personnage ou objet garde une trajectoire continue.
- Pas de collision, pas d'intersection impossible, pas de passage a travers un
  autre objet.
- Un vehicule qui s'eloigne ne croise pas la trajectoire des autres : il part
  dans une direction stable.
- La camera garde un mouvement continu, sans saut ni teleportation.

## Roues et deplacement

- Les roues tournent dans le sens du deplacement.
- Une machine qui a des roues doit avancer ou reculer reellement, pas seulement
  faire tourner ses roues sur place.
- La vitesse apparente reste coherente avec le decor et le mouvement de camera.

## Personnages

- Un personnage qui s'eloigne marche vers l'avant, dos a la camera ; il ne
  recule pas.
- Les pas, le buste et les bras restent dans le sens du deplacement.
- Pas de glissement, de marche arriere involontaire, ni de changement de
  direction sans raison visible.

## Continuite

- Les objets conservent taille, position et orientation relatives.
- La lumiere, la palette et le decor restent ceux de l'image de reference.
- Aucun texte, sous-titre, coupe ou raccord.

Si le plan demande une action physiquement incoherente, privilegier la
coherence et le signaler dans la description, plutot que produire un mouvement
impossible.
