# SCHEMAS — Architecture et flux des deux pipelines ZtudioZ

> Vue **graphique** (Mermaid) des deux approches de production, de leurs flux
> d'information, étapes, processus et de ce qui est **commun**.
> Lecture textuelle complémentaire : `README.md` (vue d'ensemble), `WORKFLOW.md`
> (pipeline cinéma), `METHODE.md` (méthode Wilder), `DOC.md` (inventaire), `AGENTS.md`
> (config/règles), `.agents/skills/ztudioz-clip/SKILL.md` (clip).

Légende récurrente :

```mermaid
flowchart LR
    D[["DONNÉE / config"]]
    P["PROCESS / étape"]
    C{"CHECKPOINT / validation humaine"}
    B["BIBLE: fiches + sheets"]
    D --> P
    P -->|"fallback provider auto"| P
```

---

## 0. Les deux pipelines en un coup d'œil

| | **Cinéma** (le core) | **Clip musical** |
|---|---|---|
| Source narrative | `scenario.md` (scénario) | `timeline_v{N}.yaml` (timeline sur MP3) |
| Unité de découpage | **Scène** → plans (storyboard) | **Section musicale** → beats |
| Rythme | Driven par le drame (Wilder) | Driven par la **musique** (timestamps MP3) |
| Flux | `parse → fiches → sheets → storyboard → board → visualize → produce → audio` | `timeline → sections → beats → images héro → vidéos (par arc) → montage sur MP3` |
| Chaînage visuel | Shot = source de vérité (I2V) | `last_frame` par arc (continuité) |
| Audio | TTS dialogues par personnage | MP3 source unique (mixé au montage) |
| Bible | Objets partagés (fiches + sheets) | Objets **à états** (cold/hopeful…) |

---

## 1. Pipeline Cinéma — cycle Wilder (8 étapes)

Chaque étape écrit un **checkpoint** et s'arrête pour **validation humaine**.
L'ordre est strict : les **sheets** (références) précèdent le storyboard, les **shots** le
suivent, et ne se lancent jamais sans les sheets de la scène.

```mermaid
flowchart TD
    S[scenario.md] --> P1[1. parse]
    P1 --> P2[2. fiches]
    P2 --> P3[3. sheets]
    P3 --> P4[4. storyboard text]
    P4 --> P5[5. board planche]
    P5 --> P6[6. visualize shots]
    P6 --> P7[7. produce vidéo I2V]
    P7 --> P8[8. audio TTS]
    P8 --> M[montage scène/film]

    subgraph BIBLE[Bible partagée]
        B1[fiche.yaml: personnage/lieu/objet]
        B2[sheet_v1.png: image de référence]
        B1 --> B2
    end

    subgraph SOURCE[Source de vérité du scénario]
        TL[parse: timeline ordonnée<br/>actions + dialogues + transitions<br/>entrelacés, avec index/groups]
    end

    P2 -.crée.-> BIBLE
    P3 --"refs (upload tmpfiles 48h)"--> P4
    P3 --> P6
    P4 --"découpage en plans (passes A/B)<br/>--> candidat shot (voir schéma détail)"--> P5
    P6 --"shots = images des plans<br/>avec refs sheets"--> P7
    P7 --"I2V: shot en frame de départ"--> M
    P8 --"voix par personnage (fiche)"--> M

    TL --> P4
```

### Détail du découpage storyboard (2 passes) et du flux d'info par plan

```mermaid
flowchart LR
    subgraph PasseA["Passe A — bornes des plans"]
        A1[LLM: liste des plans<br/>number, start_index, end_index,<br/>type, camera]
        A2[bornes contiguës sur la timeline<br/>INT/EXT/FONDU = fin de plan]
        A1 --> A2
    end
    subgraph PasseB["Passe B — par plan (avec rate-limit)"]
        B1[LLM: case = type, camera,<br/>description, durée, prompt_image]
        B2["each plan porte sa timeline<br/>(événements + start/duration)"]
        B1 --> B2
    end
    subgraph Plan["Plan produit"]
        PP["storyboard.json<br/>plans[] avec timeline"]
    end

    TL[timeline scène] --> PasseA --> PasseB --> PP
    PP --"board + visualize"--> SHOT[shot_NNN.png]
```

---

## 2. Pipeline Clip — arc-based (data-driven sur MP3)

La **timeline YAML** est la source de vérité temporelle ; le MP3 pilote durée/rythme.
Les **arcs narratifs** sont des équipes de tournage autonomes : les plans d'un même arc se
chaînent via `last_frame`, chaque arc gère sa dernière frame.

```mermaid
flowchart TD
    MP3[MP3 source] --> AN[analyse audio<br/>onsets/RMS → beats_to_timeline]
    AN --> TL["timeline_vN.yaml<br/>sections[] + beats[]"]
    B[Bible à états: perso/lieu<br/>cold, hopeful…]
    CFG[config clip: variant,<br/>refs_bible, video.active]

    TL --> PROD[ClipStep]
    CFG --> PROD
    B --> PROD

    subgraph "Mode images (mode=images)"
        I1[par section: image héro<br/>compose multi-refs sheets état]
        I1 --> IH[images héros: perso + lieu]
    end

    subgraph "Mode vidéo (mode=video)"
        V0{orchestrateur d'arcs}
        V0 --> ARC1[arc: workshop<br/>beats → plans chaînés]
        V0 --> ARC2[arc: souvenir<br/>beats → plans chaînés]
        ARC1 --> V1[vidéos par beat/plan]
        ARC2 --> V1
    end

    subgraph "Mode montage (mode=full)"
        MON[assemble_scene<br/>concat plans + mux MP3]
    end

    PROD --> I1
    IH --> V0
    V1 --> MON
    MP3 --> MON
    MON --> CLIP[clip_final.mp4]
```

### Composition des refs et synthèse de prompt par plan (détail)

```mermaid
flowchart LR
    subgraph Refs["Refs par plan (max 4 — limite Agnes)"]
        R1[last_frame du même arc]
        R2[sheet perso état]
        R3[sheet lieu état]
    end
    R1 -.-> C[compose_start_image<br/>fusion multi-refs via ImageService.compose]
    R2 -.-> C
    R3 -.-> C
    C --> S[image de départ du plan]

    subgraph Prompt["Synthèse du prompt"]
        P1[supprime fillers]
        P2[raccourcit descriptions]
        P3[tronque en dernier recours]
        NEG[prompts négatifs: pas de gens,<br/>pas d'objets modernes, + spécifique section]
        P1 --> P2 --> P3
    end
    NOTE[Limites: agnes=500, pollinations=2000, default=1500]
    S --> VID[gen vidéo i2v/keyframes]
    NEG --> VID
```

---

## 3. Flux d'information commun — la Bible + config partagées

Les **deux** pipelines lisent la même source config (`pipeline/config.yaml`) et la même
**Bible** (fiches + sheets). C'est ce socle commun qui garantit la consistance
personnage/lieu, et que la leçon « refs + contrainte forte » s'applique partout.

```mermaid
flowchart LR
    subgraph CONFIG["pipeline/config.yaml (config-driven)"]
        C1["services: active + providers[]"]
        C2["production: storyboard_delay, keyframes…"]
        C3["clip: audio, variants, timeline"]
        C4[".env: clés API (jamais versionné)"]
    end

    subgraph BIBLE2["Bible"]
        B1[fiches: personnage/lieu/objet]
        B2[sheets: images de référence]
    end

    CONFIG --> CIN[Cinéma: steps/*]
    CONFIG --> CLIP2[Clip: ClipStep]
    BIBLE2 --> CIN
    BIBLE2 --> CLIP2

    FALLBACK[Fallback provider auto<br/>ImageService/VideoService<br/>_fallback_generate] -.-> CIN
    FALLBACK -.-> CLIP2
```

---

## 4. Fallback provider — mécanisme commun (hérité du clip V4)

Quand le provider actif renvoie 402/503/429, les **deux** pipelines basculent seuls sur le
provider backup de `config.yaml`, dans l'ordre, **sans boucle** (set `attempted`).

```mermaid
flowchart TD
    G[generate: prompt + refs] --> ACT[provider actif<br/>ex: pollinations]
    ACT --"succès"--> RET[return cache/path]
    ACT --"échec 402/503/429/5xx/réseau"--> FB{_should_fallback?}
    FB --"non (400 prompt)"--> RAISE[raise]
    FB --"oui"--> ORD["get_service_providers_ordered<br/>[actif, backups…]"]
    ORD --> NEXT{backup suivant<br/>non encore tenté ?}
    NEXT --"oui"--> BACKUP[ImageService/VideoService<br/>config=backup]
    BACKUP --> ACT2[retry backup]
    ACT2 --"succès"--> RET
    ACT2 --"échec éligible"--> NEXT
    NEXT --"non (tous tentés)"--> RAISE

    NAM["garde anti-boucle: set attempted<br/>(nom provider) propagé"] -.-> NEXT
```

---

## 5. Récapitulatif des points communs / différences

```mermaid
flowchart LR
    subgraph COMM["Commun"]
        X1["config-driven: active + providers"]
        X2["Bible: fiches + sheets refs"]
        X3["consistance = refs + contrainte forte"]
        X4["cache MD5 + checkpoint + validation c/r/s/p/q"]
        X5["fallback provider auto"]
        X6["vision LLM pour valider avant la suite"]
    end
    subgraph CIN["Cinéma (core)"]
        Y1["scène → plans (storyboard)"]
        Y2["timeline du scénario = source de vérité"]
        Y3["TTS dialogues par perso"]
        Y4["shots = source de vérité vidéo"]
    end
    subgraph CLP["Clip"]
        Z1["section → beats (sur MP3)"]
        Z2["timeline YAML = source de vérité"]
        Z3["MP3 unique mixé au montage"]
        Z4["last_frame par arc, bible à états"]
    end
    COMM <--> CIN
    COMM <--> CLP
```
