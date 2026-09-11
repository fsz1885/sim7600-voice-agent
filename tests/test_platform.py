import asyncio
import json

import httpx
import pytest

from voice_agent.platform.engine import Engine
from voice_agent.platform.model import KimiAgentModel
from voice_agent.platform.state import Action, Store
from voice_agent.platform.tools import Tools
from voice_agent.providers import ProviderError


class ScriptModel:
    def __init__(self, *actions):
        self.actions = iter(actions)

    async def decide(self, task, tools):
        return Action(**next(self.actions))


def test_successful_dial_hands_same_task_to_voice_without_next_decision(tmp_path):
    async def run():
        store = Store(tmp_path / "db")
        tools = Tools(tmp_path)
        operations, handoffs = [], []

        async def execute(name, args, task_id, execution_id):
            operations.append(name)
            return {"accepted": True}

        class Phone:
            def start(self, number, goal, **kwargs):
                handoffs.append((number, goal, kwargs))

        tools.execute = execute
        engine = Engine(
            store,
            ScriptModel(
                {"action": "call_tool", "tool": "phone.dial", "arguments": {"number": "12345"}}
            ),
            tools,
        )
        engine.phone = Phone()
        task = store.create("与 kd 闲聊", ["12345"])
        engine.start(task["id"])
        await engine.jobs[task["id"]]
        assert operations == ["phone.dial"]
        assert handoffs == [("12345", task["goal"], {"task_id": task["id"], "attached": True})]
        assert store.get(task["id"])["status"] == "running"
        assert store.get(task["id"])["executions"][0]["status"] == "succeeded"
        store.close()

    asyncio.run(run())


def test_multistep_tools_complete_with_real_artifact_and_persistence(tmp_path):
    async def run():
        store = Store(tmp_path / "state.db")
        tools = Tools(tmp_path)
        (tools.knowledge / "test.md").write_text("设备已就绪", encoding="utf-8")
        model = ScriptModel(
            {"action": "call_tool", "tool": "documents.list", "plan": ["查看资料", "保存笔记"]},
            {"action": "call_tool", "tool": "documents.read", "arguments": {"name": "test.md"}},
            {"action": "call_tool", "tool": "notes.save", "arguments": {"text": "设备已就绪"}},
            {"action": "complete", "message": "笔记已保存"},
        )
        engine = Engine(store, model, tools)
        task = store.create("整理资料", [])
        engine.start(task["id"])
        await engine.jobs[task["id"]]
        final = store.get(task["id"])
        assert final["status"] == "completed"
        assert len(final["executions"]) == 3
        name = final["executions"][-1]["result"]["artifact"]
        assert (tools.artifacts / name).read_text(encoding="utf-8") == "设备已就绪"
        store.close()
        reopened = Store(tmp_path / "state.db")
        assert reopened.get(task["id"])["result"] == "笔记已保存"
        assert len(reopened.events(task["id"])) >= 8
        reopened.close()

    asyncio.run(run())


def test_phone_requires_approval_before_any_execution(tmp_path):
    async def run():
        store = Store(tmp_path / "db")
        tools = Tools(tmp_path)
        called = []

        async def execute(*args):
            called.append(args)
            return {"accepted": True}

        tools.execute = execute
        engine = Engine(
            store,
            ScriptModel(
                {"action": "call_tool", "tool": "phone.dial", "arguments": {"number": "12345"}},
                {"action": "wait", "message": "等待对端接听"},
            ),
            tools,
        )
        task = store.create("联系对方", [])
        engine.start(task["id"])
        await engine.jobs[task["id"]]
        task = store.get(task["id"])
        assert task["status"] == "waiting_approval" and not called
        task["pending"]["approved"] = True
        store.save(task, "approved")
        engine.start(task["id"])
        await engine.jobs[task["id"]]
        assert len(called) == 1
        assert store.get(task["id"])["status"] == "waiting_user"
        store.close()

    asyncio.run(run())


def test_cancel_no_late_reply(tmp_path):
    async def run():
        entered = asyncio.Event()

        class Slow:
            async def decide(self, *args):
                entered.set()
                await asyncio.sleep(60)
                return Action(action="complete", message="should not commit")

        store = Store(tmp_path / "db")
        engine = Engine(store, Slow(), Tools(tmp_path))
        task = store.create("test", [])
        engine.start(task["id"])
        await entered.wait()
        await engine.stop(task["id"], "cancelled")
        assert store.get(task["id"])["status"] == "cancelled"
        assert not store.get(task["id"])["result"]
        store.close()

    asyncio.run(run())


def test_recovery_blocks_unknown_side_effect(tmp_path):
    async def run():
        store = Store(tmp_path / "db")
        task = store.create("test", [])
        task["status"] = "running"
        task["executions"] = [{"status": "running", "tool": "phone.dial"}]
        store.save(task, "tool_started")
        store.recover()
        assert store.get(task["id"])["executions"][0]["status"] == "unknown"
        with pytest.raises(ValueError, match="未知"):
            Engine(store, ScriptModel(), Tools(tmp_path)).start(task["id"])
        store.close()

    asyncio.run(run())


def test_tools_reject_traversal_unknown_and_extra_parameters(tmp_path):
    async def run():
        tools = Tools(tmp_path)
        with pytest.raises(ValueError):
            await tools.execute("documents.read", {"name": "../secret.md"}, "t", "e")
        with pytest.raises(ValueError):
            tools.validate("shell", {"command": "anything"})
        with pytest.raises(ValueError):
            tools.validate("phone.status", {"number": "12345"})
        assert not tools.needs_approval(
            "phone.dial", {"number": "12345"}, {"allowed_numbers": ["12345"]}
        )

    asyncio.run(run())


@pytest.mark.parametrize("status,finish", [(200, "stop"), (200, "length"), (401, "stop")])
def test_coding_model_explicit_k26_nothink(monkeypatch, tmp_path, status, finish):
    key = tmp_path / "key"
    key.write_text("unit-test-key", encoding="utf-8")
    monkeypatch.setenv("AGENT_KIMI_KEY_FILE", str(key))

    def handler(request):
        assert request.url == "https://api.kimi.com/coding/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer unit-test-key"
        payload = json.loads(request.content)
        assert payload["model"] == "kimi-k2.6"
        assert payload["thinking"] == {"type": "disabled"}
        return httpx.Response(
            status,
            json={
                "choices": [
                    {
                        "finish_reason": finish,
                        "message": {"content": '{"action":"speak","message":"你好"}'},
                    }
                ]
            },
        )

    client = httpx.AsyncClient
    monkeypatch.setattr(
        "voice_agent.platform.model.httpx.AsyncClient",
        lambda **kw: client(transport=httpx.MockTransport(handler), **kw),
    )
    store = Store(tmp_path / "db")
    task = store.create("你好", [])
    if status == 200 and finish == "stop":
        assert asyncio.run(KimiAgentModel().decide(task, [])).action == "speak"
    else:
        with pytest.raises(ProviderError):
            asyncio.run(KimiAgentModel().decide(task, []))
    store.close()


def test_step_budget_pauses_instead_of_infinite_loop(tmp_path):
    async def run():
        class Repeating:
            async def decide(self, *args):
                return Action(action="call_tool", tool="documents.list")

        store = Store(tmp_path / "db")
        engine = Engine(store, Repeating(), Tools(tmp_path), max_steps=2)
        task = store.create("test", [])
        engine.start(task["id"])
        await engine.jobs[task["id"]]
        assert store.get(task["id"])["status"] == "paused"
        assert store.get(task["id"])["steps"] == 2
        store.close()

    asyncio.run(run())


def test_hardware_auth_ownership_and_idempotency(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from voice_agent.hardware_service import create_app

    class FakeModem:
        def __init__(self):
            self.dialed = []

        def dial(self, number):
            self.dialed.append(number)

    modem = FakeModem()
    with TestClient(create_app(tmp_path, lambda: modem)) as client:
        body = {"owner": "a" * 32, "execution_id": "b" * 32, "number": "12345"}
        assert client.post("/dial", json=body).status_code == 401
        client.headers["Authorization"] = (
            "Bearer " + (tmp_path / "hardware-api-key.txt").read_text()
        )
        assert client.post("/dial", json=body).status_code == 200
        assert client.post("/dial", json=body).status_code == 200
        assert modem.dialed == ["12345"]
        assert client.post("/dial", json={**body, "number": "54321"}).status_code == 409
        assert (
            client.post(
                "/dial", json={**body, "owner": "c" * 32, "execution_id": "d" * 32}
            ).status_code
            == 409
        )


def test_api_same_origin_and_persistent_task(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("numpy")
    from fastapi.testclient import TestClient

    from voice_agent.platform.api import create_app

    with TestClient(
        create_app(tmp_path, ScriptModel({"action": "ask_user", "message": "请补充目标"}))
    ) as c:
        assert c.post("/api/tasks", json={"goal": "test"}).status_code == 403
        c.headers["X-Agent-UI"] = "1"
        assert (
            c.post(
                "/api/tasks", json={"goal": "test"}, headers={"Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        r = c.post("/api/tasks", json={"goal": "test"})
        assert r.status_code == 200
        assert c.get("/api/tasks/" + r.json()["id"]).status_code == 200
