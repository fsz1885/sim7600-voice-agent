"""Opt-in, synthetic local profiling. Uses installed models; never downloads models.

Run while the console is idle. First speech call includes engine initialization,
but filesystem caches and the Ollama model may already be warm.
"""

import argparse
import asyncio
import io
import json
import time
import wave
from pathlib import Path

import numpy as np

from voice_agent import Agent, State, Task
from voice_agent.local_provider import OllamaProvider
from voice_agent.speech import LocalSpeech


def wav_info(data):
    with wave.open(io.BytesIO(data), "rb") as wav:
        rate = wav.getframerate()
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2") / 32768
    window = rate // 50
    energy = np.sqrt(
        np.mean(samples[: len(samples) // window * window].reshape(-1, window) ** 2, 1)
    )
    active = np.flatnonzero(energy > 0.008)
    return {
        "audio_seconds": len(samples) / rate,
        "leading_quiet_seconds": float(active[0] * window / rate) if len(active) else None,
        "trailing_quiet_seconds": float((len(energy) - 1 - active[-1]) * window / rate)
        if len(active)
        else None,
        "peak": float(np.max(np.abs(samples))),
    }


async def run(args):
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"synthetic": True, "speech": [], "llm": []}
    texts = [
        "您好，请问注册地址的费用是多少？",
        "不是三千元，是每年两千五百元，不包含刻章费用。",
        "好的，我确认一下。地址费用是每年两千五百元，对吗？还需要准备哪些材料呢？",
    ]
    for threads in (1, 2, 4):
        speech = LocalSpeech(args.models, threads=threads)
        for index in range(7):
            text = texts[0 if index == 0 else (index - 1) % len(texts)]
            start = time.perf_counter()
            data = speech.synthesize(text)
            elapsed = time.perf_counter() - start
            row = {
                "threads": threads,
                "index": index,
                "engine_first_call": index == 0,
                "characters": len(text),
                "tts_seconds": elapsed,
                **wav_info(data),
            }
            start = time.perf_counter()
            speech.transcribe(data)
            row["asr_seconds"] = time.perf_counter() - start
            report["speech"].append(row)
            if threads == 2 and 1 <= index <= 3:
                (args.output / f"synthetic-{index}.wav").write_bytes(data)
        if threads == 2:
            with wave.open(io.BytesIO(data), "rb") as wav:
                rate = wav.getframerate()
                raw = wav.readframes(wav.getnframes())
            raw = (raw * (24 * rate * 2 // len(raw) + 1))[: 24 * rate * 2]
            prefixes = []
            for step in range(1, 21):
                out = io.BytesIO()
                with wave.open(out, "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(rate)
                    wav.writeframes(raw[: int(step * 1.2 * rate) * 2])
                start = time.perf_counter()
                speech.transcribe(out.getvalue())
                prefixes.append(time.perf_counter() - start)
            report["asr_growing_prefixes_seconds"] = prefixes
        del speech
        print(f"speech threads={threads} complete", flush=True)

    provider = OllamaProvider(base_url=args.ollama_url)
    task = Task(goal="确认注册地址费用和所需材料", required_fields=["费用", "材料"])
    state, agent = State.for_task(task), Agent(provider, timeout=90)
    for user_text in (None, "费用是每年三千元。", "刚才说错了，是每年两千五百元。", "需要身份证。"):
        start, changes = time.perf_counter(), []

        def preview(text, changes=changes, start=start):
            if text and (not changes or changes[-1][1] != text):
                changes.append((time.perf_counter() - start, text))

        provider.on_preview = preview
        before = len(provider.calls)
        result = await agent.handle_turn(task=task, state=state, user_text=user_text)
        report["llm"].append(
            {
                "turn": len(report["llm"]),
                "wall_seconds": time.perf_counter() - start,
                "first_draft_seconds": changes[0][0] if changes else None,
                "last_draft_change_seconds": changes[-1][0] if changes else None,
                "response_characters": len(result.response),
                "action": result.action,
                "calls": provider.calls[before:],
            }
        )
        print(f"llm turn={len(report['llm'])} complete", flush=True)
    (args.output / "metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    asyncio.run(run(parser.parse_args()))
