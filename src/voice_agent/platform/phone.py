"""Live telephone PCM -> endpointing -> ASR -> Kimi -> TTS -> PCM controller."""

import asyncio
import base64
import io
import json
import time
import uuid
import wave
from collections import deque
from pathlib import Path

import httpx
import numpy as np
from scipy.signal import resample_poly


def pcm_wav(pcm, rate=8000):
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        wav.writeframes(pcm)
    return out.getvalue()


def downsample(wav_bytes):
    from math import gcd

    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise ValueError("TTS must produce mono PCM16")
        rate = wav.getframerate()
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32)
    factor = gcd(rate, 8000)
    samples = resample_poly(samples, 8000 // factor, rate // factor)
    return np.clip(samples, -32768, 32767).astype("<i2").tobytes()


class Endpoint:
    """Conservative energy endpoint detector; 200ms onset and 800ms silence.

    No AEC claim: modem echo/noise and real-world interruptions need evaluation.
    """

    def __init__(self, threshold=0.003):
        self.threshold = threshold
        self.reset()

    def reset(self):
        self.pre = deque(maxlen=15)
        self.frames = []
        self.voiced = 0
        self.silence = 0
        self.active = False

    def feed(self, frame):
        samples = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768
        voice = np.sqrt(np.mean(samples * samples)) >= self.threshold
        onset = False
        if not self.active:
            self.pre.append(frame)
            self.voiced = self.voiced + 1 if voice else 0
            if self.voiced >= 10:
                self.active = True
                self.frames = list(self.pre)
                onset = True
        else:
            self.frames.append(frame)
            self.silence = 0 if voice else self.silence + 1
            if self.silence >= 40 or len(self.frames) >= 1000:
                pcm = b"".join(self.frames)
                self.reset()
                return onset, pcm
        return onset, None


class PhoneController:
    def __init__(self, store, model, tools, speech, root, hardware_url, key_file):
        self.store, self.model, self.tools, self.speech = store, model, tools, speech
        self.root, self.url, self.key_file = Path(root), hardware_url, Path(key_file)
        self.job = None
        self.task_id = None
        self.live = {"status": "idle"}
        self.stopped = asyncio.Event()
        self.play_job = None
        self.response_job = None
        self.client = None
        self.generation = 0
        self.turn_version = 0

    def start(self, number, goal, seconds=120, *, task_id=None, attached=False):
        if self.job and not self.job.done():
            raise ValueError("已有电话正在进行")
        if not self.key_file.is_file():
            raise ValueError("缺少硬件服务密钥")
        self.stopped = asyncio.Event()
        if attached and not task_id:
            raise ValueError("接管通话需要原任务 ID")
        task = self.store.get(task_id) if task_id else self.store.create(goal, [number])
        task["status"] = "running"
        task["channel"] = "sim7600"
        self.store.save(task, "phone_start", {"max_seconds": seconds})
        if attached:
            task["messages"].append(
                {
                    "role": "assistant",
                    "content": "电话操作已提交，正在连接实时语音；接通后会自动交谈。",
                }
            )
            self.store.save(task, "phone_handoff")
        self.task_id = task["id"]
        self.live = {
            "status": "preparing",
            "task_id": task["id"],
            "number": number,
            "turns": 0,
            "sent_bytes": 0,
            "received_bytes": 0,
        }
        self.job = asyncio.create_task(self.run(number, seconds, attached=attached))
        return task

    def event(self, event, **payload):
        self.store.save(self.store.get(self.task_id), event, payload)

    async def request(self, path, body=None):
        if body is None:
            response = await self.client.get(self.url + path)
        else:
            response = await self.client.post(self.url + path, json=body)
        response.raise_for_status()
        return response.json()

    def operation(self):
        return {"owner": self.task_id, "execution_id": uuid.uuid4().hex}

    async def interrupt(self):
        self.turn_version += 1
        for job in (self.play_job, self.response_job):
            if job and job is not asyncio.current_task() and not job.done():
                job.cancel()
                try:
                    await job
                except asyncio.CancelledError:
                    pass
        self.play_job = None
        if self.client and self.live["status"] == "active":
            result = await self.request("/audio/interrupt", self.operation())
            self.generation = result["generation"]
        self.event("phone_interrupted")

    async def stop(self):
        self.stopped.set()
        if self.job and not self.job.done():
            self.job.cancel()
            try:
                await self.job
            except asyncio.CancelledError:
                pass

    async def play(self, pcm):
        generation = self.generation
        for offset in range(0, len(pcm), 1600):
            while not self.stopped.is_set():
                response = await self.client.post(
                    self.url + "/audio/write",
                    json={
                        "owner": self.task_id,
                        "generation": generation,
                        "pcm": base64.b64encode(pcm[offset : offset + 1600]).decode(),
                    },
                )
                if response.status_code == 429:
                    await asyncio.sleep(0.1)
                    continue
                response.raise_for_status()
                break
            if self.stopped.is_set():
                return
        while not self.stopped.is_set():
            state = await self.request(f"/audio/frames?owner={self.task_id}&after=2147483647")
            if state["queued_frames"] == 0:
                return
            await asyncio.sleep(0.05)

    async def reply(self, pcm, version):
        try:
            folder = self.root / "audio"
            folder.mkdir(exist_ok=True)
            name = uuid.uuid4().hex + ".wav"
            audio = pcm_wav(pcm)
            (folder / name).write_bytes(audio)
            self.event("phone_capture", audio=name, duration=len(pcm) / 16000)
            started = time.monotonic()
            text = await asyncio.to_thread(self.speech.transcribe, audio)
            if self.stopped.is_set() or version != self.turn_version:
                return
            self.live["turns"] += 1
            task = self.store.get(self.task_id)
            task["messages"].append({"role": "user", "content": text, "audio": name})
            self.store.save(
                task,
                "phone_transcript",
                {"text": text, "audio": name, "seconds": time.monotonic() - started},
            )
            if any(word in text for word in ("停止测试", "挂断电话", "结束通话")):
                self.stopped.set()
                return
            task["goal"] += "\n当前为电话实时对话，每次回复最多60字，直接与对端交流。不要说工具名。"
            # In this first live path, only non-telephone tools are available to the model.
            catalog = [t for t in self.tools.catalog() if not t["name"].startswith("phone.")]
            started = time.monotonic()
            for _ in range(3):
                action = await asyncio.wait_for(
                    self.model.decide(task, catalog), getattr(self.model, "request_timeout", 60)
                )
                if action.action != "call_tool":
                    break
                if action.tool.startswith("phone."):
                    raise ValueError("Phone control is owned by the voice session")
                result = await self.tools.execute(
                    action.tool, action.arguments, self.task_id, uuid.uuid4().hex
                )
                task["messages"].append(
                    {
                        "role": "tool",
                        "name": action.tool,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            else:
                raise ValueError("Phone tool step limit reached")
            if self.stopped.is_set() or version != self.turn_version:
                return
            current = self.store.get(self.task_id)
            current["messages"].append({"role": "assistant", "content": action.message})
            current["steps"] += 1
            self.store.save(
                current,
                "phone_reply",
                {"text": action.message, "llm_seconds": time.monotonic() - started},
            )
            output = await asyncio.to_thread(self.speech.synthesize, action.message[:400])
            if self.stopped.is_set() or version != self.turn_version:
                return
            output_name = uuid.uuid4().hex + ".wav"
            (folder / output_name).write_bytes(output)
            self.event("phone_tts", audio=output_name)
            self.play_job = asyncio.create_task(self.play(downsample(output)))
            await self.play_job
            if action.action in {"complete", "fail"}:
                self.stopped.set()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.event("phone_turn_error", error=type(exc).__name__)

    async def run(self, number, seconds, *, attached=False):
        try:
            # Load TTS/ASR before placing the call, so the other side does not wait for loading.
            greeting = await asyncio.to_thread(
                self.speech.synthesize,
                "您好，这里是语音助手测试，我会记录测试音频。请问您能听清我的声音吗？",
            )
            await asyncio.to_thread(self.speech.transcribe, greeting)
            async with httpx.AsyncClient(
                timeout=20,
                trust_env=False,
                headers={
                    "Authorization": "Bearer " + self.key_file.read_text(encoding="utf-8").strip()
                },
            ) as client:
                self.client = client
                try:
                    if not attached:
                        await self.request("/dial", {**self.operation(), "number": number})
                    self.live["status"] = "dialing"
                    self.event("phone_dialing")
                    deadline = time.monotonic() + 45
                    while time.monotonic() < deadline:
                        state = await self.request("/status")
                        if any(c["status"] == 0 for c in state["calls"]):
                            break
                        if not state["calls"]:
                            raise RuntimeError("对方未接听或通话已结束")
                        await asyncio.sleep(0.5)
                    else:
                        raise TimeoutError("接听等待超时")
                    state = await self.request("/audio/start", self.operation())
                    self.generation = state["generation"]
                    self.live["status"] = "active"
                    self.event("phone_connected")
                    self.play_job = asyncio.create_task(self.play(downsample(greeting)))
                    endpoint, sequence = Endpoint(), 0
                    deadline = time.monotonic() + seconds
                    while not self.stopped.is_set() and time.monotonic() < deadline:
                        try:
                            state = await self.request(
                                f"/audio/frames?owner={self.task_id}&after={sequence}"
                            )
                        except httpx.HTTPStatusError as exc:
                            if exc.response.status_code == 409:
                                status = await self.request("/status")
                                if not status["calls"]:
                                    self.event("phone_remote_hangup")
                                    break
                            raise
                        if self.play_job and self.play_job.done() and not self.play_job.cancelled():
                            self.play_job.result()
                        self.live.update(
                            sent_bytes=state["sent_bytes"], received_bytes=state["received_bytes"]
                        )
                        if state["error"]:
                            raise OSError("PCM serial connection failed")
                        for frame in state["frames"]:
                            if frame["sequence"] != sequence + 1:
                                endpoint.reset()
                                self.event("phone_audio_gap")
                            sequence = frame["sequence"]
                            onset, utterance = endpoint.feed(base64.b64decode(frame["pcm"]))
                            if onset:
                                await self.interrupt()
                            if utterance:
                                version = self.turn_version
                                self.response_job = asyncio.create_task(
                                    self.reply(utterance, version)
                                )
                        await asyncio.sleep(0.05)
                finally:
                    self.stopped.set()
                    for job in (self.play_job, self.response_job):
                        if job:
                            if not job.done():
                                job.cancel()
                            try:
                                await job
                            except (asyncio.CancelledError, Exception):
                                pass
                    try:
                        await self.request("/hangup", self.operation())
                        self.event("phone_released")
                    except Exception:
                        self.event("phone_cleanup_failed", message="需核对实际电话是否已挂断")
        except asyncio.CancelledError:
            self.live["status"] = "stopped"
        except Exception as exc:
            self.live.update(
                status="error",
                error=str(exc)
                if isinstance(exc, (RuntimeError, TimeoutError))
                else type(exc).__name__,
            )
            self.event("phone_error", error=self.live["error"])
        finally:
            # Attached calls already exist during prewarm; release even if prewarm fails.
            if attached and self.client is None:
                try:
                    await self.tools.execute("phone.hangup", {}, self.task_id, uuid.uuid4().hex)
                    self.event("phone_released")
                except Exception:
                    self.event("phone_cleanup_failed", message="需核对实际电话是否已挂断")
            self.client = None
            if self.live["status"] not in {"error", "stopped"}:
                self.live["status"] = "ended"
            task = self.store.get(self.task_id)
            task["status"] = "failed" if self.live["status"] == "error" else "completed"
            task["result"] = "电话测试结束；请结合音频和转写评估，结束不等于业务目标已完成。"
            self.store.save(task, "phone_ended", dict(self.live))
