# 工作台设备与模型配置

打开 http://127.0.0.1:8766，左侧新增“SIM7600 调试”和“大模型配置”。原任务、工具及实时电话入口保留。

## SIM7600 调试

先在本机安装硬件依赖并启动服务：

```powershell
uv pip install --python .venv/Scripts/python.exe pyserial==3.5
scripts/start-hardware.ps1
```

- 刷新串口列表：枚举 SIMCom USB 串口，显示 AT/Audio 端口配置；即使没有硬件也可返回空列表。
- 查询设备状态：显示 SIM 就绪、网络注册、信号、网络制式、通话数量及任务归属；查询失败会清除旧状态，避免误认为设备仍就绪。
- 只读 AT 诊断：AT、CPIN、CSQ、CEREG、CPSI、CLCC；不接受任意 AT 字符串。通话占用时拒绝诊断。
- 结果有查询时间，当前页面最多保留 12 条；不会自动轮询串口。硬件服务故障、密钥不匹配、版本过旧均显示明确错误。
- 调试接口不拨号、不接听、不修改 SIM/串口设置；电话测试仍通过原有实时电话入口及任务授权执行。

工作台通过带 Bearer 鉴权的 8767 服务访问串口，不直接打开第二条串口连接。硬件服务密钥仍在 local-data/hardware-api-key.txt；不会传给前端。

## 大模型配置

| 场景 | 接口类型 | 服务地址 | 模型 |
| --- | --- | --- | --- |
| Kimi Coding | Kimi | https://api.kimi.com/coding/v1 | 账号实际支持的模型，例如 kimi-k2.6 |
| 本机 Docker Ollama | OpenAI 兼容接口 | http://127.0.0.1:11434/v1 | qwen3.5:4b |
| 内网 llama.cpp 或其他兼容服务 | OpenAI 兼容接口 | 实际 API 根地址，通常以 /v1 结尾 | 服务端实际模型 ID |

地址不要包含 /chat/completions、用户名、密钥、查询参数。OpenAI 兼容不等于所有厂商 API 都支持；当前要求 Chat Completions、JSON object 输出及项目 Action 格式，不支持 Anthropic 原生 Messages 或仅 Responses 接口。Kimi 发送关闭思考参数；兼容接口不发送 Kimi 专用参数，思考行为由所选服务决定。

点击“添加模型”，选择提供商、填写连接信息与参数后“保存模型”。列表中的“连接测试”测试该条已保存的配置，“设为当前”切换后续任务使用的模型。测试发送一次短请求并验证动作格式，可能产生额度消耗；即使模型返回工具动作，也不会执行工具。测试通过不等于复杂任务或真实电话已验收。请求超时可设 5–180 秒，本地模型冷加载可选 120–180 秒。

模型库保存到 AGENT_DATA_DIR/model-settings.json，默认 local-data/agent/model-settings.json；重启后保留，优先于历史环境变量。版本 2 在同一个原子文件中保存 profiles 和 active_id，旧版单模型配置自动迁移，原配置与密钥保留。密钥仅存本机文件，接口只返回 has_key。留空保留同一服务的密钥；切换地址或接口类型不携带旧密钥，可勾选清除密钥。配置文件用临时文件原子替换，不进入任务上下文、API 响应或 Git；数据目录仍需按敏感本地数据保管。自定义 AGENT_DATA_DIR 也应放在 Git 之外。

任务运行或电话进行中不允许保存/测试配置；先暂停任务、结束电话。修改作用于后续请求，不会修改历史记录。模型地址仅由工作台操作者设置，模型工具不能修改配置或读取密钥。内网 HTTP 不加密，应只用于可信网络。

本机 8766 使用 Windows 原生进程，模型 Docker 的 11434 只绑定 127.0.0.1；其他机器不能通过该端口访问本机模型。容器内的 localhost 指向容器自身，部署为容器时需填写该容器实际可达的服务地址。

## 验证

- 121 项 Python 测试通过，包含密钥保存/重启、密钥不回显、切换端点不带旧密钥、跨源拒绝、运行任务阻止配置，以及只读诊断白名单和硬件鉴权。
- TypeScript/Vite 构建与 Ruff 通过。
- 隔离数据目录中，通过配置 API 保存本机 qwen3.5:4b 并调用连接测试成功（真实 Ollama，返回 speak 动作，没有执行工具）。首次 60 秒超时，改为 180 秒后成功，用时 70.7 秒，包含冷加载；不代表热态延迟。
- 本机真实硬件状态及 6 项只读 AT 查询通过；测试时 SIM READY，网络未注册，CSQ 99,99，CPSI NO SERVICE，无通话。未拨号。
- 未进行浏览器交互自动化、真实电话或云端密钥验证；默认仍保留原 Kimi 配置，不替用户选择服务。

## USB 重连恢复

SIM7600 拔插或重新枚举后 COM 号可能变化。空闲且无电话归属时，只读状态和诊断查询失败会释放旧串口、重新枚举并重试一次；仍失败时返回错误。通话占用时不自动重连，不重放拨号/接听等操作。若显式设置 SIM7600_AT_PORT，则仍尊重该配置，端口变更后需要更新配置。

本机本次 AT 从 COM5 变成 COM10、Audio 从 COM3 变成 COM8，旧服务缓存连接导致持续 503。部署修复并重启后状态/AT 查询均返回 200；网络仍报告未注册和 NO SERVICE。自动重连通过模拟断开的回归测试验证，没有要求用户再次拔插设备。

## 网络排查暂存（2026-09-11）

本轮硬件排查按用户要求暂停，尚未解决入网问题。最后读取到 SIM READY、CFUN=1、COPS=0、CNMP=2，CREG/CGREG 为 0,2，CEREG 为 0,4，CSQ 为 99,99，CPSI 为 NO SERVICE。串口查询已恢复，不能将此状态误记为网络已修复。用户反馈同一张 SIM 在手机上可用 4G；模块侧天线通路、供电及射频状态仍未完成对照验证。

NET 常亮与搜网状态一致；官方 AT 手册未找到直接判定 MAIN 天线接通/断路的查询命令。CSQ/CPSI 只能提供信号与网络信息，不能单独证明天线损坏。本轮未修改频段、APN 或固件，也未发起电话。后续继续时应先复查设备实时状态，不沿用本次读数。

参考：[Waveshare M.2 HAT 文档](https://www.waveshare.net/wiki/SIM7600G-H-M2_4G_HAT)、[SIMCom AT 手册](https://files.waveshare.com/wiki/SIM7600G-H/SIM7500_SIM7600_Series_AT_Command_Manual_V3.00.pdf)。


## 多模型管理（2026-09-11）

参考 [WorkBuddy 官方模型配置](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Model) 的提供商列表、模型编辑和本地持久化交互。

- 左侧按 Kimi / Moonshot、llama.cpp、Ollama、自定义提供商分类，可搜索名称、模型 ID、地址。
- 可保存多个模型或同一模型的不同参数配置，独立命名、编辑、测试、切换和删除；当前模型必须先切换才能删除。
- Kimi、llama.cpp、Ollama 提供 API 地址预设；自定义入口使用 OpenAI 兼容 Chat Completions，不意味着支持所有厂商原生协议。
- 编辑器可从已保存服务的 `/models` 获取模型 ID；接口不支持模型列表时允许手动填写。能力没有验证时不标记支持图片或原生工具调用。
- Temperature、Top P、最大输出 tokens、频率/存在惩罚、随机种子、推理强度和超时可调。可选参数留空不发送；Kimi 固定关闭思考，兼容服务按选择发送 reasoning_effort。
- 参数实际传到 Chat Completions 请求。服务可能不支持部分参数，应使用连接测试检查；最大输出不是上下文容量，模型上下文由服务端部署决定。
- 保存其他配置与测试其他模型不会切换当前模型。编辑当前配置立即作用于后续请求；有任务或电话运行时拒绝修改、切换、删除和测试。
- 密钥独立保存在每条模型配置中，新模型不会继承另一条的密钥；编辑原模型仅在相同端点和协议时允许留空保留。API 只返回 has_key，不回显密钥。
- 单个请求超时由所选配置决定，执行循环仍受任务总时限约束，电话仍受最大通话时长限制。

新增接口：GET/POST `/api/settings/models`，POST `/api/settings/models/{id}/activate|delete|test|discover`。
原 `/api/settings/model` 兼容接口仍读取或更新当前条目。所有 POST 保留同源校验与操作请求标识。

本机已配置内网 llama.cpp 的 `sim7600-local` 并设为当前，原 Kimi 条目保留。
真实 `/v1/models` 返回运行上下文 8192，连接与动作格式测试通过，约 1598 ms；这是一次短请求，不能代表电话全链路或稳定延迟分位数。
默认内网参数 Temperature=0.3、max_tokens=1600、timeout=120 秒、reasoning_effort=none，其余使用服务默认。
125 项 Python 测试通过，覆盖旧配置迁移、重启、独立密钥、写入失败不切换、请求参数传递与测试不激活。
浏览器实测显示两条模型、当前内网模型、参数表单；获取列表与保存成功。没有拨号。
