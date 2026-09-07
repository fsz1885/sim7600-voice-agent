# 高速 API 与本地模型候选

调研日期：2026-09-07。以下是官方公开模型/服务的候选清单，**均未在本项目实测**。
官方 tokens/s 是生成速度参考，不等于首 token、完整合法决策或电话端到端延迟。
优先级是针对中文短轮次信息收集任务的工程判断，不是通用模型排行榜。

## 建议先加入的 API

| 优先级 | 服务 / 请求模型 ID | 加入理由 | 需要验证 |
| --- | --- | --- | --- |
| P1 | 阿里云百炼 `qwen3.8-flash` | 官方提供北京地域及结构化输出，可作为国内部署网络路径的候选 | 中文条件/否定提取，非思考模式，实际 TTFT 和 P95；不是已证实低于 1 秒 |
| P1 | Cerebras `qwen-3.8-27b` | 官方目录标示约 1500 tokens/s，适合验证完整 JSON 解码能否明显加速 | 从实际部署地区访问的网络耗时、排队、账号可用性、非思考及 schema 支持 |
| P1 对照 | Groq `qwen/qwen3.8-27b` | 官方标示约 450+ tokens/s；与 Cerebras 对照相同模型家族的推理服务 | 当前标为 Preview；量化/服务实现可能不同，不能视为完全相同后端 |
| P2 | Google `gemini-3.1-flash-lite` | 官方定位低延迟轻量任务，支持结构化输出，适合信息提取基线 | 账号地区可用性、实际网络、最低合适思考级别下的质量；它不是 Live 语音模型 |

第一轮建议先测百炼和 Cerebras，再加 Groq 同模型家族对照；Gemini 作为独立模型架构对照。
已有 Kimi 普通版与 HighSpeed 保留为基线，不因为一次高延迟直接淘汰。
公开提供服务不代表本账号已有权限；各 API 需要相应账号、密钥及额度。

来源与接入注意：

- [百炼 Qwen3.8-Flash 模型页](https://help.aliyun.com/en/model-studio/qwen3-8-flash)：北京地域、模型 ID、结构化输出等能力。选择与最终部署匹配的地域，不假定国内端点一定最快。
- [百炼参数文档](https://help.aliyun.com/en/model-studio/qwen-api-via-openai-chat-completions)：接入时按模型明确设置非思考，不把 Kimi 的 thinking 参数原样传给其他厂商。
- [Cerebras 当前模型目录](https://inference-docs.cerebras.ai/models/overview)：公开端点列出 `qwen-3.8-27b` 和约 1500 tokens/s。不要照旧文章选择目录中已经不再列出的 Qwen 235B 模型。
- [Groq 模型页](https://console.groq.com/docs/model/qwen/qwen3.8-27b)：标注 Preview、约 450+ tokens/s，并给出 `reasoning_effort="none"` 的非思考模式。隐藏 reasoning 文本不等于关闭思考计算。
- [Groq 结构化输出文档](https://console.groq.com/docs/structured-outputs)：当前 Structured Outputs 不支持 streaming。其非流式 schema 测试与流式输出测试必须分开；后者先核实 JSON mode 兼容性，并保留 Core 校验。不能把首个普通文本 token 当成合法 JSON 决策。
- [Gemini 3.1 Flash-Lite 模型页](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite)：稳定 ID、轻量提取定位与 structured outputs 支持；不支持 Live API 和音频生成。

## 本地部署候选与这台机器

本机只读检测：NVIDIA GeForce GTX 1650，显存 4096 MiB，检测时空闲 2590 MiB；
系统内存约 31.95 GiB。空闲显存会变化。没有下载权重、安装推理引擎或启动本地模型。

| 候选 | 建议评测形式 | 本机适合程度 |
| --- | --- | --- |
| `Qwen/Qwen3.5-2B` | 4-bit GGUF，非思考，先限制 2K–4K 上下文、单请求 | 本机首选试验；小模型是否能保留业务条件要严格测试 |
| `Qwen/Qwen3.5-4B` | 4-bit GGUF，非思考；检查实际 GPU 驻留比例 | 4GB 卡较紧张，当前空闲显存下尤其容易需要 CPU 卸载；更适合 6–8GB 及以上显存做留余量试验 |
| `Qwen/Qwen3.5-9B` | 4-bit GGUF，非思考，短上下文 | 不作为本机低延迟首选；建议在 12–16GB 显存机器上验证速度/质量平衡 |

上述显存档位是预留运行空间的工程建议，不是模型官方最低配置或已实测占用。
按参数量乘 4 bit 粗算，2B / 4B / 9B 的纯权重下限约 1 / 2 / 4.5 GB，
实际还要加量化元数据、未量化层、KV/状态缓存、计算缓冲及引擎开销，不能直接当部署占用。
若权重落入 CPU 内存，即使能够运行，也不代表能满足 500–700 ms 的关键决策目标。
后续同 GPU 跑 ASR/TTS 还会占用显存与计算资源，需要整链路重新测量。

模型来源：

- [Qwen3.5-2B 官方模型卡](https://huggingface.co/Qwen/Qwen3.5-2B)：默认非思考模式，提供本地服务路线，提醒不同引擎速度有差异、OOM 时缩短上下文。
- [Qwen3.5-4B 官方模型卡](https://huggingface.co/Qwen/Qwen3.5-4B) 和 [Qwen3.5-9B 官方模型卡](https://huggingface.co/Qwen/Qwen3.5-9B)：支持非思考模式；不要只在提示词加入 `/nothink` 就认为已关闭思考。

本机优先尝试支持相应 GGUF 的 llama.cpp 或基于其构建的运行器；实际量化文件的来源、版本、
license 和 hash 需在下载时记录。原始模型卡不等于指定第三方量化文件已验证可靠。
仅测文本路径，避免加载无用视觉模块。大型服务器上的 vLLM/SGLang 数字不套用到 GTX 1650。

## Kimi HighSpeed 的“高负载”假设如何验证

目前只知道两次请求分别耗时 21.593 和 21.280 秒，没有 HTTP 失败或 Core 重试。
**负载/排队是合理假设，但尚无服务端证据，现有记录不能认定原因。**

完整耗时大致来自连接/网络、排队、输入预填充、可能的思考、输出解码和本地校验。
下一轮应分别记录：

| 指标 | 测量口径 / 用途 |
| --- | --- |
| 响应头到达时间 | 辅助观察网络/服务响应，不冒充 TTFT |
| 首内容 TTFT | 请求发出至第一个非空内容增量；空 SSE、role 和心跳不算，reasoning 与回答分别标记 |
| 完整响应时间 | 请求至响应结束，保留 usage、输出 tokens、请求 ID、模型回执、终止原因 |
| 完整合法决策时间 | 从 handle_turn 开始至 JSON/schema/业务校验通过，含重试；与历史结果可比 |
| 解码阶段指标 | 首内容到结束的时间及相应 token 数；注明是否含 reasoning，不能用全文字数冒充 tokens |
| 服务端计时 | 仅记录 API 确实提供的 queue/prompt/completion timing，缺失记为 null，不推测成确定数值 |

若长尾主要在 TTFT、输出长度相近而解码速度稳定，更符合排队、预填充或网络等待；
若 TTFT 正常但生成阶段变长，则检查输出长度、思考 token 和解码吞吐。
即使 TTFT 升高，也不能仅凭客户端计时断定服务端排队，需要服务端 timing 或支持方核查。

## 进入 evaluation 的统一协议

1. **固定业务要求。** 沿用相同 Task、字段和 Proposal 语义，禁止有的候选只输出一句话、
   另一些输出完整证据 JSON，却把两者总时长直接排名。厂商专属 schema/模式参数单独记载。
2. **同时做单轮快照与完整会话。** 固定历史、状态、最新回复的单轮快照便于公平计时；
   完整会话检验规划与状态累积。自动模拟对方应跟随真实提问，不把 fixture 标准答案注入状态。
3. **第一阶段先单并发。** 每个候选至少 100 个代表性轮次，覆盖三个时段；随机交错运行
   Kimi 普通/高速与候选，避免先测完 A 才测 B。更可靠的 P95/P99 需要更多样本。
4. **连接和缓存单独分组。** 先保留历史的新连接基线，再比较复用连接；冷缓存、暖缓存、
   本地模型加载/预热分别统计。固定 prompt 前缀顺序，记录可见 cache token，不混合成一个速度。
5. **记录失败与重试。** 分开显示首尝试和最终成功耗时，保留超时/HTTP错误/无效JSON，
   报完成率和样本数。不能只对成功样本算漂亮的延迟，却不披露失败比例。
6. **固定质量用例。** 多字段回答、含糊价格、币种周期、否定、不适用、更正、条件限制、
   插话和无关信息都要覆盖；检查错误确认率、证据支持、重复提问、单问题规范和提前结束。
7. **再做受控并发。** 单并发达标后，分别测 2 和 4 个会话，并记录配额/限速。
   本地记录显存、GPU/CPU 占用及卸载比例，云端记录地域和服务等级。

验收建议（项目目标，非厂商承诺）：先争取关键合法决策 P50 ≤700 ms、P95 ≤1.2 s；
接 ASR/TTS 后，另测从对方最后语音帧到实际听到有效回答的 P50 ≤1.2 s、P95 ≤2 s。
质量达标是前提，不能用错误确认或提前结束换取速度。当前非流式完整 JSON 基线仍必须保留。

本轮交付是候选研究和评测方案；未调用上述新服务，也没有提供任何未经实测的项目延迟数值。

## RTX 4060 追加选型

用户另有一张闲置 RTX 4060 可用，以下按标准桌面版 **8GB 显存**设计，
不是此前已检测到的 GTX 1650。尚未安装或检测这张卡，也没有在其上运行模型。
[NVIDIA 规格](https://www.nvidia.com/en-us/geforce/graphics-cards/40-series/rtx-4060-4060ti/)
列出 RTX 4060 为 8GB；若实际是 4060 Ti 16GB，需要按另一档配置评估。

| 角色 | 模型 | 起步量化 | 指定发布页的 GGUF 文件大小 | 判断 |
| --- | --- | --- | ---: | --- |
| 主测候选 | `Qwen/Qwen3.5-4B` | Q4_K_M | 约 3.01GB | 在 8GB 卡上比 9B 更容易给运行缓存和未来语音模块留空间；须关闭思考并验证条件提取 |
| 非思考对照 | `Qwen/Qwen3-4B-Instruct-2507` | Q4_K_M；再比较 Q5_K_M | 约 2.50GB / 2.89GB | 纯文本、仅非思考，适合作短决策和指令遵循基线；并不假定它必然更快 |
| 质量上限对照 | `Qwen/Qwen3.5-9B` | Q4_K_M | 约 6.17GB | 短上下文、LLM 独占 GPU 时可尝试；实际是否全驻留需测，不作为共享 ASR/TTS 的默认方案 |

文件大小来自量化发布者页面，只是文件大小，**不是显存实测值**；不同发布者的同名量化
可能采用不同混合精度和打包方式。这里的 GGUF 是第三方发布物，不冒充 Qwen 官方量化。

- [Qwen3.5-4B 量化发布页](https://huggingface.co/bartowski/Qwen_Qwen3.5-4B-GGUF)：Q4_K_M 约 3.01GB，Q5_K_M 约 3.44GB。基础模型见上方官方模型卡。
- [Qwen3-4B-Instruct-2507 官方模型卡](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507)：明确仅支持非思考，不生成 think 块；[量化发布页](https://huggingface.co/bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF) 给出 Q4/Q5 大小。
- [Qwen3.5-9B 量化发布页](https://huggingface.co/bartowski/Qwen_Qwen3.5-9B-GGUF)：Q4_K_M 约 6.17GB。不要把“文件小于8GB”等同于可在8GB显存稳定部署。

推荐部署方式是 **llama.cpp CUDA 的 llama-server + GGUF**，单并发起步，
上下文先设 4096，必要时测 8192，并确认权重层实际全部驻留 GPU。
模型常驻并预热；每次测试不能重新加载权重。对 Qwen3.5 用引擎支持的非思考设置，
不能仅隐藏 reasoning 输出。仅使用文本路径，不加载不需要的视觉组件。
[llama-server 官方文档](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
提供 CUDA Docker 镜像、兼容 Chat Completions 的接口及 schema 约束输出。
具体引擎构建版本、模型文件 hash 和量化来源应随首次部署记录。

未来可把模型服务作为独立容器，Agent 容器通过内网地址访问；保留现有 Core。
**目前代码还没有本地 Provider**，KimiProvider 限定官方 Kimi 地址，
不是只改 `.env` 的 URL 就能连本地服务。部署阶段需加一个本地兼容适配器并验证
streaming、JSON schema、非思考参数和错误处理。本次仅选型，没有改适配器或下载模型。

4060 消除了远端服务排队和外网请求的因素，但仍有预填充和生成耗时，不能承诺亚秒完成。
例如仅作为算术示意：若实际输出 150 token、解码 60 token/s，仅解码就需约 2.5 秒，
还没加预填充；60 token/s 不是本卡实测。要达到电话目标，必须同时减少等待播报前的
输出量，区分首个可播报短句和完整证据 JSON，并保持关键状态校验正确。

建议测试顺序：Qwen3.5-4B Q4 → Qwen3-4B-Instruct-2507 Q4/Q5 → 9B Q4。
若 4B 仍无法满足速度，加入前述 Qwen3.5-2B 作为速度下限对照；若 4B 的条件/更正理解
不足，再评估 9B 是否值得牺牲延迟和显存余量。四类模型逐个加载，不在这张卡上同时常驻。
