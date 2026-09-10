"""Native Windows SIM7600 control service; bearer authentication, task ownership."""

import asyncio
import hmac
import json
import os
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .sim7600 import ATTransport, Sim7600


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
        yield
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

    return app


def main():
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8767)


if __name__ == "__main__":
    main()
