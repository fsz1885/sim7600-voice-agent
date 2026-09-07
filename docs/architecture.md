# 架构与行为约定

## 职责边界

`Task` 定义目标和必要字段，`State` 保存独立结构化状态及原始对话。
`Agent.handle_turn(task=..., state=..., user_text=...)` 是唯一对话入口。
首次 `user_text=None` 生成开场，也可直接以用户文本开始；后续 None 被拒绝。
Provider 接收深拷贝，无法通过修改参数污染 Core 的原始状态。
同一 State 由单个调用者串行处理，不支持并发处理同一通话。

Provider 返回 JSON 字符串，Core 使用 Pydantic 验证格式，再验证业务不变量。
Anthropic 的 HTTP API 细节、认证头和 schema 封装只在适配器内。
Mock 的示例领域知识只存在于 examples，Core 没有字段名、正则或问答树。

## 每轮事务

1. 检查任务与状态对应关系，已完成或已交接时直接返回原终态决策。
2. 把对方原文追加到 history，用户轮次数加一。
3. 把目标、当前状态和完整历史交给 Provider，异步调用有超时。
4. 校验 JSON 和每项更新，在字段副本上应用，拒绝不存在字段及重复字段更新。
5. 更新必须有非空 value，以及来自最新用户原文的精确非空 evidence。
6. 所有字段 confirmed 后由 Core 输出结束语；否则模型必须指向未确认字段，
   对 partial 字段使用 clarify，不接受提前 finish。
7. 整体通过才提交所有字段；异常时一次修复尝试，仍失败则 retry，不提交半成品字段。
8. 达到轮数/停滞阈值进入 handoff，保留已知结果和缺失信息；始终记录实际返回的 Agent 文本。

`history` 包含失败轮次，便于查看实际交流过程。不会记录原始模型响应或带密钥的 HTTP 错误。
`current_decision` 保存最近有效或兜底决策；终态保存 `final_result`。
`State.model_dump_json()` / `State.model_validate_json()` 可由调用方保存和恢复；MVP 不提供数据库。
恢复应使用本程序产生且受信任的快照，不支持不可信外部状态导入。

## 状态语义与限制

- unknown：尚无证据；partial：含糊、单位/条件不完整或存在冲突；confirmed：明确且足够。
- confirmed 表示“对方的说法明确且模型认为足够”，不代表现实世界的事实已经独立核验。
- 新明确更正可更新 confirmed 的旧值；冲突可退回 partial。证据列表保留先后原文，
  完整旧值变更可从原始历史复核，当前没有独立版本化审计日志。
- 只用当前轮证据更新字段，可利用之前上下文理解短答，但不能把模型自己的问题当证据。
- 没有自动把否定答案等同于全部任务完成；不适用也必须明确确认。
- Core 检查 question target 的状态，真实自然语言是否确实只问该字段、证据是否语义支持值，
  仍由 LLM 判断。结构化输出与子串证据检查不能彻底消除语义错误或提示注入。
- 无任何状态和值变化算停滞；仅重复提供同一答案或追加重复证据不会重置停滞计数。
- 模型失败修复最多两次调用，每次受 timeout 约束；不无限重试、不自动更换厂商。
- 没有对话截断或摘要；以轮数上限约束通常的会话长度，超长单条输入可能触发厂商上下文限制，
  这类错误进入 retry/handoff。下一阶段可增加文本长度和 token 预算。
- handoff 只表示建议后续处理，当前没有真实人工坐席系统。

## 扩展一个 Provider

实现以下结构协议，并在 CLI 选择适配器；Core 无需变更：

```python
class CustomProvider:
    async def propose(self, task, state, error=None) -> str:
        # 自行调用模型；输出 Proposal 对应的 JSON 字符串。
        # 将可恢复传输/模型错误转成 ProviderError。
        ...
```

`Proposal.model_json_schema()` 是统一格式定义。`updates` 用数组传输以便发现重复更新，
`Decision.updates` 按字段名输出已提交的 FieldState，带完整证据。
`error` 是 Core 发给模型的修复提示，不包含上次原始错误内容。

## 未来语音接入

```text
电话适配层（未来 SIM7600 / SIP 等）
   → 音频处理 → ASR 最终转写
   → await agent.handle_turn(task=task, state=state, user_text=transcript)
   → decision.response → TTS → 电话适配层
```

一个通话实例拥有一份 State。ASR 的分段、中间结果去重、打断、播放控制、回声消除、
拨号、挂断及串口均在 Core 外面处理；不要把不完整的流式识别片段当成多轮用户回答。
由电话适配层消费 finish/handoff，待结束语播放完后管理线路。
建议先完成真实 LLM 质量和延迟验证，再接入模拟 ASR/TTS，最后接实机。
