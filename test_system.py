#!/usr/bin/env python3
"""Tests rapides du système ZtudioZ."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

def test_config():
    from pipeline.config import load_config, get_service_config, get_bible_config
    cfg = load_config()
    assert "services" in cfg, "Config must have services"
    assert "bible" in cfg, "Config must have bible"
    llm = get_service_config("llm")
    assert llm.get("model"), "LLM must have a model"
    bible = get_bible_config()
    assert bible.get("path") or bible.get("server"), "Bible must have a path or server"
    print("  ✓ Config OK")

def test_bible_entity():
    from pipeline.models.bible import BibleDB, Entity, EntityVersion
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        bible = BibleDB(tmp)
        entity = Entity(
            name="Test",
            slug="test",
            type="personnage",
            active_version="v1",
            versions={"v1": EntityVersion(seed=42, mix_alias="Test", attributs=["Attr1", "Attr2"], prompt_image="test")},
        )
        bible.save_entity(entity)
        loaded = bible.get_entity("test")
        assert loaded is not None, "Entity should be loadable"
        assert loaded.name == "Test", f"Expected 'Test', got {loaded.name}"
        assert loaded.get_seed() == 42
        assert len(loaded.get_active().attributs) == 2
        loaded.switch_version("v1")  # no-op but should not crash
        entities = bible.list_entities()
        assert len(entities) == 1, f"Expected 1 entity, got {len(entities)}"
    print("  ✓ Bible YAML CRUD OK")

def test_bible_multiple_versions():
    from pipeline.models.bible import BibleDB, Entity, EntityVersion
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        bible = BibleDB(tmp)
        entity = Entity(
            name="Multi",
            slug="multi",
            type="personnage",
            active_version="v1",
            versions={
                "v1": EntityVersion(seed=1, mix_alias="A", attributs=["v1a"], prompt_image="v1"),
                "v2": EntityVersion(seed=2, mix_alias="B", attributs=["v2a", "v2b"], prompt_image="v2"),
            },
        )
        bible.save_entity(entity)
        assert bible.get_active_version("multi") == "v1"
        bible.set_active_version("multi", "v2")
        assert bible.get_active_version("multi") == "v2"
        loaded = bible.get_entity("multi")
        assert loaded.get_seed() == 2
        assert len(loaded.get_active().attributs) == 2
    print("  ✓ Bible A/B testing OK")

def test_project_structure():
    from pipeline.orchestrator import ProjectInit
    import tempfile
    import os

    # `create_project` écrit dans `projets/<slug>` sous le cwd : on se place dans un
    # répertoire jetable pour ne jamais toucher aux vrais projets.
    with tempfile.TemporaryDirectory() as tmp:
        old_cwd = os.getcwd()
        os.chdir(tmp)
        try:
            script = Path(tmp) / "script.md"
            script.write_text("# Test")
            result = ProjectInit.create_project("test_proj", str(script))

            assert (result / "bible" / "personnage").exists()
            assert (result / "bible" / "lieu").exists()
            assert (result / "bible" / "objet").exists()
            assert (result / "bible" / "ambiance").exists()
            assert (result / "bible" / "scene").exists()
            assert (result / "preprod" / "storyboard").exists()
            assert (result / "production" / "video").exists()
            assert (result / "production" / "audio").exists()
            assert (result / "postprod" / "montage").exists()
            assert (result / "script").exists()

            script_in_proj = list((result / "script").glob("*.md"))
            assert len(script_in_proj) == 1
        finally:
            os.chdir(old_cwd)
    print("  ✓ Project structure creation OK")

def test_working_bible_50emeRose():
    from pipeline.models.bible import BibleDB
    bible_path = Path(__file__).parent / "projets" / "50emeRose" / "bible"
    if not bible_path.exists():
        print("  ⚠ 50emeRose bible not found, skipping")
        return
    bible = BibleDB(bible_path)
    entities = bible.list_entities()
    assert len(entities) > 0, "Bible should have entities"
    print(f"  ✓ 50emeRose bible: {len(entities)} entities loaded")
    for e in entities[:3]:
        v = e.get_active()
        assert len(v.attributs) >= 3, f"{e.slug} should have ≥3 attributes"
        assert v.prompt_image, f"{e.slug} should have a prompt_image"

def test_tool_calling_model():
    """Test que le modèle s'importe et se crée sans erreur."""
    from pipeline.services.llm import ToolCallingModel, _convert_tool_calls_in_response
    # Juste vérifier que les classes existent
    assert ToolCallingModel is not None
    assert _convert_tool_calls_in_response is not None
    print("  ✓ ToolCallingModel imports OK")

if __name__ == "__main__":
    print("ZtudioZ — System Tests")
    print("=" * 40)
    test_config()
    test_bible_entity()
    test_bible_multiple_versions()
    test_project_structure()
    test_working_bible_50emeRose()
    test_tool_calling_model()
    print("=" * 40)
    print("All tests passed ✓")
