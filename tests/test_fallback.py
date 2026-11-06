"""Repli Agnes -> Pollinations : detecte, demande a l'humain, ne decide jamais seul."""

import asyncio
import types
import urllib.error

import pytest

from studio import cli
from studio.kernel.artifacts import ArtifactStore
from studio.kernel.errors import StudioError
from studio.kernel.journal import Journal
from studio.kernel.registry import STUDIO_ROOT, load_registry
from studio.project import Project
from studio.tools.agnes import AgnesImageProvider, AgnesVideoProvider
from studio.tools.images import MockImageProvider, PollinationsImageProvider
from studio.tools.llm import MockProvider
from studio.tools.video import MockVideoProvider, PollinationsVideoProvider

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def args(**overrides):
    base = types.SimpleNamespace(
        provider=None, image_provider=None, video_provider=None, fallback="ask"
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def agnes_project(tmp_path):
    (tmp_path / "studio.yaml").write_text(
        """
        name: agnes
        providers:
          llm: {kind: mock}
          image: {kind: agnes, base_url: "http://agnes.local"}
          video: {kind: agnes, base_url: "http://agnes.local"}
        """,
        encoding="utf-8",
    )
    return Project.load(tmp_path)


# --- 1. detection ------------------------------------------------------------


def test_agnes_sain_est_detecte(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"ok": true, "status": "healthy"}'

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Response())
    assert cli._agnes_health("http://agnes.local") == (True, "sain")


def test_agnes_absent_est_detecte(monkeypatch):
    def down(*a, **k):
        raise urllib.error.URLError("connexion refusee")

    monkeypatch.setattr("urllib.request.urlopen", down)
    ok, detail = cli._agnes_health("http://agnes.local")
    assert ok is False
    assert "connexion refusee" in detail


# --- 2. decision humaine -----------------------------------------------------


def test_pollinations_explicite_ne_demande_rien():
    called = []
    assert cli._fallback_allowed("pollinations", "down", ask=lambda q: called.append(q)) is True
    assert called == []


def test_fail_ne_bascule_jamais():
    called = []
    assert cli._fallback_allowed("fail", "down", ask=lambda q: called.append(q)) is False
    assert called == []


def test_ask_hors_tty_ne_bascule_pas_sans_humain():
    called = []
    allowed = cli._fallback_allowed(
        "ask", "down", ask=lambda q: called.append(q) or True, interactive=False
    )
    assert allowed is False
    assert called == []


def test_ask_en_tty_utilise_la_reponse_humaine():
    questions = []
    allowed = cli._fallback_allowed(
        "ask", "down", ask=lambda q: questions.append(q) or True, interactive=True
    )
    assert allowed is True
    assert questions and "Pollinations" in questions[0]


# --- 3. quels adaptateurs le workflow utilise --------------------------------


def test_les_slots_suivent_les_agents_du_workflow():
    registry = load_registry(STUDIO_ROOT)

    assert cli._workflow_slots(registry, "studio:workflow/m2-keyframes") == {"image", "video"}
    assert cli._workflow_slots(registry, "studio:workflow/m1-scene") == {"image"}
    assert cli._workflow_slots(registry, "studio:workflow/m0-conversation") == set()


# --- 4. construction des adaptateurs ----------------------------------------


def test_agnes_indisponible_et_repli_accepte_bascule_pollinations(tmp_path, monkeypatch):
    project = agnes_project(tmp_path)
    monkeypatch.setattr(cli, "_agnes_health", lambda *a, **k: (False, "refuse"))

    runtime = cli._resolve_runtime_providers(
        args(fallback="pollinations"), project, {"image", "video"}
    )

    assert runtime.fallback is True
    assert isinstance(runtime.image, PollinationsImageProvider)
    assert runtime.image.model == "flux"
    assert runtime.image.ref_model == "gptimage"
    assert runtime.image.strict_refs is True
    assert isinstance(runtime.video, PollinationsVideoProvider)
    assert runtime.video.model == "wan-fast"


def test_agnes_indisponible_sans_accord_est_une_erreur_actionnable(tmp_path, monkeypatch):
    project = agnes_project(tmp_path)
    monkeypatch.setattr(cli, "_agnes_health", lambda *a, **k: (False, "refuse"))

    with pytest.raises(StudioError, match="demarrer Agnes.*--fallback pollinations"):
        cli._resolve_runtime_providers(args(fallback="fail"), project, {"image", "video"})


def test_agnes_sain_reste_le_provider_choisi(tmp_path, monkeypatch):
    project = agnes_project(tmp_path)
    monkeypatch.setattr(cli, "_agnes_health", lambda *a, **k: (True, "sain"))

    runtime = cli._resolve_runtime_providers(args(), project, {"image", "video"})

    assert runtime.fallback is False
    assert isinstance(runtime.image, AgnesImageProvider)
    assert isinstance(runtime.video, AgnesVideoProvider)


def test_parse_feedback_items_cible_un_item():
    assert cli._parse_feedback_items(
        ["gate_videos#1=garder le masque beige", "gate_videos#3=plus large"]
    ) == {"gate_videos": {1: "garder le masque beige", 3: "plus large"}}


def test_feedback_global_et_cible_melange_refuse():
    with pytest.raises(StudioError, match="feedback"):
        cli._merge_feedback(
            {"gate_videos": "correction globale"},
            {"gate_videos": {1: "correction ciblee"}},
        )


def test_un_workflow_sans_video_ne_sonde_pas_agnes_video(tmp_path, monkeypatch):
    project = agnes_project(tmp_path)
    calls = []
    monkeypatch.setattr(cli, "_agnes_health", lambda *a, **k: calls.append(1) or (True, "sain"))

    cli._resolve_runtime_providers(args(), project, {"image"})

    assert calls == [1]


# --- 5. Agnes sature en cours de run ----------------------------------------


class SaturatedAgnesVideo:
    """Le serveur repond, puis la file video est pleine au moment de payer."""

    def estimate(self, duration):
        return 0.0

    async def generate(self, **kwargs):
        raise RuntimeError("agnes video: file d'attente pleine (503 video_queue_full)")


def test_la_saturation_video_propose_le_repli_et_reprend_les_keyframes(tmp_path, monkeypatch):
    registry = load_registry(STUDIO_ROOT)
    store = ArtifactStore(tmp_path / "artifacts")
    image = asyncio.run(
        MockImageProvider().generate(agent_id="m1", model="mock", system="", prompt="plan")
    )
    ref = store.put(image)
    store = ArtifactStore(tmp_path / "artifacts")
    plan = {
        "image": store.get(ref).ref(),
        "prompt": "Plan : la lande, la nuit.",
        "motion": "travelling avant",
        "duration": 5.0,
    }

    first = cli.RuntimeProviders(
        llm=MockProvider(),
        image=MockImageProvider(),
        video=SaturatedAgnesVideo(),
        names={"llm": "mock", "image": "mock", "video": "agnes"},
        agnes_slots={"video"},
        fallback=False,
    )
    fallback_video = MockVideoProvider()

    def resolve(_args, _project, _slots, **kwargs):
        assert kwargs.get("force_fallback") is True
        return cli.RuntimeProviders(
            llm=MockProvider(),
            image=MockImageProvider(),
            video=fallback_video,
            names={"llm": "mock", "image": "mock", "video": "pollinations"},
            agnes_slots={"video"},
            fallback=True,
        )

    monkeypatch.setattr(cli, "_resolve_runtime_providers", resolve)
    monkeypatch.setattr(cli, "_fallback_allowed", lambda *a, **k: True)

    result, _ = cli._run_with_fallback(
        registry,
        first,
        None,
        tmp_path,
        "studio:workflow/m2-video",
        {"plans": [plan]},
        {"gate_storyboard": "continue", "gate_videos": "continue", "gate_montage": "continue"},
        {},
        "ask",
    )

    assert result.status == "completed", result.error
    assert fallback_video.calls == 1
