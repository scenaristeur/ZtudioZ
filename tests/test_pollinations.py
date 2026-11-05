"""Adaptateur Pollinations : requete GET deterministe, type de media reel, 0 reseau en test."""

import asyncio

import pytest

from studio.kernel.artifacts import Binary
from studio.tools import images
from studio.tools.images import PollinationsImageProvider

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_BYTES = b"\xff\xd8\xff" + b"\x00" * 32


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """Aucun test ne dort pour de vrai ; les tests de rythme remplacent ce stub."""
    monkeypatch.setattr(images, "_sleep", lambda seconds: None)


@pytest.fixture
def recorder(monkeypatch):
    calls: list[dict] = []

    def fake_get(url: str, token: str | None = None, timeout: float = 120.0):
        calls.append({"url": url, "token": token})
        import hashlib

        # les octets dependent de l'URL : changement de graine -> changement d'image
        return JPEG_BYTES + hashlib.sha256(url.encode()).digest(), ""

    monkeypatch.setattr(images, "_http_get", fake_get)
    return calls


def run(provider, prompt="phare breton au crepuscule", reference=None, references=None):
    return asyncio.run(
        provider.generate(
            agent_id="a",
            model="flux",
            system="",
            prompt=prompt,
            reference=reference,
            references=references,
        )
    )


def test_url_carries_prompt_size_and_nologo(recorder):
    provider = PollinationsImageProvider(width=256, height=256)

    binary = run(provider)

    url = recorder[0]["url"]
    assert "image.pollinations.ai/prompt/" in url
    assert "phare" in url and "nologo=true" in url and "width=256" in url
    assert binary.data[:3] == b"\xff\xd8\xff"


def test_content_type_honored_when_declared(monkeypatch):
    monkeypatch.setattr(images, "_http_get", lambda url, token=None, timeout=120.0: (PNG_BYTES, "image/png"))
    assert run(PollinationsImageProvider()).media_type == "image/png"


def test_media_type_sniffed_from_magic_bytes(recorder):
    assert run(PollinationsImageProvider()).media_type == "image/jpeg"


def test_seed_is_deterministic_per_prompt(recorder):
    provider = PollinationsImageProvider()

    first = run(provider, prompt="plan A")
    second = run(provider, prompt="plan A")
    third = run(provider, prompt="plan B")

    assert first.hash == second.hash
    seed1 = recorder[0]["url"].split("seed=")[1].split("&")[0]
    seed2 = recorder[1]["url"].split("seed=")[1].split("&")[0]
    seed_b = recorder[2]["url"].split("seed=")[1].split("&")[0]
    assert seed1 == seed2  # la graine suit le prompt, pas l'horloge
    assert seed1 != seed_b


def test_une_reference_utilise_gen_et_le_modele_image(monkeypatch, recorder):
    monkeypatch.setattr("studio.tools.public.upload_public", lambda data, name: "https://tmpfiles.org/dl/ref.png")
    provider = PollinationsImageProvider(ref_model="gptimage")

    binary = run(provider, prompt="plan", reference=Binary(PNG_BYTES, "image/png"))

    url = recorder[0]["url"]
    assert "gen.pollinations.ai/image/" in url
    assert "model=gptimage" in url
    assert "image=https%3A%2F%2Ftmpfiles.org%2Fdl%2Fref.png" in url
    assert binary.data[:3] == b"\xff\xd8\xff"


def test_plusieurs_references_passent_par_le_post_json(monkeypatch):
    import base64

    calls = []

    def fake_post(url, payload, token=None, timeout=300.0):
        calls.append({"url": url, "payload": payload, "token": token})
        return {"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode()}]}

    monkeypatch.setattr("studio.tools.public.upload_public", lambda data, name: f"https://tmpfiles.org/dl/{name}")
    monkeypatch.setattr(images, "_http_post_json", fake_post)
    provider = PollinationsImageProvider(ref_model="gptimage", multi_ref_model="gptimage")

    binary = run(
        provider,
        prompt="plan",
        references=[Binary(PNG_BYTES, "image/png"), Binary(JPEG_BYTES, "image/jpeg")],
    )

    assert binary.media_type == "image/png"
    assert calls[0]["url"].endswith("/v1/images/generations")
    assert calls[0]["payload"]["model"] == "gptimage"
    assert len(calls[0]["payload"]["image"]) == 2


def test_strict_refs_refuse_un_modele_sans_reference(monkeypatch):
    monkeypatch.setattr("studio.tools.public.upload_public", lambda data, name: "https://tmpfiles.org/dl/ref.png")
    provider = PollinationsImageProvider(ref_model="flux", strict_refs=True)

    with pytest.raises(RuntimeError, match="strict_refs"):
        run(provider, prompt="plan", reference=Binary(PNG_BYTES, "image/png"))


def test_api_key_is_forwarded_when_present(monkeypatch, recorder):
    monkeypatch.setenv("STUDIO_TEST_POLLS_KEY", "cle-secrete")
    provider = PollinationsImageProvider(api_key_env="STUDIO_TEST_POLLS_KEY")

    run(provider)

    assert recorder[0]["token"] == "cle-secrete"


def test_http_failure_is_a_clear_error(monkeypatch):
    def broken(url, token=None, timeout=120.0):
        raise OSError("reseau indisponible")

    monkeypatch.setattr(images, "_http_get", broken)

    with pytest.raises(RuntimeError, match="pollinations"):
        run(PollinationsImageProvider())


def test_free_limit_402_waits_then_retries(monkeypatch):
    import io
    import urllib.error

    sleeps: list[float] = []
    monkeypatch.setattr(images, "_sleep", lambda seconds: sleeps.append(seconds))
    calls: list[str] = []

    def limited(url, token=None, timeout=120.0):
        calls.append(url)
        if len(calls) < 3:
            body = io.BytesIO(b'{"error":{"code":"FREE_LIMIT","message":"Retry after 16s"}}')
            raise urllib.error.HTTPError(url, 402, "Payment Required", {}, body)
        return JPEG_BYTES, "image/jpeg"

    monkeypatch.setattr(images, "_http_get", limited)

    binary = run(PollinationsImageProvider())

    assert binary.media_type == "image/jpeg"
    assert sleeps == [16.0, 16.0]  # le delai annonce par l'API est respecte, pas invente


def test_free_limit_gives_up_after_max_retries(monkeypatch):
    import io
    import urllib.error

    monkeypatch.setattr(images, "_sleep", lambda seconds: None)

    def always_limited(url, token=None, timeout=120.0):
        body = io.BytesIO(b'{"error":{"code":"FREE_LIMIT","message":"Retry after 16s"}}')
        raise urllib.error.HTTPError(url, 402, "Payment Required", {}, body)

    monkeypatch.setattr(images, "_http_get", always_limited)

    with pytest.raises(RuntimeError, match="pollinations"):
        run(PollinationsImageProvider())


def test_transient_503_is_retried_with_backoff(monkeypatch):
    import io
    import urllib.error

    sleeps: list[float] = []
    monkeypatch.setattr(images, "_sleep", lambda seconds: sleeps.append(seconds))
    calls: list[str] = []

    def flaky(url, token=None, timeout=120.0):
        calls.append(url)
        if len(calls) == 1:
            body = io.BytesIO(b'{"error":{"code":"SERVICE_UNAVAILABLE","message":"at limit. Retry after 10s"}}')
            raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, body)
        return JPEG_BYTES, "image/jpeg"

    monkeypatch.setattr(images, "_http_get", flaky)

    binary = run(PollinationsImageProvider())

    assert binary.media_type == "image/jpeg"
    assert sleeps == [10.0]  # n'importe quel delai annonce est honore, pas seulement FREE_LIMIT


def test_402_service_unavailable_body_is_not_fatal(monkeypatch):
    import io
    import urllib.error

    sleeps: list[float] = []
    monkeypatch.setattr(images, "_sleep", sleeps.append)
    calls: list[str] = []

    def flaky(url, token=None, timeout=120.0):
        calls.append(url)
        if len(calls) < 2:
            body = io.BytesIO(b'{"error":{"code":"SERVICE_UNAVAILABLE","message":"Retry after 20s"}}')
            raise urllib.error.HTTPError(url, 402, "Payment Required", {}, body)
        return JPEG_BYTES, "image/jpeg"

    monkeypatch.setattr(images, "_http_get", flaky)

    assert run(PollinationsImageProvider()).media_type == "image/jpeg"
    assert sleeps == [20.0]


def test_transient_network_error_is_retried_then_raised(monkeypatch):
    monkeypatch.setattr(images, "_sleep", lambda seconds: None)

    def down(url, token=None, timeout=120.0):
        import urllib.error

        raise urllib.error.URLError("temporairement indisponible")

    monkeypatch.setattr(images, "_http_get", down)

    with pytest.raises(RuntimeError, match="pollinations"):
        run(PollinationsImageProvider())


def test_generations_are_spaced_by_the_free_interval(recorder, monkeypatch):
    # la limit gratuite est 1 image / 45 s / IP : le provider doit se sérier lui-meme
    # plutot que laisser N retries se battre sur le meme quota
    sleeps: list[float] = []
    monkeypatch.setattr(images, "_sleep", sleeps.append)
    provider = PollinationsImageProvider(min_interval=45.0)

    run(provider, prompt="plan A")
    assert sleeps == []  # le premier appel ne fait pas attendre qui que ce soit
    run(provider, prompt="plan B")
    assert len(sleeps) == 1
    assert sleeps[0] > 40.0  # le second attend la fin de la fenetre annoncee
