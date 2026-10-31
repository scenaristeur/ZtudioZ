"""D2/D3 — lignee d'artefacts : parents enregistres, dependants transitifs, invalidation avale."""

from studio.kernel.artifacts import Artifact, ArtifactStore, Binary

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def test_put_records_producer_media_and_parents(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")

    parent = store.put({"upstream": 1})
    key = store.put(
        {"shot": "phare"}, parents=(parent,), producer="studio:agent/shot-list", cost=0.2
    )

    art = store.lineage(key)
    assert isinstance(art, Artifact)
    assert art.hash == key
    assert art.parents == (parent,)
    assert art.producer == "studio:agent/shot-list"
    assert art.cost == 0.2
    assert art.media_type == "application/json"


def test_binary_artifact_declares_its_media_type(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")

    key = store.put(Binary(PNG, "image/png"), producer="studio:agent/image-creator")

    assert store.lineage(key).media_type == "image/png"


def test_dependents_are_transitive(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")

    u = store.put({"u": 1})
    a = store.put({"a": 1}, parents=(u,))
    b = store.put({"b": 1}, parents=(a,))
    c = store.put({"c": 1}, parents=(u,))
    d = store.put({"d": 1})

    assert store.dependents(u) == {a, b, c}
    assert store.dependents(a) == {b}
    assert store.dependents(d) == set()


def test_invalidate_clears_downstream_cache_only(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")

    u = store.put({"u": 1})
    a = store.put({"a": 1}, parents=(u,))
    b = store.put({"b": 1}, parents=(a,))
    c = store.put({"c": 1}, parents=(u,))
    store.remember("k-up", u)
    store.remember("k-a", a)
    store.remember("k-b", b)
    store.remember("k-c", c)

    removed = store.invalidate(a)

    assert removed == {a, b}
    assert store.cached("k-a") is None  # l'artefact vise
    assert store.cached("k-b") is None  # son aval direct et transitif
    assert store.cached("k-up") == u  # son amont reste en cache
    assert store.cached("k-c") == c  # un autre branche aussi


def test_lineage_survives_a_new_store_on_same_root(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")

    u = store.put({"u": 1})
    a = store.put({"a": 1}, parents=(u,))

    reopened = ArtifactStore(tmp_path / "artifacts")
    assert reopened.lineage(a).parents == (u,)
    assert reopened.dependents(u) == {a}
    assert reopened.get(a) == {"a": 1}
