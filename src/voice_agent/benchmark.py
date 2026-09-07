"""Opt-in live Kimi benchmark. Never invoked by tests or CI."""

import argparse
import asyncio
import json
import math
import os
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

from .core import Agent
from .models import State, Task
from .providers import KimiProvider, ProviderError


class TimedProvider:
    def __init__(self, provider):
        self.provider = provider
        self.calls = []

    async def propose(self, task, state, error=None):
        start = time.perf_counter()
        record = {"repair": error is not None, "success": False}
        try:
            result = await self.provider.propose(task, state, error)
            record["success"] = True
            return result
        except ProviderError as exc:
            record["error"] = str(exc)
            raise
        finally:
            record["seconds"] = round(time.perf_counter() - start, 3)
            self.calls.append(record)


async def benchmark(path: Path) -> dict:
    task = Task.model_validate_json(path.read_text(encoding="utf-8"))
    rows = json.loads(path.with_suffix(".dialogue.json").read_text(encoding="utf-8"))
    timeout = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
    model = os.environ["LLM_MODEL"]
    base_url = os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
    provider = TimedProvider(KimiProvider(os.environ["KIMI_API_KEY"], model, timeout, base_url))
    state, turns, used = State.for_task(task), [], set()
    agent = Agent(provider, timeout=timeout)
    user_text = None
    for _ in range(12):
        started = time.perf_counter()
        decision = await agent.handle_turn(task=task, state=state, user_text=user_text)
        seconds = round(time.perf_counter() - started, 3)
        turns.append(
            {
                "user": user_text,
                "seconds": seconds,
                "decision": decision.model_dump(),
            }
        )
        print(f"turn={len(turns)} action={decision.action} seconds={seconds}", flush=True)
        if state.status != "active" or decision.action == "retry":
            break
        # Follow the real model's question, using canned counterpart answers only.
        # Fixture updates select an answer; they are NEVER submitted as model state.
        next_row = next(
            (
                i
                for i, row in enumerate(rows)
                if i not in used
                and any(u["field"] == decision.target_field for u in row["updates"])
            ),
            None,
        )
        if next_row is None:
            break
        used.add(next_row)
        user_text = rows[next_row]["user"]
    durations = [t["seconds"] for t in turns if t["decision"]["action"] not in ("retry", "handoff")]
    return {
        "recorded_at": datetime.now(UTC).isoformat(),
        "model": model,
        "base_url": base_url,
        "measurement": "Non-streaming full Agent decision wall time, including HTTP and repair",
        "sample_note": "One dialogue; not a load test or time-to-first-token measurement",
        "summary": {
            "turns": len(turns),
            "api_calls": len(provider.calls),
            "successful_http_calls": sum(c["success"] for c in provider.calls),
            "repair_calls": sum(c["repair"] for c in provider.calls),
            "mean_seconds": round(statistics.mean(durations), 3) if durations else None,
            "median_seconds": round(statistics.median(durations), 3) if durations else None,
            "p95_nearest_rank_seconds": sorted(durations)[math.ceil(len(durations) * 0.95) - 1]
            if durations
            else None,
            "min_seconds": min(durations) if durations else None,
            "max_seconds": max(durations) if durations else None,
            "clarification_observed": any(t["decision"]["action"] == "clarify" for t in turns),
        },
        "result": state.result(),
        "turns": turns,
        "api_calls": provider.calls,
    }


def main():
    parser = argparse.ArgumentParser(description="Explicit live Kimi benchmark; consumes API quota")
    parser.add_argument("task", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(benchmark(args.task))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0 if report["result"]["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
