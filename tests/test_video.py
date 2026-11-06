"""M2 video : de vraies videos generees avant le montage, pas des plans fixes.

Le palier M2 ne savait produire que des plans fixes (image tenue 1,5 s). Ici
on introduit la primitive `studio:VideoAgent` : une image de plan + un mouvement
donne par l'humain -> un clip video genere.

Deux garanties de conception, testees ici :
- aucune depense avant la gate qui valide les fiches de plans ;
- un item en panne ne detruit pas le montage des autres plans.
"""

import asyncio
import json

import pytest

from studio.kernel.artifacts import ArtifactStore, Binary
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import STUDIO_ROOT, load_registry
from studio.tools.images import MockImageProvider
from studio.tools.llm import MockProvider
from studio.tools.video import MockVideoProvider

DURATION = 5.0


def image_ref(tmp_path, name: str) -> dict:
    """Une image PNG reelle, deja adressee par hash, comme en sortie de M1."""
    store = ArtifactStore(tmp_path / "artifacts")
    binary = asyncio.run(
        MockImageProvider().generate(agent_id=name, model="mock", system="", prompt=name)
    )
    return store.put(binary)


def fiche(tmp_path, name: str) -> dict:
    ref = image_ref(tmp_path, name)
    store = ArtifactStore(tmp_path / "artifacts")
    return {
        "image": store.get(ref).ref(),
        "prompt": f"Plan {name} : la lande bretonne, la nuit.",
        "motion": "travelling avant lent, la lanterne tremble",
        "duration": DURATION,
    }


def make_executor(tmp_path, video, *, image=None):
    return Executor(
        registry=load_registry(STUDIO_ROOT),
        provider=MockProvider(),
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
        image=image,
        video=video,
    )


def run_video(tmp_path, fiches, video, answers=None, image=None):
    executor = make_executor(tmp_path, video, image=image)
    result = asyncio.run(
        executor.run(
            "studio:workflow/m2-video",
            {"plans": fiches},
            answers={"gate_storyboard": "continue", "gate_videos": "continue", "gate_montage": "continue"} if answers is None else answers,
        )
    )
    return result, executor


# --- 1. le contrat du catalogue ---------------------------------------------


def test_le_catalogue_declare_un_video_agent():
    agent = load_registry(STUDIO_ROOT).agent("studio:agent/video-maker")

    assert str(agent["@type"]).endswith("VideoAgent")
    assert "item" in (agent.get("inputs") or {})
    assert "clip" in (agent.get("outputs") or {})


def test_le_workflow_m2_video_existe_et_demande_une_validation_avant_depense():
    workflow = load_registry(STUDIO_ROOT).workflow("studio:workflow/m2-video")
    nodes = {node["id"]: node for node in workflow["nodes"]}

    assert nodes["clips"]["foreach"] == "${plans}"
    assert nodes["gate_storyboard"]["type"].endswith("HumanGate")
    assert "gate_storyboard" in nodes["clips"].get("needs", [])
    assert "gate_videos" in nodes["board"].get("needs", [])


# --- 2. le moteur ------------------------------------------------------------


def test_un_noeud_video_sans_adaptateur_echoue_net(tmp_path):
    executor = Executor(
        registry=load_registry(STUDIO_ROOT),
        provider=MockProvider(),
        store=ArtifactStore(tmp_path / "artifacts"),
        journal=Journal(tmp_path / "journal.jsonl"),
    )

    result = asyncio.run(
        executor.run(
            "studio:workflow/m2-video",
            {"plans": [fiche(tmp_path, "a")]},
            answers={"gate_storyboard": "continue"},
        )
    )

    assert result.status == "failed"
    assert "video" in (result.error or "").lower()
    assert "adaptateur" in (result.error or "").lower()


def test_aucune_depense_avant_la_gate_des_fiches(tmp_path):
    video = MockVideoProvider()
    result, _ = run_video(tmp_path, [fiche(tmp_path, "a")], video, answers={})

    assert result.status == "blocked"
    assert result.pending_gate.id == "gate_storyboard"
    assert video.calls == 0, "la video a ete generee avant la validation humaine"


def test_deux_videos_reelles_deviennent_un_montage_sonde(tmp_path):
    video = MockVideoProvider()
    result, executor = run_video(tmp_path, [fiche(tmp_path, "a"), fiche(tmp_path, "b")], video)

    assert result.status == "completed", result.error
    assert video.calls == 2
    assert result.outputs["probe"]["codec"] == "h264"
    assert DURATION * 2 - 0.3 <= result.outputs["probe"]["duration"] <= DURATION * 2 + 0.3
    assert not [e for e in executor.journal.events() if e["type"] == "item_failed"]


def test_les_keyframes_arrivent_dans_l_ordre_debut_fin(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    start = image_ref(tmp_path, "debut")
    end = image_ref(tmp_path, "fin")
    store = ArtifactStore(tmp_path / "artifacts")
    plan = {
        "end_image": store.get(end).ref(),
        "start_image": store.get(start).ref(),
        "prompt": "Plan : le convoi traverse la tempete.",
        "motion": "travelling lateral",
        "duration": DURATION,
    }

    video = MockVideoProvider()
    result, _ = run_video(tmp_path, [plan], video)

    assert result.status == "completed", result.error
    assert video.records[0]["references"] == [start, end]
    assert video.records[0]["reference"] == start


def test_la_video_recoit_le_mouvement_et_l_image_de_reference(tmp_path):
    video = MockVideoProvider()
    result, _ = run_video(tmp_path, [fiche(tmp_path, "a")], video)

    assert result.status == "completed", result.error
    record = video.records[0]
    assert "travelling avant lent" in record["prompt"], "le mouvement est perdu en route"
    assert record["reference"], "le provider video n'a pas recu l'image du plan"
    assert record["references"], "la liste des references est vide"
    assert record["duration"] == DURATION


def test_une_reference_qui_n_est_pas_une_image_est_refusee(tmp_path):
    video = MockVideoProvider()
    mp4 = Binary(b"\x00\x00\x00\x20ftypisom", "video/mp4")

    with pytest.raises(ValueError, match="image"):
        asyncio.run(
            video.generate(
                agent_id="studio:agent/video-maker",
                model="mock",
                system="",
                prompt="plan",
                reference=mp4,
                duration=DURATION,
            )
        )


def test_le_cout_est_estime_par_seconde_et_refuse_avant_appel(tmp_path):
    video = MockVideoProvider(cost_per_second=1.0)  # 5 s -> 5.0, budget 0.50
    result, executor = run_video(tmp_path, [fiche(tmp_path, "a")], video)

    assert result.status == "failed"
    assert video.calls == 0, "le budget a ete refuse mais l'appel est parti"
    assert [e for e in executor.journal.events() if e["type"] == "budget_refused"]


def test_un_plan_en_panne_n_empeche_pas_le_montage_des_autres(tmp_path):
    video = MockVideoProvider(fail_on=2)
    result, executor = run_video(tmp_path, [fiche(tmp_path, "a"), fiche(tmp_path, "b")], video)

    assert result.status == "completed", result.error
    assert result.outputs["probe"]["codec"] == "h264"
    failed = [e for e in executor.journal.events() if e["type"] == "item_failed"]
    assert len(failed) == 1


# --- 3. le bug trouve par le run reel : tmpfiles refuse urllib -----------------


def test_l_upload_public_envoie_un_user_agent(monkeypatch):
    """Trouve en reel : tmpfiles renvoie 403 a l'UA par defaut de urllib."""
    from studio.tools import video as video_module

    seen = []

    class Response:
        def __init__(self, payload: bytes):
            self._payload = payload
            self.headers = {"content-type": "text/html"}

        def read(self) -> bytes:
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        seen.append(request)
        if "api/v1/upload" in request.full_url:
            return Response(json.dumps({"data": {"url": "https://tmpfiles.org/abc/probe.jpg"}}).encode())
        return Response(b'<a href="https://tmpfiles.org/dl/abc/probe.jpg">x</a>')

    monkeypatch.setattr(video_module.urllib.request, "urlopen", fake_urlopen)

    url = video_module._upload_public(b"data", "probe.jpg")

    assert url == "https://tmpfiles.org/dl/abc/probe.jpg"
    assert seen, "aucune requete emise"
    for request in seen:
        assert request.get_header("User-agent") == video_module.USER_AGENT


def test_le_seed_reste_dans_les_bornes_de_l_api(monkeypatch):
    """Trouve en reel : un seed sur 64 bits est refuse (max 2147483647)."""
    from urllib.parse import parse_qs, urlparse

    from studio.tools import video as video_module

    captured = {}

    class Response:
        headers = {"content-type": "video/mp4"}

        def read(self) -> bytes:
            return b"\x00\x00\x00\x20ftypisom" + b"0" * 32

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        return Response()

    monkeypatch.setattr(video_module.urllib.request, "urlopen", fake_urlopen)
    provider = video_module.PollinationsVideoProvider()

    asyncio.run(
        provider.generate(
            agent_id="studio:agent/video-maker",
            model="wan-fast",
            system="",
            prompt="plan",
            duration=DURATION,
        )
    )

    query = parse_qs(urlparse(captured["url"]).query)
    assert 0 <= int(query["seed"][0]) <= 2_147_483_647
