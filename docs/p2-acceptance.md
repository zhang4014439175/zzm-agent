# P2 阶段验收说明

## 一句话说明

P2 把 zzm-agent 的终端入口从“能调用 Agent”推进为可日常使用的产品闭环：配置和项目规则可解释，会话可恢复，交互与脚本入口共享运行时，输出层次清楚，并能安全完成代码审查和 Git 交付准备。

## 解决的问题

只有 ReAct 循环和 Conversation Runtime 仍不足以支撑日常开发。用户需要知道配置来自哪里、项目指令是否生效、关闭终端后如何恢复会话、CI 如何消费稳定 JSON、Git 暂存是否会静默改动，以及 review、PR 文案和 CI 日志能否保留可核验证据。

如果这些能力只分别存在而没有阶段验收，常见失败模式包括：交互 CLI 与 `exec` 走不同状态入口；项目配置覆盖了托管策略却无法解释；推理、工具过程和最终答案混排；脚本输出夹杂 UI 文本；Git index 被无确认修改；CI 长日志分析后没有 Artifact 可以追踪。

## 具体例子

开发者进入一个含 `AGENTS.md` 和项目配置的仓库：

1. ConfigManager 合并全局与项目配置，并能指出 `model.model_name` 来自项目层；
2. MemoryStore 加载项目指令，用户创建新会话后仍可切回原会话并恢复历史；
3. 本地交互输出把 reasoning、过程状态和 final 分开；CI 使用 `zzm-agent exec --json` 得到 JSONL event/result；
4. 用户执行 stage 时必须批准，范围错误时可立即 undo；
5. `/review --cached` 只读审查暂存区，`/pr` 生成包含测试和风险的草稿；
6. `/ci ci.log` 把完整日志保存为 `ci-log` Artifact，再输出根因、相关文件、最小修复与验证命令。

## 使用场景

- 首次启动、切换项目或 profile 时核对配置来源；
- monorepo 中加载就近的 `AGENTS.md` / `ZZM.md`；
- 多次终端会话之间创建、切换和恢复上下文；
- 人工 REPL、管道、批处理与 CI 使用同一 QueryEngine；
- 提交前审查 diff、调整暂存范围并生成交付文案；
- CI 失败时保留完整日志证据并获得可执行分析。

## 执行链路

```text
配置文件 / 环境 / profile
  -> ConfigManager -> 来源与锁定审计
项目指令 / 自动记忆
  -> MemoryStore -> MemoryLoadState -> 模型上下文
用户输入
  -> REPL 或 exec -> QueryEngine -> AgentLoop
  -> ModelStreamEvent -> TerminalRenderer 或 JSONL
Git / CI 命令
  -> GitWorkflow / ArtifactStore -> QueryEngine -> 审查或交付草稿
会话结束或切换
  -> SessionStore / ConversationState -> 恢复入口
```

## 关键事件或数据结构

- `ConfigLoadResult` / `ConfigOrigin`：记录最终值、作用域来源和托管锁定；
- `InstructionFile` / `MemoryLoadState`：记录指令文件优先级、截断和注入来源；
- `ConversationState` / `TurnState`：为 `/status`、权限、Artifact 和恢复命令提供统一状态；
- `ModelStreamEvent`：让终端 renderer 与 JSONL 输出共享 status、reasoning、content、tool 和 final 语义；
- `GitSnapshot`：区分 staged 与 unstaged diff；
- `ArtifactRecord(kind="ci-log")`：保存完整 CI 日志及稳定引用。

## 代码定位

- `zzm_agent/core/config.py`：配置合并、profile、来源和锁定策略；
- `zzm_agent/memory/instructions.py`、`zzm_agent/memory/store.py`：项目指令、自动记忆和会话历史；
- `zzm_agent/cli_support/runtime.py`：REPL、`exec`、stdin、JSONL 和 completion；
- `zzm_agent/cli_support/commands.py`：状态、恢复、配置、权限、Artifact、review 和 Git/CI 命令；
- `zzm_agent/cli_support/rendering.py`：结构化终端事件渲染与纯文本降级；
- `zzm_agent/cli_support/git_workflow.py`：Git 状态、确认写入与 index 回滚；
- `zzm_agent/core/query_engine.py`：交互式与非交互式入口共享的 Turn 编排器。

## 验收结果

P2 的五项阶段标准均有自动化证据：

- 启动、配置、执行、审查、交付准备和恢复形成终端闭环；
- REPL、slash command 与 `exec` 复用 QueryEngine、状态和权限入口；
- renderer 能区分推理过程、工具/状态事件和最终结论；
- `exec --json` 可供 CI 使用，非交互权限不会等待人工输入；
- 配置、项目指令、自动记忆、会话和 CI Artifact 来源可解释。

## 边界与非目标

- P2 不提供 OS 级文件系统或网络沙箱；
- Git 工作流不直接 commit、push 或创建远程 PR；
- stage/unstage 只保留进程内最近一次逆操作，持久化 ChangeSet 属于 8.4；
- Skills、MCP 和 Plugin 命令仍是后续阶段入口；
- 异步 TUI、后台任务、多 Agent 和桌面端不属于 P2。

## 测试与验证

新增 `tests/test_p2_acceptance.py`，覆盖：

- 配置来源、项目指令和跨会话恢复；
- `exec --json` 机器输出与 PlainTextRenderer 人工输出分层；
- Git index 写操作确认与反向回滚；
- staged review、PR 草稿、CI 日志 Artifact 的只读交付链路。

定向命令：

```text
pytest tests\test_p2_acceptance.py -q
```

定向结果：`4 passed`。

全量命令：

```text
pytest tests -q
```

全量结果：`314 passed, 2 skipped`。
