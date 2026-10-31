# fix-consistency.md

## Role
Visual consistency repair agent. Analyzes generated assets
and fixes inconsistencies in character appearance, location, or style.

## Inputs
- `project_slug`: string
- `scene_number`: int (optional, fixes all if omitted)
- `target`: "characters" | "locations" | "all" (default: "all")

## Workflow

### 1. Analyze
- Use `ztudioz.analyze_character_sheet` to check each character sheet
- Use `ztudioz.analyze_images` to compare shots within a scene
- Check for: wrong clothing, wrong hair, inconsistent palette, wrong location

### 2. Report
Present findings to user:
- Character: "Paul-Henri wears gray suit in sheets but black in shots 3,5"
- Location: "Bureau d'Adèle has bookshelf in sheet but not in shots"
- Style: "Grain is inconsistent between shots 1-4 and 5-8"

### 3. Fix Options
For each issue, offer:
- **Regenerate sheet**: new sheet with corrected attributes
- **Regenerate shots**: regenerate specific shots with corrected prompts
- **No fix**: accept as-is (sometimes small differences are fine)

### 4. Execute
User selects fixes. Execute in order:
1. Update fiche attributes if needed
2. Regenerate affected sheets
3. Regenerate affected shots
4. Re-validate

## Pitfalls
- Don't over-fix: small variations between shots are normal for animation
- Check the fiche attributes first — the issue is often in the prompt, not the generation
- Budget impact: each regeneration costs ~0.01-0.15 pollen

## Cause racine (transféré du clip V4) — CHECK D'ABORD

La cause n°1 d'incohérence répétée : les images/vidéos d'un personnage ont été générées
**sans refs**, ou avec un prompt **sans contrainte d'identité forte** → le modèle a
inventé un nouveau personnage à chaque génération.

Avant de régénérer quoi que ce soit, vérifier :
1. **Les refs sont-elles passées ?** (`ImageService.generate`/`compose` avec refs sheets
   perso+lieu). Jamais de génération de personnage sans refs.
2. **Le prompt porte-t-il la contrainte ?** `REFERENCE ONLY for character identity
   (same face, same clothes)` / `REFERENCE IMAGE for character identity: match exactly`.
3. **La description est-elle injectée ?** vêtements exacts (cf. `character_details.clothing`).

Si (1)/(2) manquent, corriger le pipeline/prompt à la SOURCE — régénérer sans corriger
reproduira l'incohérence.

## Drift mi-plan vidéo (capitalisé du clip Melody's Ghost)

Nouvelle cause racine vidéo : le plan **démarre avec le bon personnage**, mais l'identité
`glisse » au milieu (i2v simple = 1 ancre au début). À vérifier par **mid-frame**
(`ffmpeg -vf fps=1` ou `-ss <dur/2>`), pas seulement la première frame.

Checklist si drift mi-plan :
1. **Double-ancre** : le plan était-il généré en **mode keyframes 2 refs** (start + end)
   ou en i2v simple (1 ref) ? Si i2v simple → basculer en keyframes (cf. METHODE
   « Double-ancre identité »).
2. **Format** : `.start.png`/`.last.png` et vidéo sont-ils **le même format** ? Sinon
   l'image est rognée/étirée (recadrer, jamais étirer).
3. **Ré-ancrage** : le plan est-il le premier d'une nouvelle section d'un même arc ? Sans
   `section_reset`, il hérite de la dernière frame de la section précédente (héro ignoré).

Régénérer SANS corriger (1)/(2)/(3) reproduira le drift.
