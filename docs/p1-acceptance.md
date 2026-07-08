# P1 阶段验收说明

## 一句话说明

P1 把 zzm-agent 从“一个能跑 ReAct 的循环”推进为“有正式 Conversation Runtime 的 Agent”。它让会话、Turn、Loop、权限、Usage、消息、工具结果、事件、Artifact、Checkpoint、状态快照和 CLI 主入口都有明确边界。

## 解决的问题

没有 P1 时，很多运行时信息散落在 `AgentLoop` 的局部变量或临时字段里：

- CLI 想显示当前 Turn 状态时，只能扒内部字段；
- 工具结果、展示内容、大输出 Artifact 和模型 Observation 容易混在一起；
- 流式输出里的正文、推理摘要、工具调用和最终结果容易混成一堆；
- 会话中断或重启后，只能依赖历史消息，无法判断状态是否可恢复；
- 权限、Usage、Memory 来源、取消状态没有统一可序列化结构；
- 未来桌面端、多 Agent、后台任务如果各自直接调 `AgentLoop`，会复制多套状态逻辑。

P1 的目标就是把这些运行时事实收敛到明确对象和统一入口，为 P2 之后的 CLI 产品化、沙箱、MCP、长任务、桌面端打基础。

## 具体例子

用户在 CLI 输入：

```text
帮我运行测试并修复失败。
```

P1 之后的内部链路是：

```text
CLI
→ QueryEngine.submit_message()
→ ConversationState.active_turn = TurnState(...)
→ StateSnapshotStore 保存 turn.started 快照
→ AgentLoop.run()
→ ModelAdapter 规范化模型响应
→ ModelStreamEvent 分层发出 status / reasoning / content / tool_call / final
→ ToolResult 区分模型内容、展示内容和 Artifact
→ ConversationMessageStore 原子提交本轮消息
→ TurnState.complete(...)
→ StateSnapshotStore 保存 turn.completed 快照
→ QueryResult.reply 返回最终回答
```

这样 CLI 只需要消费正文事件，状态快照可以用于恢复判断，未来桌面端可以复用同一套 QueryEngine 和事件协议。

## 使用场景

- 用户日常在 CLI 中提交消息，CLI 通过 QueryEngine 而不是直接拼装 AgentLoop；
- 模型流式输出时，CLI 只渲染 `content_delta`，不会把 reasoning、工具参数或最终事件混到正文里；
- 工具输出很长时，ToolResult 可以把展示内容、模型 Observation 和 Artifact 分开；
- 权限被拒绝、工具失败、Stop Hook 阻塞或用户取消时，TurnState / LoopState 能记录原因；
- 程序重启或异常退出后，StateSnapshotStore 和 RecoveryValidator 能判断上次状态是否可恢复；
- 后续 P2/P9 的 CLI 命令和桌面端可以从状态对象读取会话、Turn、Usage、Artifact、事件和恢复信息。

## 执行链路

```text
用户输入
→ CLI runtime
→ QueryEngine
→ ConversationState / TurnState
→ AgentLoop
→ ModelAdapter
→ ModelStreamEvent
→ ToolRegistry / ToolResult / ArtifactStore
→ ConversationMessageStore
→ StateSnapshotStore
→ QueryResult
```

P1 保留了旧入口：

```text
AgentLoop.run(user_input, stream=False)
```

旧入口仍可用于测试、内部调用和迁移期兼容，但新的 CLI 主路径优先通过 QueryEngine。

## 关键事件或数据结构

- `ApplicationState`：进程级状态入口，保存配置、模型注册、会话集合、应用级 Usage 和事件；
- `ConversationState`：跨 Turn 会话状态，保存消息、权限、Usage、文件缓存、Memory 来源、事件、Artifact、Checkpoint 和 active turn；
- `TurnState`：一次用户请求的状态，保存输入、状态、Usage、权限请求、工具结果、Artifact、Loop 和最终回复；
- `LoopState`：ReAct 循环内部状态，保存阶段、转换原因、模型调用、工具轮、Reflection、Stop Hook、阻塞和取消信息；
- `ConversationMessageStore`：区分模型上下文、pending 消息和持久化消息，保证 Turn 结束时原子提交；
- `UsageState`：按模型、Turn、Conversation、Task、Application 累计调用次数、Token、费用和工具调用；
- `PermissionState`：记录权限请求、授权、拒绝、过期、孤立请求和授权作用域；
- `EventBus`：记录运行时事件，供回放、UI 和调试使用；
- `ArtifactStore`：保存长工具输出或产物，避免把完整大结果塞进模型上下文；
- `CheckpointStore`：记录可恢复检查点；
- `StateSnapshotStore`：保存带 schema version 和 checksum 的状态快照；
- `ModelStreamEvent`：区分 status、reasoning_summary、content_delta、tool_call_delta、tool_result、usage、final_message 和 error；
- `QueryEngine`：跨 Turn 统一入口，负责调用 AgentLoop 和在 Turn 边界保存状态快照。

## 代码定位

- `zzm_agent/core/runtime_state.py`
  - `ApplicationState`
  - `ConversationState`
  - `TurnState`
  - `LoopState`
  - `UsageState`
  - `PermissionState`
  - `EventBus`
  - `ArtifactStore`
  - `CheckpointStore`
- `zzm_agent/core/runtime_messages.py`
  - `ConversationMessageStore`
- `zzm_agent/core/tool_results.py`
  - `ToolResult`
  - `ToolProgressEvent`
  - `RendererRegistry`
  - `DisplayMode`
- `zzm_agent/core/state_serialization.py`
  - `StateSnapshotStore`
  - `RecoveryValidator`
  - `StateEnvelope`
- `zzm_agent/core/model_adapter.py`
  - `OpenAIChatCompletionsAdapter`
  - `ModelCapabilities`
  - `ModelStreamChunk`
- `zzm_agent/core/model_stream.py`
  - `ModelStreamEvent`
  - `ModelStreamEventKind`
- `zzm_agent/core/query_engine.py`
  - `QueryEngine`
  - `QueryResult`
- `zzm_agent/core/agent_loop.py`
  - `AgentLoop.run()`
  - `on_stream_event`
- `zzm_agent/cli_support/runtime.py`
  - `build_runtime()`
  - `run_repl()`

## 验收结果

P1 阶段验收通过。

已经确认：

- 五种状态作用域及所有权可测试；
- LoopPhase / LoopTransition 能描述 ReAct 阶段变化；
- pending 消息可以在 Turn 成功时提交，失败或中断时不污染历史；
- Usage、Permission、Memory 来源、FileState、Cancellation 可序列化；
- EventBus、ArtifactStore、CheckpointStore 能进入 ConversationState；
- ToolResult 能区分模型内容、展示内容和 Artifact；
- ModelStreamEvent 能区分 reasoning、content、tool call 和 final；
- QueryEngine 已成为 CLI 主消息入口；
- StateSnapshotStore 已由 QueryEngine 在真实 Turn 边界调用；
- 旧的同步 `AgentLoop.run()` 仍保持兼容。

## 边界与非目标

P1 不解决以下问题：

- 不实现完整 ConfigManager / Profile；
- 不实现 `/resume`、`/status`、`/config`、`/permissions` 等产品级 CLI 命令；
- 不实现 OS 级文件系统和网络沙箱；
- 不实现 MCP、Skills 和 Plugin 分发；
- 不实现长任务 Planner、后台任务、多 Agent 和桌面端；
- 不提供完整跨进程恢复 UI，只提供状态快照和恢复判定基础。

这些能力从 P2 开始继续推进。

## 测试与验证

新增 P1 验收测试：

```text
tests/test_p1_acceptance.py
```

覆盖点：

- QueryEngine 提交消息后保存 ConversationState 快照；
- 快照可通过 RecoveryValidator 判定为 recoverable；
- 快照可恢复为 ConversationState，并保留 active Turn 的最终回复；
- ModelStreamEvent 能区分 reasoning、content_delta 和 final_message；
- 旧 `AgentLoop.run()` 同步调用仍可工作。

本次运行命令：

```text
pytest tests\test_p1_acceptance.py tests\test_query_engine_streaming.py tests\test_agent_loop.py -q --basetemp C:\Users\zhangzm\.codex\memories\zzm-agent-pytest-p1-acceptance
```

结果：

```text
46 passed in 21.01s
```

## 后续进入 P2 的迁移点

P2 应在 P1 的 QueryEngine 和状态体系之上继续做终端产品化：

- ConfigManager、Profile 和配置来源审计；
- Agent 指令文件和跨会话自动记忆；
- `/status`、`/resume`、`/sessions`、`/config`、`/permissions` 等 CLI 命令；
- 非交互 `exec`、stdin 管道和 JSON 事件输出；
- Git / Review / Commit / PR 日常工作流。
