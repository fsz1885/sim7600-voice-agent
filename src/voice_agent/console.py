"""Single-machine, single-flight voice console around the existing Agent Core."""

import argparse
import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .core import Agent
from .local_provider import DEFAULT_MODEL, OllamaProvider
from .models import State, Task
from .providers import MockProvider
from .speech import MAX_AUDIO_BYTES, LocalSpeech


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: str = Field(min_length=1, max_length=500)
    required_fields: list[str] = Field(min_length=1, max_length=20)
    mode: Literal["local", "mock"] = "local"
    speech: bool = True


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=1500)


class PlaybackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    audio: str
    status: Literal["started", "ended", "interrupted", "failed"]


@dataclass
class Session:
    id: str
    state: State
    agent: Agent
    mode: str
    speech: bool
    events: list[dict] = field(default_factory=list)
    audio: dict[str, Path] = field(default_factory=dict)
    stopped: bool = False
    busy: bool = False
    job: asyncio.Task | None = None
    generation: int = 0
    pending_input: str | None = None
    draft: str | None = None

    def emit(self, stage, status="done", **data):
        self.events.append(
            {
                "id": len(self.events) + 1,
                "time": time.time(),
                "stage": stage,
                "status": status,
                "generation": self.generation,
                **data,
            }
        )

    def snapshot(self):
        return {
            "id": self.id,
            "mode": self.mode,
            "transport": "local_simulation",
            "stopped": self.stopped,
            "busy": self.busy,
            "generation": self.generation,
            "pending_input": self.pending_input,
            "draft": self.draft,
            "state": self.state.model_dump(),
            "result": self.state.result(),
            "events": self.events,
        }


def create_app(data_dir=None, models_dir=None, provider_factory=None, speech_engine=None):
    data_dir = Path(data_dir or os.getenv("VOICE_DATA_DIR", "local-data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    speech = speech_engine or LocalSpeech(
        Path(models_dir or os.getenv("VOICE_MODELS_DIR", "models"))
    )
    model = os.getenv("LLM_MODEL", DEFAULT_MODEL)
    base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    timeout = float(os.getenv("LLM_TIMEOUT_SECONDS", "90"))
    context = int(os.getenv("LLM_CONTEXT", "8192"))
    local = OllamaProvider(model, timeout, base_url, context)
    sessions: dict[str, Session] = {}
    gate = asyncio.Lock()
    preview_gate = asyncio.Lock()
    app = FastAPI(title="本地语音通话控制台", docs_url=None, redoc_url=None)
    app.state.sessions = sessions

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = urlsplit("http://" + request.headers.get("host", "")).hostname
        if host not in {"127.0.0.1", "localhost", "::1", "testserver"}:
            return JSONResponse({"detail": "仅允许本机访问"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "不允许跨站访问"}, status_code=403)
        if request.method == "POST" and request.headers.get("x-voice-console") != "1":
            return JSONResponse({"detail": "缺少本机控制台请求标识"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; "
            "worker-src 'self' blob:; "
            "media-src 'self' blob:; connect-src 'self'; img-src 'self' data:; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    def get_session(sid):
        if sid not in sessions:
            raise HTTPException(404, "会话不存在，服务重启后请创建新会话")
        return sessions[sid]

    def save(session):
        folder = data_dir / session.id
        folder.mkdir(exist_ok=True)
        path = folder / "session.json"
        temporary = folder / "session.tmp"
        temporary.write_text(
            json.dumps(session.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)

    def ready(session, allow_completed=False):
        if session.stopped or (
            session.state.status != "active"
            and not (allow_completed and session.state.status == "completed")
        ):
            raise HTTPException(409, "会话已结束")
        if session.busy or gate.locked():
            raise HTTPException(409, "正在处理上一轮，请稍候")

    async def run_turn(session, text):
        try:
            async with gate:
                if session.stopped:
                    return
                session.emit("llm", "running", label="模型理解与字段提取")
                started = time.perf_counter()
                # Stop never leaves a half-applied or late-arriving turn in the live state.
                working = session.state.model_copy(deep=True)
                decision = await session.agent.handle_turn(
                    task=working.task, state=working, user_text=text
                )
                if session.stopped:
                    return
                session.state = working
                session.pending_input = None
                session.draft = None
                session.emit(
                    "llm",
                    label="模型返回并完成 Core 校验",
                    duration_ms=round((time.perf_counter() - started) * 1000),
                    action=decision.action,
                )
                session.emit(
                    "fields",
                    label="字段与证据",
                    updates=decision.model_dump()["updates"],
                    reason=decision.reason,
                )
                session.emit(
                    "reply", label="助手回复", text=decision.response, action=decision.action
                )
                save(session)
                if session.speech:
                    session.emit("tts", "running", label="合成中文回复")
                    started = time.perf_counter()
                    try:
                        audio = await asyncio.to_thread(speech.synthesize, decision.response)
                        if session.stopped:
                            return
                        name = uuid.uuid4().hex + ".wav"
                        path = data_dir / session.id / name
                        path.write_bytes(audio)
                        session.audio[name] = path
                        session.emit(
                            "tts",
                            label="回复音频就绪",
                            duration_ms=round((time.perf_counter() - started) * 1000),
                            audio=f"/api/sessions/{session.id}/audio/{name}",
                        )
                    except (ValueError, RuntimeError, OSError):
                        session.emit("tts", "error", label="语音合成失败，文字回复已保留")
                if session.state.status != "active":
                    session.emit(
                        "complete",
                        label="任务已完成"
                        if session.state.status == "completed"
                        else "任务未完成，需要后续处理",
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            session.emit("error", "error", label="本轮处理失败，请检查本地服务后重试")
        finally:
            session.draft = None
            session.pending_input = None
            session.busy = False
            save(session)

    def launch(session, text):
        session.generation += 1
        session.pending_input = text
        session.busy = True
        session.job = asyncio.create_task(run_turn(session, text))

    @app.get("/api/health")
    async def health():
        available, running = False, []
        try:
            async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                res = await client.get(local.base_url + "/api/tags")
                res.raise_for_status()
                available = any(m.get("name") == model for m in res.json().get("models", []))
                res = await client.get(local.base_url + "/api/ps")
                if res.is_success:
                    running = [
                        {k: m.get(k) for k in ("name", "size", "size_vram")}
                        for m in res.json().get("models", [])
                    ]
        except (httpx.HTTPError, ValueError, TypeError):
            pass
        return {
            "model": model,
            "llm": available,
            "running_models": running,
            "speech": speech.availability(),
            "transport": "本地模拟 · 未连接电话线路",
            "context": context,
        }

    @app.post("/api/sessions")
    async def start(body: StartRequest):
        if any(s.busy or not s.stopped for s in sessions.values()):
            raise HTTPException(409, "请先结束当前会话")
        if len(sessions) >= 20:
            del sessions[next(iter(sessions))]
        if any(len(f) > 80 for f in body.required_fields):
            raise HTTPException(422, "字段名称最长 80 字符")
        try:
            task = Task(goal=body.goal, required_fields=body.required_fields)
        except ValueError:
            raise HTTPException(422, "目标和字段不能为空，字段不能重复") from None
        provider = (
            provider_factory()
            if provider_factory
            else (
                OllamaProvider(model, timeout, base_url, context)
                if body.mode == "local"
                else MockProvider()
            )
        )
        session = Session(
            uuid.uuid4().hex,
            State.for_task(task),
            Agent(provider, timeout=timeout),
            body.mode,
            body.speech,
        )
        sessions[session.id] = session
        if isinstance(provider, OllamaProvider):

            def preview(text):
                if not session.stopped:
                    session.draft = text

            provider.on_preview = preview
        session.emit(
            "session", label="本地语音会话开始" if body.mode == "local" else "规则模拟开始"
        )
        save(session)
        launch(session, None)
        return session.snapshot()

    @app.get("/api/current-session")
    async def current_session():
        # The single-user console must recover even when tab storage was lost.
        return next((s.snapshot() for s in sessions.values() if not s.stopped or s.busy), None)

    @app.get("/api/sessions/{sid}")
    async def snapshot(sid: str):
        return get_session(sid).snapshot()

    @app.get("/api/sessions/{sid}/stream")
    async def stream(sid: str, request: Request):
        session = get_session(sid)

        async def updates():
            previous = None
            while not await request.is_disconnected():
                current = json.dumps(session.snapshot(), ensure_ascii=False)
                if current != previous:
                    yield "data: " + current + "\n\n"
                    previous = current
                if session.stopped and not session.busy:
                    break
                await asyncio.sleep(0.1)

        return StreamingResponse(
            updates(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    @app.post("/api/sessions/{sid}/preview")
    async def preview_audio(sid: str, request: Request):
        session = get_session(sid)
        if session.stopped:
            raise HTTPException(409, "会话已结束")
        if preview_gate.locked():
            raise HTTPException(429, "正在识别预览")
        async with preview_gate:
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > MAX_AUDIO_BYTES:
                    raise HTTPException(413, "录音过大")
            try:
                text = await asyncio.to_thread(speech.transcribe, bytes(data))
            except (ValueError, RuntimeError, OSError):
                return {"text": ""}
            return {"text": text if not session.stopped else ""}

    @app.post("/api/sessions/{sid}/turn")
    async def turn(sid: str, body: TurnRequest):
        session = get_session(sid)
        ready(session)
        if not body.text.strip():
            raise HTTPException(422, "请输入有效文字")
        session.emit("input", label="已提交对方回答", text=body.text)
        launch(session, body.text)
        return {"accepted": True}

    @app.post("/api/sessions/{sid}/audio-turn")
    @app.post("/api/sessions/{sid}/transcribe")
    async def transcribe(sid: str, request: Request):
        session = get_session(sid)
        automatic = request.url.path.endswith("/audio-turn")
        ready(session, allow_completed=automatic)
        session.busy = True
        launched = False
        try:
            async with gate:
                data = bytearray()
                async for chunk in request.stream():
                    data.extend(chunk)
                    if len(data) > MAX_AUDIO_BYTES:
                        raise HTTPException(413, "录音过大")
                session.emit("asr", "running", label="识别本地录音")
                started = time.perf_counter()
                text = await asyncio.to_thread(speech.transcribe, bytes(data))
                if session.stopped:
                    raise HTTPException(409, "会话已结束，转写未提交")
                name = uuid.uuid4().hex + ".wav"
                path = data_dir / sid / name
                path.write_bytes(data)
                session.audio[name] = path
                session.emit(
                    "asr",
                    label="语音自动提交" if automatic else "转写待确认",
                    turn=session.state.turns + 1 if automatic else None,
                    text=text,
                    audio=f"/api/sessions/{sid}/audio/{name}",
                    duration_ms=round((time.perf_counter() - started) * 1000),
                )
            if automatic:
                if not text.strip():
                    raise ValueError("未识别到有效语音")
                session.emit(
                    "input",
                    label="自动提交对方发言",
                    text=text,
                    turn=session.state.turns + 1,
                    audio=f"/api/sessions/{sid}/audio/{name}",
                )
                if session.state.status == "completed":
                    session.state.status = "active"
                    session.state.final_result = None
                launch(session, text)
                launched = True
            return {"text": text, "accepted": automatic, "generation": session.generation}
        except ValueError as exc:
            session.emit("asr", "error", label=str(exc))
            raise HTTPException(422, str(exc)) from None
        except (RuntimeError, OSError):
            session.emit("asr", "error", label="语音识别不可用")
            raise HTTPException(503, "语音识别不可用，请检查模型安装") from None
        finally:
            if not launched:
                session.busy = False
            save(session)

    @app.post("/api/sessions/{sid}/interrupt")
    async def interrupt(sid: str):
        session = get_session(sid)
        session.emit("interruption", label="检测到新发言，停止当前回复播放")
        save(session)
        return {"generation": session.generation}

    @app.post("/api/sessions/{sid}/stop")
    async def stop(sid: str):
        session = get_session(sid)
        if not session.stopped:
            session.stopped = True
            session.emit(
                "stop", label="本地会话已停止", completed=session.state.status == "completed"
            )
            # Let native CPU work finish; stopped checks suppress late state/audio publication.
            save(session)
        return session.snapshot()

    @app.post("/api/sessions/{sid}/playback")
    async def playback(sid: str, body: PlaybackRequest):
        session = get_session(sid)
        if body.audio not in session.audio:
            raise HTTPException(404, "音频不存在")
        if len(session.events) >= 2000:
            raise HTTPException(429, "事件数量达到限制")
        session.emit(
            "playback",
            label={
                "started": "浏览器开始播放",
                "ended": "浏览器播放结束",
                "interrupted": "播放已打断",
                "failed": "浏览器播放失败",
            }[body.status],
            playback_status=body.status,
            audio_name=body.audio,
        )
        save(session)
        return {"ok": True}

    @app.get("/api/sessions/{sid}/audio/{name}")
    async def audio(sid: str, name: str):
        session = get_session(sid)
        if name not in session.audio:
            raise HTTPException(404, "音频不存在")
        return FileResponse(session.audio[name], media_type="audio/wav")

    @app.get("/api/sessions/{sid}/export")
    async def export(sid: str):
        return JSONResponse(
            get_session(sid).snapshot(),
            headers={"Content-Disposition": f'attachment; filename="session-{sid}.json"'},
        )

    app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")
    return app


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description="本机语音控制台")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
