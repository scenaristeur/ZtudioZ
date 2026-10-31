"""D15 — les skills v1 deviennent joignables : roots additionnels au chargement et au CLI."""

import subprocess
import sys
from pathlib import Path

import pytest

from studio.kernel.registry import STUDIO_ROOT, load_registry
from tests.helpers import ECHO_TOOLS, catalog

V1_SKILLS = Path("/home/smag/dev/ZtudioZ/.opencode/skills")


def pack_dir(root):
    pack = root / "consistency-rules"
    pack.mkdir(parents=True)
    (pack / "SKILL.md").write_text("# Regles\nNe jamais changer l'heure au soleil.", encoding="utf-8")
    return root


def test_extra_skill_root_is_loaded(tmp_path):
    root = catalog(tmp_path, tools=ECHO_TOOLS)

    registry = load_registry(root, extra_skill_roots=[pack_dir(tmp_path / "externes")])

    assert "Ne jamais changer" in registry.skill("consistency-rules")


def test_agent_can_reference_an_external_skill(tmp_path):
    agent = """
"@id": "studio:agent/echo"
type: studio:LlmAgent
name: Echo
version: 1
model: mock/echo
prompt: "x"
skills: [consistency-rules]
"""
    root = catalog(tmp_path, agents={"echo": agent}, tools=ECHO_TOOLS)

    registry = load_registry(root, extra_skill_roots=[pack_dir(tmp_path / "externes")])

    assert "Ne jamais changer" in registry.skill("consistency-rules")


@pytest.mark.skipif(not V1_SKILLS.exists(), reason="dépôt v1 absent de cette machine")
def test_les_11_skills_v1_sont_des_packs_valables():
    registry = load_registry(STUDIO_ROOT, extra_skill_roots=[V1_SKILLS])

    found = [name for name in registry.skills if name.startswith("ztudioz-")]
    assert len(found) >= 11
    assert "scenario-structure" in registry.skills  # les packs v3 restent


def test_cli_skills_root_flag(tmp_path):
    root = catalog(tmp_path, tools=ECHO_TOOLS)
    externes = pack_dir(tmp_path / "externes")

    completed = subprocess.run(
        [
            sys.executable, "-m", "studio.cli",
            "--root", str(root), "--skills-root", str(externes), "validate",
        ],
        cwd=STUDIO_ROOT.parent,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "skills: 1" in completed.stdout
