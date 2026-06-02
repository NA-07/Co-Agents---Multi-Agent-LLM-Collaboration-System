import os

import pytest

from src.utils import llm_provider


def test_should_use_google_vertex_with_project(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-project")
    assert llm_provider._should_use_google_vertex({}) is True


def test_should_use_google_vertex_with_vertex_base_url():
    assert llm_provider._should_use_google_vertex(
        {"base_url": "https://us-central1-aiplatform.googleapis.com/v1/projects/demo/locations/us-central1/endpoints/openapi"}
    ) is True


def test_build_google_vertex_base_url_global():
    assert (
        llm_provider._build_google_vertex_base_url("demo-project", "global")
        == "https://aiplatform.googleapis.com/v1/projects/demo-project/locations/global/endpoints/openapi"
    )


def test_normalize_google_vertex_model_name_alias():
    assert (
        llm_provider._normalize_google_vertex_model_name("gemini-2.0-flash")
        == "google/gemini-2.0-flash-001"
    )


def test_vertex_rejects_developer_api_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-project")
    with pytest.raises(ValueError, match="does not accept a Gemini Developer API key"):
        llm_provider.get_llm_model(
            provider="google",
            model_name="gemini-2.0-flash",
            api_key="AIzaFakeDeveloperKey",
        )


def test_vertex_uses_chatopenai(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    def fake_token(_explicit_token=None):
        return "ya29.test-token", "demo-project"

    monkeypatch.setattr(llm_provider, "_get_google_vertex_access_token", fake_token)

    llm = llm_provider.get_llm_model(
        provider="google",
        model_name="gemini-2.0-flash",
        temperature=0.1,
    )

    assert llm.__class__.__name__ == "ChatOpenAI"
    assert llm.model_name == "google/gemini-2.0-flash-001"
    assert str(llm.openai_api_base) == "https://aiplatform.googleapis.com/v1/projects/demo-project/locations/global/endpoints/openapi"


def test_google_developer_api_stays_default(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "AIzaDeveloperKey")

    llm = llm_provider.get_llm_model(
        provider="google",
        model_name="gemini-2.0-flash",
    )

    assert llm.__class__.__name__ == "RateLimitAwareChatGoogle"