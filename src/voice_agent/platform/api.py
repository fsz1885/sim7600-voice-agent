import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from ..diagnostics import DiagnosticQuery
from ..providers import ProviderError
from ..speech import LocalSpeech
from .engine import Engine
from .model import KimiAgentModel
from .phone import PhoneController
from .settings import ModelSettings
from .state import Store
from .tools import Tools


class CreateTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: str = Field(min_length=1, max_length=5000)
    allowed_numbers: list[str] = Field(default_factory=list, max_length=10)


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=5000)


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: bool


class PhoneStart(BaseModel):
    number: str = Field(pattern=r"^\+?[0-9]{3,20}$")
    goal: str = Field(min_length=1, max_length=2000)
    seconds: int = Field(default=120, ge=15, le=180)


def create_app(root=None, model=None, tools=None, speech=None):
    root = Path(root or os.getenv("AGENT_DATA_DIR", "local-data/agent"))
    store = Store(root / "tasks.sqlite3")
    store.recover()
    model = model or KimiAgentModel(root / "model-settings.json")
    tools = tools or Tools(root)
    engine = Engine(store, model, tools)
    speech = speech or LocalSpeech(
        Path(os.getenv("VOICE_MODELS_DIR", "docs/speech-evaluation/models"))
    )
    speech_gate = asyncio.Lock()
    phone = PhoneController(
        store,
        model,
        tools,
        speech,
        root,
        os.getenv("HARDWARE_URL", "http://127.0.0.1:8767"),
        os.getenv("HARDWARE_KEY_FILE", "local-data/hardware-api-key.txt"),
    )
    audio_dir = root / "audio"
    engine.phone = phone
    audio_dir.mkdir(exist_ok=True)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await phone.stop()
        await engine.shutdown()
        store.close()

    app = FastAPI(title="声程 · 自主语音 Agent", lifespan=lifespan)
    app.state.store, app.state.engine = store, engine

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        from urllib.parse import urlsplit

        host = urlsplit("http://" + request.headers.get("host", "")).hostname
        origin = request.headers.get("origin")
        if host not in {"127.0.0.1", "localhost", "::1", "testserver"} or (
            origin and origin != str(request.base_url).rstrip("/")
        ):
            return JSONResponse({"detail": "仅允许本机同源访问"}, status_code=403)
        if request.method == "POST" and request.headers.get("x-agent-ui") != "1":
            return JSONResponse({"detail": "缺少操作请求标识"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; media-src 'self' blob:; img-src 'self' data:; "
            "frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Pydantic model-level errors can otherwise echo the entire password form.
        return JSONResponse(
            {"detail": "请求参数无效，请检查服务地址、模型和密钥格式"}, status_code=422
        )

    config_test = asyncio.Lock()

    def configurable():
        if not isinstance(model, KimiAgentModel):
            raise HTTPException(409, "当前注入的模型不支持在线配置")

    def idle():
        if any(not job.done() for job in engine.jobs.values()) or (
            phone.job and not phone.job.done()
        ):
            raise HTTPException(409, "请先暂停运行中的任务并结束电话，再配置或测试模型")
        if config_test.locked():
            raise HTTPException(409, "模型连接测试正在进行")

    @app.get("/api/settings/model")
    async def model_settings():
        configurable()
        return model.public_settings()

    @app.post("/api/settings/model")
    async def save_model(body: ModelSettings):
        configurable()
        idle()
        try:
            return model.configure(body)
        except (ValueError, OSError):
            raise HTTPException(422, "配置保存失败，请检查配置和数据目录权限") from None

    @app.get("/api/settings/models")
    async def model_library():
        configurable()
        return model.library()

    @app.post("/api/settings/models")
    async def save_profile(body: ModelSettings):
        configurable()
        idle()
        try:
            return model.save_profile(body)
        except (ValueError, OSError):
            raise HTTPException(422, "保存失败，请检查模型配置与目录权限") from None

    @app.post("/api/settings/models/{profile_id}/activate")
    async def activate_profile(profile_id: str):
        configurable()
        idle()
        try:
            return model.activate(profile_id)
        except (ValueError, OSError):
            raise HTTPException(409, "切换失败，请检查已保存的模型") from None

    @app.post("/api/settings/models/{profile_id}/delete")
    async def delete_profile(profile_id: str):
        configurable()
        idle()
        try:
            return model.delete_profile(profile_id)
        except (ValueError, OSError):
            raise HTTPException(409, "删除失败：请先切换到其他模型，并检查目录权限") from None

    def selected_model(profile_id):
        configurable()
        try:
            return model.for_profile(profile_id)
        except ValueError:
            raise HTTPException(404, "模型配置不存在") from None

    @app.post("/api/settings/models/{profile_id}/discover")
    async def discover_models(profile_id: str):
        idle()
        selected = selected_model(profile_id)
        return await fetch_model_list(selected)

    @app.post("/api/settings/models/{profile_id}/reveal-key")
    async def reveal_key(profile_id: str):
        # Explicit local operator action only; list/detail APIs remain redacted.
        return JSONResponse(
            {"api_key": selected_model(profile_id).key()}, headers={"Cache-Control": "no-store"}
        )

    @app.post("/api/settings/models/discover")
    async def discover_draft(body: ModelSettings):
        configurable()
        idle()
        key = body.api_key.get_secret_value()
        if body.id:
            previous = selected_model(body.id)
            if (
                not key
                and not body.clear_key
                and body.base_url == previous.base_url
                and body.provider == previous.provider
            ):
                key = previous.key()
        selected = KimiAgentModel()
        selected.apply(body.model_copy(update={"api_key": type(body.api_key)(key)}))
        return await fetch_model_list(selected)

    async def fetch_model_list(selected):
        async with config_test:
            try:
                async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
                    key = selected.key()
                    response = await client.get(
                        selected.base_url + "/models",
                        headers={"Authorization": "Bearer " + key} if key else {},
                    )
                    response.raise_for_status()
                    ids = sorted(
                        {
                            item["id"]
                            for item in response.json()["data"]
                            if isinstance(item.get("id"), str) and len(item["id"]) <= 200
                        }
                    )
                    return {"models": ids[:200], "source": "server"}
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                detail = {
                    401: "密钥无效或已失效；Kimi Coding 与 Moonshot 的密钥不通用",
                    403: "服务拒绝访问；检查账号权限或服务使用限制",
                    404: "服务未提供 /models 接口，或 API 根地址不正确；可手动填写模型 ID",
                    429: "请求限流或额度不足，请检查账户后重试",
                }.get(code, "模型服务返回错误，请检查服务状态")
                raise HTTPException(502, f"获取模型列表失败：HTTP {code}，{detail}") from None
            except httpx.TimeoutException:
                raise HTTPException(504, "获取模型列表超时，请检查网络与服务地址") from None
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
                raise HTTPException(502, "获取模型列表失败；可手动填写服务端模型 ID") from None

    @app.post("/api/settings/models/{profile_id}/test")
    async def test_profile(profile_id: str):
        return await run_model_test(selected_model(profile_id))

    @app.post("/api/settings/model/test")
    async def test_model():
        return await run_model_test(model)

    async def run_model_test(selected):
        configurable()
        idle()
        async with config_test:
            start = time.perf_counter()
            try:
                result = await selected.decide(
                    {
                        "goal": "请用 speak 简短回复连接测试成功，不调用工具",
                        "plan": [],
                        "messages": [],
                        "allowed_numbers": [],
                    },
                    [],
                )
                return {
                    "ok": True,
                    "model": selected.model,
                    "action": result.action,
                    "duration_ms": round((time.perf_counter() - start) * 1000),
                    "message": "连接与动作格式校验通过；没有执行任何工具",
                }
            except ProviderError as exc:
                raise HTTPException(502, f"模型测试失败：{exc}") from None
            except Exception:
                raise HTTPException(
                    502, "模型测试失败：请检查服务地址、模型、密钥及 JSON 动作兼容性"
                ) from None

    async def hardware(path, body=None):
        key_path = Path(os.getenv("HARDWARE_KEY_FILE", "local-data/hardware-api-key.txt"))
        if not key_path.is_file():
            raise HTTPException(503, "硬件服务未配置，请先运行 scripts/start-hardware.ps1")
        try:
            async with httpx.AsyncClient(
                timeout=20,
                trust_env=False,
                headers={"Authorization": "Bearer " + key_path.read_text(encoding="utf-8").strip()},
            ) as client:
                base = os.getenv("HARDWARE_URL", "http://127.0.0.1:8767").rstrip("/")
                response = await (
                    client.get(base + path) if body is None else client.post(base + path, json=body)
                )
            if not response.is_success:
                message = {
                    401: "硬件服务密钥不匹配",
                    409: "电话占用中，请结束通话后诊断",
                    404: "硬件服务版本过旧，请更新并重启",
                }.get(response.status_code, "设备不可用，请检查 USB、串口占用及驱动")
                raise HTTPException(503, message)
            return response.json()
        except (httpx.HTTPError, OSError, ValueError):
            raise HTTPException(503, "无法连接硬件服务，请检查 8767 服务是否启动") from None

    @app.get("/api/hardware/status")
    async def hardware_status():
        return await hardware("/status")

    @app.get("/api/hardware/ports")
    async def hardware_ports():
        return await hardware("/debug/ports")

    @app.post("/api/hardware/query")
    async def hardware_query(body: DiagnosticQuery):
        return await hardware("/debug/query", body.model_dump())

    def get(task_id):
        try:
            return store.get(task_id)
        except KeyError:
            raise HTTPException(404, "任务不存在") from None

    @app.get("/api/health")
    async def health():
        return {
            "model": getattr(model, "model", "test"),
            "thinking": "disabled"
            if getattr(model, "provider", "kimi") == "kimi"
            else "server-default",
            "endpoint": getattr(model, "base_url", "test"),
            "configured": (bool(model.key()) or model.provider == "openai-compatible")
            if isinstance(model, KimiAgentModel)
            else True,
            "speech": speech.availability(),
            "tools": tools.catalog(),
        }

    @app.get("/api/tasks")
    async def tasks():
        return [
            {k: task[k] for k in ("id", "goal", "status", "updated_at", "steps")}
            for task in store.list()
        ]

    @app.get("/api/phone")
    async def phone_status():
        return phone.live

    @app.post("/api/phone/start")
    async def phone_start(body: PhoneStart):
        try:
            return phone.start(body.number, body.goal, body.seconds)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/phone/stop")
    async def phone_stop():
        await phone.stop()
        return phone.live

    @app.post("/api/phone/interrupt")
    async def phone_interrupt():
        if not phone.job or phone.job.done():
            raise HTTPException(409, "没有进行中的电话")
        await phone.interrupt()
        return phone.live

    @app.post("/api/tasks")
    async def create(body: CreateTask):
        import re

        if not body.goal.strip() or any(
            not re.fullmatch(r"\+?[0-9]{3,20}", n) for n in body.allowed_numbers
        ):
            raise HTTPException(422, "目标为空或号码格式无效")
        task = store.create(body.goal.strip(), body.allowed_numbers)
        engine.start(task["id"])
        return get(task["id"])

    @app.get("/api/tasks/{task_id}")
    async def detail(task_id: str):
        return get(task_id)

    @app.get("/api/tasks/{task_id}/events")
    async def events(task_id: str, request: Request, after: int = 0):
        get(task_id)
        try:
            cursor = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            raise HTTPException(422) from None

        async def generate():
            nonlocal cursor
            while not await request.is_disconnected():
                for event in store.events(task_id, cursor):
                    cursor = event["id"]
                    yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.3)

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.post("/api/tasks/{task_id}/message")
    async def message(task_id: str, body: Message):
        task = get(task_id)
        if task["status"] not in {"waiting_user", "paused"} or not body.text.strip():
            raise HTTPException(409, "请先暂停运行中的任务，或新建已结束任务")
        task["messages"].append({"role": "user", "content": body.text})
        store.save(task, "user_message", {"text": body.text})
        try:
            engine.start(task_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return get(task_id)

    @app.post("/api/tasks/{task_id}/approval")
    async def approval(task_id: str, body: Approval):
        task = get(task_id)
        if task["status"] != "waiting_approval" or not task["pending"]:
            raise HTTPException(409, "没有待确认操作")
        if body.approve:
            task["pending"]["approved"] = True
        else:
            task["pending"] = None
            task["messages"].append(
                {"role": "user", "content": "我拒绝了上一项工具操作，请另寻方案。"}
            )
        store.save(task, "approved" if body.approve else "rejected")
        engine.start(task_id)
        return get(task_id)

    @app.post("/api/tasks/{task_id}/{operation}")
    async def control(task_id: str, operation: str):
        task = get(task_id)
        if task.get("channel") == "sim7600":
            if operation not in {"pause", "cancel"}:
                raise HTTPException(409, "电话结束后需新建通话")
            if phone.task_id == task_id:
                await phone.stop()
            return get(task_id)
        if operation in {"pause", "cancel"}:
            await engine.stop(task_id, "paused" if operation == "pause" else "cancelled")
        elif operation == "resume":
            task = get(task_id)
            if task["status"] not in {"paused", "waiting_user"}:
                raise HTTPException(409, "当前状态不能恢复")
            try:
                engine.start(task_id)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
        else:
            raise HTTPException(404)
        return get(task_id)

    @app.post("/api/transcribe")
    async def transcribe(request: Request):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 1_000_000:
                raise HTTPException(413, "每段音频最长 30 秒，使用单声道 PCM WAV")
        async with speech_gate:
            try:
                text = await asyncio.to_thread(speech.transcribe, bytes(data))
            except (ValueError, RuntimeError, OSError) as exc:
                raise HTTPException(422, str(exc)) from None
        name = uuid.uuid4().hex + ".wav"
        (audio_dir / name).write_bytes(data)
        return {"text": text, "audio": name}

    @app.post("/api/synthesize")
    async def synthesize(body: Message):
        if len(body.text) > 400:
            raise HTTPException(422, "单次语音回复最多 400 字")
        async with speech_gate:
            try:
                data = await asyncio.to_thread(speech.synthesize, body.text)
            except (ValueError, RuntimeError, OSError) as exc:
                raise HTTPException(422, str(exc)) from None
        name = uuid.uuid4().hex + ".wav"
        (audio_dir / name).write_bytes(data)
        return {"audio": name}

    @app.get("/api/audio/{name}")
    async def audio(name: str):
        path = audio_dir / name
        if (
            path.parent.resolve() != audio_dir.resolve()
            or path.suffix != ".wav"
            or not path.is_file()
        ):
            raise HTTPException(404)
        return FileResponse(path, media_type="audio/wav")

    @app.get("/api/artifacts/{name}")
    async def artifact(name: str):
        path = (root / "artifacts" / name).resolve()
        if path.parent != (root / "artifacts").resolve() or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, filename=path.name)

    static = Path(os.getenv("AGENT_FRONTEND_DIR", "frontend/dist"))
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True))
    return app


def main():
    import uvicorn

    uvicorn.run(create_app(), host=os.getenv("AGENT_HOST", "127.0.0.1"), port=8766)


if __name__ == "__main__":
    main()
