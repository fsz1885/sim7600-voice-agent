"""Native Windows SIM7600 control service; bearer authentication, task ownership."""

import asyncio
import base64
import hmac
import json
import os
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .diagnostics import QUERIES, DiagnosticQuery
from .pcm_bridge import PCMBridge
from .sim7600 import ATTransport, Sim7600, discover_ports


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner: str = Field(pattern=r"^[a-f0-9]{32}$")
    execution_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    number: str | None = Field(default=None, pattern=r"^\+?[0-9]{3,20}$")


def create_app(root="local-data", modem_factory=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    key_file = root / "hardware-api-key.txt"
    if not key_file.exists():
        import secrets

        key_file.write_text(secrets.token_hex(32), encoding="utf-8")
    key = key_file.read_text(encoding="utf-8").strip()
    if len(key) < 32:
        raise ValueError("Hardware key must contain at least 32 characters")
    journal_path = root / "hardware-state.json"
    journal = (
        json.loads(journal_path.read_text(encoding="utf-8"))
        if journal_path.exists()
        else {"owner": None, "operations": {}}
    )
    transport = None
    modem = None
    gate = asyncio.Lock()
    bridge = None

    def close_audio():
        nonlocal bridge
        if bridge:
            bridge.close()
            bridge = None

    async def watchdog():
        while True:
            await asyncio.sleep(2)
            async with gate:
                if bridge:
                    try:
                        calls = await asyncio.to_thread(connect().calls)
                        expired = time.monotonic() - bridge.last_access > 15
                        if not calls or expired or bridge.error:
                            close_audio()
                            await asyncio.to_thread(connect().usb_audio, False)
                            if expired or calls:
                                await asyncio.to_thread(connect().hangup)
                            journal["owner"] = None
                            save()
                    except Exception:
                        close_audio()

    def save():
        temp = journal_path.with_suffix(".tmp")
        temp.write_text(json.dumps(journal), encoding="utf-8")
        temp.replace(journal_path)

    def connect():
        nonlocal transport, modem
        if modem is None:
            if modem_factory:
                modem = modem_factory()
            else:
                transport = ATTransport(os.getenv("SIM7600_AT_PORT"))
                modem = Sim7600(transport)
        return modem

    @asynccontextmanager
    async def lifespan(app):
        monitor = asyncio.create_task(watchdog())
        yield
        monitor.cancel()
        try:
            await monitor
        except asyncio.CancelledError:
            pass
        close_audio()
        if transport:
            transport.close()

    app = FastAPI(lifespan=lifespan)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        from fastapi.responses import JSONResponse

        if not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + key):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/status")
    async def status():
        async with gate:
            try:
                device = connect()
                health = await asyncio.to_thread(device.health)
                calls = await asyncio.to_thread(device.calls)
                if not calls and journal["owner"]:
                    journal["owner"] = None
                    save()
                return {**health, "calls": [asdict(c) for c in calls], "owner": journal["owner"]}
            except Exception:
                raise HTTPException(
                    503, "设备不可用，检查串口占用及连接；必要时重启硬件服务"
                ) from None

    @app.get("/debug/ports")
    async def ports():
        try:
            return {
                "ports": await asyncio.to_thread(discover_ports),
                "at_port": os.getenv("SIM7600_AT_PORT", "自动识别"),
                "audio_port": os.getenv("SIM7600_AUDIO_PORT", "自动识别"),
            }
        except Exception:
            raise HTTPException(503, "端口枚举失败，请检查 pyserial 与 USB 驱动") from None

    @app.post("/debug/query")
    async def query(body: DiagnosticQuery):
        async with gate:
            if bridge or journal["owner"]:
                raise HTTPException(409, "电话占用中，请在通话结束后运行诊断")
            try:
                command, prefixes = QUERIES[body.query]
                result = await asyncio.to_thread(connect().at.command, command, prefixes=prefixes)
                return {"command": command, "lines": result.lines, "result": result.result}
            except Exception:
                raise HTTPException(503, "诊断失败，检查 USB、串口占用或重启硬件服务") from None

    @app.post("/{action}")
    async def operate(action: str, body: Operation):
        if action not in {"dial", "answer", "hangup"}:
            raise HTTPException(404)
        if action == "dial" and not body.number:
            raise HTTPException(422, "number is required")
        async with gate:
            operations = journal["operations"]
            fingerprint = {"action": action, **body.model_dump()}
            if body.execution_id in operations:
                prior = operations[body.execution_id]
                if prior["request"] != fingerprint:
                    raise HTTPException(409, "执行 ID 参数不一致")
                if prior["status"] != "succeeded":
                    raise HTTPException(409, "上次执行结果未知，不自动重放")
                return prior["result"]
            if journal["owner"] not in {None, body.owner}:
                raise HTTPException(409, "电话由其他任务占用")
            if action == "hangup" and journal["owner"] is None:
                return {"released": True, "message": "此任务没有电话占用"}
            operation = {"request": fingerprint, "status": "running", "time": time.time()}
            operations[body.execution_id] = operation
            journal["owner"] = body.owner
            save()
            try:
                device = connect()
                if action == "dial":
                    await asyncio.to_thread(device.dial, body.number)
                elif action == "answer":
                    await asyncio.to_thread(device.answer)
                else:
                    close_audio()
                    await asyncio.to_thread(device.hangup)
                    await asyncio.to_thread(device.usb_audio, False)
                    journal["owner"] = None
                result = {
                    "accepted": True,
                    "action": action,
                    "message": "AT 命令完成；拨号不等于对端已接听",
                }
                operation.update(status="succeeded", result=result)
                save()
                return result
            except Exception:
                operation["status"] = "unknown"
                save()
                raise HTTPException(503, "硬件操作失败或结果未知，请核对实际电话状态") from None

    class AudioWrite(BaseModel):
        owner: str = Field(pattern=r"^[a-f0-9]{32}$")
        generation: int
        pcm: str = Field(max_length=22000)

    def owns(owner):
        if journal["owner"] != owner:
            raise HTTPException(409, "此任务未拥有电话")

    @app.post("/audio/start")
    async def audio_start(body: Operation):
        nonlocal bridge
        async with gate:
            owns(body.owner)
            if bridge is None:
                new_bridge = PCMBridge(os.getenv("SIM7600_AUDIO_PORT"))
                try:
                    await asyncio.to_thread(connect().usb_audio, True)
                except Exception:
                    new_bridge.close()
                    raise HTTPException(409, "需要已接通的语音电话") from None
                bridge = new_bridge
            return bridge.read(0)

    @app.get("/audio/frames")
    async def audio_frames(owner: str, after: int = 0):
        owns(owner)
        if bridge is None:
            raise HTTPException(409, "音频已关闭")
        return bridge.read(after)

    @app.post("/audio/write")
    async def audio_write(body: AudioWrite):
        owns(body.owner)
        if bridge is None:
            raise HTTPException(409, "音频已关闭")
        try:
            bridge.write(base64.b64decode(body.pcm, validate=True), body.generation)
        except BufferError:
            raise HTTPException(429, "音频发送队列已满") from None
        except ValueError:
            raise HTTPException(409, "音频格式或播放代次无效") from None
        return {"accepted": True}

    @app.post("/audio/interrupt")
    async def audio_interrupt(body: Operation):
        owns(body.owner)
        if bridge is None:
            raise HTTPException(409, "音频已关闭")
        return {"generation": bridge.interrupt()}

    return app


def main():
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8767)


if __name__ == "__main__":
    main()
