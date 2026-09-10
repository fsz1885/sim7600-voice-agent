# sim7600-voice-agent

目标驱动的 AI 电话助手：**通用自主 Agent 工作台 + SIM7600 双向语音 + 结构化询问 Core**。
已实现真实拨号与 PCM → 中文 ASR → Kimi → TTS → 电话回传，并完成一次三轮真实通话。
当前没有 SIP 或电话级软件回声消除；ASR/LLM/TTS 仍按轮次处理，尚非端到端流式语音。

## 选择运行入口

| 入口 | 用途 | 部署说明 |
| --- | --- | --- |
| 8766 通用工作台 | 自主任务、工具、实时电话；默认 Kimi Coding kimi-k2.6 非思考 | [运行指南](docs/autonomous-agent.md) |
| 8767 原生硬件服务 | 独占 SIM7600 AT/Audio 串口，供工作台调用 | [硬件 SDK 与 CLI](docs/sim7600.md) |
| 8765 原询问控制台 | 结构化字段采集、浏览器连续语音，可用本机或内网 LLM | [本地部署](docs/local-deployment.md)、[内网部署](docs/lan-deployment.md) |
| CLI / 基础 Docker | 离线模拟、Core 开发和测试 | 下文快速开始 |

当前真实电话使用 Windows 原生部署；工作台 Docker 方案尚未本机验收。
电话测试结果与响应时间见 [电话评估](docs/phone-evaluation.md)。

## 原询问控制台：Windows + RTX 4060 本地运行

完整安装、启动、使用与限制见 [本地部署指南](docs/local-deployment.md)。
基础 Docker / CLI 仍不需要语音依赖；控制台使用独立的 `requirements-local.lock`。
运行本地服务后访问 `http://127.0.0.1:8765`，可以查看目标、对话、字段证据、
每轮阶段耗时与音频；说话结束后自动提交给 Agent。模型和语音均在本机处理，不需要 API Key。

已有 `models/` 权重时，直接启动两个常驻容器：

```powershell
docker compose -f compose.voice.yml up -d --build
docker compose -f compose.voice.yml ps
```

Docker Desktop 中的名称为 `sim7600-console` 和 `sim7600-ollama`。
首次安装模型以及原生 Windows 备选方式见部署指南。


界面明确区分“本地大模型”和“规则模拟”，二者都不是实际电话线路。
录音与过程快照保存在被 Git 忽略的 `local-data/`，模型在 `models/`。

输入任务目标、必要字段、独立状态和文本回复，输出结构化决策以及可追溯的最终结果。
真实 LLM 根据目标、历史和状态动态选择问题；Core 不包含公司注册场景的固定决策树。

## 快速开始（仅需 Docker Desktop / Docker Engine + Compose）

在仓库根目录运行，不需要安装宿主机 Python 依赖，也不需要 API Key：

```bash
docker compose build
docker compose run --rm app test
docker compose run --rm app lint
docker compose run --rm app demo examples/company-registration.json
docker compose run --rm app chat examples/company-registration.json
```

`demo` 自动重放完整离线场景；`chat` 交互输入文本，`/quit` 或 EOF 退出。
CI / 非交互终端使用 `docker compose run --rm -T app ...`。
提前退出会输出 `completed: false` 和缺失字段，不会误报完成。

默认 `mock` 是**明确受限的离线模拟器**，支持：

- 示例文件中完全匹配的自然语言语句，可一次提供多个字段。
- 当前问题的简单单字段回答；含“大概 / 可能 / 不清楚”等词时标记为 partial。
- 通用输入 `字段=值;字段=值`，如 `注册地址费用=免费;代理记账价格=?两千多`。

Mock 不具备通用语义理解；自由表达、复杂金额单位判断、条件推理和动态业务优先级需要真实 LLM。
Mock 的选题顺序为 partial 优先、再取剩余字段，它仅用于测试，不代表真实 Agent 的规划机制。

## 使用真实 LLM

复制 `.env.example` 为 `.env`，填写：

```dotenv
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=填写你自己的密钥
LLM_MODEL=填写账号可用且支持结构化输出的模型ID
LLM_TIMEOUT_SECONDS=30
```

再运行上面的 `chat` 命令。Compose 自动读取仓库根目录 `.env` 并注入环境变量；
本地 Python 模式应由 shell 设置环境变量，不自动加载 `.env`。
`demo` 只允许 mock，防止自动重放时意外产生模型调用费用。
`.env` 已同时从 Git 和 Docker 构建上下文排除，密钥只放在请求头，不放进模型上下文。

真实适配器使用 Anthropic Messages HTTP API 的 `output_config.format` JSON Schema 输出，
并检查截断、拒绝和 HTTP 失败。[官方接口说明](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)。
新增模型厂商只需实现 `Provider.propose`，无需修改 Core。

### Kimi

设置 `LLM_PROVIDER=kimi`、`KIMI_API_KEY`、`LLM_MODEL` 和 `KIMI_BASE_URL`。
两类密钥与接口不能混用：

| 密钥来源 | KIMI_BASE_URL | LLM_MODEL |
| --- | --- | --- |
| Moonshot 开放平台 | `https://api.moonshot.cn/v1` | 账号可用模型，例如 `kimi-k2.5` |
| Kimi Code | `https://api.kimi.com/coding/v1` | `kimi-for-coding` |

接口区别见 [Kimi Code 官方说明](https://www.kimi.com/code/docs/)。
适配器关闭 thinking，采用 JSON mode + Core 本地 schema/业务验证；JSON mode 本身不保证业务正确。
模型列表、权限与额度以账号实际返回为准，服务拒绝访问时不会伪装其他客户端绕过限制。

```bash
docker compose build
docker compose run --rm app chat examples/company-registration.json
# 配置真实模型后，如需离线 demo，请显式覆盖 Provider：
docker compose run --rm -e LLM_PROVIDER=mock app demo examples/company-registration.json
```

手动执行真实延迟评测（会消耗 API 额度，CI 不运行）：

```bash
# 已在 shell 设置所需环境变量时
python -m voice_agent.benchmark examples/company-registration.json --output results/kimi.json
```

评测使用真实模型选择问题，再匹配固定的模拟对方回答，不把 fixture 里的字段答案直接传给模型。
测量从调用 Core 到完整结构化决策返回的耗时，包含网络、完整生成和修复重试，
不是首 token 延迟，也不是 ASR/TTS 或完整电话链路耗时。单次会话的 P95 仅为小样本描述。
实际评测记录、延迟统计及未解决的行为问题见 [Kimi 实测报告](docs/kimi-evaluation.md)。

## 结构化询问 Core 的架构与状态

```text
CLI / 自动化测试 / 控制台 ASR
             │ 文本
             ▼
        Agent.handle_turn
          │          │
          │          └── State：任务、字段、原始历史、决策、结果
          ▼
     Provider 协议
       ├── AnthropicProvider：理解、提取、动态规划
       └── MockProvider：离线语句映射及简单字段输入
          │ JSON proposal
          ▼
  Core 校验 → 原子提交字段 → 检查完成 / 停滞 → Decision
             │ 文本与结构化结果
             ▼
       CLI / 控制台 TTS
```

技术选择：Python 3.11+ 便于测试和后续语音生态对接；Pydantic 负责结构验证和 JSON Schema；
HTTPX 提供异步 HTTP、超时和离线传输测试；pytest + Ruff 保持依赖与工程结构简单。
`requirements.lock` 固定运行、测试、lint 和构建依赖，Docker 与 CI 共用。

- `State.history` 保存原始 user / assistant 文本，`State.fields` 独立保存业务状态。
- 字段具有 `unknown / partial / confirmed`、当前值、原话证据和轮次。更正时保留旧证据。
- 每轮输出 `response / action / target_field / updates / reason`。
- `continue / clarify / finish` 来自有效模型决策；Core 增加 `retry / handoff` 容错动作。
- 校验字段白名单、重复更新、非空值、最新 user 原文证据、未确认的提问目标。
- 只有全部字段 confirmed 才完成。明确“不适用”也必须有对方提供的证据。
- 无效输出整体拒绝，一次修复重试仍失败时保留原字段并返回 retry。
- 默认 40 个用户轮次或连续 4 轮无状态进展后 handoff，结果仍为未完成。

更多行为约定、限制和未来接口见 [架构说明](docs/architecture.md)。

## 目录

```text
.
├── README.md
├── LICENSE
├── pyproject.toml
├── requirements.lock
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── .github/workflows/ci.yml
├── docs/
│   ├── architecture.md
│   └── example-run.txt
├── src/voice_agent/
│   ├── __init__.py
│   ├── models.py
│   ├── core.py
│   ├── providers.py
│   └── cli.py
├── tests/
│   ├── test_core.py
│   └── test_provider_cli.py
└── examples/
    ├── company-registration.json
    └── company-registration.dialogue.json
```

## 示例结果

完整可重现输出见 [示例运行记录](docs/example-run.txt)。其中包含：

```text
User: 地址本身免费，但是必须在我们这里做代理记账，不用实际办公。
Agent [continue]: 请问代理记账价格？
User: 大概两千多吧。
Agent [clarify]: 关于代理记账价格，能再明确一下具体情况吗？
User: 确定是每年2400元人民币。
Agent [continue]: 请问银行开户支持？
```

最终结果的精简视图（实际同时输出每个字段的状态和原文证据）：

```json
{
  "completed": true,
  "fields": {
    "是否可以提供注册地址": "是",
    "注册地址费用": "免费（须绑定代理记账）",
    "是否要求实际办公": "否",
    "是否强制绑定代理记账": "是",
    "代理记账价格": "2400元/年",
    "银行开户支持": "支持协助开户，最终由银行审核",
    "ICP备案支持": "支持，须按主管部门要求提交材料",
    "所需材料": "法人和股东身份证、公司名称、经营范围、注册资本、持股比例",
    "注册周期": "材料齐全后5–7个工作日"
  }
}
```

## Python 调用与本地开发（可选）

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .
voice-agent test
voice-agent lint
voice-agent chat examples/company-registration.json
```

```python
import asyncio
from voice_agent import Agent, State, Task
from voice_agent.providers import MockProvider

async def main():
    task = Task(goal="确认费用", required_fields=["费用"])
    state = State.for_task(task)
    agent = Agent(MockProvider())
    opening = await agent.handle_turn(task=task, state=state, user_text=None)
    print(opening.response)
    decision = await agent.handle_turn(task=task, state=state, user_text="每年1000元")
    print(decision.model_dump())
    print(state.final_result)

asyncio.run(main())
```

CI 自动执行 Python 3.11 / 3.12 / 3.13 的测试、lint、离线示例，以及 Docker 构建与容器测试。
测试覆盖初始化、信息更新、澄清、跳过已确认字段、任务完成、完整对话、异常 JSON、无效字段、
证据校验、原子回滚、修复重试、更正、超时、无进展退出、状态恢复、HTTP 适配器和 CLI。
全部自动测试无需密钥、网络服务或硬件；真实 LLM 质量需单独评估，不将离线测试等同于语义质量保证。

## 当前能力与下一阶段

评估报告：[Kimi 实测](docs/kimi-evaluation.md)、[模型候选](docs/model-candidates.md)、
[单卡 4060 选型与本地 ASR/TTS 实测](docs/speech-evaluation/README.md)。
语音评估提供独立的 CPU 脚本与原始结果；原询问控制台提供 VAD 自动切句 → ASR → Core → TTS 链路。
通用工作台已接入 SIM7600 实时电话，实测见 [电话评估](docs/phone-evaluation.md)。
历史评估报告描述当时的版本与机器环境。

已实现独立状态、可替换 Provider、每轮结构化决策、自然结束、结果证据、离线交互及完整演示、
Docker、固定依赖、MIT License 和基础 CI。

建议下一阶段先建立真实模型评测集，覆盖复杂条件、否定、插话、矛盾、未知、不适用和金额单位，
测量完成率、重复询问率和延迟。控制台 ASR 把最终转写传给 `user_text`，
TTS 消费 `decision.response`，电话适配层根据 finish / handoff 管理通话；
SIM7600 仅属于电话适配层，不进入 Agent Core。已有[独立硬件 API 与 CLI](docs/sim7600.md)，
支持端口发现、通话控制及 PCM 音频测试；工作台通过独立电话会话控制器接入 ASR/LLM/TTS。
后续重点是模型尾延迟、首段合成、分句与打断质量，以及真实电话评测集。

## 内网模型部署

使用另一台机器上的 llama.cpp，同时在本机运行语音控制台，见
[内网部署与 Provider 切换](docs/lan-deployment.md)。

## 通用自主语音 Agent 工作台

新工作台使用任务执行循环、工具授权、SQLite 持久化和 React 前端，默认 Kimi Coding
端点的 kimi-k2.6 非思考模式。运行入口及实测限制见
[自主 Agent 第一版](docs/autonomous-agent.md)，默认端口 8766。
