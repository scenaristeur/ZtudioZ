# SPECS — Spécifications techniques détaillées

> ⚠️ **Archive partielle** : cet inventaire décrit en partie l'ancien état du système (providers Pollinations par défaut). L'inventaire vivant est `DOC.md` ; les providers actifs actuels sont `agnes` (image/vidéo) + `albert` (LLM/vision). Les exemples ci-dessous restent valables pour la structure, mais `image.active`/`video.model` sont à lire dans `pipeline/config.yaml`.

## 1. Config YAML (`pipeline/config.yaml`)

Tous les paramètres du système, modifiables sans toucher au code.

```yaml
project:
  name: "50emeRose"
  root: "../projets/50emeRose"

services:
  llm:
    active: albert
    providers:
      albert:
        type: tool_calling_model
        model: openai/gpt-oss-120b
        api_key: ${OPENAI_API_KEY}
        base_url: ${OPENAI_BASE_URL}
        temperature: 0.2
        max_tokens: 4000
      pollinations:
        type: chat_completion
        model: gemini-fast
  vision:
    active: albert
    providers:
      albert:
        model: mistralai/Mistral-Small-3.2-24B-Instruct-2506
        temperature: 0.1
  image:
    active: pollinations
    providers:
      pollinations:
        type: api
        model: flux
        width: 256
        height: 256
        seed: null
        quality: medium
      focus:
        type: gradio
        url: ${FOOCUS_URL}
  video:
    active: pollinations
    providers:
      pollinations:
        type: api
        model: seedance
        width: 256
        height: 256
        duration: 5
        seed: null
  audio:
    active: pollinations
    providers:
      pollinations:
        type: tts
        voice: onyx
        model: default

bible:
  active: yaml
  providers:
    yaml:
      path: "bible/"
    grist:
      server: ${GRIST_SERVER}
      doc_id: ${GRIST_DOC_ID}
      api_key: ${GRIST_API_KEY}

production:
  default_seed: 42
  consistency_check: true
  human_validation: true
  retry_on_failure: true
  max_retries: 3
  parallel_shots: false
```

## 2. Bible YAML — Format versionné

### Personnage (`bible/personnages/<slug>/fiche.yaml`)

```yaml
name: "Paul-Henri Delorme"
slug: paul-henri
type: personnage
created: 2026-07-09
active_version: v2

versions:
  v1:
    seed: 42
    mix_alias: "Bacri × Binoche"
    attributs:
      - "Silhouette masculine légèrement voûtée"
      - "Manteau en velours côtelé usé"
      - "Chemise sans cravate, col ouvert"
      - "Cheveux poivre et sel en bataille"
      - "Mains calleuses de fleuriste"
      - "Regard baissé ou lointain"
      - "Posture en retrait, comme effacé"
      - "Ombre portée longue et mince"
      - "Démarche lente et mesurée"
      - "Aucun sourire, douceur triste"
    personnalite: "Doux, mélancolique, déphasé, observateur"
    palette: "tons sépia, marron, vert doux, gris chaud"
    accessoires: ["sécateur", "bouquet de roses blanches"]
    prompt_image: "Portrait de Paul-Henri Delorme, fleuriste mélancolique, 42 ans, manteau velours côtelé usé, chemise sans cravate, cheveux poivre et sel, regard doux et lointain, lumière douce, style cinématographique"
    tenues:
      jour: "Manteau velours côtelé marron, chemise bleu pâle"
      soir: "Veste en laine grise, pull col roulé noir"
  v2:
    seed: 99
    mix_alias: "Arditi × Deneuve"
    attributs:
      - "Silhouette masculine élancée"
      - "Manteau en laine grise, légèrement élimé"
      - "Pull col roulé noir"
      - "Cheveux gris, coiffés en arrière"
      - "Mains fines et calleuses"
      - "Regard perçant mais fatigué"
      - "Posture droite mais cassée"
      - "Ombre portée sur le mur"
      - "Démarche lente et digne"
      - "Sourire rare et sincère"
    personnalite: "Doux, mélancolique, déphasé, observateur"
    palette: "gris, bleu nuit, blanc cassé"
    accessoires: ["sécateur", "bouquet de roses blanches"]
    prompt_image: "Portrait de Paul-Henri Delorme, fleuriste mélancolique, 42 ans, manteau gris élimé, pull col roulé noir, cheveux gris coiffés en arrière, regard fatigué mais perçant, lumière tamisée, style cinématographique"
```

### Lieu (`bible/lieux/<slug>/fiche.yaml`)

```yaml
name: "Boutique de Fleurs"
slug: boutique
type: lieu
active_version: v1
versions:
  v1:
    seed: 7
    attributs:
      - "Pièce étroite et longue (8m x 6m)"
      - "Murs vieux rose délavé"
      - "Comptoir en bois foncé usé"
      - "Étagères presque vides"
      - "Vitrine poussiéreuse donnant sur la rue"
      - "Carrelage ancien au sol"
      - "Seaux en zinc alignés devant"
      - "Roses partout mais souvent fanées"
      - "Lumière grise ou pénombre"
      - "Coucou suisse au mur du fond"
    atmosphere: "Surannée, mélancolique, hors du temps"
    palette: "vieux rose, bois foncé, gris, zinc"
    eclairage: "Lumière naturelle tamisée par la vitrine"
    prompt_image: "Intérieur de boutique de fleurs parisienne, modeste et vide, comptoir en bois, murs vieux rose, coucou suisse au mur, lumière douce tamisée, atmosphère mélancolique, style cinématographique"
```

### Objet (`bible/objets/<slug>/fiche.yaml`)

Même structure : `name`, `slug`, `type: objet`, `versions[]` avec attributs, seed, prompt_image.

## 3. ToolCallingModel

Adapter qui étend `OpenAICompatibleModel` de Camel AI.
Intercepte les réponses contenant `[TOOL_CALLS]` texte → les convertit en `tool_calls` natifs Camel.

**Comportement :**
- Parse `[TOOL_CALLS]func_name{...}` dans la réponse
- Gère les accolades imbriquées et les chaînes JSON mal formées
- Insère des messages assistant factices entre tool→user (contournement API Albert)
- Mode verbose pour debug

## 4. Services — Interface abstraite

```python
class LLMService(ABC):
    def chat(self, messages, tools=None, model=None) -> Message
    def vision(self, images, prompt, model=None) -> str

class ImageService(ABC):
    def generate(self, prompt, seed=None, **kwargs) -> str  # returns path

class VideoService(ABC):
    def generate(self, prompt, ref_image=None, **kwargs) -> str  # returns path

class AudioService(ABC):
    def say(self, text, voice=None) -> str  # returns path
    def respond(self, prompt, voice=None) -> str

class BibleDB(ABC):
    def get_entity(self, slug, version=None) -> Entity
    def get_active_version(self, slug) -> str
    def switch_version(self, slug, version) -> None
    def list_entities(self, type=None) -> list[Entity]
```

## 5. Pipeline — Étapes

Chaque étape est une classe avec `run(context) → context` :

| Étape | Entrée | Sortie | Service |
|-------|--------|--------|---------|
| `ParseScene` | script.md | scenes list | LLM |
| `GenerateFiche` | scene | fiches.yaml | LLM |
| `GenerateSheets` | fiche | images | Image |
| `GenerateStoryboard` | scene + fiches | storyboard frames | Image |
| `ProduceVideo` | storyboard frame | video shot | Video |
| `GenerateAudio` | dialogue text | audio file | Audio |
| `ConsistencyCheck` | refs + shots | rapport | Vision |
| `AssembleScene` | video + audio | scene montage | ffmpeg |
| `AssembleFilm` | scenes | film final | ffmpeg |

## 6. Structure de dossiers

```
projets/<slug>/
  script/
    scenario.md              # Script source
    parsed/                  # Sortie parsing (scenes/*.yaml)
  bible/
    personnages/<slug>/      # fiche.yaml + sheets/*.png
    lieux/<slug>/            # fiche.yaml + sheets/*.png
    objets/<slug>/           # fiche.yaml + sheets/*.png
  preprod/
    storyboard/<scene>/      # storyboard frames
    moodboards/              # palettes et ambiances
  production/
    video/<scene>/           # shots vidéo générés
    audio/<scene>/           # VO, dialogues, bruitages
  postprod/
    montage/                 # scènes assemblées
    output/                  # films finaux
```

## 7. Modèles Pollinations — Tests & Comparaison

### Image

| Modèle | Ref images | Sous-titres | Qualité | Usage |
|--------|-----------|-------------|---------|-------|
| `gptimage` | Oui | Non | Basique | Fallback |
| `gptimage-large` | Oui | Non | Bonne | Génération sans description |
| `gpt-image-2` | Oui (plusieurs via `\|`) | Oui (génère légendes) | Excellente | **Par défaut** (sketches, sheets) |

- `gptimage-large` gardé en option dans la config pour les cas où on veut une image propre sans sous-titre généré automatiquement
- `gpt-image-2` recommandé car il respecte bien les références ET produit des descriptions utiles

### Vidéo

| Modèle | Ref images | Durée max | Particularité |
|--------|-----------|-----------|---------------|
| `ltx-2` | Non | 5s | Text→video seulement |
| `seedance-2.0` | Oui (max 2 : start + end frame) | 10s | Image→video avec start/end |
| `grok-video-pro` | Oui (multiples) | 5s | **Par défaut** — meilleur support multi-ref |
| `veo` | ? | 4-8s | Audio natif possible |

- `grok-video-pro` semble le meilleur compromis pour l'instant
- `seedance-2.0` pourrait être utile pour les transitions plan→plan (image de début + image de fin)

### TODO (reporté)
- [ ] Évaluer les coûts pollen de chaque modèle (pricing API)
- [ ] Tester systématiquement chaque modèle sur les mêmes prompts
- [ ] Enregistrer les points forts/faibles dans un fichier de benchmark
- [ ] Déterminer le meilleur modèle par type de plan (large/moyen/gros plan)
- [ ] Vérifier si `gptimage-large` sans sous-titre est préférable pour les sheets personnages
- [ ] Tester `veo` avec audio natif pour la production finale
- [ ] Tester `seedance-2.0` avec exactement 2 images (start + end frame)

## 8. Architecture Agents CAMEL-AI

### Principe

Chaque spécialité métier (character design, storyboard, production vidéo, audio)
devient un **agent CAMEL-AI autonome**, exposé via MCP.

Chaque agent :
- Hérite de `BaseToolkit` (Camel)
- Est décoré avec `@MCPServer()` (Camel)
- Contient ses outils spécialisés (`FunctionTool`)
- Peut utiliser un `ChatAgent` CAMEL pour l'orchestration interne
- Tourne comme serveur MCP indépendant (stdio ou sse)

### Structure

```
agents/
├── __init__.py
├── character_designer.py   # Fiches + sheets personnages
├── storyboarder.py         # Storyboard textuel + board
├── video_producer.py       # Start/end frames + wan-fast
└── audio_designer.py       # TTS dialogues par personnage
```

### Exemple : CharacterDesignerAgent

```python
@MCPServer()
class CharacterDesignerAgent(BaseToolkit):
    def __init__(self):
        super().__init__()
        self.agent = ChatAgent(
            system_message="Tu es le character designer...",
            tools=self.get_tools(),
        )

    def get_tools(self):
        return [
            FunctionTool(self.create_character_fiche),
            FunctionTool(self.generate_character_sheet),
            FunctionTool(self.describe_sheet),
        ]
```

### Communication inter-agents

Les agents ne se parlent pas directement entre eux.
Ils sont orchestrés par l'assistant principal (moi) qui :
1. Reçoit une instruction utilisateur
2. Appelle l'agent approprié via son MCP
3. Récupère le résultat
4. Le passe à l'agent suivant si nécessaire

### Configuration opencode.jsonc

```json
{
  "mcp": {
    "character-designer": {
      "type": "local",
      "command": [".venv/bin/python", "-m", "agents.character_designer", "--mode", "stdio"],
      "enabled": true
    },
    "storyboarder": {
      "type": "local",
      "command": [".venv/bin/python", "-m", "agents.storyboarder", "--mode", "stdio"],
      "enabled": true
    }
  }
}
```

### Avantages

- **Découplage** : chaque agent est autonome, testable indépendamment
- **Spécialisation** : chaque agent a son system prompt, ses outils, ses données
- **Scalabilité** : on peut ajouter/supprimer des agents sans casser le reste
- **Parallélisation** : les agents peuvent tourner simultanément sur des ports différents
- **Réutilisabilité** : un agent Character Designer peut servir pour n'importe quel projet

### Réflexion pour la suite

- Créer un `OrchestratorAgent` qui reçoit une instruction haut niveau ("produis la scène 5") et chaîne automatiquement les appels aux sous-agents
- Chaque agent pourrait avoir son propre prompt builder + templates YAML spécialisés
- Les agents pourraient partager une base de connaissances commune (la bible)
- Version HTTP (SSE) pour les agents lourds (vidéo), version stdio pour les agents légers (fiches)
- Monitoring : chaque agent pourrait reporter sa consommation pollen et son temps d'exécution

## 9. Contraintes

- **Pas de hardcoded paths** : tout passe par config.yaml
- **Pas de hardcoded seeds** : seeds dans la bible ou la config
- **Taille d'image configurable** : `image.pollinations.width/height` (agnes → `valid_sizes` en config)
- **Checkpoint après chaque étape** : reprise possible
- **Retry automatique** : 3 tentatives avec backoff (agnes apihub : `apihub_retries`/`apihub_retry_base_wait`/`apihub_retry_max_wait`)
- **Validation humaine** : chaque étape peut être mise en pause pour approbation
- **Pas de clé API en dur** : `vision.py` lève une erreur si `OPENAI_API_KEY` manque (plus de fallback)
