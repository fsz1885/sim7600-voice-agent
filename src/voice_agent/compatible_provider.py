"""Explicitly configured Chat Completions server, on this host or the LAN."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .local_provider import OllamaProvider, response_preview
from .providers import ProviderError


class CompatibleProvider(OllamaProvider):
    def __init__(self, model, timeout, base_url, context=8192, api_key=""):
        super().__init__(model, timeout, context=context)
        url = urlsplit(base_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path.rstrip("/") != "/v1"
        ):
            raise ValueError("LLM_BASE_URL must be an HTTP(S) endpoint ending in /v1")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def propose(self, task, state, error=None):
        if self.on_preview:
            self.on_preview("")
        source = self.request_payload(task, state, error)
        payload = {
            "model": self.model,
            "messages": source["messages"],
            "temperature": 0,
            "max_tokens": 1200,
            "reasoning_effort": "none",
            "stream": self.on_preview is not None,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "decision", "strict": True, "schema": source["format"]},
            },
        }
        output, finish, usage = "", None, {}
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, trust_env=False, headers=self.headers
            ) as client:
                if not payload["stream"]:
                    response = await client.post(self.base_url + "/chat/completions", json=payload)
                    response.raise_for_status()
                    data = response.json()
                    choice = data["choices"][0]
                    output = choice["message"]["content"]
                    finish, usage = choice.get("finish_reason"), data.get("usage", {})
                else:
                    payload["stream_options"] = {"include_usage": True}
                    done = False
                    async with client.stream(
                        "POST", self.base_url + "/chat/completions", json=payload
                    ) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            raw = line[5:].strip()
                            if raw == "[DONE]":
                                done = True
                                break
                            data = json.loads(raw)
                            if data.get("error"):
                                raise ProviderError("Compatible model stream failed")
                            if data.get("usage"):
                                usage = data["usage"]
                            for choice in data.get("choices", []):
                                if choice.get("index", 0) != 0:
                                    continue
                                output += choice.get("delta", {}).get("content") or ""
                                finish = choice.get("finish_reason") or finish
                                if len(output) > 20000:
                                    raise ProviderError("Compatible model output too large")
                                self.on_preview(response_preview(output))
                    if not done:
                        raise ProviderError("Compatible model stream interrupted")
            if finish != "stop" or not isinstance(output, str) or not output.strip():
                raise ProviderError("Compatible model returned incomplete output")
            self.calls.append({"model": self.model, "usage": usage})
            self.calls = self.calls[-80:]
            return output
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError) as exc:
            raise ProviderError("Compatible model request failed") from exc
        finally:
            if self.on_preview:
                self.on_preview("")


def configured_provider(model=None, timeout=90, context=8192, name=None):
    """Selection is server configuration; browser requests cannot override destinations."""
    name = name or os.getenv("VOICE_LLM_PROVIDER", "ollama")
    if name == "ollama":
        return OllamaProvider(
            model or "qwen3.5:4b", timeout,
            os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"), context,
        )
    if name == "compatible":
        key_file = os.getenv("LLM_API_KEY_FILE", "")
        key = Path(key_file).read_text(encoding="utf-8-sig").strip() if key_file else os.getenv(
            "LLM_API_KEY", ""
        )
        return CompatibleProvider(
            model or "sim7600-local", timeout,
            os.getenv("LLM_BASE_URL", "http://127.0.0.1:8080/v1"), context, key,
        )
    raise ValueError("VOICE_LLM_PROVIDER must be ollama or compatible")
