import asyncio
import json

import httpx
import pytest

from voice_agent import State, Task
from voice_agent.local_provider import OllamaProvider, local_schema
from voice_agent.providers import ProviderError


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://192.168.1.3:11434",
        "http://user:secret@localhost:11434",
        "http://localhost/api",
    ],
)
def test_local_endpoint_only(url):
    with pytest.raises(ValueError):
        OllamaProvider(base_url=url)


@pytest.mark.parametrize(
    "status,body,success",
    [
        (200, {"done": True, "done_reason": "stop", "message": {"content": "{}"}}, True),
        (200, {"done": True, "done_reason": "length", "message": {"content": "{}"}}, False),
        (200, {"done": False, "message": {"content": "{}"}}, False),
        (200, {"done": True, "done_reason": "stop", "message": {"content": ""}}, False),
        (503, {}, False),
    ],
)
def test_local_contract(monkeypatch, status, body, success):
    def handler(request):
        assert request.url.host == "127.0.0.1"
        assert "authorization" not in request.headers
        payload = json.loads(request.content)
        assert payload["format"] == local_schema(task, opening=True)
        assert payload["format"]["properties"]["response"]["minLength"] == 1
        assert payload["format"]["properties"]["updates"]["maxItems"] == 0
        assert payload["think"] is False and payload["stream"] is False
        assert payload["options"]["num_ctx"] == 8192
        return httpx.Response(status, json=body)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        "voice_agent.local_provider.httpx.AsyncClient",
        lambda **kw: client(transport=httpx.MockTransport(handler), **kw),
    )
    task = Task(goal="确认费用", required_fields=["费用"])
    provider = OllamaProvider()
    if success:
        assert asyncio.run(provider.propose(task, State.for_task(task))) == "{}"
        assert len(provider.calls) == 1
    else:
        with pytest.raises(ProviderError):
            asyncio.run(provider.propose(task, State.for_task(task)))


def test_context_overflow_rejected_without_silent_history_loss():
    task = Task(goal="字" * 9000, required_fields=["费用"])
    with pytest.raises(ProviderError, match="context"):
        asyncio.run(OllamaProvider().propose(task, State.for_task(task)))


def test_docker_model_service_is_allowed_but_other_hosts_are_not():
    assert OllamaProvider(base_url="http://ollama:11434").base_url == "http://ollama:11434"
    for host in ["ollama.example.com", "192.168.1.3", "host.docker.internal"]:
        with pytest.raises(ValueError):
            OllamaProvider(base_url=f"http://{host}:11434")


def test_response_preview_never_exposes_structured_fields():
    from voice_agent.local_provider import response_preview

    assert response_preview('{"response":"你好') == "你好"
    assert response_preview('{"response":"你好","updates":[') == "你好"
    assert response_preview(r'{"response":"你好\u4e') == "你好"
    assert response_preview('{"updates":[{"response":"不应展示') == ""


@pytest.mark.parametrize("complete", [True, False])
def test_stream_delivers_preview_before_completion(monkeypatch, complete):
    async def run():
        seen = []

        class Chunks(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield (json.dumps({"message": {"content": '{"response":"你好'}}) + "\n").encode()
                assert "你好" in seen  # callback ran before the remaining JSON arrived
                if complete:
                    yield (
                        json.dumps(
                            {"message": {"content": '"}'}, "done": True, "done_reason": "stop"}
                        )
                        + "\n"
                    ).encode()

        def handler(request):
            assert json.loads(request.content)["stream"] is True
            return httpx.Response(200, stream=Chunks())

        client = httpx.AsyncClient
        monkeypatch.setattr(
            "voice_agent.local_provider.httpx.AsyncClient",
            lambda **kw: client(transport=httpx.MockTransport(handler), **kw),
        )
        provider = OllamaProvider()
        provider.on_preview = seen.append
        task = Task(goal="费用", required_fields=["费用"])
        if complete:
            assert await provider.propose(task, State.for_task(task)) == '{"response":"你好"}'
        else:
            with pytest.raises(ProviderError):
                await provider.propose(task, State.for_task(task))

    asyncio.run(run())
