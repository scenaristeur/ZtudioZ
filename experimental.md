pour chaque plan déterminé dans le storyboard minuté de chaque scène, déterminer les images intermédiaires :

- start : la première image, générée avec gpt-image2 qui accepte plusieurs références
- à chaque action, évenement du plan ou toutes les 5 secondes, déterminer le prompt de l'image intermédiaire correspondant.
- si l'image nécessite plusieurs références -> gpt-image2, si elle est une évolution de l'image précédente (déplacement, action, evenement...) sans ajout d'une nouvelle image de référence -> agnès avec comme image source l'image intermédiare précédente.
- enchainer ensuite ces images de façon pertinentes pour créer des vidéos en utilisant agnes-keyframes .
