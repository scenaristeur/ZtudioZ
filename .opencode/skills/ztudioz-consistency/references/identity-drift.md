# Drift d'identité et format — arbitrage i2v vs keyframes

Détail de référence pour `ztudioz-consistency`. Héritage du clip « Melody's Ghost ».

## 1. Le symptôme : « le milieu du plan glisse »

Cause racine : un **i2v simple (1 seule ref)** ne tient l'identité d'un personnage qu'**au
début** du plan. Le générateur réinterprète visage et tenue au milieu : le start est bon, puis
le plan « glisse » vers un autre homme. À la fin, la dérive est Revenue.

C'est le défaut le plus fréquent du système, et il est **invisible à l'œil** sur une
frame isolée. Il faut regarder la **mid-frame**.

## 2. Le correctif validé : la double-ancre (keyframes 2 refs)

`VideoService.generate(ref_images=[start, end])` — mode `keyframes`, l'identité est **bornée
des deux côtés** :

1. `start` : image composée = dernière frame du plan précédent + sous-réf pose/lieu du beat.
2. `end` : image composée = **pose finale du beat**, même logique de refs. Le plan doit finir
   sur le bon personnage.
3. Le prompt vidéo porte `REFERENCE IMAGE for character identity: match exactly (same face,
   same clothes, same hair).` + la description biblique du personnage.

Si la composition de l'end échoue (502/503 apihub), repli automatique en i2v simple — mais
alors le plan **peut** dériver : le vérifier par vision (mid-frame) et le régénérer.

## 3. Format d'image unifié pour le chaînage

Le chaînage par `last_frame` impose que l'image de départ composée (`.start.png`), la
`.last.png` (dernière frame du plan précédent) et la vidéo de sortie ait **exactement le
même format**. Sinon l'image est rognée ou étirée d'un plan à l'autre.

- Extraire la `.last.png` **en natif** (même taille que la vidéo, ex. 704×512) — pas de
  recadrage 16:9 à l'extraction.
- Recadrer l'image de départ composée au **ratio de sortie du serveur vidéo**
  (`_crop_to_video_ratio`) **avant** l'i2v.
- Héro 1344×768 ≠ vidéo 704×512 ⇒ recadrer vers le format vidéo pour le chaînage.

**Recadrer, jamais étirer.**

## 4. Ré-ancrage par frontière de section

Dans un arc multi-sections (intro + couplet + refrain au même lieu), la dernière frame de la
section précédente « pollue » la suivante : le héro validé de la nouvelle section n'est
jamais utilisé.

- `chaining.section_reset: true` → ré-ancrer sur le **héro** à chaque frontière de section.
- Le chaînage **intra-section** est conservé.

## 5. Arbitrage i2v vs keyframes

| Symptôme | Cause | Correctif |
|---|---|---|
| Identité glisse en milieu de plan | i2v 1 ref | keyframes 2 refs (double-ancre) |
| Identité correcte au début, fausse à la fin | end-frame absente ou non composée | vérifier la composition de `end` ; 502/503 apihub ? |
| Cadrage dérive d'un plan à l'autre | ratio image ≠ ratio vidéo | `_crop_to_video_ratio`, recadrage natif |
| Format étiré / rogné | last_frame non extraite en natif | ré-extraire la `.last.png` en natif |
| Héros de section jamais utilisé | chaînage non ré-ancré | `section_reset: true` |
| Personnage différent à chaque plan | refs non passées au générateur | composer avec les sheets (refs + contrainte forte) |
| Personnage différent après repli modèle | `flux`/`sana` ignorent les refs | `strict_refs` / `no_model_fallback` : ré-uploader les refs et réessayer |

## 6. Vérification

Toujours vérifier par **mid-frame** en plus du début et de la fin. L'ancrage double garantit
les extrémités ; c'est l'observation du milieu qui prouve que ça n'a pas glissé entre les
deux. Outil : `ztudioz_analyze_images` sur un montage des trois frames.
