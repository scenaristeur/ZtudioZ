"""Test anti-drift documentation / code.

Pourquoi
--------
La documentation de ce dépôt a déjà menti : un fichier cité qui n'existe pas, un bug
annoncé comme présent alors qu'il est corrigé, un cycle d'étapes qui ne correspond plus à
`AVAILABLE_STEPS`. Ces mensonges coûtent cher parce qu'ils sont invisibles : on ne relit
pas ce qu'on croit déjà savoir.

Ce test vérifie l'inverse de ce qui est vérifiable automatiquement, et seulement ça :
les invariants que le code peut confirmer en une milliseconde. Le reste — Justesse des
explications, qualité de la prose — reste à la relecture humaine.

Il se lance avec `test_doc_drift.py`, indépendamment de `test_system.py`, pour rester
exécutable pendant qu'un autre agent modifie le code.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS = [ROOT / "AGENTS.md", ROOT / "AGENTS_CODE.md"]
SKILLS = sorted((ROOT / ".opencode" / "skills").glob("ztudioz-*/SKILL.md"))

# Chemins de modules que la doc cite. Un module renommé casse la doc silencieusement.
KNOWN_MODULES = [
    "pipeline/orchestrator.py",
    "pipeline/config.py",
    "pipeline/consumption.py",
    "pipeline/services/prompt_builder.py",
    "pipeline/services/image.py",
    "pipeline/services/vision.py",
    "pipeline/services/llm.py",
    "pipeline/steps/visualize.py",
    "pipeline/steps/storyboard.py",
    "pipeline/steps/produce.py",
    "pipeline/steps/keyframes_step.py",
    "pipeline/steps/audio_step.py",
    "pipeline/steps/fiche_step.py",
    "pipeline/steps/sheets.py",
    "pipeline/scene_constraints.py",
    "pipeline/shot_verify.py",
    "pipeline/cost_preflight.py",
    "pipeline/services/refsig.py",
    "pipeline/state.py",
    "pipeline/clip/verify.py",
    "pipeline/services/montage.py",
]

failures: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)


def _doc_text() -> str:
    chunks = []
    for path in DOCS + SKILLS:
        if path.exists():
            chunks.append(path.read_text(encoding="utf-8"))
    return "\n".join(chunks)


def test_modules_exist() -> None:
    """Tout module cité dans la liste de référence doit exister."""
    for rel in KNOWN_MODULES:
        if not (ROOT / rel).is_file():
            fail(f"module référencé absent : {rel}")


def test_no_broken_module_paths_in_docs() -> None:
    """Les chemins `pipeline/....py` cités dans la doc doivent exister.

    Attrape le cas rencontré : la doc citait `pipeline/prompt_builder.py` alors que le
    fichier est `pipeline/services/prompt_builder.py`.
    """
    pattern = re.compile(r"`(pipeline/[A-Za-z0-9_/]+\.py)(?::[0-9,]+)?`")
    for match in pattern.finditer(_doc_text()):
        rel = match.group(1)
        if not (ROOT / rel).is_file():
            fail(f"la doc cite un fichier inexistant : {rel}")


def test_available_steps_match_cycle() -> None:
    """Le cycle documenté doit couvrir les étapes réellement disponibles.

    La doc peutMentionner des étapes supplémentaires (`montage` n'est pas une étape du
    pipeline mais existe comme service) ; elle ne doit pas en omettre une.
    """
    src = (ROOT / "pipeline" / "orchestrator.py").read_text(encoding="utf-8")
    match = re.search(r"AVAILABLE_STEPS\s*=\s*\[(.*?)\]", src, re.S)
    if not match:
        fail("AVAILABLE_STEPS introuvable dans orchestrator.py")
        return
    steps = re.findall(r"[\"']([a-z_]+)[\"']", match.group(1))
    text = _doc_text()
    for step in steps:
        if step == "init":
            continue  # `init` ouvre le cycle, il n'est pas toujours nommé
        if f"`{step}`" not in text and f"**{step}**" not in text:
            fail(f"l'étape '{step}' existe dans le code mais n'est décrite nulle part")


def test_no_claim_of_fixed_bug() -> None:
    """Un bug marqué corrigé ne doit pas être présenté comme actif.

    Symétrique du précédent : si la doc dit « non corrigé » pour un bug corrigé, elle
    enverra un agent réparer un correctif.
    """
    code = "\n".join(
        (ROOT / rel).read_text(encoding="utf-8")
        for rel in ("pipeline/orchestrator.py",)
        if (ROOT / rel).is_file()
    )
    text = _doc_text()
    # Le bug 1 : le checkpoint écrivait "production" au lieu de "produce".
    if 'save_checkpoint' in code and '"production"' not in code:
        for doc in DOCS:
            if not doc.exists():
                continue
            body = doc.read_text(encoding="utf-8")
            if re.search(r'`produce`\s*jamais\s+reconnu', body) and "corrigé" not in body:
                fail(f"{doc.name} présente le bug 'produce jamais reconnu' comme actif")


def test_validation_modes_documented() -> None:
    """Les modes de validation réels doivent être documentés."""
    src = (ROOT / "pipeline" / "orchestrator.py").read_text(encoding="utf-8")
    match = re.search(r"VALIDATION_MODES\s*=\s*\((.*?)\)", src, re.S)
    if not match:
        return  # la constante a été retirée : rien à synchroniser
    modes = re.findall(r"[\"']([a-z_]+)[\"']", match.group(1))
    text = _doc_text()
    for mode in modes:
        if f"`{mode}`" not in text:
            fail(f"mode de validation '{mode}' supporté par le code mais non documenté")


def test_skills_frontmatter_present() -> None:
    """Chaque skill doit avoir un frontmatter parseable."""
    for skill in SKILLS:
        content = skill.read_text(encoding="utf-8")
        if not content.startswith("---"):
            fail(f"{skill.relative_to(ROOT)} : frontmatter absent")
            continue
        end = content.find("\n---", 3)
        if end == -1:
            fail(f"{skill.relative_to(ROOT)} : frontmatter non fermé")
            continue
        try:
            import yaml
            meta = yaml.safe_load(content[3:end]) or {}
        except Exception as exc:  # noqa: BLE001
            fail(f"{skill.relative_to(ROOT)} : frontmatter illisible ({exc})")
            continue
        if not meta.get("name") or not meta.get("description"):
            fail(f"{skill.relative_to(ROOT)} : name ou description manquant")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
    if failures:
        print(f"❌ Dérive documentation/code : {len(failures)} problème(s)")
        for f in failures:
            print(f"   - {f}")
        return 1
    print(f"✅ Documentation alignée ({len(tests)} contrôles, {len(SKILLS)} skills)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
