import logging

import httpx
import openai
import pytest

from re_ass.paper_summariser.providers.api import OpenAIAPI, OpenAICompatibleAPI


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
        "max_retries": 0,
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


def test_openai_compatible_logs_finish_reason(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    monkeypatch.setattr("openai.OpenAI", lambda **kwargs: FakeOpenAIClient(**kwargs))
    provider = OpenAICompatibleAPI({"model": "local-model", "base_url": "http://127.0.0.1:1234/v1"})

    with caplog.at_level(logging.INFO, logger="re_ass.paper_summariser.providers.api"):
        provider.process_document("", False, "system prompt", "user prompt", max_tokens=4096)

    assert any("finish_reason=stop" in record.message for record in caplog.records)


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


def test_openai_compatible_loads_api_key_from_env_file(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients: list[FakeOpenAIClient] = []
    env_file = tmp_path / ".env.llm"
    env_file.write_text(
        "export OTHER_KEY=ignored\n"
        "export LOCAL_LLM_API_KEY='env-file-key'\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("LOCAL_LLM_API_KEY", raising=False)

    def fake_openai_client(**kwargs):
        client = FakeOpenAIClient(**kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr("openai.OpenAI", fake_openai_client)

    OpenAICompatibleAPI(
        {
            "model": "local-model",
            "base_url": "http://127.0.0.1:1234/v1",
            "api_key_env": "LOCAL_LLM_API_KEY",
            "env_file": str(env_file),
        }
    )

    assert clients[0].kwargs["api_key"] == "env-file-key"
    assert "LOCAL_LLM_API_KEY" not in __import__("os").environ


def test_openai_compatible_fails_fast_when_configured_api_key_is_missing(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOCAL_LLM_API_KEY", raising=False)
    monkeypatch.setattr("openai.OpenAI", lambda **kwargs: FakeOpenAIClient(**kwargs))

    with pytest.raises(ValueError, match=r"LOCAL_LLM_API_KEY"):
        OpenAICompatibleAPI(
            {
                "model": "local-model",
                "base_url": "http://127.0.0.1:1234/v1",
                "api_key_env": "LOCAL_LLM_API_KEY",
                "env_file": str(tmp_path / "missing.env"),
            }
        )


def _bad_request(param: str, message: str) -> openai.BadRequestError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(400, request=request)
    body = {"message": message, "type": "invalid_request_error", "param": param, "code": "unsupported_value"}
    return openai.BadRequestError(message, response=response, body=body)


class ScriptedCreate:
    """Records each call and raises queued errors before returning ``result``."""

    def __init__(self, result, errors=()) -> None:
        self.result = result
        self.errors = list(errors)
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.errors:
            raise self.errors.pop(0)
        return self.result


def _chat_result(text: str = "chat output"):
    message = type("Message", (), {"content": text})
    choice = type("Choice", (), {"message": message, "finish_reason": "stop"})
    return type("Response", (), {"choices": [choice]})


def _responses_result(text: str = "pdf output"):
    # Reasoning models emit a reasoning item (no text) before the message; the SDK's
    # ``output_text`` joins the message text.
    reasoning = type("Reasoning", (), {"type": "reasoning", "content": []})
    return type("Response", (), {"output": [reasoning], "output_text": text})


def _make_openai_provider(monkeypatch: pytest.MonkeyPatch, *, chat_create=None, responses_create=None) -> OpenAIAPI:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def fake_openai_client(**kwargs):
        completions = type("Completions", (), {"create": staticmethod(chat_create)})
        chat = type("Chat", (), {"completions": completions})
        responses = type("Responses", (), {"create": staticmethod(responses_create)})
        return type("Client", (), {"chat": chat, "responses": responses})

    monkeypatch.setattr("openai.OpenAI", fake_openai_client)
    return OpenAIAPI({"model": "gpt-test", "temperature": 0.2})


def test_openai_chat_sends_max_completion_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    create = ScriptedCreate(_chat_result())
    provider = _make_openai_provider(monkeypatch, chat_create=create)

    output = provider.process_document("", False, "system prompt", "user prompt", max_tokens=4096, temperature=0.0)

    assert output == "chat output"
    assert create.calls == [
        {
            "model": "gpt-test",
            "messages": [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "user prompt"},
            ],
            "max_completion_tokens": 4096,
            "temperature": 0.0,
        }
    ]


def test_openai_chat_drops_rejected_temperature_and_remembers(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    error = _bad_request(
        "temperature",
        "Unsupported value: 'temperature' does not support 0.0 with this model. "
        "Only the default (1) value is supported.",
    )
    create = ScriptedCreate(_chat_result(), errors=[error])
    provider = _make_openai_provider(monkeypatch, chat_create=create)

    with caplog.at_level(logging.WARNING):
        first = provider.process_document("", False, "system", "user", max_tokens=100, temperature=0.0)
        second = provider.process_document("", False, "system", "user", max_tokens=100, temperature=0.0)

    assert first == second == "chat output"
    assert [("temperature" in call) for call in create.calls] == [True, False, False]
    assert all(call["max_completion_tokens"] == 100 for call in create.calls)
    warnings = [record for record in caplog.records if "rejected temperature" in record.getMessage()]
    assert len(warnings) == 1


def test_openai_chat_reraises_other_bad_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _bad_request("max_tokens", "Unsupported parameter: 'max_tokens' is not supported with this model.")
    create = ScriptedCreate(_chat_result(), errors=[error])
    provider = _make_openai_provider(monkeypatch, chat_create=create)

    with pytest.raises(openai.BadRequestError):
        provider.process_document("", False, "system", "user", max_tokens=100, temperature=0.0)

    assert len(create.calls) == 1


def test_openai_pdf_uses_responses_api_with_temperature_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _bad_request("temperature", "Unsupported parameter: 'temperature' is not supported with this model.")
    create = ScriptedCreate(_responses_result(), errors=[error])
    provider = _make_openai_provider(monkeypatch, responses_create=create)

    output = provider.process_document(b"%PDF-1.4", True, "system", "user", max_tokens=2048)

    assert output == "pdf output"
    assert len(create.calls) == 2
    first, second = create.calls
    assert first["temperature"] == 0.2
    assert first["max_output_tokens"] == 2048
    assert "max_tokens" not in first
    assert first["input"][1]["content"][1]["filename"] == "document.pdf"
    assert "temperature" not in second
    assert {key: value for key, value in first.items() if key != "temperature"} == second
