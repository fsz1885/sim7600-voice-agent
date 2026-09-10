import asyncio
import json

import httpx
import pytest

from voice_agent.compatible_provider import CompatibleProvider, configured_provider
from voice_agent.models import State, Task
from voice_agent.providers import ProviderError


@pytest.mark.parametrize("ending", ["ok", "truncated", "disconnect", "auth"])
def test_remote_stream_contract(monkeypatch, ending):
    seen = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"index":0,"delta":{"content":"{\\"response\\":\\"Hello"}}]}\n\n'  # noqa: E501
            assert seen[-1] == "Hello"
            if ending == "disconnect":
                return
            finish = "stop" if ending == "ok" else "length"
            data = {"choices": [{"delta": {"content": '"}'}, "finish_reason": finish}]}
            yield ("data: " + json.dumps(data) + "\n\n").encode()
            yield b'data: {"choices":[],"usage":{"completion_tokens":4}}\n\n'
            yield b"data: [DONE]\n\n"

    def handler(request):
        assert request.url == "http://10.3.71.235:8080/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-only"
        payload = json.loads(request.content)
        assert payload["reasoning_effort"] == "none"
        assert (
            payload["response_format"]["json_schema"]["schema"]["properties"]["updates"]["maxItems"]
            == 0
        )
        if ending == "auth":
            return httpx.Response(401)
        return httpx.Response(200, stream=Stream())

    client = httpx.AsyncClient
    monkeypatch.setattr(
        "voice_agent.compatible_provider.httpx.AsyncClient",
        lambda **kw: client(transport=httpx.MockTransport(handler), **kw),
    )
    provider = CompatibleProvider(
        "sim7600-local", 10, "http://10.3.71.235:8080/v1", api_key="test-only"
    )
    provider.on_preview = seen.append
    task = Task(goal="确认费用", required_fields=["费用"])
    if ending == "ok":
        assert asyncio.run(provider.propose(task, State.for_task(task))) == '{"response":"Hello"}'
        assert provider.calls[-1]["usage"]["completion_tokens"] == 4
    else:
        with pytest.raises(ProviderError):
            asyncio.run(provider.propose(task, State.for_task(task)))
    assert seen[-1] == ""


def test_provider_selection_and_secret_file(monkeypatch, tmp_path):
    key = tmp_path / "key.txt"
    key.write_text("test-only\n", encoding="utf-8")
    monkeypatch.setenv("VOICE_LLM_PROVIDER", "compatible")
    monkeypatch.setenv("LLM_API_KEY_FILE", str(key))
    assert configured_provider().headers == {"Authorization": "Bearer test-only"}
    monkeypatch.setenv("VOICE_LLM_PROVIDER", "ollama")
    assert configured_provider().base_url == "http://127.0.0.1:11434"


def test_health_and_console_use_configured_remote(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    from fastapi.testclient import TestClient

    from voice_agent.console import create_app

    monkeypatch.setenv("VOICE_LLM_PROVIDER", "compatible")
    monkeypatch.setenv("LLM_BASE_URL", "http://10.3.71.235:8080/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-only")
    monkeypatch.delenv("LLM_API_KEY_FILE", raising=False)

    def handler(request):
        assert request.url.path == "/v1/models"
        assert request.headers["authorization"] == "Bearer test-only"
        return httpx.Response(200, json={"data": [{"id": "sim7600-local"}]})

    client = httpx.AsyncClient
    monkeypatch.setattr(
        "voice_agent.console.httpx.AsyncClient",
        lambda **kw: client(transport=httpx.MockTransport(handler), **kw),
    )
    with TestClient(create_app(data_dir=tmp_path)) as browser:
        response = browser.get("/api/health")
        assert response.json()["llm"] is True
        assert "test-only" not in response.text
