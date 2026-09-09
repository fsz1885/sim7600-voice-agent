"""Opt-in real HTTP/speech/model smoke. Input is synthetic, never real call data."""

import argparse
import json
import time
from pathlib import Path

import httpx

from voice_agent.speech import LocalSpeech


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--mode", choices=["local", "mock"], default="local")
    parser.add_argument("--models", type=Path, default=Path("models"))
    parser.add_argument("--output", type=Path, default=Path("local-data/continuous-smoke.json"))
    args = parser.parse_args()
    speech = LocalSpeech(args.models)
    with httpx.Client(
        base_url=args.base_url, timeout=180, trust_env=False, headers={"X-Voice-Console": "1"}
    ) as client:
        assets = [
            "/",
            "/app.js",
            "/vendor/vad/bundle.min.js",
            "/vendor/vad/silero_vad_v5.onnx",
            "/vendor/vad/vad.worklet.bundle.min.js",
            "/vendor/ort/ort.wasm.min.js",
            "/vendor/ort/ort-wasm-simd-threaded.mjs",
            "/vendor/ort/ort-wasm-simd-threaded.wasm",
        ]
        for path in assets:
            response = client.get(path)
            response.raise_for_status()
            assert response.content
        response = client.post(
            "/api/sessions",
            json={
                "goal": "确认每年的费用",
                "required_fields": ["费用"],
                "mode": args.mode,
                "speech": True,
            },
        )
        response.raise_for_status()
        sid = response.json()["id"]

        def settled():
            until = time.monotonic() + 200
            while time.monotonic() < until:
                response = client.get(f"/api/sessions/{sid}")
                response.raise_for_status()
                data = response.json()
                if not data["busy"]:
                    return data
                time.sleep(0.3)
            raise TimeoutError("Session did not settle")

        try:
            settled()
            texts = ["费用大概两千多，具体我还不确定。", "费用确定为每年两千四百元。"]
            if args.mode == "local":
                texts.append("更正一下，刚才说错了，费用是每年三千元。")
            decisions = []
            for index, text in enumerate(texts, 1):
                response = client.post(
                    f"/api/sessions/{sid}/audio-turn",
                    content=speech.synthesize(text),
                    headers={"Content-Type": "audio/wav"},
                )
                response.raise_for_status()
                assert response.json()["accepted"]
                data = settled()
                assert data["state"]["turns"] == index
                decisions.append(
                    {
                        "source": text,
                        "transcript": response.json()["text"],
                        "fields": data["state"]["fields"],
                    }
                )
                print(json.dumps(decisions[-1], ensure_ascii=False), flush=True)
            for event in data["events"]:
                if event.get("audio"):
                    assert client.get(event["audio"]).content.startswith(b"RIFF")
            health = client.get("/api/health").json()
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(
                    {
                        "method": "Synthetic audio-turn HTTP API with real ASR/Core/TTS; "
                        "no physical microphone or browser playback tested.",
                        "mode": args.mode,
                        "health": health,
                        "decisions": decisions,
                        "session": data,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            assert data["result"]["completed"]
            if args.mode == "local":
                assert (
                    "3000" in data["state"]["fields"]["费用"]["value"]
                    or "三千" in data["state"]["fields"]["费用"]["value"]
                )
            print("Automatic audio HTTP smoke: PASS", flush=True)
        finally:
            client.post(f"/api/sessions/{sid}/stop", json={}).raise_for_status()


if __name__ == "__main__":
    main()
