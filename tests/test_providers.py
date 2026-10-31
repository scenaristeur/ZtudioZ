"""Choix du modele par les adaptateurs reels : les placeholders mock cedaient la place."""

from studio.tools.llm import OpenAICompatProvider


def make(base_model="openai/gpt-oss-120b"):
    return OpenAICompatProvider(model=base_model, base_url="http://localhost:1/v1")


def test_agent_declaring_a_real_model_keeps_it():
    assert make()._resolve_model("mistralai/Ministral-3-8B-Instruct-2512") == (
        "mistralai/Ministral-3-8B-Instruct-2512"
    )


def test_mock_placeholder_falls_back_to_the_provider_model():
    assert make()._resolve_model("mock/echo") == "openai/gpt-oss-120b"
    assert make()._resolve_model("") == "openai/gpt-oss-120b"
    assert make()._resolve_model("mock-image/png") == "openai/gpt-oss-120b"
