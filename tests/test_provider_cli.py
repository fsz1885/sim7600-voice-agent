import asyncio
import json
import subprocess
import sys

import httpx
import pytest

from voice_agent import State, Task
from voice_agent.models import Proposal
from voice_agent.providers import AnthropicProvider, ProviderError


@pytest.mark.parametrize(
    "status,body,success",
    [
        (200, {"stop_reason": "end_turn", "content": [{"type": "text", "text": "{}"}]}, True),
        (200, {"stop_reason": "max_tokens", "content": []}, False),
        (200, {"stop_reason": "refusal", "content": []}, False),
        (200, {"stop_reason": "end_turn", "content": []}, False),
        (200, {"stop_reason": "end_turn"}, False),
        (429, {"error": "rate limited"}, False),
        (401, {"error": "unauthorized"}, False),
    ],
)
def test_real_adapter_http_contract_offline(monkeypatch, status, body, success):
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        assert request.headers["x-api-key"] == "test-only-key"
        return httpx.Response(status, json=body)

    client_class = httpx.AsyncClient
    monkeypatch.setattr(
        "voice_agent.providers.httpx.AsyncClient",
        lambda **kw: client_class(transport=httpx.MockTransport(handler), **kw),
    )
    task = Task(goal="goal", required_fields=["field"])
    provider = AnthropicProvider("test-only-key", "test-model")
    if success:
        assert asyncio.run(provider.propose(task, State.for_task(task))) == "{}"
    else:
        with pytest.raises(ProviderError):
            asyncio.run(provider.propose(task, State.for_task(task)))
    payload = captured[0]
    assert payload["model"] == "test-model"
    assert payload["output_config"]["format"]["schema"] == Proposal.model_json_schema()
    assert "test-only-key" not in json.dumps(payload)


def test_api_configuration_required():
    with pytest.raises(ValueError):
        AnthropicProvider("", "")


def test_demo_cli(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    result = subprocess.run(
        [sys.executable, "-m", "voice_agent.cli", "demo", "examples/company-registration.json"],
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    assert "Conversation completed." in result.stdout
    assert '"completed": true' in result.stdout


def test_chat_quit_incomplete(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    result = subprocess.run(
        [sys.executable, "-m", "voice_agent.cli", "chat", "examples/company-registration.json"],
        input="/quit\n",
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    assert '"completed": false' in result.stdout
