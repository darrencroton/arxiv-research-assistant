import logging

import pytest

from re_ass.paper_summariser.providers.api import OpenAICompatibleAPI


class FakeModelsResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.payload


class FakeOpenAIClient:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.chat = FakeChat()


class FakeChat:
    def __init__(self) -> None:
        self.completions = FakeCompletions()


class FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = type("Message", (), {"content": "model output"})
        choice = type("Choice", (), {"message": message, "finish_reason": "stop"})
        return type("Response", (), {"choices": [choice]})


def test_openai_compatible_requires_base_url() -> None:
    with pytest.raises(ValueError, match=r"base_url"):
        OpenAICompatibleAPI({"model": "local-model"})


def test_openai_compatible_requires_model() -> None:
    with pytest.raises(ValueError, match=r"model"):
        OpenAICompatibleAPI({"base_url": "http://127.0.0.1:1234/v1"})


def test_openai_compatible_processes_chat_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    clients: list[FakeOpenAIClient] = []

    def fake_openai_client(**kwargs):
        client = FakeOpenAIClient(**kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr("openai.OpenAI", fake_openai_client)

    provider = OpenAICompatibleAPI(
        {
            "model": "local-model",
            "base_url": "http://127.0.0.1:1234/v1",
            "api_key_env": "",
            "temperature": 0.15,
            "timeout": 3600,
        }
    )

    output = provider.process_document("", False, "system prompt", "user prompt", max_tokens=4096)

    assert output == "model output"
    assert clients[0].kwargs == {
        "api_key": "not-needed",
        "base_url": "http://127.0.0.1:1234/v1",
        "timeout": 3600.0,
    }
    assert clients[0].chat.completions.calls == [
        {
            "model": "local-model",
            "messages": [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "user prompt"},
            ],
            "temperature": 0.15,
            "max_tokens": 4096,
        }
    ]


def test_openai_compatible_warns_when_response_is_truncated(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    def fake_openai_client(**kwargs):
        client = FakeOpenAIClient(**kwargs)
        message = type("Message", (), {"content": "truncated output"})
        choice = type("Choice", (), {"message": message, "finish_reason": "length"})
        client.chat.completions.create = lambda **kw: type("Response", (), {"choices": [choice]})()
        return client

    monkeypatch.setattr("openai.OpenAI", fake_openai_client)

    provider = OpenAICompatibleAPI({"model": "local-model", "base_url": "http://127.0.0.1:1234/v1"})

    with caplog.at_level(logging.WARNING, logger="re_ass.paper_summariser.providers.api"):
        output = provider.process_document("", False, "system", "user", max_tokens=512)

    assert output == "truncated output"
    assert any("finish_reason=length" in record.message and "512" in record.message for record in caplog.records)


def test_openai_compatible_readiness_checks_models_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def fake_get(url, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeModelsResponse({"data": [{"id": "local-model"}]})

    monkeypatch.setenv("LOCAL_LLM_API_KEY", "test-key")
    monkeypatch.setattr("openai.OpenAI", lambda **kwargs: FakeOpenAIClient(**kwargs))
    monkeypatch.setattr("re_ass.paper_summariser.providers.api.requests.get", fake_get)

    provider = OpenAICompatibleAPI(
        {
            "model": "local-model",
            "base_url": "http://127.0.0.1:1234/v1/",
            "api_key_env": "LOCAL_LLM_API_KEY",
            "timeout": 3600,
        }
    )

    provider.validate_runtime_ready()

    assert captured == {
        "url": "http://127.0.0.1:1234/v1/models",
        "headers": {"Authorization": "Bearer test-key"},
        "timeout": 15.0,
    }
