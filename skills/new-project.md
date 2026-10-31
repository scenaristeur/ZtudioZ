# new-project.md

## Role
Project initialization agent. Creates the directory structure
and initializes a new film project.

## Inputs
- `project_slug`: string (e.g., "50eme_rose")
- `script_path`: path to existing script (optional)
- `source_path`: path to source materials (optional)

## Steps

### 1. Create Directory Structure
```
projets/{slug}/
├── sources/               # Raw input materials
├── script/
│   └── parsed/            # scene_XX.yaml (after parsing)
├── bible/
│   ├── ambiance/          # style-guide.yaml, prompt_templates.yaml
│   ├── personnage/
│   ├── lieu/
│   └── objet/
├── preprod/
│   └── storyboard/        # scene_XX/ directories
├── production/
│   ├── audio/
│   └── video/
├── postprod/
│   └── montage/
├── logs/
└── .checkpoint.json
```

### 2. Copy/Link Source Materials
- If `source_path` provided, copy to `sources/`
- If `script_path` provided, copy to `script/scenario_v1.md`

### 3. Initialize Bible
- Create `bible/ambiance/style-guide.yaml` with default film style
- Create `bible/ambiance/prompt_templates.yaml` with default templates

### 4. Initialize Checkpoint
- Create `.checkpoint.json` with all steps at "pending"

### 5. Parse Script (if provided)
- If script exists, call `parse_script` to extract scenes

### 6. Log
- Log the project creation in `.history.json`

## Validation
- Verify all directories exist
- Verify `.checkpoint.json` is valid JSON
- If script was parsed, verify scene count > 0
