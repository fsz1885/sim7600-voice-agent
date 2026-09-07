# 单卡 RTX 4060 8GB：本地模型与语音评估

日期：2026-09-07。当前电脑检测到 GTX 1650 4GB、Ryzen 5 3600、32GB RAM；4060 尚未装入。
本目录包含独立 Windows Python 评估脚本与报告，不是 Docker 服务，没有改动现有 Kimi 配置。
原始实测记录见 [baseline.json](baseline.json)，模型来源与归档哈希见 [model-manifest.json](model-manifest.json)。
模型权重、虚拟环境与生成音频不提交 Git。

## 推荐 LLM

统一假设：单会话、GGUF Q4_K_M、CUDA 全层卸载、仅文本、不加载视觉投影、FP16 KV；关闭思考。
下表为容量规划估算，不是 4060 实测上限。上下文包括系统提示、状态、历史、用户输入和输出。

| 模型 | 特点 | 标称原生窗口 | ASR/TTS 在 CPU 时可尝试的容量档位 | 电话任务起步窗口 |
|---|---|---:|---:|---:|
| Qwen3.5-4B | 首选质量/资源平衡候选；混合线性/全注意力；Q4 文件约 3.01GB | 262,144 token | 32K–64K，逐档压测 | 8K |
| Qwen3-4B-Instruct-2507 | 纯文本、仅非思考；Q4 文件约 2.50GB；传统 GQA 缓存随长度增长更快 | 262,144 token | 16K–24K，24K 余量较小 | 4K–8K |
| Qwen3.5-2B | 速度与显存基线；复杂更正、条件保留需要重点评测；Q4 通常约 1–2GB，按实际文件核算 | 262,144 token | 64K，可进一步试探但不建议电话使用长窗口 | 4K–8K |

模型链接：[Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B)、[Qwen3-4B-Instruct-2507](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507)、[Qwen3.5-2B](https://huggingface.co/Qwen/Qwen3.5-2B)。
量化来源为第三方：[4B](https://huggingface.co/bartowski/Qwen_Qwen3.5-4B-GGUF)、[2507](https://huggingface.co/bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF)、[2B](https://huggingface.co/bartowski/Qwen_Qwen3.5-2B-GGUF)。文件大小不是显存占用。

估算依据：FP16 KV/token = 2(K,V) × 全注意力层数 × KV 头数 × head_dim × 2 bytes。
Qwen3.5-4B 为 8×4×256，约 32KiB/token；8K/32K/64K 对应约 0.25/1/2GiB。
Qwen3.5-2B 为 6×2×256，约 12KiB/token；64K 对应约 0.75GiB。
Qwen3-4B-Instruct-2507 为 36×8×128，约 144KiB/token；8K/16K/24K 对应 1.125/2.25/3.375GiB。
此外仍需权重、计算缓冲、混合模型循环状态、桌面显示和运行时余量；引擎版本、batch、并发、缓存精度都会改变容量。
混合模型的计算不只包含上述全注意力 KV，因此不能用这一个公式承诺可部署上限。
配置来源：[3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/config.json)、[3.5-2B](https://huggingface.co/Qwen/Qwen3.5-2B/blob/main/config.json)、[2507](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507/blob/main/config.json)。

可装下不等于响应快：长输入增加预填充时间。电话任务优先保留结构化状态、证据和近期对话，完整历史存磁盘。
三者逐个评测，不同时加载。暂不以 9B 作为单卡语音系统默认配置。

## ASR/TTS 部署组合

默认：4060 跑 LLM；CPU 跑 ASR、TTS 和 Agent Core。同一台电脑完全本地运行。

- ASR：sherpa-onnx streaming Zipformer small bilingual zh-en，2023-02-16。中英混合，流式识别；这是轻量速度基线，不代表最新或最佳中文准确率。
- TTS：sherpa-onnx VITS/MeloTTS zh_en。中英文、单音色，FP32 ONNX 文件约 163MiB，输出 44.1kHz。按短句合成；不是文本 token 输入即可立即产出音频的双流式模型。英文词典外词、数字和多音字需人工听测。
- 后续质量对照：中文 Zipformer zh int8 2025-06-30；TTS Fun-CosyVoice3-0.5B-2512。CosyVoice 支持流式，但完整系统还有其他组件，0.5B 不是全部运行显存。
- 若以后让 CosyVoice 与 LLM 共用 4060，先给语音运行时预留约 2–3GiB 作为试验预算（不是测量），LLM 从 4K 开始测 8K；若超出预算优先缩短上下文或回到 CPU 轻量 TTS。不能承诺两者在所有设置下都能共存。

来源：[ASR](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/online-transducer/zipformer-transducer-models.html)、[TTS](https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/vits.html)、[CosyVoice](https://github.com/QwenAudio/CosyVoice)。

## 本地重跑

本轮 CPU 实测（5 条合成语音，预热后，两个模型同时驻留但顺序调用）：

| 指标 | 结果 |
|---|---:|
| ASR 冷加载 | 1.297 秒 |
| TTS 冷加载 | 4.323 秒 |
| TTS 完整短句平均耗时 | 1.818 秒 |
| TTS 完整短句范围 | 1.292–2.210 秒 |
| ASR 加权计算 RTF | 0.0682，约每秒音频需要 68ms 计算 |

质量问题：第三句“不包含刻章费用”在回环结果中成为“五包含刻”，第五句丢失“材料”，第二句“具体”成为“笔体”。
错误来自整个合成→识别链路，尚未人工听辨归因，不能全部断言为 ASR 单方问题。
这套方案已可本地重跑评估，但尚不适合直接作为电话生产配置；尤其否定词和费用条件需要提升。
不报告 4060 速度、真实语音准确率、端到端延迟或 P95：本轮没有这些证据。

首次复建：在本目录打开 PowerShell，使用 uv 创建独立 Python 3.12 环境并下载模型（两个归档合计约 625MB，解压还需额外空间）：

```powershell
uv venv .venv --python 3.12
uv pip install --python .\.venv\Scripts\python.exe -r requirements.lock
.\.venv\Scripts\python.exe .\setup_models.py
```

然后重跑：

```powershell
.\.venv\Scripts\python.exe .\evaluate.py
```

附加识别自己的录音：

```powershell
.\.venv\Scripts\python.exe .\evaluate.py --wav 'D:\recordings\test.wav'
```

输出 results/speech-evaluation.json 和 sample-1.wav 到 sample-5.wav。
运行期间 ASR/TTS 同时常驻，调用按序执行，默认每个模型 2 个 CPU 线程。
没有调用付费 API、LLM、麦克风或电话设备；没有后台常驻服务。

测试先预热，再生成 5 条项目场景语音并送入 ASR，记录完整短句 TTS 耗时与 ASR 计算 RTF。
ASR 分 20ms 块快速回放，没有按真实时钟等待，尾部补 0.66 秒静音并显式结束输入。
因此 RTF 是计算速度比，不是说完到识别完成的延迟；TTS 是完整短句耗时，不是流式首包。
合成语音回环只能证明基本工作，不能代替真人电话准确率测试，也没有评估双工抢占和回声消除。
后续用真人录音、电话窄带、金额、日期、否定、更正和背景噪声构建测试集；记录 CER、关键字段错误率、VAD 截断、TTS 首音频、完整 Agent 决策和实际首声延迟。

复建环境：Python 3.12，按 requirements.lock 安装，运行 setup_models.py 下载官方发布资产并解压。
model-manifest.json 记录下载 URL、归档大小与 SHA256；这是本次文件的追踪哈希，不是独立发布者签名验证。
