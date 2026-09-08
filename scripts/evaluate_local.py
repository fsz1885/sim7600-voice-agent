"""Explicit real local inference smoke evaluation; no cloud keys or fixture answers sent."""

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

from voice_agent import Agent, State, Task
from voice_agent.local_provider import DEFAULT_MODEL, OllamaProvider
from voice_agent.speech import LocalSpeech


async def evaluate(output, models, model):
    if output.exists():
        raise FileExistsError("Choose a new output to preserve previous measurements")
    provider = OllamaProvider(model=model)
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        response = await client.get(provider.base_url + "/api/tags")
        response.raise_for_status()
        if model not in {m["name"] for m in response.json()["models"]}:
            raise RuntimeError("Model not installed; evaluation did not start")
    cases = [
        (
            "不确定",
            ["费用", "材料"],
            ["大概两千多，具体我不确定。"],
            lambda s: s.fields["费用"].status == "partial" and s.status != "completed",
        ),
        (
            "更正",
            ["费用", "材料"],
            ["费用是每年三千元。", "刚才说错了，不是三千，是每年两千五百元。"],
            lambda s: (
                s.fields["费用"].status == "confirmed"
                and len(s.fields["费用"].evidence) == 2
                and any(x in s.fields["费用"].value for x in ("2500", "两千五百", "二千五百"))
            ),
        ),
        (
            "否定",
            ["实际办公", "材料"],
            ["不要求实际办公，材料我还不清楚。"],
            lambda s: (
                s.fields["实际办公"].status == "confirmed"
                and s.fields["材料"].status != "confirmed"
                and s.status != "completed"
            ),
        ),
        (
            "条件与完成",
            ["地址费用", "是否强制代理记账"],
            ["地址免费，但必须在这里购买代理记账服务。"],
            lambda s: s.status == "completed" and "记账" in s.fields["地址费用"].value,
        ),
        (
            "明确不适用",
            ["银行开户费用"],
            ["不提供银行开户服务，所以银行开户费用不适用。"],
            lambda s: s.status == "completed" and "不适用" in s.fields["银行开户费用"].value,
        ),
    ]
    rows = []
    for name, fields, utterances, check in cases:
        task = Task(goal="准确确认" + "、".join(fields), required_fields=fields)
        state, agent = State.for_task(task), Agent(provider, timeout=90)
        turns = []
        for text in [None, *utterances]:
            start = time.perf_counter()
            decision = await agent.handle_turn(task=task, state=state, user_text=text)
            turns.append(
                {
                    "text": text,
                    "decision": decision.model_dump(),
                    "seconds": time.perf_counter() - start,
                }
            )
        passed = bool(check(state)) and all(
            t["decision"]["action"] not in {"retry", "handoff"} for t in turns
        )
        rows.append({"name": name, "passed": passed, "turns": turns, "result": state.result()})
        print(f"{name}: {'PASS' if passed else 'FAIL'}", flush=True)
    speech = LocalSpeech(models)
    speech_rows = []
    for text in [
        "您好，请问注册地址的费用是多少？",
        "不是三千元，是每年两千五百元，不包含刻章费用。",
    ]:
        start = time.perf_counter()
        audio = await asyncio.to_thread(speech.synthesize, text)
        tts = time.perf_counter() - start
        start = time.perf_counter()
        transcript = await asyncio.to_thread(speech.transcribe, audio)
        speech_rows.append(
            {
                "synthetic_input": text,
                "transcript": transcript,
                "tts_seconds": tts,
                "asr_seconds": time.perf_counter() - start,
            }
        )
    durations = [t["seconds"] for row in rows for t in row["turns"]]
    result = {
        "model": provider.model,
        "context": provider.context,
        "method": "Synthetic written scenarios; TTS-to-ASR loopback, not real phone data. "
        "Durations include cold loads and repairs; no browser playback latency.",
        "passed": sum(r["passed"] for r in rows),
        "cases": rows,
        "median_decision_seconds": statistics.median(durations),
        "max_decision_seconds": max(durations),
        "calls": provider.calls,
        "speech": speech_rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "total": len(rows),
                "median_seconds": result["median_decision_seconds"],
            }
        ),
        flush=True,
    )
    return 0 if result["passed"] == len(rows) else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--models", type=Path, default=Path("models"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(evaluate(args.output, args.models, args.model)))
