# 通用自主语音 Agent：第一版

入口 http://127.0.0.1:8766。旧询问控制台 8765 及其 Core 保留独立运行。
新平台不要求用户预先填写 required_fields；模型选择下一步动作和已注册工具。

## 架构与当前实现

| 层 | 实现 |
| --- | --- |
| 工作台 | React + TypeScript，任务列表、计划、对话、工具结果、授权、设备/模型配置展示 |
| 任务 API | FastAPI，REST 控制和可重连 SSE 事件 |
| 执行循环 | speak / ask_user / wait / call_tool / complete / fail，单次最多 12 步、执行检查 300 秒上限 |
| 状态 | SQLite WAL，任务快照与事件同一事务保存；任务工具操作带执行 ID |
| 工具 | 文档列表/读取、笔记保存、电话状态/拨号/接听/挂断 |
| 硬件 | Windows 原生 8767，Bearer 鉴权、任务拥有权、操作日志与执行 ID 去重 |
| 语音 | CPU SenseVoice + Matcha/Vocos，按键录音或上传短 WAV，转写确认后提交、回复分段合成播放 |
| 实时电话 | 独立电话会话控制器，双向 PCM 帧、能量分句、ASR/LLM/TTS 轮次、打断及挂断 |
| LLM | Kimi Coding `/coding/v1/chat/completions`，`kimi-k2.6`，`thinking: {type: disabled}` |

模型实现 `async decide(task, tools) -> Action`。执行器与具体 API 分离；旧 Provider
仍服务原询问 Core，不把通用任务硬套进字段提取 Schema。任务权限在工具执行前检查。
没有 shell、任意 URL 抓取或任意文件读写工具。知识工具只读取知识目录中限长 Markdown；
结果保存到固定 artifacts 目录。模型看到的是工具执行结果，不能凭回答直接触发串口操作。

任务创建时可以授权特定电话号码。未授权的拨号及接听会进入 waiting_approval，
确认只适用于那一次确切操作。挂断只释放本任务拥有的电话，不能挂断其他任务的电话。
暂停/停止时取消模型请求，尝试释放所属电话；无法确认的外部操作标为 unknown，
禁止自动恢复重放。完成/失败后同样尝试释放电话，清理失败明确记录。
服务重启将运行任务暂停，不会自动重拨；需核实未知操作后新建任务。
连续两次工具失败或超出步数上限暂停。当前没有自动重试副作用操作。

## 本机运行

```powershell
# Python 环境沿用项目 .venv；硬件端额外需要 pyserial
uv pip install --python .venv/Scripts/python.exe -r requirements-local.lock
uv pip install --python .venv/Scripts/python.exe pyserial==3.5
uv pip install --python .venv/Scripts/python.exe --no-deps --no-build-isolation -e .
cd frontend
npm ci --ignore-scripts
npm run build
cd ..
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/start-hardware.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/start-workbench.ps1
```

Kimi 密钥放在 `local-data/kimi-agent-key.txt`；硬件密钥首次启动生成到
`local-data/hardware-api-key.txt`。二者均不提交，不进入 Docker 构建上下文。
日志和进程信息也在 local-data。脚本不会配置登录自动启动。
停止：`scripts/stop-workbench.ps1`，然后 `scripts/stop-hardware.ps1`。
不要同时运行独立 SIM7600 CLI 占用 AT 串口。

环境变量可覆盖 AGENT_KIMI_BASE_URL、AGENT_KIMI_MODEL、AGENT_KIMI_KEY_FILE、
AGENT_DATA_DIR、HARDWARE_URL、HARDWARE_KEY_FILE、VOICE_MODELS_DIR。
默认工作台和硬件服务都只监听 Windows 回环地址。现有 .env 不会自动载入新平台，
防止历史 Kimi/3070 配置混用。

数据目录默认 `local-data/agent/`：tasks.sqlite3、knowledge、artifacts、audio。
知识文档需人工放进 knowledge，程序不会默认读取整个项目或用户磁盘。
本次在 knowledge 放入项目 SIM7600 使用说明用于合成任务测试。
新工作台可在“大模型配置”页保存服务地址、模型、密钥和请求超时；已有保存配置优先于环境变量。详见 [工作台设备与模型配置](workbench-settings.md)。

### Docker 业务服务（当前本机部署）

2026-09-11 已从 Windows 原生工作台迁移至 Docker，访问 http://127.0.0.1:8765。
沿用原 Compose 项目 sim7600-voice-agent、服务 console、容器名 sim7600-console，
使用 Dockerfile.agent 重建镜像。容器内部端口 8766 映射到宿主机 8765。
Windows 原生 8766 工作台已停止；SIM7600 硬件服务仍由 Windows 原生 8767 访问串口。

```powershell
docker compose -f compose.agent.yml up -d --build console
docker compose -f compose.agent.yml ps
docker compose -f compose.agent.yml logs --tail 100 console
# 停止容器工作台
docker compose -f compose.agent.yml stop console
```

原 compose.voice.yml / compose.lan.yml 提供旧询问控制台，与新配置共用容器名称和端口，
请选择一个运行，不要交替执行不同 Compose 文件的 up 命令。
任务和模型库继续使用 local-data/agent，模型只读挂载 docs/speech-evaluation/models。
迁移前已将 SQLite 与模型库备份至 local-data/docker-migration-时间戳（含密钥，不提交）。
原询问控制台的数据仍保留在 local-data，其数据结构与新工作台不同，不自动导入。

容器通过 host.docker.internal:8767 访问带 Bearer 鉴权的原生硬件服务，密钥只读挂载。
本机已验证容器到硬件服务通信、内网 LLM、TTS → WAV → ASR 和健康检查。
不要同时启动 Windows 原生工作台与容器共享同一 SQLite 数据库。
容器 restart 策略为 unless-stopped；仍需 Docker Desktop 引擎运行，硬件服务独立启动。

## 已验证与限制（2026-09-10）

- Kimi Coding 模型列表未列出 kimi-k2.6，但直接请求 HTTP 200，响应 model=kimi-k2.6。
  带 disabled 的独立短回复测试 finish=stop，reasoning_content 长度为 0。
- 真实 Kimi 文档任务：自主 list → read → notes.save → complete，4 步、22.297 秒。
  文件确实生成。摘要把“多个候选端口时需指定”误述为“始终需指定”，内容质量未完全通过。
- 真实 Kimi 设备任务：phone.status → complete，2 步、5.062 秒。通过原生服务查询
  到 SIM 就绪、LTE 注册、无当前呼叫。测试未拨号、未接听。
- 目标不明确任务：1 步、4.063 秒，进入 waiting_user 并提出澄清。
- 自动测试覆盖多步文件任务、授权门控、取消无晚到提交、重启恢复、未知副作用禁止重放、
  路径越界、参数校验、Kimi 参数与截断、步数上限、硬件鉴权/所有权/执行 ID 去重。
- 前端通过 TypeScript 和 Vite 构建，并在真实浏览器检查任务详情、计划、结果和下载链接。
- 合成音频经新工作台 HTTP 转写 → Kimi 回复 → TTS WAV 回环通过；仅验证音频/API 链路。
- 首版 106 项 Python 测试通过；新增电话链路测试后共 111 项通过，Ruff 通过。
  原生工作台停止/重启后原有 4 条测试任务仍在。

当前是可运行的通用任务/语音工作台基础版，并非全部规划已经完成：

1. 浏览器语音是按键录音、最长 25 秒；不是连续 VAD 对话。上传只支持短 PCM WAV。
2. Agent 动作先等完整 JSON 校验，回复之后分段 TTS；没有首 token 即播的实时语音链路。
3. SIM7600 实时电话已通过工作台独立入口接入 PCM → 分句 → ASR → Kimi → TTS → PCM。
   通用任务中的 phone.dial 仍只负责拨号；自动对话请使用“SIM7600 实时电话”入口。
4. 暂停/停止不等于撤销已执行操作；进程强杀、断电、USB 断开仍可能需要人工核对电话。
5. wait 暂停等待用户继续，不会自动监听外部事件唤醒；没有跨任务长期记忆或自动调度。
6. 计划和摘要由模型生成，仍可能误读资料；工具执行成功不能证明文本内容正确。
7. 已完成一次真实电话三轮对话，详见 [电话链路实测](phone-evaluation.md)。
   尚未完成浏览器真实麦克风、主观音质、噪声/回声、长时压力评估。

下一步优先优化电话响应尾延迟和分句、打断，再实现连续浏览器语音，
最后扩展知识检索和更多业务工具。原询问 Core 后续可注册为结构化采集工具。

## 复现检查

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src tests scripts
cd frontend
npm ci --ignore-scripts
npm run build
```

官方 Kimi 非思考与模型说明：
https://platform.kimi.com/docs/guide/kimi-k2-6-quickstart
