"""Le skill opérateur ne doit pas dériver du moteur.

C'est la maladie exacte de la v1 : trois descriptions du même processus qui ne se
recoupent plus. Ici, une seule garantie simple dans les deux sens — ce que le manuel
documente existe, et ce que le moteur rend exécutable est documenté.
"""

import re
from pathlib import Path

from studio.kernel.graph import NODE_KEYS

SKILL = Path(__file__).resolve().parents[1] / ".opencode" / "skills" / "ztudioz-studio-v3" / "SKILL.md"

# Champs qui changent le comportement d'execution d'un noeud. Les champs de lecture
# (name, description) et ceux declares au niveau agent (budget, inputs, outputs) ne
# sont pas les affaires du manuel de pilotage.
SEMANTIC_FIELDS = {
    "agent",
    "needs",
    "foreach",
    "as",
    "limit",
    "parallel",
    "on_item_failure",
    "type",
    "question",
    "options",
}


def node_table() -> str:
    """Retourne le bloc du manuel qui documente les champs d'un noeud."""
    text = SKILL.read_text(encoding="utf-8")
    match = re.search(r"Champs d'un nœud.*?(?=\n\nRègle de portage)", text, re.S)
    assert match, "le manuel ne documente plus les champs d'un noeud"
    return match.group(0)


def test_le_manuel_ninventait_ancun_champ():
    documented = set(re.findall(r"^\| `([a-z_]+)", node_table(), re.M))

    assert documented, "aucun champ trouvé dans le tableau du manuel"
    assert documented <= NODE_KEYS, f"champs inventés par le manuel: {documented - NODE_KEYS}"


def test_le_manuel_documente_tous_les_champs_executables():
    documented = set(re.findall(r"^\| `([a-z_]+)", node_table(), re.M))

    missing = SEMANTIC_FIELDS - documented
    assert not missing, f"champs du moteur absents du manuel: {sorted(missing)}"


def test_tout_champ_accepte_par_le_graphe_est_classe():
    """Un nouveau champ de `NODE_KEYS` doit etre classe : documente au manuel, ou exempte."""
    exemptes = {"id", "name", "description", "inputs", "outputs", "budget"}
    documented = set(re.findall(r"^\| `([a-z_]+)", node_table(), re.M))
    unclasses = NODE_KEYS - SEMANTIC_FIELDS - exemptes - documented

    assert not unclasses, f"champs ni documentes ni exemptes: {sorted(unclasses)}"
