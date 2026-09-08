# 本地连续语音与信息溯源

默认部署使用 Docker Compose：`sim7600-console` 提供网页与 CPU 中文 ASR/TTS，
`sim7600-ollama` 使用 RTX 4060 运行 Qwen3.5 4B。没有云端回退，不需要 API Key。
这是浏览器模拟电话场景，尚未接入 SIM7600 线路。

## Docker 启动

Windows 需要 Docker Desktop 的 WSL 2 后端及支持 GPU 的 NVIDIA 驱动，见
[Docker GPU 说明](https://docs.docker.com/desktop/features/gpu/)。Linux 需要 NVIDIA
Container Toolkit，见 [Ollama Docker 说明](https://docs.ollama.com/docker)。
已有本机 `models/` 权重时，在仓库根目录执行：

```powershell
docker compose -f compose.voice.yml up -d --build
docker compose -f compose.voice.yml ps
docker compose -f compose.voice.yml logs --tail 50
```

打开 `http://127.0.0.1:8765`。Docker Desktop 的 `sim7600-voice-agent` 项目下有
`sim7600-console` 和 `sim7600-ollama` 两个常驻容器，重启 Docker 后会自动重启。
默认的 `docker compose` 仍是原有 CLI 工具；启动网页必须指定 `-f compose.voice.yml`。
Ollama 仅在容器网络内提供 API，网页端口只发布到宿主机回环地址。
不要同时运行原生控制台占用 8765，也不要同时在原生 Ollama 中加载模型占用显存。

首次安装（没有模型文件）：

```powershell
docker compose -f compose.voice.yml up -d ollama
docker compose -f compose.voice.yml exec ollama ollama pull qwen3.5:4b
# 用 Python 3.12 建立环境安装语音权重；已有权重时无需再次执行
uv venv .venv --python 3.12
uv pip install --python .venv/Scripts/python.exe -r requirements-local.lock
uv pip install --python .venv/Scripts/python.exe --no-deps --no-build-isolation -e .
.venv/Scripts/python.exe scripts/setup_speech.py
New-Item -ItemType Directory -Force local-data
docker compose -f compose.voice.yml up -d --build
```

Linux 使用 `.venv/bin/python`，并确保挂载的 `local-data/` 可由容器 UID 10001 写入。
模型安装脚本校验 SHA-256，不下载历史评估中的其他候选模型。
LLM 权重约 3.4 GB，容器镜像、CPU 语音模型及构建缓存另占空间。

```powershell
# 检查 GPU 推理（开始对话后运行）
docker compose -f compose.voice.yml exec ollama ollama ps
# 停止；权重、音频与快照保留
docker compose -f compose.voice.yml down
```

## 连续通话

1. 使用 Chrome 或 Edge 打开本机地址，设定目标与所需字段，点击“开始语音通话”。
2. 允许一次麦克风访问。之后直接说话，约 900 毫秒停顿后自动识别、提交、回复，
   不需要逐轮录音或确认转写。超过约 25 秒的连续发言自动切段。
3. 助手播放时可以说话打断；旧回复停止播放，新发言按顺序处理。
   模型繁忙时最多暂存 8 段发言，超限或识别失败会明确提示重说，不伪造转写。
4. 字段卡片显示当前值、状态、原话证据和对应轮次音频，可回听核对来源。
   过程记录同时保存识别原文、字段更新、校验理由及实际浏览器播放事件。
5. 字段全部确认后仍可口头更正；控制台重新将完成状态交给 Core 校验。
   点击“结束会话”才释放麦克风、清空待处理音频并阻止晚到结果提交。
6. 刷新或关闭后重新打开网页，会从服务端找回未结束的会话；因浏览器权限限制，
   需要点击一次“恢复语音连接”。也可先“结束会话”再新建。关闭网页不会自动结束服务端会话。
   不会自动回放之前的回复。文字调试是折叠的辅助入口。

建议佩戴耳机。浏览器开启 echoCancellation/noiseSuppression，但没有验证所有设备的
外放回声抑制，不能保证与 ChatGPT 语音同等的噪声环境表现。
Silero VAD v5、ONNX Runtime WASM 与 worklet 均由本机提供，运行时没有 CDN 请求。
浏览器脚本依赖由 `package-lock.json` 固定，Docker 构建自动复制必要资源。

## 溯源与限制

音频和 `session.json` 保存在 `local-data/<会话ID>/`，不提交到 Git。
可导出包含证据、事件时间和音频路径的 JSON；音频文件需另行保留。
服务重启后不自动恢复历史会话 API，原始文件仍在磁盘；内存最多保留 20 个会话。
已确认表示表述明确，不表示现实事实已经核实。Core 的字段白名单、原话子串证据、
原子更新与完成条件保持不变；ASR 的误识别仍可能成为错误证据，口头更正同样留痕。

说话期间约每 1.2 秒对当前片段进行一次临时 ASR，显示可修正的文字预览；
不是逐 token 的原生流式 ASR。VAD 切段后重新识别并定稿，立即显示最终转写。
LLM 使用 Ollama NDJSON 流式输出，通过 SSE 约每 100 毫秒推送变化，
展示“正在生成（待校验）”的 response 草稿；不会显示 JSON 字段或推理文本。
流中断、修复重试或挂断会撤销草稿，只有 Core 校验通过才更新正式历史与字段。
TTS 仍在最终校验后完整句合成播放，不播放尚未验证的草稿。
GPU LLM 默认 8192 上下文、1200 输出 token，单次请求 90 秒、Core 最多修复一次。
打断只取消播放，已经启动的模型/CPU 计算继续完成，避免丢失前一段发言的字段证据。
未实现真实拨号、接听、AT 指令、电话音频路由或人工坐席转接。

## 原生 Windows 备选

先停止 Docker 的同名服务。安装 Python 依赖与语音权重同上，额外执行：

```powershell
npm ci --ignore-scripts
npm run vendor
```

从 [Ollama v0.33.3](https://github.com/ollama/ollama/releases/tag/v0.33.3)
下载 Windows ZIP 到 `runtime/ollama/`，设置 `OLLAMA_MODELS` 指向仓库 `models/ollama/`，
启动 `ollama serve` 并安装模型。然后执行 `scripts/start-local.ps1`。
这是备选方式，原生进程不会出现在 Docker Desktop 容器列表中。

## 验证

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src tests scripts
npm ci --ignore-scripts
npm run vendor
npm test
node --check src/voice_agent/static/app.js
```

Python 测试验证 Core、API 与停止边界；Node 测试以模拟麦克风/VAD 回调验证自动提交、
排队和打断，不代表真实浏览器音频设备测试。真实模型结果见
[本机评估](local-evaluation.md)，Docker 实测见 [连续语音验证](continuous-voice-validation.md)。
语音评估使用合成场景，均不是实录电话数据。
