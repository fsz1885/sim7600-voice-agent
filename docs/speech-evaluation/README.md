# 单卡 RTX 4060 8GB：本地模型与语音评估

日期：2026-09-07。当前电脑检测到 GTX 1650 4GB、Ryzen 5 3600、32GB RAM；4060 尚未装入。

## 2026-09-08：扩展电话场景测试集

[最新电话场景评估](runs/phone-cpu-2026-09-08/README.md) 将独立文本从 12 句扩展为 **60 句**。
[固定文本与检查项](phone-cases-v2.json) 保留原 12 句，新增 48 句，覆盖：

- 报价、区间价格、含税/不含税、首年免费、按月/按年、额外收费。
- 否定与条件：不能代办、没同意扣费、无需加急、不要取消、不能保证。
- 口语自我更正：周三改周四、门牌号更正、姓氏解释、股东数量更正、填充词与犹豫。
- 不确定回答与条件依赖：要问同事、未带齐材料、房东证明、周末不计入期限。
- 回拨时段、日期、中文地址、多音字、数字逐位读法；信息均为虚构测试内容。
- 电话交互：信号差请求重复、打错电话、拒绝联系、不方便接听、转经理、退款话题、结束与追加问题。

3 个 TTS 各合成 60 句，共 180 条基础音频。每条分为清晰、模拟窄带 μ-law、
模拟窄带 μ-law + 12dB 带限高斯噪声三种条件；4 个 ASR 共 2160 次语音识别。
另有静音、噪声、50Hz 嗡声三种无语音控制，各模型一次，共 12 次。
这些控制检查没有语音时是否出现非空转写，不混入 CER 分母。

新增测试揭示的问题：SenseVoice 在清晰/加噪语音上的 CER 为 2.92% / 5.01%，
但在本轮静音、白噪声、50Hz 嗡声三个无语音输入上都输出了“我”；其他三个 ASR 在这三条控制中为空。
这说明不能只根据 CER 选择上线组合，后续需要评估 VAD/无语音过滤，而不是把离线识别输出直接当作用户发言。
这三个控制不是无语音误报率的充分统计样本。
Paraformer 对应 CER 为 5.46% / 11.27%；Matcha 平均完整句合成 0.316 秒。

**真实度边界：** 仍然全部是合成音频。书写的“嗯”“等一下”不等于真人的实际犹豫节奏，
模拟带通/编解码/噪声也不等于真实 SIM7600 或运营商录音。尚未覆盖真实口音、远场、
双人重叠、回声、实际打断时序、丢包、背景音乐与 VAD 端点检测。
本集是用于迭代的开发集，不是独立盲测集；不能用它宣布生产准确率。

使用已安装的语音环境，在本目录运行：

```powershell
.\.venv\Scripts\python.exe .\test_phone_channel.py
.\.venv\Scripts\python.exe .\phone_suite.py --run my-phone-comparison
.\.venv\Scripts\python.exe .\report_phone.py --run my-phone-comparison
```

原始数据与报告保存在各 run 目录，本次 run 为 `phone-cpu-2026-09-08`。
首轮音频生成不覆盖已有 TTS JSON。文本哈希在 ASR 开始时校验，所有模型共享同一批变换后的音频数组。
派生条件采用固定噪声种子；本次保留 180 条原音频和第 03 句的六条处理音频供试听。

下一批真人数据应使用获准、脱敏的录音，并由人工建立逐字参考文本；
不能用待评估 ASR 自己的转写作为正确答案。保留原采样率、来源类型、说话人匿名编号、
噪声/口音标签与对话轮次，按说话人划分开发集/盲测集。ASR 真人集与 TTS 合成听测集分开报告。

## 多模型 CPU 中文对比

新增 [4 个 ASR × 3 个 TTS 的 CPU 评估](runs/cpu-zh-2026-09-07/README.md)，
包含 36 条合成音频、288 次识别记录和本地浏览器试听页面。
原采样率与 8kHz 降采样分别报告，不混合成一个准确率。

本轮结论：

- **优先后续验证 Paraformer 流式 ASR + Matcha TTS。** Matcha 完整短句平均 0.253 秒，
  MeloTTS 1.390 秒、Kokoro 中文 2.344 秒；这是 CPU 逐个模型测量，不是整套系统延迟。
- 原采样率合成语音 CER：旧 Zipformer small 7.6%、中文 Zipformer 2.4%、Paraformer 2.4%、SenseVoice 1.4%。
  中文 Zipformer 的关键短语全部命中为 31/36，Paraformer 为 30/36；前者计算 RTF 0.149，后者 0.102，
  因此保留两者比较准确性/响应速度的取舍，而不把其中一个视为全面胜出。
- SenseVoice 对 Kokoro 的 12 条音频字面 CER 为 0%，对 Matcha 为 0.5%、MeloTTS 为 3.6%；
  这只是固定小样本回环结果。SenseVoice 是整句离线，适合作离线转写对照，不能把其低 RTF 等同于更早返回文字。
- Matcha 的费用更正句经 SenseVoice 转写时“刻章”成了“客章”；MeloTTS 同句出现“五是三千元至两千五百元”。
  这些是链路错误，仍需人工听辨确认归因。不得用 ASR 一致性代替主观音质评分。
- 部分 ASR 在 8kHz 数据上分数反而改善；小样本重采样可能改变声学特征，不能据此声称电话音质更好。

| 角色 | 候选及来源 | 当前实际配置 |
|---|---|---|
| ASR 对照 | [Zipformer small 中英双语 2023-02-16](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/online-transducer/zipformer-transducer-models.html) | 流式，优先 FP32，greedy_search |
| ASR | [中文 Zipformer 2025-06-30](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/online-transducer/zipformer-transducer-models.html) | 流式，encoder/joiner INT8、decoder FP32 |
| ASR | [Paraformer 中英流式 ONNX](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/online-paraformer/paraformer-models.html) | encoder/decoder INT8；不是把 FunASR 所有同名变体视为同一模型 |
| ASR | [SenseVoice](https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html) | 整句离线，INT8，language=zh，use_itn=False |
| TTS 对照 | [MeloTTS zh_en](https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/vits.html) | FP32、sid=0、44.1kHz |
| TTS | [Matcha icefall zh Baker + Vocos](https://k2-fsa.github.io/sherpa/onnx/tts/all/Chinese/matcha-icefall-zh-baker.html) | 中文单音色、3 步声学模型、Vocos 22kHz、sid=0 |
| TTS | [Kokoro 82M v1.1-zh](https://k2-fsa.github.io/sherpa/onnx/tts/all/Chinese-English/kokoro-multi-lang-v1_1.html) | FP32、中文女声 zf_001（sid=3）、24kHz |

此轮模型逐个运行，不是 LLM/ASR/TTS 并发测试。选择 ONNX CPU 实现，因此没有安装需要更重运行环境的 CosyVoice/Qwen3-TTS。
本轮未微调模型或根据识别结果修改测试文本。关键短语清单在测试前固定；检查不能验证词序、作用范围和最终业务语义。
CER 未统一数字写法及同音异体字，不能单靠 CER 判定金额或条件正确性。
所有 TTS 音频尚需人工听辨自然度；运行中 Matcha 出现词典 unknown token `shei2`、Kokoro 出现 `❓` 警告，
保留结果用于诊断，警告本身不能证明具体测试句读错。

在本目录重跑（先按下文安装基础模型与环境）：

```powershell
uv pip install --python .\.venv\Scripts\python.exe -r requirements.lock
.\.venv\Scripts\python.exe .\setup_comparison.py
.\.venv\Scripts\python.exe .\compare_cpu.py --run my-cpu-comparison
.\.venv\Scripts\python.exe .\report_comparison.py --run my-cpu-comparison
```

新模型下载哈希见 [comparison-manifest.json](comparison-manifest.json)。
脚本拒绝覆盖已有 TTS 结果；请为新测量使用新的 `--run` 名称。
比较中的 WAV 来自 TTS JSON 所记录的文件哈希，ASR 开始前验证所有文件一致。

## 首次基线与单卡规划

本目录包含独立 Windows Python 评估脚本与报告，不是 Docker 服务，没有改动现有 Kimi 配置。
原始实测记录见 [baseline.json](baseline.json)，模型来源与归档哈希见 [model-manifest.json](model-manifest.json)。
模型权重与虚拟环境不提交 Git。本轮五条合成测试音频已提交，便于听辨回环错误。

| 音频 | TTS 输入文本 |
|---|---|
| [sample-1.wav](results/sample-1.wav) | 您好，请问注册一家公司的费用是多少？ |
| [sample-2.wav](results/sample-2.wav) | 大概两千多元，具体要看公司的情况。 |
| [sample-3.wav](results/sample-3.wav) | 不是三千元，是两千五百元，不包含刻章费用。 |
| [sample-4.wav](results/sample-4.wav) | 需要身份证和经营地址证明。 |
| [sample-5.wav](results/sample-5.wav) | 材料齐全以后，大约五个工作日可以办理完成。 |

这些音频由本地 MeloTTS 生成，不是真人录音。重新运行脚本会覆盖本地同名音频；
仓库提交的音频对应 baseline.json 所记录的首次测试。

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
