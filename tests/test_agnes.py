"""Providers Agnes locaux : image->image et vidéo keyframes, sans réseau en test."""

import asyncio
import json

import pytest

from studio.kernel.artifacts import Binary
from studio.project import Project
from studio.tools import agnes
from studio.tools.agnes import AgnesImageProvider, AgnesVideoProvider

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff" + b"\x00" * 32
MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 32


def image_ref(data=PNG, media_type="image/png"):
    return Binary(data, media_type)


def run_image(provider, prompt="desert de sel", reference=None, references=None):
    return asyncio.run(
        provider.generate(
            agent_id="studio:agent/image-creator",
            model="agnes",
            system="",
            prompt=prompt,
            reference=reference,
            references=references,
        )
    )


def run_video(provider, prompt="travelling", reference=None, references=None, duration=5):
    return asyncio.run(
        provider.generate(
            agent_id="studio:agent/video-maker",
            model="agnes",
            system="",
            prompt=prompt,
            reference=reference,
            references=references,
            duration=duration,
        )
    )


@pytest.fixture
def local_api(monkeypatch):
    calls = {"posts": [], "gets": [], "json": []}

    def fake_post(url, fields, files=None, timeout=300.0):
        calls["posts"].append({"url": url, "fields": fields, "files": files or {}})
        return {"task_id": "task-1"}

    def fake_bytes(url, timeout=300.0):
        calls["gets"].append(url)
        if "/api/video/" in url:
            return MP4, "video/mp4"
        return PNG, "image/png"

    def fake_json(url, timeout=60.0):
        calls["json"].append(url)
        return {"status": "completed"}

    monkeypatch.setattr(agnes, "_post_multipart", fake_post)
    monkeypatch.setattr(agnes, "_get_bytes", fake_bytes)
    monkeypatch.setattr(agnes, "_get_json", fake_json)
    return calls


def test_image_texte_vers_image_sans_reference(local_api):
    binary = run_image(AgnesImageProvider(base_url="http://agnes.local"))

    post = local_api["posts"][0]
    assert post["url"] == "http://agnes.local/api/image/generate"
    assert post["fields"]["prompt"] == "desert de sel"
    assert "reference_image" not in post["files"]
    assert binary.media_type == "image/png"
    assert binary.data == PNG


def test_image_vers_image_envoie_la_reference(local_api):
    run_image(AgnesImageProvider(), reference=image_ref())

    files = local_api["posts"][0]["files"]
    assert "reference_image" in files
    filename, data, media_type = files["reference_image"]
    assert data == PNG and media_type == "image/png"
    assert filename.endswith(".png")


def test_plusieurs_references_passent_par_la_composition(monkeypatch):
    calls = {}

    def fake_json(url, body, headers, timeout=300.0):
        calls.update(url=url, body=body, headers=headers)
        return {"data": [{"url": "https://cdn.local/compose.png"}]}

    monkeypatch.setenv("AGNES_API_KEY", "secret")
    monkeypatch.setattr(agnes, "_post_json", fake_json)
    monkeypatch.setattr(
        agnes, "_get_bytes", lambda url, timeout=300.0: (JPEG, "image/jpeg")
    )

    binary = run_image(
        AgnesImageProvider(size="1024x1024"),
        references=[image_ref(), image_ref(JPEG, "image/jpeg")],
    )

    assert calls["url"].endswith("/v1/images/generations")
    assert calls["headers"]["Authorization"] == "Bearer secret"
    images = calls["body"]["extra_body"]["image"]
    assert len(images) == 2
    assert images[0].startswith("data:image/png;base64,")
    assert calls["body"]["size"] == "1024x1024"
    assert binary.media_type == "image/jpeg"


def test_la_composition_sans_cle_echoue(monkeypatch):
    monkeypatch.delenv("AGNES_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="AGNES_API_KEY"):
        run_image(AgnesImageProvider(), references=[image_ref(), image_ref()])


def test_video_image_vers_video(local_api):
    binary = run_video(AgnesVideoProvider(dev=True), reference=image_ref())

    post = local_api["posts"][0]
    assert post["url"].endswith("/api/tasks/simple")
    assert post["fields"]["mode"] == "i2v"
    assert post["fields"]["duration"] == "5"
    assert post["fields"]["video_width"] == "480"
    assert "reference_image" in post["files"]
    assert "end_frame_image" not in post["files"]
    assert local_api["json"][0].endswith("/api/tasks/task-1")
    assert local_api["gets"][0].endswith("/api/video/task-1")
    assert binary.media_type == "video/mp4"
    assert binary.data == MP4


def test_video_keyframes_envoie_deux_images(local_api):
    run_video(
        AgnesVideoProvider(dev=False, width=1152, height=768, duration=10),
        references=[image_ref(), image_ref(JPEG, "image/jpeg")],
        duration=10,
    )

    post = local_api["posts"][0]
    assert post["fields"]["mode"] == "keyframes"
    assert post["fields"]["duration"] == "10"
    assert post["fields"]["video_width"] == "1152"
    assert post["files"]["reference_image"][0].startswith("start")
    assert post["files"]["end_frame_image"][0].startswith("end")


def test_une_tache_video_en_echec_leve_une_erreur(monkeypatch):
    monkeypatch.setattr(
        agnes, "_post_multipart", lambda *a, **k: {"task_id": "task-ko"}
    )
    monkeypatch.setattr(
        agnes,
        "_get_json",
        lambda *a, **k: {"status": "failed", "current_message": "VRAM pleine"},
    )

    with pytest.raises(RuntimeError, match="VRAM pleine"):
        run_video(AgnesVideoProvider(poll_interval=0))


def queue_full_error():
    import io
    import urllib.error

    return urllib.error.HTTPError(
        "http://agnes.local/api/tasks/simple",
        503,
        "Service Unavailable",
        {},
        io.BytesIO(b'{"detail":"video_queue_full"}'),
    )


def test_la_file_video_pleine_est_retentee_puis_reussie(monkeypatch):
    sleeps: list[float] = []
    attempts = {"n": 0}

    def fake_post(url, fields, files=None, timeout=300.0):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise queue_full_error()
        return {"task_id": "task-2"}

    monkeypatch.setattr(agnes, "_post_multipart", fake_post)
    monkeypatch.setattr(agnes, "_get_json", lambda *a, **k: {"status": "completed"})
    monkeypatch.setattr(
        agnes, "_get_bytes", lambda url, timeout=300.0: (MP4, "video/mp4")
    )
    monkeypatch.setattr(agnes, "_sleep", sleeps.append)

    binary = run_video(
        AgnesVideoProvider(max_retries=1, retry_delay=12), reference=image_ref()
    )

    assert attempts["n"] == 2
    assert sleeps == [12]
    assert binary.data == MP4


def test_la_file_video_pleine_finit_en_erreur_explicite(monkeypatch):
    monkeypatch.setattr(
        agnes, "_post_multipart", lambda *a, **k: (_ for _ in ()).throw(queue_full_error())
    )
    monkeypatch.setattr(agnes, "_sleep", lambda seconds: None)

    with pytest.raises(RuntimeError, match="file d'attente pleine.*video_queue_full"):
        run_video(AgnesVideoProvider(max_retries=1), reference=image_ref())


def test_un_projet_peut_declarer_agnes(tmp_path):
    (tmp_path / "studio.yaml").write_text(
        """
        name: agnes
        providers:
          llm: {kind: mock}
          image: {kind: agnes, base_url: "http://agnes.local", size: 1024x1024}
          video: {kind: agnes, base_url: "http://agnes.local", dev: false}
        """,
        encoding="utf-8",
    )

    project = Project.load(tmp_path)

    image = project.build("image")
    video = project.build("video")
    assert isinstance(image, AgnesImageProvider)
    assert isinstance(video, AgnesVideoProvider)
    assert image.base_url == "http://agnes.local"
    assert image.size == "1024x1024"
    assert video.dev is False
