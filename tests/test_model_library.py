import asyncio
import json

import httpx
import pytest

from voice_agent.platform.model import KimiAgentModel
from voice_agent.platform.settings import ModelSettings, write_settings


def config(**kw):
    return ModelSettings(
        provider="openai-compatible", base_url="http://local.example/v1", model="local-model", **kw
    )


def test_legacy_migration_profiles_and_restart(tmp_path):
    path = tmp_path / "models.json"
    write_settings(path, config(api_key="legacy-secret"))
    model = KimiAgentModel(path)
    initial = model.library()["active_id"]
    second = model.save_profile(config(name="Second", api_key="second-secret", temperature=0.2))
    assert model.active_id == initial
    model.activate(second["id"])
    restored = KimiAgentModel(path)
    assert restored.key() == "second-secret"
    assert len(restored.library()["profiles"]) == 2
    assert "secret" not in json.dumps(restored.library())
    with pytest.raises(ValueError):
        restored.delete_profile(second["id"])
    restored.activate(initial)
    assert restored.key() == "legacy-secret"
    restored.delete_profile(second["id"])
    assert len(KimiAgentModel(path).profiles) == 1


def test_credentials_isolated_and_failed_write_keeps_active(tmp_path, monkeypatch):
    model = KimiAgentModel(tmp_path / "models.json")
    first = model.save_profile(config(api_key="first-secret"))
    second = model.save_profile(config())
    assert not second["has_key"]  # Even the same URL never inherits a different profile's key.
    changed = config(id=first["id"])
    changed.base_url = "http://other.example/v1"
    assert not model.save_profile(changed)["has_key"]
    active = model.active_id

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr("voice_agent.platform.settings.write_json", fail)
    with pytest.raises(OSError):
        model.activate(second["id"])
    assert model.active_id == active


def test_parameters_reach_request_and_default_omitted(tmp_path, monkeypatch):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"action":"speak","message":"ok"}'},
                    }
                ]
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )
    model = KimiAgentModel(tmp_path / "models.json")
    model.configure(
        config(
            temperature=0.3,
            top_p=0.8,
            max_tokens=500,
            seed=42,
            frequency_penalty=0.2,
            presence_penalty=-0.1,
            reasoning="none",
        )
    )
    task = {"goal": "test", "plan": [], "messages": [], "allowed_numbers": []}
    asyncio.run(model.decide(task, []))
    for key, value in {
        "temperature": 0.3,
        "top_p": 0.8,
        "max_tokens": 500,
        "seed": 42,
        "frequency_penalty": 0.2,
        "presence_penalty": -0.1,
        "reasoning_effort": "none",
    }.items():
        assert seen[-1][key] == value
    model.configure(config())
    asyncio.run(model.decide(task, []))
    assert "temperature" not in seen[-1] and "reasoning_effort" not in seen[-1]


def test_library_api_test_and_discover_do_not_change_active(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    from fastapi.testclient import TestClient

    from voice_agent.platform.api import create_app

    def handler(request):
        assert request.headers["authorization"] == "Bearer local-secret"
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "local-model"}]})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"action":"speak","message":"ok"}'},
                    }
                ]
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )
    with TestClient(create_app(tmp_path), headers={"X-Agent-UI": "1"}) as client:
        initial = client.get("/api/settings/models").json()["active_id"]
        payload = config().model_dump(mode="json")
        payload["api_key"] = "local-secret"
        result = client.post("/api/settings/models", json=payload)
        assert result.status_code == 200 and "local-secret" not in result.text
        profile = result.json()["id"]
        path = f"/api/settings/models/{profile}"
        assert client.post(path + "/discover", json={}).json()["models"] == ["local-model"]
        assert client.post(path + "/test", json={}).status_code == 200
        assert client.get("/api/settings/models").json()["active_id"] == initial
        assert (
            client.post(
                path + "/activate", json={}, headers={"Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert client.post(path + "/activate", json={}).status_code == 200
        assert client.post(path + "/delete", json={}).status_code == 409
