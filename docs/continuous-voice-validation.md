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
