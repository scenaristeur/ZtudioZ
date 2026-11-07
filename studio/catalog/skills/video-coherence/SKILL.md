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
- Quand un vehicule s'arrete, ses roues s'arretent aussi : pas de roue qui
  tourne sur place, pas de glisse du vehicule.
- La vitesse apparente reste coherente avec le decor et le mouvement de camera.

## Personnages

- Un personnage qui s'eloigne marche vers l'avant, dos a la camera ; il ne
  recule pas.
- Les pas, le buste et les bras restent dans le sens du deplacement.
- Pas de glissement, de marche arriere involontaire, ni de changement de
  direction sans raison visible.

## Destination et espace

- Respecter la destination du plan : le personnage ou le vehicule va vers
  l'objet ou le lieu indique (par exemple disparaitre derriere la grosse
  machine), pas vers un autre decor.
- La cible, la direction et le point d'arrivee restent ceux du plan, meme si un
  autre decor est plus visible.
- Un personnage qui quitte le cadre le fait dans la direction demandee, sans
  changer de trajectoire au dernier moment.

## Continuite

- Les objets conservent taille, position et orientation relatives.
- La lumiere, la palette et le decor restent ceux de l'image de reference.
- Aucun texte, sous-titre, coupe ou raccord.

Si le plan demande une action physiquement incoherente, privilegier la
coherence et le signaler dans la description, plutot que produire un mouvement
impossible.
