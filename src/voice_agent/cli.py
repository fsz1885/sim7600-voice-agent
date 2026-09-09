import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from .compatible_provider import configured_provider
from .core import Agent
from .local_provider import DEFAULT_MODEL, OllamaProvider
from .models import State, Task
from .providers import AnthropicProvider, KimiProvider, MockProvider


async def chat(path: Path, scripted: bool = False) -> int:
    task = Task.model_validate_json(path.read_text(encoding="utf-8"))
    fixture_path = path.with_suffix(".dialogue.json")
    dialogue = json.loads(fixture_path.read_text(encoding="utf-8")) if fixture_path.exists() else []
    provider_name = os.getenv("LLM_PROVIDER", "mock")
    timeout = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
    if provider_name == "mock":
        provider = MockProvider({row["user"]: row["updates"] for row in dialogue})
        print("[Mock: 离线演示，仅支持示例语句、单字段回答或 字段=值；?值 表示含糊。]")
    elif provider_name == "anthropic":
        provider = AnthropicProvider(
            os.getenv("ANTHROPIC_API_KEY", ""),
            os.getenv("LLM_MODEL", ""),
            timeout,
        )
    elif provider_name == "kimi":
        provider = KimiProvider(
            os.getenv("KIMI_API_KEY", ""),
            os.getenv("LLM_MODEL", ""),
            timeout,
            os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1"),
        )
    elif provider_name == "ollama":
        provider = OllamaProvider(
            os.getenv("LLM_MODEL") or DEFAULT_MODEL,
            timeout,
            os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            int(os.getenv("LLM_CONTEXT", "8192")),
        )
    elif provider_name == "compatible":
        provider = configured_provider(
            os.getenv("LLM_MODEL") or None, timeout,
            int(os.getenv("LLM_CONTEXT", "8192")), name="compatible",
        )
    else:
        raise ValueError("LLM_PROVIDER must be mock, anthropic, kimi, ollama or compatible")
    if scripted and provider_name != "mock":
        raise ValueError("demo is offline-only; set LLM_PROVIDER=mock")
    if scripted and not dialogue:
        raise ValueError("No dialogue fixture for this task")
    agent, state = Agent(provider, timeout=timeout), State.for_task(task)
    decision = await agent.handle_turn(task=task, state=state, user_text=None)
    print(f"Agent: {decision.response}")
    rows = iter(dialogue)
    while state.status == "active":
        if scripted:
            row = next(rows, None)
            if row is None:
                break
            user_text = row["user"]
            print(f"User: {user_text}")
        else:
            try:
                user_text = input("User (/quit 退出): ")
            except EOFError:
                break
            if user_text.strip() == "/quit":
                break
        decision = await agent.handle_turn(task=task, state=state, user_text=user_text)
        print(f"Agent [{decision.action}]: {decision.response}")
    print("Conversation completed." if state.status == "completed" else "Conversation incomplete.")
    print(json.dumps(state.result(), ensure_ascii=False, indent=2))
    return 0 if not scripted or state.status == "completed" else 1


def main() -> int:
    # Windows terminals and redirected output should preserve the Chinese example.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Text-only goal-driven voice agent MVP")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("chat", "demo"):
        sub.add_parser(command).add_argument("task", type=Path)
    sub.add_parser("test")
    sub.add_parser("lint")
    args = parser.parse_args()
    if args.command in {"test", "lint"}:
        command = (
            [sys.executable, "-m", "pytest", "-q"]
            if args.command == "test"
            else [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "src",
                "tests",
            ]
        )
        return subprocess.call(command)
    try:
        return asyncio.run(chat(args.task, scripted=args.command == "demo"))
    except (ValueError, OSError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nConversation interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
