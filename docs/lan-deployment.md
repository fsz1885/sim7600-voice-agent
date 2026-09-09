# 内网 LLM 与本机语音控制台

Core 使用 Provider 接口，字段证据和原子更新校验与部署位置无关。
控制台通过 `VOICE_LLM_PROVIDER=ollama|compatible` 选择协议；CLI 通过
`LLM_PROVIDER=compatible` 使用相同兼容适配器。浏览器的“配置的大模型”使用
服务端配置，不能通过浏览器请求任意地址。切换配置后重启控制台。

| 配置 | Ollama 本机 / 原 Docker 部署 | llama.cpp 本机或内网 |
| --- | --- | --- |
| VOICE_LLM_PROVIDER | ollama（默认） | compatible |
| 地址变量 | OLLAMA_BASE_URL | LLM_BASE_URL，包含 /v1 |
| 例子 | http://127.0.0.1:11434 | http://10.3.71.235:8080/v1 |
| LLM_MODEL | qwen3.5:4b | sim7600-local |
| 认证 | 无 | LLM_API_KEY_FILE，或 LLM_API_KEY |

兼容接口使用 Chat Completions、Bearer、JSON Schema 和 SSE。流式仅预览 response，
完整 stop + DONE 后交给 Core；截断、断流、认证错误不会提交部分字段，无云端回退。
TTS 仍等 Core 校验通过后合成。不是逐句音频流式播放。
兼容适配器针对当前 llama.cpp 验证，不保证所有兼容服务都支持同样的 Schema 和
reasoning_effort 参数。原 Ollama 地址限制保持不变。

## 本机启动（CPU 语音 + 内网 3070）

将独立 LLM 密钥放入仓库 `local-data/3070-api-key.txt`，该目录不提交 Git，
也不进入 Docker 构建上下文。Compose 将其作为只读 secret 文件提供给后端，
浏览器、健康接口和日志不返回密钥。

已有历史评估语音权重时执行：

```powershell
docker compose -f compose.lan.yml up -d --build
docker compose -f compose.lan.yml ps
docker compose -f compose.lan.yml logs --tail 50 console
```

打开 http://127.0.0.1:8765。Docker Desktop 显示 sim7600-console，
LLM 在另一台机器运行，因此本机不启动 Ollama 容器或占用本机 GPU。
ASR 为 CPU SenseVoice INT8，TTS 为 CPU Matcha + Vocos。
语音目录挂载自 `docs/speech-evaluation/models`；新机器需要先安装这些权重。
可通过 VOICE_REMOTE_URL / VOICE_REMOTE_MODEL 环境变量覆盖 Compose 默认目标。
网页仅本机可访问；录音保存在本机，转写及字段上下文经内网发送到模型服务器。

```powershell
# 停止控制台，保留数据，不停止远端 LLM
docker compose -f compose.lan.yml down
# 恢复原本机 GPU + Ollama 部署（需要本机 GPU 和对应模型）
docker compose -f compose.voice.yml up -d --build
```

两种 Compose 使用同一个控制台名称和端口，不要同时启动。
容器具有 unless-stopped 重启策略；Docker Desktop 本身需要处于运行状态。
客户端已授权地址为 10.3.81.19/32，服务端地址为 10.3.71.235；DHCP/VPN 改变后
应重新检查路由、精确防火墙范围及接口健康。

## 验证

自动化测试不需要真实密钥；真实回环会调用内网模型并创建合成测试会话：

```powershell
.venv/Scripts/python.exe -m pytest -q
npm test
.venv/Scripts/python.exe scripts/smoke_continuous.py --models docs/speech-evaluation/models --output local-data/lan-3070-smoke.json
```

真实回环使用 TTS 合成输入，经过 HTTP 音频提交、ASR、远端 LLM、Core、TTS，
不代表真实麦克风、扬声器或 SIM7600 电话线路验收。

### 2026-09-09 本机实测

容器中的鉴权模型列表检查通过。合成三轮“不确定 → 每年 2400 元 → 更正为每年
3000 元”通过，状态依次 partial / confirmed / confirmed，原话证据保留。
[原始回环记录](lan-3070-smoke.json) 不含密钥，输入全部为合成数据。
三轮 LLM 完整结果（含 Core 校验）1893 / 1802 / 1816 ms；后两轮 ASR
205 / 228 ms，TTS 366 / 349 ms。首次 ASR 3902 ms、开场 TTS 3844 ms，
包含初始化。这些是服务阶段耗时，不是首字或端到端延迟，也不是受控 GPU 排名。
82 项 Python、10 项 Node 测试通过，Ruff 通过；Docker 构建和真实回环通过。
