"""Local Ollama adapter. No API key or automatic cloud fallback."""

import json
import re
from urllib.parse import urlsplit

import httpx

from .models import Proposal, State, Task
from .providers import SYSTEM_PROMPT, ProviderError

DEFAULT_MODEL = "qwen3.5:4b"


def response_preview(raw):
    """Decode only the leading response string, never partial JSON/schema fields."""
    match = re.match(r'^\s*\{\s*"response"\s*:\s*("(?:[^"\\]|\\.)*)', raw)
    if not match:
        return ""
    value = match.group(1)
    for trim in range(min(12, len(value))):
        try:
            text = json.loads((value[:-trim] if trim else value) + '"')
            return text.encode("utf-16", "surrogatepass").decode("utf-16").rstrip()[:400]
        except (ValueError, UnicodeError):
            pass
    return ""


def local_schema(task: Task, opening=False):
    schema = Proposal.model_json_schema()
    schema["properties"]["response"].update(minLength=1, maxLength=400)
    schema["properties"]["reason"].update(minLength=1, maxLength=200)
    schema["properties"]["target_field"] = {"enum": [*task.required_fields, None]}
    update = schema["$defs"]["Update"]["properties"]
    update["field"] = {"type": "string", "enum": task.required_fields}
    for key in ("value", "evidence"):
        update[key]["minLength"] = 1
    if opening:
        schema["properties"]["updates"]["maxItems"] = 0
        schema["properties"]["action"] = {"type": "string", "enum": ["continue"]}
        schema["properties"]["target_field"] = {"type": "string", "enum": task.required_fields}
    return schema


class OllamaProvider:
    def __init__(
        self, model=DEFAULT_MODEL, timeout=90, base_url="http://127.0.0.1:11434", context=8192
    ):
        url = urlsplit(base_url)
        if (
            url.scheme != "http"
            or url.hostname not in {"127.0.0.1", "localhost", "::1", "ollama"}
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ValueError("Local model URL must use HTTP loopback or the ollama Docker service")
        if not model or timeout <= 0 or context < 2048:
            raise ValueError("Invalid local model configuration")
        self.model, self.timeout = model, timeout
        self.base_url, self.context = base_url.rstrip("/"), context
        self.calls: list[dict] = []
        self.on_preview = None

    async def propose(self, task: Task, state: State, error: str | None = None) -> str:
        if self.on_preview:
            self.on_preview("")
        content = json.dumps(
            {
                "task": task.model_dump(),
                "fields": {n: f.model_dump() for n, f in state.fields.items()},
                "history": [m.model_dump() for m in state.history],
                "latest_user_message": state.history[-1].content
                if state.history and state.history[-1].role == "user"
                else None,
                "repair": error,
                "instruction": "JSON 第一个属性必须是 response。然后输出其余属性。"
                "这是对方的最新回答，必须将其中每个相关字段写入 updates，"
                "reason 中的描述不算更新。先提取，再问一个未确认字段。"
                "latest_user_message=null 时请主动询问一个具体字段，不要要求对方提供消息。"
                "finish 时 response 也必须是非空中文结束语。"
                "带前提条件不等于信息不完整；明确的必要条件应保留在 value 并 confirmed，"
                "不要仅仅为了重复确认而标成 partial。"
                "提问目标若在当前状态或本轮更新中为 partial，action 必须是 clarify。",
            },
            ensure_ascii=False,
        )
        # Conservative admission budget; never silently discard old evidence/history.
        schema = local_schema(task, opening=not state.history)
        prompt = SYSTEM_PROMPT + "\nJSON Schema:\n" + json.dumps(schema, ensure_ascii=False)
        if len(content) + len(prompt) > self.context - 1600:
            raise ProviderError("Local context budget exceeded")
        payload = {
            "model": self.model,
            "stream": self.on_preview is not None,
            "think": False,
            "keep_alive": "30m",
            "format": schema,
            "options": {"temperature": 0, "num_ctx": self.context, "num_predict": 1200},
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": content},
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
                if self.on_preview is None:
                    response = await client.post(self.base_url + "/api/chat", json=payload)
                    response.raise_for_status()
                    data = response.json()
                    output = data["message"]["content"]
                else:
                    output, data = "", {}
                    async with client.stream(
                        "POST", self.base_url + "/api/chat", json=payload
                    ) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if not line:
                                continue
                            data = json.loads(line)
                            if data.get("error"):
                                raise ProviderError("Local streaming request failed")
                            output += data.get("message", {}).get("content", "")
                            if len(output) > 20000:
                                raise ProviderError("Local streaming output too large")
                            self.on_preview(response_preview(output))
                            if data.get("done"):
                                break
            if data.get("done") is not True or data.get("done_reason") != "stop":
                raise ProviderError("Local model returned incomplete output")
            if not isinstance(output, str) or not output.strip():
                raise ProviderError("Local model returned no text")
            self.calls.append(
                {
                    key: data.get(key)
                    for key in (
                        "model",
                        "total_duration",
                        "load_duration",
                        "prompt_eval_count",
                        "prompt_eval_duration",
                        "eval_count",
                        "eval_duration",
                    )
                }
            )
            self.calls = self.calls[-80:]
            return output
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise ProviderError("Local model request failed") from exc
