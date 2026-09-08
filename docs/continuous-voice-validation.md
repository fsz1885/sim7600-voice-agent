# 连续语音验证记录

2026-09-08，Windows + RTX 4060。本次修改前 68 项 Python 测试通过。

- 修改后 71 项 Python 测试通过，包括自动音频提交、字段对应原音频、完成后更正、
  ASR 处理中挂断，以及 Docker 模型地址约束。
- 6 项 Node 交互测试通过：VAD 结束自动提交、忙碌时顺序排队、挂断后停止收音、
  说话时立即打断 WebAudio。测试使用模拟 DOM/麦克风回调，不是浏览器设备测试。
- 实际 Silero VAD v5 + ONNX Runtime WASM 1.22.0：两个中文合成发言之间插入静音，
  自动产生两次 SpeechRealStart 和 SpeechEnd，切出 3.456 秒、3.328 秒两段音频。
  这是 Node 中执行真实模型和帧处理器，没有测试浏览器 AudioWorklet。
- Docker 控制台镜像成功构建；容器内真实 SenseVoice + Matcha/Vocos 经 HTTP
  `/audio-turn` 回环通过，转写“不确定”后 partial，明确 2400 元后 confirmed。
  此处字段处理使用 Mock，只验证音频/API 链路，不能当作大模型语义评估。

本次没有真实电话、麦克风、物理扬声器、外放回声或用户噪声环境验证。
输入音频都是合成数据。历史模型质量限制仍见 local-evaluation.md。

复测自动 HTTP 链路（会启动一次合成测试会话，结束后自动停止）：

```powershell
.venv/Scripts/python.exe scripts/smoke_continuous.py --mode local --output local-data/new-smoke.json
```

`node scripts/vad_smoke.cjs <16kHz-float32-raw-file>` 用于显式的两段 VAD 回环检测，
输入应为两段语音，每段之间和结尾有至少 1.5 秒静音。该检查不在默认 CI 中运行。

## Docker GPU 完整回环

实际启动 `sim7600-console`、`sim7600-ollama`，两者健康检查通过。
容器内 nvidia-smi 显示 RTX 4060 / 8188 MiB / 驱动 591.86；ollama ps 显示
Qwen3.5 4B、8192 上下文、100% GPU，模型分配 3,341,958,511 字节全部在 GPU。

真实 ASR + 本地 LLM + TTS 自动 HTTP 链路通过三轮：

| 发言 | 字段结果 | 状态 |
| --- | --- | --- |
| 费用大概两千多，具体还不确定 | 2000 多 | partial |
| 费用确定为每年两千四百元 | 每年 2400 元 | confirmed |
| 更正，费用是每年三千元 | 每年 3000 元 | confirmed |

三轮原话证据与对应音频全部保留，完成状态后的更正也已处理。
原始结果：[continuous-voice-docker-smoke.json](continuous-voice-docker-smoke.json)。
首次冷启动模型阶段共 108.436 秒（包含 Core 重试），首次 ASR 4.052 秒、TTS 3.368 秒。
预热后三轮模型阶段分别为 2.466 / 2.230 / 2.440 秒，后两次 ASR 为 0.123 / 0.137 秒，
三轮 TTS 为 0.162 / 0.266 / 0.257 秒。阶段耗时不含真实麦克风、浏览器 VAD 停顿、
网络往返与扬声器播放，不等同于端到端通话延迟。

代码提交 01f2f7f 的 GitHub CI 已全部通过：Python 3.11/3.12/3.13、Windows 本地
控制台测试（含 Node 测试）与 Docker 构建/原有 CLI 验证。
