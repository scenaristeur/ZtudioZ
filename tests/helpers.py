"""Helpers de test : construction de catalogues YAML-LD jetables et provider factice."""

from pathlib import Path
from textwrap import dedent

CTX = '"@context": "studio:context"\n'


def write(path: Path, text: str) -> Path:
    """Ecrit un fichier en creant ses parents, en dedentant le contenu."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(text).lstrip(), encoding="utf-8")
    return path


def catalog(
    tmp_path: Path,
    *,
    agents: dict[str, str] | None = None,
    workflows: dict[str, str] | None = None,
    tools: str | None = None,
    skills: dict[str, str] | None = None,
    schemas: dict[str, str] | None = None,
) -> Path:
    """Construit un catalogue minimal et retourne la racine studio."""
    root = tmp_path / "studio"
    for slug, body in (agents or {}).items():
        write(root / "catalog" / "agents" / f"{slug}.yamlld", CTX + dedent(body).lstrip())
    for slug, body in (workflows or {}).items():
        write(root / "catalog" / "workflows" / f"{slug}.yamlld", CTX + dedent(body).lstrip())
    if tools is not None:
        write(root / "catalog" / "tools.yamlld", CTX + dedent(tools).lstrip())
    else:
        write(root / "catalog" / "tools.yamlld", CTX + "tools: {}\n")
    for slug, body in (skills or {}).items():
        write(root / "catalog" / "skills" / slug / "SKILL.md", body)
    for name, body in (schemas or {}).items():
        write(root / "catalog" / "schemas" / name, body)
    (root / "__init__.py").parent.mkdir(parents=True, exist_ok=True)
    (root / "__init__.py").write_text("", encoding="utf-8")
    return root


ECHO_AGENT = """
"@id": "studio:agent/echo"
type: studio:LlmAgent
name: Echo
version: 1
model: mock/echo
prompt: "Reponds a ${node}."
"""

ECHO_TOOLS = """
tools:
  echo:
    kind: python
    impl: studio.tools.llm:echo
"""


def echo_agent(**overrides: str) -> str:
    """Retourne la declaration d'un agent echo, avec surcharges de champs."""
    body = ECHO_AGENT
    for key, value in overrides.items():
        if key == "id":
            body = body.replace('"studio:agent/echo"', f'"studio:agent/{value}"')
        elif key == "version":
            body = body.replace("version: 1", f"version: {value}")
    return body
