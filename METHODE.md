# MÉTHODE — Production ZtudioZ

> Document de référence décrivant le processus de production complet.
> Établi après 3 rechargements budget, 17 scènes produites, ~200 tentatives API.
>
> ⚠️ **Processus** : la méthode (cycle, validation, cohérence) reste la référence. Les **outils/modèles** ont évolué : la production actuelle passe par `pipeline/steps/` (provider `agnes` par défaut, config `active:`) et le MCP ztudioz, plus par les scripts Pollinations directs décrits ci-dessous (ceux-ci ont servi pour 50eme_rose). Voir `DOC.md` pour l'inventaire à jour. La règle "fallback interdit" est maintenant activable en config via `strict_refs: true`.
>
> **Capitalisation clip → cinéma** : les leçons du clip « Melody's Ghost » (états visuels,
> sous-références, double-ancre identité, format unifié, ré-ancrage par section) sont
> consignées dans la section *« Capitalisation clip → cinéma »* ci-dessous et dans
> `skills/produce-scene.md`, `skills/fix-consistency.md` ainsi que les skills `ztudioz-clip`
> / `ztudioz-keyframes`. Voir aussi `50emeRose_refactor.md` (projets/50eme_rose) pour la
> reprise de la série avec ces mécanismes.

## Philosophie (Wilder)

1. **Une scène à la fois** — Jamais la suivante sans validation.
2. **Intention d'abord** — Chaque scène a un but dramatique défini avant tout.
3. **Cohérence avant tout** — Mêmes vêtements, mêmes lieux, mêmes personnages d'un plan à l'autre.
4. **Pas de fallback sans refs** — Mieux vaut échouer que produire une image incohérente.
5. **Shots préprod comme source de vérité** — Ce sont les images validées. On ne régénère pas.

## Cycle par scène — 8 étapes

```
0. INTENTION  1. EXTRAIRE  2. FICHER  3. SHEETS
4. STORYBOARD  5. BOARD  6. SHOTS  7. VIDÉO + AUDIO
```

### Étape 0 — Intention

| | Description |
|---|-------------|
| **Action** | Lire la scène dans `sources/conversation originale.md`, extraire le but dramatique |
| **Outil** | Lecture manuelle du script |
| **Livrable** | `script/parsed/scene_N.yaml` avec champs : title, location, characters, ambiance, intention, description, character_details |
| **Character_details** | Pour chaque personnage : `clothing` (vêtements exacts dans cette scène) et `attitude` |
| **Règle** | L'intention guide TOUTE la suite. Elle influence les prompts_image, les cadrages, les dialogues |
| **Validation** | 👤 L'intention correspond-elle à la scène ? Les vêtements sont-ils justes ? |

### Étape 1 — Extraire

| | Description |
|---|-------------|
| **Action** | Lister les personnages, lieux, objets présents dans la scène |
| **Outil** | `from pipeline import slugify` — slugify le nom de chaque entité |
| **Livrable** | Liste des slugs nécessaires |
| **Règle** | Un personnage = un slug. Si doublon (`paul-henri` + `paul-henri-delorme`), garder le plus riche |

### Étape 2 — Ficher

| | Description |
|---|-------------|
| **Action** | Créer la fiche YAML canonique de chaque entité |
| **Outil** | Écriture YAML manuelle |
| **Livrable** | `bible/<type>/<slug>/fiche.yaml` |
| **Structure** | name, slug, type, active_version, voice (pour TTS), versions.v1 : {seed, attributs, personnalite/atmosphere, palette, accessoires, prompt_image} |
| **Voix TTS** | Définir `voice:` pour chaque personnage (daniel, emily, rachel, echo, dorothy, arnold, sage, josh, …) |
| **Règle** | Une version canonique unique. Pas de v1/v2/v7 |
| **Validation** | 👤 Les attributs sont-ils concrets ? La palette est-elle cohérente ? |

### Étape 3 — Sheets (images de référence)

| | Description |
|---|-------------|
| **Ordre** | Objets → Personnages → Lieux |
| **Action** | Générer une planche photo multi-vues pour chaque entité |
| **Méthode** | `pollinations_generateImageUrl` + téléchargement avec `?key=` |
| **Commande** | Prompt template character_sheet/location_sheet/object_sheet dans `bible/ambiance/prompt_templates.yaml` |
| **Variables** | `{ambiance}`, `{format}`, `{palette}`, `{name}`, `{attributs}`, `{accessoires}`, `{personnalite}` |
| **Modèle** | `gpt-image-2` (0.03 pollen) |
| **Taille** | 768×768 |
| **Livrable** | `bible/personnages/slug/sheet_v1.png` (etc.) |
| **Blocage** | **Budget API** : 0.03 pollen/image. 39 sheets → ~1.2 pollen |
| **Alternative** | Pas d'alternative viable. `flux` (0.00175) ne supporte pas les refs images |
| **Validation** | 👤 Vérifier avec vision : l'image ressemble à la fiche, la palette est bonne |

### Étape 4 — Storyboard (découpage textuel)

| | Description |
|---|-------------|
| **Action** | Écrire le découpage en plans de la scène |
| **Outil** | Édition manuelle du JSON |
| **Livrable** | `preprod/storyboard/scene_N/storyboard.json` |
| **Champs par plan** | number, type (PLAN LARGE/MOYEN/GROS PLAN/…), camera, description, duration_seconds, prompt_image, dialogue, intention |
| **prompt_image** | Description anglaise détaillée incluant : vêtements exacts des personnages, lieu précis, action, style ("sepia warm tones, photographic style") |
| **Règle** | Les vêtements dans `prompt_image` doivent correspondre à `character_details` du parsed YAML |
| **Blocage** | Si `prompt_image` n'est pas assez spécifique, l'IA invente des vêtements/lieux différents |
| **Solution** | Mentionner les vêtements explicitement : "Paul-Henri (florist in gray suit, same as bank scene)" |
| **Validation** | 👤 Chaque prompt décrit-il la scène correctement ? Les vêtements sont-ils cohérents entre les plans ? |

### Étape 5 — Board (planche storyboard)

| | Description |
|---|-------------|
| **Action** | Générer une planche en grille de tous les plans de la scène |
| **Script** | `scripts/generate_board.py` (ou MCP `storyboard_board`) |
| **Méthode** | Construire la description de chaque plan, uploader les sheets ref, appeler `gen.pollinations.ai/image/` avec refs |
| **Modèle** | `gpt-image-2` |
| **Taille** | 768 × (64 + lignes × 192) |
| **Grille** | 1-4 plans: 2×2, 5-6: 2×3, 7-8: 2×4, 9-12: 3×4 |
| **Livrable** | `preprod/storyboard/scene_N/storyboard_board.png` |
| **Blocage** | Le modèle ne rend pas le texte d'en-tête (projet, scène, version) |
| **Solution** | Ajouter l'en-tête en post-production |
| **Validation** | 👤 Les vignettes sont-elles dans le bon ordre ? Lisibles ? |

### Étape 6 — Shots (images des plans)

| | Description |
|---|-------------|
| **Action** | Générer une image par plan à partir du `prompt_image` |
| **Script** | `scripts/generate_shot.py <project> <scene> <shot>` |
| **Méthode** | Uploader les sheets de la scène → appeler `gen.pollinations.ai/image/` avec refs → sauvegarder |
| **Modèle** | `gpt-image-2` (fallback interdit — si échec, ré-uploader les refs et réessayer) |
| **Taille** | 512×512 |
| **Livrable** | `preprod/storyboard/scene_N/shot_N.png` |
| **Blocage** | **Refs expirent** (tmpfiles 1h par défaut). Les images ref deviennent inaccessibles par l'API OpenAI |
| **Solution** | Upload avec `expire=172800` (48h max). Ré-uploader avant chaque shot si nécessaire |
| **Blocage** | **Modération Azure** : `gptimage` bloque les images avec visages (content safety). |
| **Solution** | Utiliser `gpt-image-2` à la place (modération différente) |
| **Validation** | 👤 **CRITIQUE** : Vérifier : (1) les bons personnages, (2) les bons vêtements, (3) le bon lieu, (4) la bonne action. Si un seul élément est faux, corriger le `prompt_image` et regénérer |

### Étape 7 — Vidéo

| | Description |
|---|-------------|
| **Action** | Générer une vidéo pour chaque plan à partir du shot préprod |
| **Script** | `scripts/generate_videos.py <project> <scene>` |
| **Méthode** | Uploader le shot préprod → appeler `gen.pollinations.ai/video/` avec ref → sauvegarder |
| **Modèle** | `wan-fast` (0.01 pollen/s, 480p, supporte refs images) |
| **Alternative** | `grok-video-pro` (0.05/s, meilleure qualité, 720p) — 5× plus cher |
| **Taille** | Durée définie dans `duration_seconds` du storyboard |
| **Livrable** | `production/video/scene_N/shot_N.mp4` |
| **Blocage** | **seedance-2.0** : bloqué par content moderation |
| **Solution** | Utiliser `wan-fast` par défaut, `grok-video-pro` si meilleure qualité nécessaire |
| **Règle CRITIQUE** | **Toujours utiliser les shots préprod comme frame de départ.** Ne jamais régénérer les frames vidéo |
| **Règle (clip V4/MG)** | **Double-ancre identité** : si l'identité dérive en milieu de plan (i2v simple), passer en **mode keyframes 2 refs** (start + end composé « pose finale ») — cf. section *« Double-ancre identité »* ci-dessous. Ne jamais régénérer la vidéo sans corriger l'ancrage à la source |
| **Validation** | 👤 Vérifier le mouvement, la cohérence des personnages (mid-frame, pas seulement le début) |

### Étape 8 — Audio

| | Description |
|---|-------------|
| **Action** | Générer les dialogues TTS pour chaque plan |
| **Script** | `scripts/generate_audio.py <project> <scene>` |
| **Méthode** | Lire le `dialogue` dans storyboard.json → parser les répliques → trouver la `voice:` dans la fiche → appeler TTS |
| **API** | `GET https://gen.pollinations.ai/audio/{text}?voice={voice}&key={KEY}` |
| **Voix** | Définies dans chaque `fiche.yaml` (champ `voice:`) |
| **Livrable** | `production/audio/scene_N/shot_N_role.mp3` |
| **Validation** | 👤 La voix correspond-elle au personnage ? Le débit est-il correct ? |

## Règles strictes de cohérence

### Vêtements
- Définis dans `character_details.clothing` du parsed YAML
- Identiques pour tous les plans d'une même scène
- Mentionnés explicitement dans chaque `prompt_image`
- Exemple : "Paul-Henri (florist, gray suit, same as bank)" ou "Paul-Henri (florist, beige apron, in his shop)"

### Lieux
- Le fond ne doit pas changer entre les plans d'une même scène
- Si le plan 1 montre la boutique, les plans 2-3 ne peuvent pas montrer le bureau
- Mentionner le lieu explicitement dans chaque `prompt_image`

### Références
- Les shots préprod sont la **source de vérité** pour les vidéos
- On ne régénère pas les frames vidéo
- Si un shot préprod est mauvais, on le regénère avec `generate_shot.py`, on valide, puis on regénère la vidéo
- **La consistance = refs + contrainte forte, pas la description textuelle** (leçon du clip
  V4). Toujours composer AVEC les refs sheets (perso + lieu), même pour un personnage
  unique, en portant dans le prompt : `REFERENCE ONLY for character identity (same face,
  same clothes. No other people.)` (image) / `REFERENCE IMAGE for character identity:
  match exactly (same face, same clothes, same hair).` (vidéo).
- Ordre des blocs du prompt image (≤ ~800 chars) : contraintes critiques → style → staging
  → lumière → mood → perso → ratio.

### États visuels (states) — capitalisé du clip Melody's Ghost

Une fiche peut déclarer **plusieurs états visuels** d'une même entité (pose, moment,
angle, humeur) sous `versions.v1.states` (YAML anchors). Chaque état = `sheet` (image de
référence dédiée) **ou** `prompt_image` (généré dynamiquement en sous-référence) :

```yaml
versions:
  v1:
    _base: &base
      attributs: [...]
      prompt_image: "..."
    states:
      assis:      # sheet dédiée
        sheet: sheet_v1.assis.png
        prompt_image: "..."
      au-volant:  # sous-référence dynamique (pas de sheet déclarée)
        prompt_image: "..."
        changes: {posture: "au volant"}
```

**Règle** : un état AVEC `sheet` → image statique ; un état SANS `sheet` (mais avec
`prompt_image`) → **sous-référence dynamique** générée (compose mono-réf base + ancre
`REFERENCE ONLY`) et mise en cache dans `preprod/clip/v2/_subrefs/`. Le fallback sheet de
base ne s'applique QUE si la sheet de l'état est déclarée mais que le fichier manque.

**Usage cinéma** : au lieu de recadrer un plan autour d'un personnage « assis » alors que
le script veut qu'il « marche », déclarer l'état `marche` (prompt_image) et composer la
référence de pose. La **position suit la timeline**, pas la sheet de base.

### Sous-références (subrefs) — capitalisé du clip Melody's Ghost

Avant d'ingénérer le héro/plan d'un beat, un mécanisme (`_state_sheet`) peut générer en
cache une **variation de l'image de référence** par état : pose corrective du perso, angle
du lieu, déterminés par la timeline. Les sous-références servent de **refs à la composition**
(perso + lieu), pas de refs directes à tout mélanger dans la vidéo.

- `_refs_for_section(sec, beat)` : beat-aware — lit `states.perso`/`states.lieu` du beat ou
  de la section.
- `_state_sheet(slug, state, kind)` : sheet de l'état existante OU génération subref en
  cache.
- Les sous-références sont **partagées par cache** entre plans/sections (une pose = un fichier).

### Double-ancre identité (mode keyframes) — capitalisé du clip Melody's Ghost

**Cause racine du « drift milieu »** : un générateur vidéo **i2v simple (1 ref)** ne tient
l'identité d'un personnage qu'AU DÉBUT — au milieu du plan, il réinterprète le visage/la
tenue. Le héro/start est bon mais le plan « glisse » vers un autre homme.

**Fix validé** : **mode keyframes à 2 refs** (`VideoService.generate(ref_images=[start,
end])`) — l'identité est **bornée des deux côtés** :

1. `start` : image composée (dernière frame du plan précédent + sous-réfs pose/lieu du beat).
2. `end` : image composée = **pose finale du beat**, même logique de refs → le plan doit
   finir sur le bon personnage.
3. Le prompt vidéo porte `REFERENCE IMAGE for character identity: match exactly (same face,
   same clothes, same hair).` (+ description biblique du perso).

Repli automatique en i2v simple si la composition de l'end échoue (502/503 apihub) : dans
ce cas le plan peut dériver — le vérifier par vision et le régénérer.

### Format d'image unifié — capitalisé du clip Melody's Ghost

**Chaînage par last_frame** : `.last.png` (dernière frame du plan précédent), l'image de
départ composée (`.start.png`), et la vidéo de sortie doivent être **EXACTEMENT le même
format**, sinon l'image est rognée/étirée d'un plan à l'autre.

- Extraire `.last.png` **en natif** (même taille que la vidéo, ex. 704×512) — pas de
  recadrage 16:9 à l'extraction.
- Recadrer l'image de départ composée au **ratio de sortie du serveur vidéo** (`_crop_to_video_ratio`)
  avant l'i2v.
- Héro 1344×768 ≠ vidéo 704×512 ⇒ recadrer vers le format vidéo pour le chaînage. Recadrer,
  jamais étirer.

### Ré-ancrage par section (section_reset)

Dans un arc multi-sections (ex. clip : intro + couplet + refrain au même lieu), la dernière
frame de la section précédente « pollue » la suivante : le héro validé de la nouvelle
section n'est jamais utilisé. **Ré-ancrer sur le héro à chaque frontière de section**
(`chaining.section_reset: true`) — le chaînage interne (intra-section) est conservé.

### Fallback interdit
- Si `gpt-image-2` échoue (500, moderation, budget), ne PAS tomber sur `flux`
- `flux` ignore les images de référence → produit des personnages différents
- Solution : ré-uploader les refs avec `expire=172800` et réessayer
- **Distinction** : ce « fallback interdit » vise le passage à un **modèle sans refs**,
  pas un changement de **provider**. Le fallback de *provider* (ex. Pollinations en 402 →
  Agnes) est FAIT et automatique dans `ImageService`/`VideoService` (`_fallback_generate`),
  et un provider backup doit conserver le même `strict_refs`/attachement aux refs.

## Scripts

| Script | Usage | Commande |
|--------|-------|----------|
| `generate_shot.py` | Génère UN plan avec refs filtrées | `python scripts/generate_shot.py <project> <scene> <shot>` |
| `generate_shots.py` | Génère TOUS les plans d'une scène | `python scripts/generate_shots.py <project> <scene>` |
| `generate_videos.py` | Génère les vidéos depuis les shots préprod | `python scripts/generate_videos.py <project> <scene>` |
| `generate_audio.py` | Génère l'audio TTS des dialogues | `python scripts/generate_audio.py <project> <scene>` |
| `pipeline/utils.py` | `upload_to_tmpfiles(path, expire=172800)` | Import Python |

## Budget Pollinations

| Opération | Coût | Pour 17 scènes |
|-----------|------|----------------|
| Image `gpt-image-2` (512×512) | ~0.03 pollen | 58 shots → ~1.7 |
| Image `gpt-image-2` (768×768) | ~0.05 pollen | 39 sheets → ~2.0 |
| Image `flux` | 0.00175 | (fallback sans refs) |
| Vidéo `wan-fast` (5s) | 0.05 pollen | 55 vidéos → ~2.75 |
| Vidéo `grok-video-pro` (5s) | 0.25 pollen | (alternative qualité) |
| Audio TTS (10s) | ~0.001 pollen | ~40 fichiers → ~0.04 |
| **Total estimé** | | **~6-7 pollen** |

**Attention** : le budget réel a été 3× plus élevé à cause des essais/erreurs, réessais après timeouts, et tests de modèles.

## Blocages récurrents

| Blocage | Cause | Solution |
|---------|-------|----------|
| **Budget épuisé** | Requêtes API coûteuses | Recharger sur enter.pollinations.ai. Utiliser `wan-fast` (5× moins cher que grok) |
| **Modération Azure** | `gptimage` sur Azure bloque les visages | Utiliser `gpt-image-2` (modération différente) |
| **Refs expirées** | tmpfiles 1h par défaut | Upload avec `expire=172800` |
| **Refs inaccessibles** | OpenAI ne peut pas télécharger depuis tmpfiles | Ré-uploader avec expire max. Si persiste, générer sans refs |
| **Incohérence vêtements** | `prompt_image` pas assez spécifique | Ajouter les vêtements exacts dans le prompt |
| **Seedance bloqué** | Content moderation trop stricte | Utiliser `grok-video-pro` ou `wan-fast` |
| **Timeout MCP** | Temps de génération > timeout client | Augmenter le timeout MCP ou utiliser les scripts directement |

## Validation checklist

Avant de passer à l'étape suivante, cocher :

- [ ] L'intention de la scène est claire
- [ ] Les fiches ont des attributs concrets
- [ ] Les sheets ressemblent aux fiches (vérifié par vision)
- [ ] Les prompts_image du storyboard mentionnent les vêtements exacts
- [ ] Les shots préprod montrent les bons personnages aux bons endroits
- [ ] Les vidéos utilisent les shots préprod comme frames
- [ ] Les voix TTS correspondent aux personnages
- [ ] Le budget est suffisant pour la suite

## Projets

### 50eme_rose (terminé — reprise possible via refactor)

> La production historique (ancien pipeline Pollinations direct) présente des défauts
> d'incohérence. Voir **`projets/50eme_rose/50emeRose_refactor.md`** pour le brief de
> reprise/refonte avec les mécanismes capitalisés du clip (states, subrefs, double-ancre,
> format unifié, refs systématiques).

| Métrique | Valeur |
|----------|--------|
| Scènes | 17 |
| Fiches | 39 (8 objets + 23 persos + 8 lieux) |
| Sheets | 39 |
| Shots | 58 |
| Board | 17 |
| Vidéos | 55 |
| Audio | ~40 fichiers |
| Budget | ~15 pollen (3 rechargements) |
