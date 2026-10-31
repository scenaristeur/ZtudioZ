"""D1 — artefacts binaires : les images vivent en fichiers bruts, adresses par contenu."""

from studio.kernel.artifacts import ArtifactStore, Binary, canonical, digest

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
JPEG = b"\xff\xd8\xff" + b"\x01" * 12


def test_binary_is_stored_as_raw_file_and_read_back(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    image = Binary(PNG, "image/png")

    key = store.put(image)

    raw_file = store.root / f"{key}.png"
    assert raw_file.exists()
    assert raw_file.read_bytes() == PNG
    assert not (store.root / f"{key}.json").exists()

    back = store.get(key)
    assert isinstance(back, Binary)
    assert back.data == PNG
    assert back.media_type == "image/png"


def test_identical_bytes_are_deduplicated(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")

    first = store.put(Binary(PNG, "image/png"))
    second = store.put(Binary(PNG, "image/png"))

    assert first == second
    assert len(list(store.root.glob("*.png"))) == 1


def test_binary_hash_is_over_raw_bytes(tmp_path):
    import hashlib

    image = Binary(PNG, "image/png")

    assert image.hash == hashlib.sha256(PNG).hexdigest()[:16]
    assert digest(image) == image.hash
    assert digest(Binary(PNG, "image/jpeg")) == digest(image)  # le contenu prime


def test_binary_can_be_nested_in_a_json_value(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    image = Binary(PNG, "image/png")

    key = store.put({"view": image, "notes": "ok"})

    assert key == store.put({"view": Binary(PNG, "image/png"), "notes": "ok"})
    assert image.hash in canonical({"view": image})  # serialise par reference, pas en base64
    store.get(key)


def test_media_type_selects_the_extension(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")

    key = store.put(Binary(JPEG, "image/jpeg"))

    assert (store.root / f"{key}.jpg").exists()
