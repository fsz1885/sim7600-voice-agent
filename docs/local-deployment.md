# Windows 本地语音控制台

控制台运行在 `127.0.0.1:8765`，Ollama 运行在 `127.0.0.1:11434`。
RTX 4060 运行 Qwen3.5 4B Q4_K_M（think=false、仅文本请求）；CPU 运行 SenseVoice INT8 和 Matcha + Vocos。
无云端回退，不读取另一台机器的 `.env`，不需要 API Key。模型下载需要联网。

## 安装

使用 Python 3.12、Git 和 uv，在仓库根目录执行 README 的虚拟环境及依赖安装命令。
`requirements-local.lock` 固定本地控制台依赖；基础 `requirements.lock` 保持不变。

从 [Ollama 官方 v0.33.3 发布页](https://github.com/ollama/ollama/releases/tag/v0.33.3)
下载 `ollama-windows-amd64.zip`，解压到 `runtime/ollama/`。
本次安装包 SHA-256：`52cb36a62e7e501f61514f60212dec7117b6c098811357585e02fffe32d2fcd7`。
可用 `Get-FileHash <安装包> -Algorithm SHA256` 校验。
Windows 原生 NVIDIA 支持见 [官方说明](https://docs.ollama.com/windows)。

在一个 PowerShell 中启动模型服务：

```powershell
$env:OLLAMA_MODELS = Join-Path (Get-Location) 'models\ollama'
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_NUM_PARALLEL = '1'
$env:OLLAMA_MAX_LOADED_MODELS = '1'
$env:OLLAMA_NO_CLOUD = '1'
.\runtime\ollama\ollama.exe serve
```

在第二个 PowerShell 中安装模型与启动控制台：

```powershell
.\runtime\ollama\ollama.exe pull qwen3.5:4b
.\.venv\Scripts\python.exe scripts/setup_speech.py
.\scripts\start-local.ps1
```

语音安装脚本只下载三个选定发布物，并核对已有 `comparison-manifest.json` 中的
SHA-256 与大小，使用安全 tar 解压。权重不入 Git。不要重新运行全部历史评估模型安装器。
Ollama 模型约 3.4 GB；安装包约 1.47 GB；完整解压、下载缓存与语音模型还需要额外空间。

本机已装好的环境不必重复安装，之后只需运行 `scripts/start-local.ps1`。
脚本复用已有 Ollama 服务；若尚未运行则尝试隐藏启动。若环境不允许后台启动，
使用上面的两个终端方式。退出控制台使用 Ctrl+C；已有 Ollama 服务单独退出。
未注册开机自启或 Windows 系统服务。

## 使用

1. 打开 `http://127.0.0.1:8765`，刷新并检查模型、ASR、TTS 安装状态。
2. 填写目标与每行一个的字段，选择本地模型，开始会话。规则模拟仅用于离线操作验证。
3. 输入对方回答，或点击录音，讲完后停止。录音最长 30 秒，分轮操作，不自动连续监听。
4. 核对转写，尤其是金额、否定与前提条件，可修改后再发送。
5. 查看模型/Core 校验、字段更新、原话证据、回复和 TTS 音频。结束语生成后任务完成；
   浏览器播放开始/结束单独记录，不能把 TTS 完成当作实际播放完成。
6. 可停止播放、结束会话、导出过程 JSON；刷新同一浏览器标签可恢复当前会话。

音频与 `session.json` 自动保存到 `local-data/<会话ID>/`；服务重启后不自动加载历史快照，
但文件保留。内存最多保留最近 20 个会话，磁盘文件由使用者按需清理。
界面端文本通过 `textContent` 显示，禁止跨站 API 写入；服务固定监听回环地址。
这是本机单使用者工具，不提供多用户认证，不应直接暴露到局域网或公网。

## 实现边界

- 目前所有会话都是本地模拟线路。未检测到 SIM7600 串口，未实现拨号、接听、挂断 AT 指令、
  电话音频路由或人工坐席转接；`handoff` 是任务未完成的处理建议。
- CPU ASR 在整段录音结束后识别。只添加能量静音门限，不是完整 VAD；噪声仍可能产生错误转写。
  浏览器请求回声消除不等于已实现电话 AEC。录音时停止助手播放，尚无说话自动打断。
- Matcha 是中文单音色完整句合成，不是流式首包；中英文混读、数字和发音仍需人工听辨。
- Core 未改变：JSON/schema、字段白名单、最新原话子串证据及完成条件仍由 Core 验证。
  子串证据不证明语义正确，模型确认不等于现实事实核验。
- 默认 8192 上下文，输出上限 1200 token，单请求 90 秒、Core 最多一次修复。
  保守字符预算超限会失败进入 retry/handoff，避免静默丢弃旧证据；不是精确 tokenizer 计数。
- 阶段耗时来自服务端，包含首次模型加载；没有测量真实麦克风到扬声器的端到端延迟。
  浏览器自动播放可能被阻止，可手动点击事件中的音频。健康状态的“已安装”不代表质量通过。

## 验证与复测

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q docs/speech-evaluation/test_phone_channel.py
.\.venv\Scripts\python.exe -m ruff check src tests scripts
node --check src/voice_agent/static/app.js
# 显式调用本地模型；保存新文件以保留旧测试记录：
.\.venv\Scripts\python.exe scripts/evaluate_local.py --output local-data/evaluation-new.json
```

离线测试使用模拟 HTTP/语音对象，不声称测试了模型准确率。真实本地实测另见
[本机评估说明](local-evaluation.md)，使用书写场景和合成语音回环，不是实录电话。
