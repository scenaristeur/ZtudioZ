"""D24 — les contraintes du brief doivent voyager jusqu'aux plans, et etre respecte.

Deux garanties distinctes :
1. transport : le brief (avec ses contraintes) arrive jusqu'a l'agent qui decoupe
   les plans ;
2. enforcement : le fan-out est borne deterministement par la contrainte, un LLM
   qui surcompte ne peut pas produire 6 plans la ou 2 sont demandes.
"""

import asyncio
import json

import jsonschema
import pytest

from studio.kernel.artifacts import ArtifactStore
from studio.kernel.errors import GraphError
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import STUDIO_ROOT, load_registry
from studio.tools.images import MockImageProvider
from studio.tools.llm import MockProvider
from tests.helpers import ECHO_TOOLS, catalog

BRIEF_WITH_CONSTRAINT = {
    "logline": "Un tourbier suit un feu follet.",
    "synopsis": "La lande, la nuit, deux plans.",
    "characters": ["Le tourbier"],
    "locations": ["La tourbiere"],
    "open_questions": [],
    "constraints": {"max_shots": 2},
}

FAN_WF = """
"@id": "studio:workflow/fan"
type: studio:Workflow
inputs:
  items: {type: list, required: true}
  n: {type: int, required: true}
nodes:
  - id: work
    agent: studio:agent/echo
    foreach: "${items}"
    limit: "${n}"
"""

FAN_AGENT = """
"@id": "studio:agent/echo"
type: studio:LlmAgent
name: Echo
version: 1
model: mock/echo
prompt: "Traite l'item."
inputs:
  item:
    type: string
    required: true
outputs:
  text:
    type: string
"""


def make(tmp_path, *, workflow=FAN_WF, agent=FAN_AGENT):
    root = catalog(
        tmp_path,
        agents={"echo": agent},
        workflows={"fan": workflow},
        tools=ECHO_TOOLS,
    )
    provider = MockProvider(default=lambda prompt: {"text": "ok"})
    journal = Journal(tmp_path / "journal.jsonl")
    executor = Executor(
        registry=load_registry(root),
        provider=provider,
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=journal,
    )
    return executor, provider, journal


def run(executor, n):
    return asyncio.run(
        executor.run("studio:workflow/fan", {"items": ["a", "b", "c", "d", "e"], "n": n})
    )


# --- 1. le brief sait porter une contrainte ---------------------------------


def test_le_brief_peut_porter_une_contrainte_de_plans():
    schema = json.loads((STUDIO_ROOT / "catalog" / "schemas" / "brief.schema.json").read_text())

    jsonschema.validate(BRIEF_WITH_CONSTRAINT, schema)


def test_max_shots_doit_etre_au_moins_un():
    schema = json.loads((STUDIO_ROOT / "catalog" / "schemas" / "brief.schema.json").read_text())
    illegal = dict(BRIEF_WITH_CONSTRAINT, constraints={"max_shots": 0})

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(illegal, schema)


def test_max_shots_refuse_un_non_entier():
    schema = json.loads((STUDIO_ROOT / "catalog" / "schemas" / "brief.schema.json").read_text())
    illegal = dict(BRIEF_WITH_CONSTRAINT, constraints={"max_shots": "deux"})

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(illegal, schema)


# --- 2. le fan-out est borne ------------------------------------------------


def test_la_limite_borne_le_fan_out(tmp_path):
    executor, provider, _ = make(tmp_path)

    result = run(executor, n=2)

    assert result.status == "completed"
    assert provider.calls == 2
    assert len(result.outputs["text"]) == 2


def test_une_limite_superieure_aux_items_ne_perd_rien(tmp_path):
    executor, _, _ = make(tmp_path)

    result = run(executor, n=3)

    assert len(result.outputs["text"]) == 3


def test_sans_limite_tous_les_items_sont_traites(tmp_path):
    workflow = FAN_WF.replace('    limit: "${n}"\n', "")
    executor, provider, _ = make(tmp_path, workflow=workflow)

    result = run(executor, n=2)

    assert provider.calls == 5
    assert len(result.outputs["text"]) == 5


def test_la_troncature_est_journalisee_pas_silencieuse(tmp_path):
    executor, _, journal = make(tmp_path)

    run(executor, n=2)

    events = [e for e in journal.events() if e["type"] == "items_truncated"]
    assert len(events) == 1
    assert events[0]["node"] == "work"
    assert events[0]["produced"] == 5
    assert events[0]["kept"] == 2


def test_elargir_la_limite_ne_rappelle_que_les_items_manquants(tmp_path):
    """Le cache est par item : 2 -> 4 plans ne repaie pas les 2 premiers."""
    executor, provider, _ = make(tmp_path)

    run(executor, n=2)
    assert provider.calls == 2

    result = run(executor, n=4)

    assert provider.calls == 4
    assert len(result.outputs["text"]) == 4


def test_la_meme_limite_recache(tmp_path):
    executor, provider, _ = make(tmp_path)

    run(executor, n=2)
    after_first = provider.calls

    run(executor, n=2)

    assert provider.calls == after_first


def test_limite_nulle_est_refusee_au_chargement(tmp_path):
    workflow = FAN_WF.replace('limit: "${n}"', "limit: 0")
    root = catalog(
        tmp_path,
        agents={"echo": FAN_AGENT},
        workflows={"fan": workflow},
        tools=ECHO_TOOLS,
    )
    registry = load_registry(root)

    with pytest.raises(GraphError, match="limit"):
        registry.graph("studio:workflow/fan")


def test_limite_non_entiere_est_refusee(tmp_path):
    workflow = FAN_WF.replace('limit: "${n}"', 'limit: "deux"')
    root = catalog(
        tmp_path,
        agents={"echo": FAN_AGENT},
        workflows={"fan": workflow},
        tools=ECHO_TOOLS,
    )
    registry = load_registry(root)

    with pytest.raises(GraphError, match="limit"):
        registry.graph("studio:workflow/fan")


def test_limite_non_resoluble_est_refusee_en_execution(tmp_path):
    workflow = FAN_WF.replace('limit: "${n}"', 'limit: "${inconnu}"')
    executor, _, _ = make(tmp_path, workflow=workflow)

    result = run(executor, n=2)

    assert result.status == "failed"
    assert "inconnu" in (result.error or "")


# --- 3. le transport jusqua l'agent de decoupage ----------------------------


def test_shot_list_declare_le_brief_comme_entree():
    registry = load_registry(STUDIO_ROOT)
    agent = registry.agent("studio:agent/shot-list")

    assert "brief" in (agent.get("inputs") or {}), "le decoupage ne peut pas voir le brief"


def test_m1_scene_declare_le_brief_comme_entree():
    registry = load_registry(STUDIO_ROOT)
    workflow = registry.workflow("studio:workflow/m1-scene")

    assert "brief" in (workflow.get("inputs") or {})


def test_le_brief_traverse_reellement_jusqua_agent(tmp_path):
    agent = """
"@id": "studio:agent/echo"
type: studio:LlmAgent
name: Echo
version: 1
model: mock/echo
prompt: "Decoupe."
inputs:
  brief:
    type: object
    required: true
outputs:
  shots:
    type: array
"""
    workflow = """
"@id": "studio:workflow/fan"
type: studio:Workflow
inputs:
  brief: {type: object, required: true}
nodes:
  - id: work
    agent: studio:agent/echo
"""
    root = catalog(tmp_path, agents={"echo": agent}, workflows={"fan": workflow}, tools=ECHO_TOOLS)
    seen = {}

    def spy(prompt):
        seen.update(json.loads(prompt))
        return {"shots": ["plan 1"]}

    provider = MockProvider(default=spy)
    executor = Executor(
        registry=load_registry(root),
        provider=provider,
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
    )

    result = asyncio.run(executor.run("studio:workflow/fan", {"brief": BRIEF_WITH_CONSTRAINT}))

    assert result.status == "completed"
    assert seen["brief"]["constraints"]["max_shots"] == 2


def test_le_pack_de_connaissance_exige_de_coucher_les_contraintes():
    registry = load_registry(STUDIO_ROOT)
    pack = registry.skill("scenario-structure")

    assert "constraints" in pack


def test_le_mock_honore_minimum_sur_un_entier():
    """Un mock qui fabrique des valeurs doit fabriquer des valeurs CONFORMES."""
    value = MockProvider._fake_value({"type": "integer", "minimum": 1})

    assert value >= 1
    jsonschema.validate(value, {"type": "integer", "minimum": 1})


# --- 4. D24 : la garde-fou sur le catalogue livre ---------------------------

SIX_SHOTS = {
    "shots": [
        {"id": n, "prompt": f"Plan {n} de la lande."} for n in range(1, 7)
    ]
}

FEU_FOLLET = {
    "logline": "Un tourbier suit un feu follet dans la tourbière.",
    "synopsis": "La lande la nuit, la lanterne contre la lumière bleue.",
    "characters": ["Le tourbier"],
    "locations": ["La lande", "La tourbière"],
    "open_questions": [],
    "constraints": {"max_shots": 2, "freeform": ["pas de dialogue", "fin ambigue"]},
}

M1_INPUTS = {
    "treatment": "Séquence 1 : la lande. Séquence 2 : la lanterne. Séquence 3 : le feu follet.",
    "reference": "lande bretonne, nuit, lumière bleue",
    "brief": FEU_FOLLET,
}


def make_m1(tmp_path, responses):
    provider = MockProvider(responses=responses)
    image = MockImageProvider()
    executor = Executor(
        registry=load_registry(STUDIO_ROOT),
        provider=provider,
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
        image=image,
    )
    return executor, provider, image


def test_feu_follet_ne_peut_plus_produire_six_plans(tmp_path):
    """D24 : l'auteur a dit « deux plans maximum », le decoupage ne dechiffre plus seul."""
    executor, provider, image = make_m1(
        tmp_path,
        {"shot-list": SIX_SHOTS, "continuity-checker": {"verdict": {"ok": True, "notes": "bon"}}},
    )

    result = asyncio.run(
        executor.run("studio:workflow/m1-scene", M1_INPUTS, answers={"gate_images": "continue"})
    )

    assert result.status == "completed", result.error
    assert len(result.outputs["shots"]) == 6, "le LLM a bien surcompte, c'est le cas testé"
    assert len(result.outputs["image"]) == 2
    assert image.calls == 3  # 1 planche de reference + 2 images, pas 6

    truncated = [e for e in executor.journal.events() if e["type"] == "items_truncated"]
    assert truncated[0]["produced"] == 6
    assert truncated[0]["kept"] == 2


def test_sans_contrainte_m1_produit_tous_les_plans(tmp_path):
    executor, _, image = make_m1(
        tmp_path,
        {"shot-list": SIX_SHOTS, "continuity-checker": {"verdict": {"ok": True, "notes": "bon"}}},
    )

    inputs = {**M1_INPUTS, "brief": {k: v for k, v in FEU_FOLLET.items() if k != "constraints"}}
    result = asyncio.run(
        executor.run("studio:workflow/m1-scene", inputs, answers={"gate_images": "continue"})
    )

    assert result.status == "completed", result.error
    assert len(result.outputs["image"]) == 6
    assert image.calls == 7


def test_contrainte_absente_ne_borne_pas_le_fan_out(tmp_path):
    """Une feuille manquante sous une racine resolue = pas de borne, pas d'erreur."""
    workflow = """
"@id": "studio:workflow/fan"
type: studio:Workflow
inputs:
  items: {type: list, required: true}
  brief: {type: object, required: true}
nodes:
  - id: work
    agent: studio:agent/echo
    foreach: "${items}"
    limit: "${brief.constraints.max_shots}"
"""
    executor, provider, journal = make(tmp_path, workflow=workflow, agent=FAN_AGENT)

    result = asyncio.run(
        executor.run(
            "studio:workflow/fan",
            {"items": ["a", "b", "c"], "brief": {"logline": "x"}},
        )
    )

    assert result.status == "completed"
    assert provider.calls == 3
    assert not [e for e in journal.events() if e["type"] == "items_truncated"]


def test_la_borne_vient_reellement_du_brief(tmp_path):
    workflow = """
"@id": "studio:workflow/fan"
type: studio:Workflow
inputs:
  items: {type: list, required: true}
  brief: {type: object, required: true}
nodes:
  - id: work
    agent: studio:agent/echo
    foreach: "${items}"
    limit: "${brief.constraints.max_shots}"
"""
    executor, provider, journal = make(tmp_path, workflow=workflow, agent=FAN_AGENT)

    result = asyncio.run(
        executor.run(
            "studio:workflow/fan",
            {"items": ["a", "b", "c"], "brief": BRIEF_WITH_CONSTRAINT},
        )
    )

    assert result.status == "completed"
    assert provider.calls == 2
    assert [e for e in journal.events() if e["type"] == "items_truncated"][0]["kept"] == 2
