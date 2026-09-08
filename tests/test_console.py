import asyncio
import io
import wave

import httpx
import pytest

pytest.importorskip("fastapi")
np = pytest.importorskip("numpy")

from voice_agent.console import create_app  # noqa: E402
from voice_agent.providers import MockProvider  # noqa: E402
from voice_agent.speech import decode_wav  # noqa: E402

HEADERS = {"X-Voice-Console": "1"}


def wav_bytes(silent=False, seconds=1):
    data = io.BytesIO()
    with wave.open(data, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        samples = (
            np.zeros(int(16000 * seconds))
            if silent
            else (np.sin(np.arange(int(16000 * seconds)) * 0.1) * 10000)
        )
        wav.writeframes(samples.astype("<i2").tobytes())
    return data.getvalue()


class Speech:
    def availability(self):
        return {"asr": True, "tts": True}

    def transcribe(self, data):
        decode_wav(data)
        return "每年2400元"

    def synthesize(self, text):
        return wav_bytes()


async def settled(client, sid):
    for _ in range(100):
        data = (await client.get(f"/api/sessions/{sid}")).json()
        if not data["busy"]:
            return data
        await asyncio.sleep(0.01)
    raise AssertionError("Session did not settle")


def test_voice_round_trip_and_evidence(tmp_path):
    async def run():
        app = create_app(tmp_path, provider_factory=MockProvider, speech_engine=Speech())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=HEADERS
        ) as client:
            response = await client.post(
                "/api/sessions",
                json={
                    "goal": "确认费用",
                    "required_fields": ["费用"],
                    "speech": True,
                },
            )
            assert response.status_code == 200
            sid = response.json()["id"]
            await settled(client, sid)
            response = await client.post(f"/api/sessions/{sid}/transcribe", content=wav_bytes())
            assert response.json()["text"] == "每年2400元"
            before = (await client.get(f"/api/sessions/{sid}")).json()
            assert before["state"]["turns"] == 0  # ASR is not silently submitted.
            await client.post(f"/api/sessions/{sid}/turn", json={"text": "每年2400元"})
            result = await settled(client, sid)
            assert result["result"]["completed"] is True
            assert result["state"]["fields"]["费用"]["evidence"][0]["quote"] == "每年2400元"
            assert {"asr", "tts", "llm", "fields", "complete"} <= {
                e["stage"] for e in result["events"]
            }
            audio = next(
                e["audio"] for e in result["events"] if e["stage"] == "tts" and "audio" in e
            )
            assert (await client.get(audio)).content.startswith(b"RIFF")
            assert (await client.get(f"/api/sessions/{sid}/audio/missing.wav")).status_code == 404
            assert (
                await client.post(f"/api/sessions/{sid}/turn", json={"text": "修改"})
            ).status_code == 409
            assert (await client.get(f"/api/sessions/{sid}/export")).json()["result"]["completed"]
            assert (tmp_path / sid / "session.json").is_file()

    asyncio.run(run())


def test_stop_in_flight_does_not_commit_late_model_result(tmp_path):
    async def run():
        release = asyncio.Event()

        class Slow(MockProvider):
            async def propose(self, *args, **kw):
                await release.wait()
                return await super().propose(*args, **kw)

        app = create_app(tmp_path, provider_factory=Slow, speech_engine=Speech())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=HEADERS
        ) as client:
            res = await client.post(
                "/api/sessions", json={"goal": "费用", "required_fields": ["费用"]}
            )
            sid = res.json()["id"]
            assert (
                await client.post(f"/api/sessions/{sid}/turn", json={"text": "1"})
            ).status_code == 409
            await client.post(f"/api/sessions/{sid}/stop", json={})
            release.set()
            result = await settled(client, sid)
            assert result["stopped"] and not result["result"]["completed"]
            assert result["state"]["history"] == []

    asyncio.run(run())


def test_local_request_boundaries(tmp_path):
    async def run():
        app = create_app(tmp_path, speech_engine=Speech())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            assert (await client.post("/api/sessions", json={})).status_code == 403
            assert (await client.get("/", headers={"Host": "evil.example"})).status_code == 403
            assert (
                await client.post(
                    "/api/sessions",
                    json={},
                    headers={
                        **HEADERS,
                        "Origin": "https://evil.example",
                    },
                )
            ).status_code == 403
            assert (
                await client.post(
                    "/api/sessions",
                    json={"goal": "a", "required_fields": ["x", "x"]},
                    headers=HEADERS,
                )
            ).status_code == 422

    asyncio.run(run())


@pytest.mark.parametrize(
    "data",
    [b"not wav", wav_bytes(True), wav_bytes(seconds=31), wav_bytes()[:-20]],
    ids=["invalid", "silence", "too-long", "truncated"],
)
def test_invalid_or_silent_audio_rejected(data):
    with pytest.raises(ValueError):
        decode_wav(data)


def test_automatic_audio_turn_keeps_source_and_accepts_correction(tmp_path):
    async def run():
        class CorrectingSpeech(Speech):
            text = "每年2400元"

            def transcribe(self, data):
                decode_wav(data)
                return self.text

        speech = CorrectingSpeech()
        app = create_app(
            tmp_path,
            provider_factory=lambda: MockProvider(
                {
                    text: [
                        {"field": "费用", "value": text, "evidence": text, "status": "confirmed"}
                    ]
                    for text in ["每年2400元", "更正，每年3000元"]
                }
            ),
            speech_engine=speech,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=HEADERS
        ) as client:
            sid = (
                await client.post(
                    "/api/sessions",
                    json={"goal": "确认费用", "required_fields": ["费用"], "speech": True},
                )
            ).json()["id"]
            await settled(client, sid)
            for turn, text in enumerate(["每年2400元", "更正，每年3000元"], 1):
                speech.text = text
                response = await client.post(f"/api/sessions/{sid}/audio-turn", content=wav_bytes())
                assert response.status_code == 200
                assert response.json()["accepted"]
                result = await settled(client, sid)
                assert result["state"]["turns"] == turn
                assert result["state"]["fields"]["费用"]["evidence"][-1]["quote"] == text
                source = next(
                    e for e in result["events"] if e["stage"] == "asr" and e.get("turn") == turn
                )
                assert (await client.get(source["audio"])).content == wav_bytes()
                assert any(
                    e["stage"] == "tts"
                    and e.get("audio")
                    and e["generation"] == response.json()["generation"]
                    for e in result["events"]
                )
            await client.post(f"/api/sessions/{sid}/stop", json={})
            assert (
                await client.post(f"/api/sessions/{sid}/audio-turn", content=wav_bytes())
            ).status_code == 409

    asyncio.run(run())


def test_stop_during_automatic_asr_discards_late_input(tmp_path):
    import threading

    async def run():
        entered, release = threading.Event(), threading.Event()

        class SlowSpeech(Speech):
            def transcribe(self, data):
                entered.set()
                release.wait(3)
                return super().transcribe(data)

        app = create_app(tmp_path, provider_factory=MockProvider, speech_engine=SlowSpeech())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=HEADERS
        ) as client:
            sid = (
                await client.post(
                    "/api/sessions", json={"goal": "确认费用", "required_fields": ["费用"]}
                )
            ).json()["id"]
            await settled(client, sid)
            job = asyncio.create_task(
                client.post(f"/api/sessions/{sid}/audio-turn", content=wav_bytes())
            )
            assert await asyncio.to_thread(entered.wait, 2)
            await client.post(f"/api/sessions/{sid}/stop", json={})
            release.set()
            assert (await job).status_code == 409
            result = await settled(client, sid)
            assert result["state"]["turns"] == 0
            assert not any(e["stage"] == "input" for e in result["events"])

    asyncio.run(run())


def test_new_browser_can_find_and_end_abandoned_session(tmp_path):
    async def run():
        app = create_app(tmp_path, provider_factory=MockProvider, speech_engine=Speech())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", headers=HEADERS
        ) as first:
            assert (await first.get("/api/current-session")).json() is None
            sid = (
                await first.post(
                    "/api/sessions",
                    json={"goal": "确认费用", "required_fields": ["费用"], "speech": False},
                )
            ).json()["id"]
            await settled(first, sid)
        # New client has no cookies, tab storage or knowledge of the session ID.
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", headers=HEADERS
        ) as reopened:
            recovered = (await reopened.get("/api/current-session")).json()
            assert recovered["id"] == sid
            assert recovered["state"]["history"]
            await reopened.post(f"/api/sessions/{sid}/stop", json={})
            assert (await reopened.get("/api/current-session")).json() is None
            assert (
                await reopened.post(
                    "/api/sessions",
                    json={"goal": "新目标", "required_fields": ["材料"], "speech": False},
                )
            ).status_code == 200
            new_sid = (await reopened.get("/api/current-session")).json()["id"]
            await settled(reopened, new_sid)

    asyncio.run(run())
