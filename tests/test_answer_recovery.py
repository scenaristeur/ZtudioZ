"""Reprise d'un run echoue : une gate posee reste repondable si l'echec l'a laissee en attente.

Un retry cible peut echouer en cours de route (reseau, provider). Le run passe
alors `failed` et `pending_gate` ne le voit plus. `answer` doit pourtant pouvoir
reprendre la meme gate, sans rouvrir une gate deja franchie.
"""

from studio.project import answerable_gate


def events(status="failed", answered=False):
    body = [
        {"run": "r", "type": "run_started", "workflow": "w", "inputs": {}},
        {
            "run": "r",
            "type": "gate_asked",
            "node": "gate_verdict",
            "question": "Le verdict est-il bon ?",
            "options": ["continue", "retry", "stop"],
        },
    ]
    if answered:
        body.append(
            {"run": "r", "type": "gate_answered", "node": "gate_verdict", "answer": "continue"}
        )
    body.append({"run": "r", "type": "run_finished", "status": status})
    return body


def test_un_run_failed_reste_repondable_a_sa_gate():
    gate = answerable_gate(events(), "r", "gate_verdict")

    assert gate is not None
    assert gate["id"] == "gate_verdict"
    assert gate["options"] == ["continue", "retry", "stop"]


def test_une_gate_deja_franchie_ne_se_rouvre_pas_apres_un_echec():
    assert answerable_gate(events(answered=True), "r", "gate_verdict") is None


def test_un_run_termine_ne_rouvre_pas_ses_gates():
    assert answerable_gate(events(status="completed"), "r", "gate_verdict") is None


def test_une_autre_gate_n_est_pas_repondable():
    assert answerable_gate(events(), "r", "gate_images") is None
