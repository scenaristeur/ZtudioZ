# regenerate.md

## Role
Asset regeneration agent. Regenerates a specific shot, video, or audio
file from its saved prompt and parameters.

## Inputs
- `project_slug`: string
- `target_type`: "shot" | "video" | "audio" | "sheet"
- `scene_number`: int
- `shot_number`: int (for shot/video/audio)
- `entity_slug`: string (for sheet)

## Workflow

### 1. Find the Asset
- Look up the asset in `.history.json` by kind + scene + shot
- If multiple versions exist, show the user the version list
- User selects which version to regenerate

### 2. Load Prompt and Parameters
- Read the saved prompt from `history/{kind}s/{hash}.json`
- Read model, seed, and other parameters

### 3. Regenerate
Call the appropriate MCP tool with the saved prompt:
- Shot: `ztudioz.storyboard_visualize` with specific shot
- Video: `ztudioz.produce_video` with specific shot
- Audio: `ztudioz.generate_audio` with specific shot
- Sheet: `ztudioz.generate_sheets` with specific entity

### 4. Version
- The old file is automatically versioned by the step
- Log the regeneration in `.history.json`

### 5. Cascade Check
If the regenerated asset is upstream (e.g., a sheet), warn the user:
"This will affect downstream assets (shots, videos). Regenerate them too?"

## Pitfalls
- Always check if the asset already exists before regenerating
- The original prompt may reference expired tmpfiles URLs
- Budget: each regeneration costs the same as the original
