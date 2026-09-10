"""Local operator configuration. Secrets never appear in read API responses."""

import json
import os
import tempfile
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["kimi", "openai-compatible"]
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr = SecretStr("")
    clear_key: bool = False
    timeout_seconds: int = Field(default=60, ge=5, le=180)

    @model_validator(mode="after")
    def validate_endpoint(self):
        self.base_url = self.base_url.strip().rstrip("/")
        self.model = self.model.strip()
        url = urlsplit(self.base_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or any(c.isspace() for c in self.base_url)
            or not self.model
        ):
            raise ValueError("需要有效的 HTTP(S) 服务地址和模型名称；地址不能包含密钥")
        if self.provider == "kimi" and self.base_url not in {
            "https://api.moonshot.cn/v1",
            "https://api.moonshot.ai/v1",
            "https://api.kimi.com/coding/v1",
        }:
            raise ValueError("Kimi 模式需要官方端点；其他服务请选择 OpenAI 兼容接口")
        key = self.api_key.get_secret_value()
        if len(key) > 4096 or any(c.isspace() for c in key):
            raise ValueError("密钥格式无效")
        if self.clear_key and key:
            raise ValueError("不能同时填写新密钥和清除密钥")
        return self


def write_settings(path, settings):
    if path is None:
        raise ValueError("未配置保存路径")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = settings.model_dump(mode="json")
    data["api_key"] = settings.api_key.get_secret_value()
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".model-settings-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
