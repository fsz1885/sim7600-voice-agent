import asyncio
import json

import httpx
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("numpy")
pytest.importorskip("scipy")
from fastapi.testclient import TestClient

from voice_agent.hardware_service import create_app as hardware_app
from voice_agent.platform.api import create_app
from voice_agent.platform.model import KimiAgentModel
from voice_agent.platform.settings import ModelSettings
from voice_agent.sim7600 import ATResponse, Sim7600

HEADERS = {"X-Agent-UI": "1"}


def config(**kw):
    return {
        "provider": "openai-compatible",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "local-model",
        **kw,
    }


def test_settings_persist_without_disclosing_or_forwarding_old_secret(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        response = client.post("/api/settings/model", json=config(api_key="private-test-secret"))
        assert response.status_code == 200
        assert response.json()["has_key"]
        assert "private-test-secret" not in response.text
        assert "private-test-secret" not in client.get("/api/settings/model").text
    model = KimiAgentModel(tmp_path / "model-settings.json")
    assert model.key() == "private-test-secret"
    model.configure(ModelSettings(**config(model="another-model")))
    assert model.key() == "private-test-secret"
    model.configure(ModelSettings(**config(base_url="https://different.example/v1")))
    assert model.key() == ""
    model.configure(ModelSettings(**config(api_key="new-key")))
    model.configure(ModelSettings(**config(clear_key=True)))
    assert KimiAgentModel(tmp_path / "model-settings.json").key() == ""


def test_validation_and_origin_do_not_echo_key_or_write_settings(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.post("/api/settings/model", json=config()).status_code == 403
        response = client.post(
            "/api/settings/model",
            headers=HEADERS,
            json=config(base_url="file:///tmp", api_key="private-test-secret"),
        )
        assert response.status_code == 422
        assert "private-test-secret" not in response.text
        assert not (tmp_path / "model-settings.json").exists()
        assert (
            client.post(
                "/api/settings/model",
                json=config(),
                headers={**HEADERS, "Origin": "https://other.example"},
            ).status_code
            == 403
        )


def test_model_test_uses_saved_compatible_endpoint_and_executes_no_tools(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        assert str(request.url) == "http://127.0.0.1:11434/v1/chat/completions"
        assert "authorization" not in request.headers
        body = json.loads(request.content)
        assert body["model"] == "local-model"
        assert "thinking" not in body
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": (
                                '{"action":"call_tool","tool":"phone.dial",'
                                '"arguments":{"number":"12345"}}'
                            )
                        },
                    }
                ]
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )
    with TestClient(create_app(tmp_path), headers=HEADERS) as client:
        assert client.post("/api/settings/model", json=config()).status_code == 200
        assert client.get("/api/health").json()["configured"]
        response = client.post("/api/settings/model/test", json={})
        assert response.status_code == 200
        assert response.json()["action"] == "call_tool"
        assert len(requests) == 1
        assert client.get("/api/tasks").json() == []


def test_running_task_blocks_model_change(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:

        async def running():
            task = app.state.store.create("running test", [])
            app.state.engine.jobs[task["id"]] = asyncio.create_task(asyncio.sleep(60))

        client.portal.call(running)
        assert client.post("/api/settings/model", json=config()).status_code == 409
        assert client.post("/api/settings/model/test", json={}).status_code == 409


def test_hardware_whitelist_and_authenticated_read_only_queries(tmp_path, monkeypatch):
    sent = []

    class Transport:
        def command(self, command, **kw):
            sent.append(command)
            return ATResponse(command, ("+CSQ: 20,99",), "OK")

    monkeypatch.setattr(
        "voice_agent.hardware_service.discover_ports",
        lambda: [{"device": "COM7", "description": "SIMCom AT port", "hwid": "test"}],
    )
    app = hardware_app(tmp_path, modem_factory=lambda: Sim7600(Transport()))
    key = (tmp_path / "hardware-api-key.txt").read_text()
    with TestClient(app) as client:
        assert client.get("/debug/ports").status_code == 401
        client.headers["Authorization"] = "Bearer " + key
        assert client.get("/debug/ports").json()["ports"][0]["device"] == "COM7"
        assert client.post("/debug/query", json={"query": "signal"}).json()["result"] == "OK"
        assert client.post("/debug/query", json={"query": "ATD12345;"}).status_code == 422
        assert sent == ["AT+CSQ"]


def test_diagnostics_refuse_owned_call(tmp_path):
    (tmp_path / "hardware-state.json").write_text(json.dumps({"owner": "a" * 32, "operations": {}}))
    app = hardware_app(tmp_path, modem_factory=lambda: pytest.fail("must not open serial"))
    with TestClient(
        app, headers={"Authorization": "Bearer " + (tmp_path / "hardware-api-key.txt").read_text()}
    ) as client:
        assert client.post("/debug/query", json={"query": "ping"}).status_code == 409


def test_workbench_hardware_missing_config_is_actionable(tmp_path, monkeypatch):
    monkeypatch.setenv("HARDWARE_KEY_FILE", str(tmp_path / "missing"))
    with TestClient(create_app(tmp_path), headers=HEADERS) as client:
        assert client.get("/api/hardware/status").status_code == 503
        assert "start-hardware.ps1" in client.get("/api/hardware/ports").text
        assert client.post("/api/hardware/query", json={"query": "dial"}).status_code == 422


@pytest.mark.parametrize("route", ["status", "query"])
def test_idle_read_reconnects_after_usb_port_changes(tmp_path, monkeypatch, route):
    from voice_agent.sim7600 import ModemError

    connections = []

    class Transport:
        def __init__(self, port):
            self.closed = False
            self.fail = False
            connections.append(self)

        def close(self):
            self.closed = True

        def command(self, command, **kw):
            if self.fail:
                raise ModemError("USB disconnected")
            lines = {"AT+CPIN?": ("+CPIN: READY",), "AT+CEREG?": ("+CEREG: 0,1",)}
            return ATResponse(command, lines.get(command, ()), "OK")

    monkeypatch.setattr("voice_agent.hardware_service.ATTransport", Transport)
    app = hardware_app(tmp_path)
    key = (tmp_path / "hardware-api-key.txt").read_text()
    with TestClient(app, headers={"Authorization": "Bearer " + key}) as client:
        assert client.get("/status").status_code == 200
        connections[0].fail = True
        response = (
            client.get("/status")
            if route == "status"
            else client.post("/debug/query", json={"query": "ping"})
        )
        assert response.status_code == 200
        assert len(connections) == 2
        assert connections[0].closed
        assert not connections[1].closed


def test_owned_status_failure_does_not_reconnect_or_clear_owner(tmp_path):
    owner = "a" * 32
    (tmp_path / "hardware-state.json").write_text(json.dumps({"owner": owner, "operations": {}}))
    created = []

    class Broken:
        def health(self):
            raise RuntimeError("disconnected")

    def factory():
        created.append(1)
        return Broken()

    app = hardware_app(tmp_path, modem_factory=factory)
    key = (tmp_path / "hardware-api-key.txt").read_text()
    with TestClient(app, headers={"Authorization": "Bearer " + key}) as client:
        assert client.get("/status").status_code == 503
        assert client.get("/status").status_code == 503
        assert len(created) == 1
        assert json.loads((tmp_path / "hardware-state.json").read_text())["owner"] == owner
