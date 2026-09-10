import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from ..speech import LocalSpeech
from .engine import Engine
from .model import KimiAgentModel
from .phone import PhoneController
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
    model = model or KimiAgentModel()
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

    def get(task_id):
        try:
            return store.get(task_id)
        except KeyError:
            raise HTTPException(404, "任务不存在") from None

    @app.get("/api/health")
    async def health():
        return {
            "model": getattr(model, "model", "test"),
            "thinking": "disabled",
            "endpoint": getattr(model, "base_url", "test"),
            "configured": bool(model.key()) if hasattr(model, "key") else True,
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
