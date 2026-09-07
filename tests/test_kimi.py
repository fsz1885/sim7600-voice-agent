import asyncio
import json

import httpx
import pytest

from voice_agent import State, Task
from voice_agent.providers import KimiProvider, ProviderError


@pytest.mark.parametrize(
    "status,body,success",
    [
        (200, {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}, True),
        (200, {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}, False),
        (200, {"choices": [{"finish_reason": "stop", "message": {"content": None}}]}, False),
        (200, {"choices": []}, False),
        (200, {}, False),
        (401, {"error": "unauthorized"}, False),
        (403, {"error": "forbidden"}, False),
        (429, {"error": "rate limited"}, False),
        (500, {"error": "unavailable"}, False),
    ],
)
def test_kimi_http_contract(monkeypatch, status, body, success):
    def handler(request):
        assert str(request.url) == "https://api.kimi.com/coding/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-only-key"
        payload = json.loads(request.content)
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["model"] == "kimi-for-coding"
        assert "test-only-key" not in json.dumps(payload)
        return httpx.Response(status, json=body)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "voice_agent.providers.httpx.AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    task = Task(goal="goal", required_fields=["field"])
    provider = KimiProvider(
        "test-only-key",
        "kimi-for-coding",
        base_url="https://api.kimi.com/coding/v1",
    )
    if success:
        assert asyncio.run(provider.propose(task, State.for_task(task))) == "{}"
    else:
        with pytest.raises(ProviderError):
            asyncio.run(provider.propose(task, State.for_task(task)))


def test_kimi_configuration():
    with pytest.raises(ValueError):
        KimiProvider("", "")
    with pytest.raises(ValueError):
        KimiProvider("test-only-key", "model", base_url="http://untrusted.example")
