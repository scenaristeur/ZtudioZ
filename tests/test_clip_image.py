"""M2 sur de vraies images : une image M1 doit devenir un clip montable.

Le workflow M2 ne savait produire que des clips de couleur pure (`lavfi color`).
Les images de M1 ne pouvaient donc pas entrer dans le montage : la chaine
M0 -> M1 -> M2 s'arretait a la porte du montage.
"""

import asyncio
import json

import pytest

from studio.kernel.artifacts import ArtifactStore, Binary
from studio.kernel.dispatch import ToolContext
from studio.kernel.executor import Executor
from studio.kernel.journal import Journal
from studio.kernel.registry import STUDIO_ROOT, load_registry
from studio.tools import media
from studio.tools.images import MockImageProvider
from studio.tools.llm import MockProvider


def png_ref(tmp_path, name="image"):
    store = ArtifactStore(tmp_path / "artifacts")
    binary = asyncio.run(
        MockImageProvider().generate(agent_id=name, model="mock", system="", prompt=name)
    )
    return store, store.put(binary), store.get(store.put(binary))


def test_une_image_devient_un_clip_mp4(tmp_path):
    store, key, _ = png_ref(tmp_path)
    ctx = ToolContext(store=store, registry=load_registry(STUDIO_ROOT))

    clip = media.clip({"item": {"image": store.get(key).ref(), "duration": 0.4}}, ctx)

    assert isinstance(clip, Binary)
    assert clip.media_type == "video/mp4"
    assert len(clip.data) > 0


def test_le_clip_dune_image_a_la_duree_demandee(tmp_path):
    store, key, _ = png_ref(tmp_path)
    ctx = ToolContext(store=store, registry=load_registry(STUDIO_ROOT))

    clip = media.clip({"item": {"image": store.get(key).ref(), "duration": 0.6}}, ctx)
    written = tmp_path / "clip.mp4"
    written.write_bytes(clip.data)
    report = media.probe({"montage": {"artifact": store.put(Binary(clip.data, "video/mp4"))}}, ctx)

    assert 0.5 <= report["duration"] <= 0.8
    assert report["codec"] == "h264"


def test_un_clip_de_couleur_reste_possible(tmp_path):
    """Regression : la fiche couleur d'origine ne doit pas disparaitre."""
    store = ArtifactStore(tmp_path / "artifacts")
    ctx = ToolContext(store=store, registry=load_registry(STUDIO_ROOT))

    clip = media.clip({"item": {"color": "red", "duration": 0.3}}, ctx)

    assert clip.media_type == "video/mp4"


def test_un_mp4_ne_devient_pas_un_clip_par_erreur(tmp_path):
    """Une reference non image (deja un mp4) est refusee, pas perdue en silence."""
    store = ArtifactStore(tmp_path / "artifacts")
    ctx = ToolContext(store=store, registry=load_registry(STUDIO_ROOT))
    previous = media.clip({"item": {"color": "red", "duration": 0.2}}, ctx)
    key = store.put(previous)

    with pytest.raises(RuntimeError, match="image"):
        media.clip({"item": {"clip": store.get(key).ref(), "duration": 0.3}}, ctx)


def test_m2_monte_les_images_de_m1(tmp_path):
    """Bout en bout : deux images -> deux clips -> un montage sonde."""
    store = ArtifactStore(tmp_path / "artifacts")
    images = []
    for name in ("plan-1", "plan-2"):
        binary = asyncio.run(
            MockImageProvider().generate(agent_id=name, model="mock", system="", prompt=name)
        )
        images.append(store.put(binary))

    executor = Executor(
        registry=load_registry(STUDIO_ROOT),
        provider=MockProvider(),
        store=store,
        journal=Journal(tmp_path / "journal.jsonl"),
    )
    clips = [{"image": store.get(key).ref(), "duration": 0.4} for key in images]
    result = asyncio.run(
        executor.run("studio:workflow/m2-montage", {"clips": clips}, answers={"gate_montage": "continue"})
    )

    assert result.status == "completed", result.error
    assert result.outputs["probe"]["codec"] == "h264"
    assert 0.7 <= result.outputs["probe"]["duration"] <= 0.9
    assert len([e for e in executor.journal.events() if e["type"] == "item_failed"]) == 0
