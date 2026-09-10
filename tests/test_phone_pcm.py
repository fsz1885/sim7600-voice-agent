import asyncio
import base64
import queue
import time

import pytest

from voice_agent.pcm_bridge import PCMBridge
from voice_agent.platform.state import Store


class Serial:
    def __init__(self, *args, **kwargs):
        self.input = queue.Queue()
        self.output = []

    def read(self, count):
        try:
            return self.input.get(timeout=0.01)
        except queue.Empty:
            return b""

    def write(self, data):
        self.output.append((time.monotonic(), data))
        return len(data)

    def reset_output_buffer(self):
        pass

    def close(self):
        pass


def test_pcm_fragmentation_pacing_and_interrupt():
    serial = Serial()
    bridge = PCMBridge("fake", lambda *a, **kw: serial)
    try:
        serial.input.put(b"a" * 123)
        serial.input.put(b"a" * 517)
        deadline = time.monotonic() + 2
        while bridge.sequence < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        frames = bridge.read(1)["frames"]
        assert len(frames) == 1 and frames[0]["sequence"] == 2
        assert base64.b64decode(frames[0]["pcm"]) == b"a" * 320
        bridge.write(b"b" * 16000, 0)
        deadline = time.monotonic() + 2
        while len(serial.output) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(serial.output) >= 3
        assert serial.output[2][0] - serial.output[0][0] >= 0.03
        assert bridge.interrupt() == 1
        size = len(serial.output)
        time.sleep(0.06)
        assert len(serial.output) == size
        with pytest.raises(ValueError, match="Stale"):
            bridge.write(b"xx", 0)
        with pytest.raises(ValueError):
            bridge.write(b"x", 1)
    finally:
        bridge.close()
    assert all(not thread.is_alive() for thread in bridge.threads)


def test_endpoint_silence_onset_and_end():
    np = pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    from voice_agent.platform.phone import Endpoint

    endpoint = Endpoint()
    silence = bytes(320)
    voiced = np.full(160, 1000, dtype="<i2").tobytes()
    for _ in range(100):
        assert endpoint.feed(silence) == (False, None)
    for _ in range(9):
        assert endpoint.feed(voiced) == (False, None)
    assert endpoint.feed(voiced) == (True, None)
    for _ in range(39):
        assert endpoint.feed(silence) == (False, None)
    onset, audio = endpoint.feed(silence)
    assert not onset and len(audio) == 55 * 320
    assert not endpoint.active


def test_tts_conversion_preserves_duration():
    np = pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    from voice_agent.platform.phone import downsample, pcm_wav

    samples = (np.sin(np.arange(22050) * 2 * np.pi * 440 / 22050) * 10000).astype("<i2")
    pcm = downsample(pcm_wav(samples.tobytes(), 22050))
    assert len(pcm) == 16000
    assert 6500 < np.std(np.frombuffer(pcm, dtype="<i2")) < 7500


def test_audio_routes_enforce_owner_and_generation(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from voice_agent import hardware_service

    class Modem:
        def dial(self, number):
            pass

        def usb_audio(self, enabled):
            pass

        def hangup(self):
            pass

    serial = Serial()
    monkeypatch.setattr(
        hardware_service, "PCMBridge", lambda port: PCMBridge("fake", lambda *a, **k: serial)
    )
    with TestClient(hardware_service.create_app(tmp_path, Modem)) as client:
        client.headers["Authorization"] = (
            "Bearer " + (tmp_path / "hardware-api-key.txt").read_text()
        )
        body = {"owner": "a" * 32, "execution_id": "b" * 32}
        assert client.post("/audio/start", json=body).status_code == 409
        assert client.post("/dial", json={**body, "number": "12345"}).status_code == 200
        assert client.post("/audio/start", json=body).status_code == 200
        assert client.get("/audio/frames", params={"owner": "c" * 32}).status_code == 409
        assert client.post("/audio/interrupt", json=body).json()["generation"] == 1
        payload = {"owner": body["owner"], "generation": 0, "pcm": "AAA="}
        assert client.post("/audio/write", json=payload).status_code == 409
        payload["generation"] = 1
        assert client.post("/audio/write", json=payload).status_code == 200
        assert client.post("/hangup", json={**body, "execution_id": "d" * 32}).status_code == 200
        assert client.get("/audio/frames", params={"owner": body["owner"]}).status_code == 409


def test_stop_during_prewarm_never_dials(tmp_path):
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    from voice_agent.platform.phone import PhoneController, pcm_wav

    class Speech:
        def synthesize(self, text):
            return pcm_wav(bytes(3200))

        def transcribe(self, audio):
            return "测试"

    async def run():
        key = tmp_path / "key"
        key.write_text("test")
        store = Store(tmp_path / "db")
        phone = PhoneController(store, None, None, Speech(), tmp_path, "http://invalid", key)
        task = phone.start("12345", "test")
        with pytest.raises(ValueError, match="已有"):
            phone.start("12345", "test")
        await asyncio.sleep(0)
        await phone.stop()
        assert phone.live["status"] == "stopped"
        assert not any(e["type"] == "phone_dialing" for e in store.events(task["id"]))
        store.close()

    asyncio.run(run())
