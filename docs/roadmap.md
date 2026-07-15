# zzm-agent 升级改进路线图

> **执行说明：进入本文档后，先查看下方“执行进度总览”，根据勾选状态确认当前进度；每次只执行一个最小任务点。任务通过验收后，必须先补齐对应功能说明文档，再将对应的 `- [ ]` 更新为 `- [x]`，不得提前勾选或一次笼统勾选多个任务。**

标记说明：

- [x] 已完成：实现、测试、文档和对应验收均已完成；
- [ ] 未完成：尚未开始、正在进行或尚未通过完整验收；
- 任务开始后仍保持 `- [ ]`，只有满足完成定义后才改为 `- [x]`；
- 每个任务点完成后必须新增或更新对应功能说明文档，并严格使用下方“功能说明文档生成 Prompt”；
- 文档的第一读者是不了解本次代码实现的项目使用者，不是代码审查者；“读者不看源码也能明白功能有什么用、什么时候生效、现在能做到什么”是文档验收条件；
- 技术细节用于开发者定位代码，必须放在通俗说明之后；不能用术语替代解释，也不能靠类名、函数名和数据结构反向说明功能；
- 本清单是路线图进度的唯一状态来源，后文章节用于说明设计与验收要求；
- 默认从上到下执行；如果因依赖关系调整顺序，应在任务旁补充简短说明。

### 功能说明文档生成 Prompt

后续完成每个路线图任务时，使用以下 Prompt 编写对应的 `docs/<任务编号>-*.md`：

```text
请为刚完成的路线图任务编写一份中文功能说明文档。

目标读者：项目作者或普通使用者。他知道这个项目大概是做什么的，但不知道本次实现细节，也不熟悉相关技术术语。文档的首要目标是让他看懂，不是展示实现有多复杂。

写作规则：
1. 开头先用 3～5 句大白话说明：以前有什么具体问题、本次增加了什么、用户能感受到什么变化。
2. 必须提供一个从“用户做了什么”开始的完整实际例子，按时间顺序写清系统如何处理、最后用户会看到什么。不要用抽象占位符充当例子。
3. 单独增加“功能的大致流程”，从功能开始生效的入口写到最终结果，按顺序说明系统大致经过哪些阶段、每个阶段解决什么问题，以及成功、失败或冲突时分别会发生什么。这里面向普通使用者，只讲用户能理解的过程，不得用类名、函数名和字段名代替流程说明，也不能与“给开发者看的实现位置”重复。
4. 单独列出“开发前”和“开发后”的区别。描述可观察行为，不要只写内部组件变化。
5. 单独列出“现在已经能做到什么”和“现在还做不到什么”。不得把未来预留接口描述成已经可用的功能。
6. 第一次出现英文或技术术语时，必须立刻用一句日常语言解释。能用中文日常表达时，不使用术语。例如不能只写 deadline、LIFO、callback、token、checkpoint。
7. 不允许用类名、函数名、字段名或执行链路作为功能解释。读者即使跳过所有代码内容，也必须能理解前面至少 70% 的文档。
8. 通俗说明完成后，再增加“给开发者看的实现位置”，简要列出相关代码文件、关键类/函数和执行链路。每个代码名称后说明它具体负责什么。
9. 最后写测试覆盖和实际测试结果，并用普通语言说明这些测试证明了什么。
10. 避免空泛表达，如“提升健壮性”“完善机制”“统一能力”。如果使用，必须紧接具体说明：防止了什么现象，失败时会看到什么。
11. 全文优先使用短句。每段只表达一个重点。不要连续堆砌超过 3 个陌生术语。

固定结构：
- 一句话说明
- 为什么需要它
- 一个实际使用例子
- 功能的大致流程
- 开发前和开发后的区别
- 现在已经支持的能力
- 当前限制（还做不到什么）
- 给开发者看的实现位置
- 测试如何证明它有效

完成后进行一次“非技术读者检查”：隐藏“给开发者看的实现位置”一节，确认剩余内容仍能独立回答以下问题：
- 这个功能是干什么的？
- 我什么时候会用到或遇到它？
- 它具体改变了什么？
- 它从开始生效到产生结果，大致会经过什么过程？
- 它现在有什么限制？

只要其中一个问题无法从文档直接得到答案，就继续改写，不能提交文档或勾选路线图任务。
```

## 执行进度总览

> **当前下一任务：8.5 Token Budget、自动压缩与上下文解释。**

### 当前能力基线

- [x] Native Tool Calling 驱动的 ReAct 循环
- [x] 流式与非流式模型输出
- [x] 多轮工具调用及 Observation 回填
- [x] 最大工具迭代次数限制和连续重复调用熔断
- [x] 工具风险分级、执行确认和 `auto_approve`
- [x] 结构化工具错误及有限自动重试
- [x] 工具事件、耗时、Diff、Token 和费用可观测性
- [x] 多会话、Semantic / Episodic Memory
- [x] 记忆检索、上下文压缩和 Pinned Context
- [x] PromptManager、回放测试和固定基准任务
- [x] Prompt 评估、候选生成、Diff、应用和回滚

### P0：ReAct 可靠性与评测

- [x] 5.1 ProgressMonitor 无进展检测：识别工具循环、重复结果、连续失败和多轮无新信息等停滞信号
- [x] 5.2 一次性 Reflection 纠偏：首次停滞时插入结构化反思提示，要求模型换策略而不是盲目重试
- [x] 5.3 工具错误恢复增强：细分错误类型、重试策略、退避规则和恢复建议，提升模型从失败中恢复的能力
- [x] 5.4 回放基准扩充（在 5.2、5.3 后执行）：补充权限错误、空结果循环、参数修正、反思换路和安全停止等固定评测场景
- [x] P0 阶段验收：验证 ReAct 可靠性增强不破坏权限、安全熔断、短任务性能和现有回放基线

### P1：Conversation Runtime 与完整状态管理

- [x] 6.1 状态生命周期与所有权模型：定义 Application、Conversation、Turn、Loop、Task 和 WorkingMemory 的创建、修改、持久化和销毁边界
- [x] 6.2 ApplicationState / ConversationState / TurnState / LoopState：把当前散落在 AgentLoop 中的计数器、消息、权限、用量和运行状态收敛到明确状态对象
- [x] 6.3 LoopPhase / LoopTransition 正式状态机：用显式阶段和转换原因描述 ReAct 循环，支持 follow-up、reflection、stop hook、取消和失败
- [x] 6.4 运行时消息、待提交消息与持久化消息分层：区分当前执行视图、未提交消息、已提交历史和模型上下文视图
- [x] 6.5 完整 UsageState 及多作用域累计：按模型、Turn、Conversation、Task 和应用层累计 Token、调用次数和费用
- [x] 6.5.1 Prompt 输出约束与结构化回复协议：统一 system prompt 中的工具调用边界、最终回复版式和不同任务类型的回答协议
- [x] 6.6 完整 PermissionState 及权限生命周期：记录权限请求、授权、拒绝、过期、孤立请求和不同作用域的权限决定
- [x] 6.7-6.8 文件状态缓存与 Memory 加载去重：合并开发 FileStateCache 和 MemoryLoadState，统一处理路径规范化、版本、重复注入、失效和上下文来源追踪
- [x] 6.9 CancellationController 基础层级模型：为会话、Turn、模型请求和工具调用建立可传播的取消控制基础
- [x] 6.10 Hook 系统、Stop Hook 与阻塞重试保护：支持执行前后扩展点，并防止 Stop Hook 无限阻塞最终回复
- [x] 6.11 EventBus、ArtifactStore 与 CheckpointStore：统一记录事件、保存大结果/产物，并为恢复与回放提供检查点
- [x] 6.12-6.15 工具结果、进度事件与展示协议：合并开发 ToolResult、ToolProgressEvent、ToolRenderer / RendererRegistry 和 DisplayMode，统一打通模型内容、展示内容、Artifact、进度和折叠策略
- [x] 6.16 状态序列化、版本迁移与恢复协议：让 Conversation、Turn、Task 等状态可持久化、可升级并能在重启后安全恢复
- [x] 6.17-6.20 QueryEngine、ModelAdapter、StreamEvent 与 CLI 主链路迁移：合并开发跨 Turn 编排器、模型适配层、分层流事件和 CLI 主执行路径，避免先迁移 CLI 后再重改流式与模型协议
- [x] P1 阶段验收：确认完整状态体系可观察、可恢复，并保持现有同步 ReAct 调用兼容

### P2：配置、指令文件与 CLI 产品化

- [x] 7.1 ConfigManager、Profile 与配置作用域：合并开发全局、项目、本地和托管配置，统一模型、权限、MCP、Skills、UI 和功能开关来源
- [x] 7.2 Agent 指令文件与自动记忆：支持 `AGENTS.md` / `ZZM.md` 分层加载、就近覆盖、来源审计、大小预算和跨会话自动记忆
- [x] 7.3 Slash Command 与交互式 CLI：合并开发 `/status`、`/resume`、`/sessions`、`/config`、`/permissions`、`/artifacts`、`/plan`、`/review` 等核心命令
- [x] 7.3A 终端输出分层与可降级渲染：先解决思考过程、工具执行和最终总结混排问题，建立可复用的 CLI 渲染边界
- [x] 7.3B 响应语言策略、系统语言检测与全局语言设置：支持系统 locale 默认识别、会话语言继承、用户全局语言偏好和单轮语言覆盖
- [x] 7.4 非交互 `exec`、stdin 管道与 JSON 输出：支持脚本、CI、批处理、`--json` 事件流、最终结果输出文件和 shell completion
- [x] 7.5 Git / Review / Commit / PR 工作流：合并开发 diff review、stage/unstage、commit message、branch、PR 描述和 CI 失败分析入口
- [x] P2 阶段验收：确认终端版具备可恢复、可配置、可脚本化、可审查和可日常高频使用的产品体验

### P3：本地执行安全、沙箱与上下文治理

- [x] 8.1 工具生命周期、参数校验与权限网关：合并开发工具注册、参数 schema 校验、风险分级、权限确认、执行前后事件和结果记录
- [x] 8.2 文件系统与网络沙箱 Profile：支持 read/write/deny、workspace roots、敏感文件拒读、网络域名 allow/deny、localhost/private network 规则和 Windows/WSL 差异
- [x] 8.3 工具超时、取消与资源清理：为模型请求、Shell、文件操作、MCP 工具和后台进程提供超时、用户取消、安全检查点和清理回调
- [x] 8.4 ChangeSet、Patch 与 `/undo`：记录受管文件变更、生成可审查 Patch、支持按变更集撤销并处理冲突
- [x] 8.4A.1 统一终止原因与结束可观测性：区分 completed、yielded、blocked、failed、cancelled，所有结束路径必须显示并持久化原因
- [x] 8.4A.2 空模型回复与异常完成恢复：空内容且无工具调用不得标记完成，记录 provider finish reason，有限恢复后明确阻塞
- [ ] 8.5 Token Budget、自动压缩与上下文解释：合并开发上下文预算、超长工具结果 Artifact 化、自动 compact、prompt cache 策略和上下文来源说明
- [ ] 8.4A.3 SegmentResult 与安全让出：把工具轮次/上下文段上限从“终止任务”改为 yielded 检查点，不把内部换段暴露为任务失败
- [ ] 8.4A.4 QueryEngine 自动续段与基础完成门禁：压缩后自动继续同一任务，只有明确完成、阻塞、失败或取消才把控制权交回用户
- [ ] 8.4A 阶段验收：确认长工具任务不会静默结束或因单段轮次耗尽而假完成，简单任务无额外续跑开销
- [ ] 8.6 本地工具 Renderer 合集：合并开发 FileRead、FileEdit、Search、Shell、动态活动描述和纯文本降级渲染
- [ ] P3 阶段验收：确认本地工具执行有确定性安全边界、可撤销、可取消、可解释，长结果不会污染模型上下文，且所有任务结束原因可见

### P4：MCP、Skills 与 Plugin 分发

- [ ] 9.1 MCP Client 与连接治理：支持 stdio / HTTP / SSE / WebSocket 连接、能力发现、动态工具更新、鉴权、重连、限流和错误隔离
- [ ] 9.2 Skills 模块化与发现状态：合并开发 Skill 格式、渐进式加载、显式/隐式触发、SkillDiscoveryState、资源预算和禁用策略
- [ ] 9.3 工具 Schema 按需装载与 Tool Search：根据任务、Skill、MCP server 和阶段延迟暴露工具，减少 schema token 浪费
- [ ] 9.4 Plugin Manifest、安装与启停：支持插件打包 Skills、MCP 配置、资源、UI 元数据、权限声明、依赖和版本
- [ ] 9.5 MCP / Skill / Plugin Renderer：统一展示来源、连接状态、激活原因、工具进度、权限请求和远程错误
- [ ] P4 阶段验收：确认外部工具生态可安装、可禁用、可解释、可审计，并不会绕过核心权限和沙箱

### P5：长任务规划、工作记忆与任务恢复

- [ ] 10.0 TaskRouter 与自动规划策略：按 simple、standard、planned、durable 路由任务，支持 planning_mode=auto|always|never 和显式 `/plan`
- [ ] 10.1 TaskState 与 WorkingMemory：合并开发任务目标、步骤、发现、产物、阻塞、临时事实和压缩注入策略
- [ ] 10.2 外层 Planner、计划 Diff 与重规划：在 AgentLoop 外部拆解任务、调度步骤、反思失败、调整计划并保留简单任务轻量路径
- [ ] 10.2A TaskRunner、计划感知 CompletionGate 与持续执行：模型最终回复只作为完成提议，必须验证计划步骤、验收条件和证据后才能结束
- [ ] 10.3 用户干预、暂停与恢复：支持确认、修改、跳过、重试、暂停、从检查点恢复和不可恢复原因报告
- [ ] 10.4 PlannerRenderer 与 TaskProgressRenderer：展示目标、步骤、当前动作、计划变化、完成比例、Usage、Artifacts 和阻塞原因
- [ ] P5 阶段验收：确认复杂任务能跨 Turn 执行和恢复，用户能看懂计划变化，简单任务不会被强制 Planner 化

### P6：异步、并发、后台任务与自动化

- [ ] 11.1 Async Agent Loop 与只读工具并发：合并开发 `async_run()`、同步兼容、低风险只读并发和顺序回填策略
- [ ] 11.2 ToolCallScheduler、后台进程与取消传播：统一调度并发工具、后台进程、模型流、CancellationController 和资源清理
- [ ] 11.3 Circuit Breaker 与外部依赖降级：覆盖 Provider、MCP Server、网络工具和后台服务的熔断、半开探测和手动恢复
- [ ] 11.4 Automations、定时任务与事件触发：支持 recurring task、monitor、webhook/channel trigger、失败重试、通知和运行历史
- [ ] 11.5 ConcurrentToolsRenderer 与 BackgroundProcessRenderer：展示并发工具、后台任务、日志 Artifact、退出码、取消和失败原因
- [ ] 11.6 基于 prompt_toolkit 的异步交互式终端 TUI：在 async_run() 和任务调度稳定后，实现固定底部输入框、状态栏和 Esc 异步取消
- [ ] P6 阶段验收：确认异步、并发、后台与自动化任务可观察、可取消、可恢复，且写操作不会错误并发

### P7：多 Agent 协作与 Worktree 隔离

- [ ] 12.1 Sub-Agent / TaskTool 与子 Agent 状态：合并开发委派协议、独立上下文、权限边界、Usage 汇总、取消传播和结构化结果
- [ ] 12.2 Git Worktree 隔离与合并审查：支持子 Agent 独立分支/工作树、测试、Diff、冲突检查、合并前审核和失败清理
- [ ] 12.3 Swarm / Agent Team 编排：支持多 Agent 依赖图、角色、并行/串行调度、共享事实、冲突结论处理和资源上限
- [ ] 12.4 SubAgentRenderer 与 SwarmRenderer：展示 Agent 拓扑、任务分配、状态、成本、阻塞、证据和整体收敛
- [ ] P7 阶段验收：确认多 Agent 可控、可核验、可取消、可清理，并能在大任务中带来可衡量收益

### P8：浏览器、Computer Use、Web 测试与 CI 集成

- [ ] 13.1 Browser Controller 与网页调试：支持打开页面、点击、输入、截图、DOM 检查、控制台日志和本地 Web App 冒烟测试
- [ ] 13.2 Computer Use 高风险能力边界：支持桌面应用操作前的显式授权、录屏/截图证据、敏感区域保护和失败回退
- [ ] 13.3 Web / CI / GitHub 集成：支持 CI 失败分析、PR 自动审查、issue/PR 触发任务、状态回写和安全凭据边界
- [ ] 13.4 浏览器与 CI Renderer：展示页面状态、截图、测试结果、PR 评论、CI 日志和可复现证据
- [ ] P8 阶段验收：确认 Agent 能处理真实 Web/CI 工作流，但浏览器、电脑操作和远程集成都受权限、审计和回放约束

### P9：Client API、App Server 与桌面客户端

- [ ] 14.1 App Server / 本地桥接协议：为 CLI、桌面和未来 Web UI 提供提交消息、取消、权限、会话、任务、Artifact 和事件订阅 API
- [ ] 14.2 Desktop Client 边界与主工作台：桌面端只作为 QueryEngine 前端，展示会话、Turn、任务、Usage、Artifacts、恢复入口和错误状态
- [ ] 14.3 桌面权限、取消与工具进度界面：复用 PermissionState、CancellationController、EventBus 和 RendererRegistry，不复制运行时状态机
- [ ] 14.4 Artifact / Diff / 日志 / Replay 查看器：提供长内容、Patch、测试日志、后台任务日志和回放记录的专门视图
- [ ] 14.5 桌面端端到端验收：覆盖 CLI 与桌面行为一致性、重启恢复、权限请求、取消、后台任务和桥接层异常
- [ ] P9 阶段验收：确认桌面端与终端版共享同一核心运行时，UI 异常不改变 Agent 执行结果

### P10：企业治理、安全审计与可运维性

- [ ] 15.1 Secret Redaction、外部内容隔离与 Prompt Injection 防护：把网页、MCP、日志、工具输出视为不可信输入并记录来源
- [ ] 15.2 Governance、Managed Config 与审计日志：支持组织级策略、禁止覆盖项、权限审计、数据保留和导出
- [ ] 15.3 Telemetry、性能指标与成本报表：记录成功率、延迟、Token、费用、工具耗时、失败原因和阶段对比
- [ ] 15.4 安全扫描与发布门禁：集成 SAST/依赖扫描/自定义安全 review，生成可追踪发现和修复证据
- [ ] P10 阶段验收：确认系统具备企业可控性、安全审计和长期运维能力

### P11：最终产品验收与发布

- [ ] 16.1 端到端基准与真实任务套件：覆盖代码理解、修改、测试、PR、Web 调试、长任务、多 Agent、恢复和桌面操作
- [ ] 16.2 兼容性与迁移验证：覆盖 Windows、WSL、macOS/Linux、不同 shell、旧会话、旧配置和旧记忆数据
- [ ] 16.3 用户文档、示例项目与故障排查：补齐终端版、桌面版、插件、MCP、权限、安全和自动化文档
- [ ] 16.4 发布检查清单：确认安装、升级、回滚、隐私、安全、性能、可观测性和支持流程
- [ ] P11 阶段验收：确认 zzm-agent 达到可长期日常使用的终端版和桌面版最终产品标准

---

## 1. 路线图目标

zzm-agent 已经具备较完整的 ReAct 核心循环、工具执行安全底座、分层记忆、上下文压缩、Prompt 管理、可观测性和回放评估能力。

后续升级的目标不是机械复制其他 Agent 框架，而是在保持核心简单、可测试和可控的前提下，逐步提高：

- 现有 ReAct 的任务成功率和错误恢复能力；
- 本地文件与命令执行的安全性和可撤销性；
- 终端版的可配置、可恢复、可脚本化和 Git/PR 日常工作流；
- 模型、上下文、工具和外部协议的扩展能力；
- 长任务的规划、状态保持、暂停和恢复能力；
- 异步执行、并发工具和后台任务的运行效率；
- 多 Agent 协作和隔离执行能力；
- 桌面端、浏览器、CI 和企业治理等产品化能力。

Datawhale Hello-Agents、Claude Code、Codex 等项目作为设计参考，但不作为逐项复刻清单。每项升级都应对应明确的用户痛点，并通过单元测试、回放基准或可量化指标证明收益。

---

## 2. 当前能力基线

### 2.1 已完成

以下内容是“执行进度总览”中已完成基线的详细说明，不在此处重复维护勾选状态：

- Native Tool Calling 驱动的 ReAct 循环；
- 流式与非流式模型输出；
- 多轮工具调用及 Observation 回填；
- 最大工具迭代次数限制和连续重复调用熔断；
- Low / Medium / High 工具风险分级及执行确认；
- 结构化工具错误及有限自动重试；
- 工具事件、耗时、Diff、Token 和费用可观测性；
- 多会话、Semantic / Episodic Memory；
- 记忆检索、上下文压缩和 Pinned Context；
- PromptManager、回放测试和固定基准任务；
- Prompt 评估、候选生成、Diff、应用和回滚。

### 2.2 当前主要缺口

- QueryEngine 已成为 CLI 主消息入口，但未来桌面端、后台任务和多 Agent 仍需要继续接入同一入口；
- ModelAdapter 与 ModelStreamEvent 已完成基础协议，后续仍需扩展更多 provider 能力声明和 JSON/桌面事件输出；
- 缺少完整配置作用域、Profile、Agent 指令文件和跨会话自动记忆；
- CLI 缺少产品级 `/resume`、`/status`、`/config`、`/permissions`、`/review`、`exec`、JSON 输出和管道化能力；
- 工具运行时校验、OS 级沙箱、网络权限、超时和取消机制仍可加强；
- 文件变更缺少任务级统一记录和一键撤销；
- Git / PR / Code Review / CI 失败分析尚未形成日常工作流；
- 工具输出、记忆、指令文件、Skill、MCP 和历史消息尚未形成完整的分区预算与自动压缩策略；
- 尚未接入 MCP、Skills 和 Plugin 分发机制；
- 缺少 TaskState、WorkingMemory 和外层 Planner；
- Agent Loop 仍为同步执行，多个 tool call 顺序运行；
- 缺少后台任务、自动化、子 Agent、Worktree 隔离和 Swarm 编排；
- 缺少浏览器控制、Computer Use、Web/CI 集成和可复现视觉证据；
- 缺少 App Server / Client API，桌面端尚无稳定桥接协议；
- 缺少 Secret Redaction、Prompt Injection 防护、托管配置、审计日志和可运维指标。

---

## 3. 路线图原则

1. **真实痛点优先**：先解决已经出现的失败模式，再扩大能力边界。
2. **确定性机制优先**：权限、校验、超时和熔断不能只依赖模型自觉。
3. **保持 AgentLoop 聚焦**：AgentLoop 负责单次用户轮次内的 ReAct；Planner 在外层编排。
4. **短任务保持轻量**：普通问答和简单工具调用不承担 Planner、Reflection 等额外模型开销。
5. **抽象由实现推动**：BaseAgent 等统一抽象保留在正式计划中，但应基于多个真实 Agent 实现提炼。
6. **写操作默认串行**：文件写入、Shell 和存在副作用的工具不得盲目并发。
7. **状态可观察、可恢复**：长任务必须能展示进度、报告阻塞并支持恢复。
8. **兼容现有入口**：异步改造、多 Provider 和 Planner 不应破坏现有同步调用方式。
9. **每阶段可独立验收**：实现、测试、文档和回放指标同时满足后才算完成。
10. **终端版先产品化**：CLI 的恢复、配置、脚本化、Git/PR 和 review 工作流必须早于桌面 UI。
11. **多端共享同一内核**：CLI、桌面端、未来 Web UI 和自动化任务都必须通过 QueryEngine / Client API 调用同一运行时。
12. **外部内容默认不可信**：网页、MCP、日志、CI 输出和工具结果必须隔离来源，避免 prompt injection 和敏感数据外泄。
13. **所有规划能力正式保留**：后置阶段代表实施顺序，不代表可选、搁置或取消。

---

## 4. 整体演进路线

```mermaid
flowchart TD
    A["当前基线：可靠的单轮 ReAct"] --> B["P0：ReAct 可靠性与评测"]
    B --> C["P1：Conversation Runtime 与完整状态管理"]
    C --> D["P2：配置、指令文件与 CLI 产品化"]
    D --> E["P3：本地执行安全、沙箱与上下文治理"]
    E --> F["P4：MCP、Skills 与 Plugin 分发"]
    F --> G["P5：长任务规划、工作记忆与任务恢复"]
    G --> H["P6：异步、并发、后台任务与自动化"]
    H --> I["P7：多 Agent 协作与 Worktree 隔离"]
    I --> J["P8：浏览器、Computer Use、Web 测试与 CI 集成"]
    J --> K["P9：Client API、App Server 与桌面客户端"]
    K --> L["P10：企业治理、安全审计与可运维性"]
    L --> M["P11：最终产品验收与发布"]
```

### 4.1 实际执行顺序

阶段编号表示能力分类，实际执行按依赖关系推进：

```text
5.1 ProgressMonitor（已完成）
→ 5.2 Reflection
→ 5.3 错误恢复
→ 5.4 回放基准
→ P0 阶段验收
→ P1 Conversation Runtime 与完整状态管理
→ P2 配置、指令文件与 CLI 产品化
→ P3 本地执行安全、沙箱与上下文治理
→ P4 MCP、Skills 与 Plugin 分发
→ P5 长任务规划、工作记忆与任务恢复
→ P6 异步、并发、后台任务与自动化
→ P7 多 Agent 协作与 Worktree 隔离
→ P8 浏览器、Computer Use、Web 测试与 CI 集成
→ P9 Client API、App Server 与桌面客户端
→ P10 企业治理、安全审计与可运维性
→ P11 最终产品验收与发布
```

P0 先完成现有 ReAct 的可靠性闭环；P1 收敛运行时状态，并在 6.17-6.20 把 QueryEngine、ModelAdapter、StreamEvent 和 CLI 主链路一次打通；P2 先把终端版做成可日常使用的产品，再进入更重的沙箱、生态、长任务、多端和企业能力。

### 4.2 完整概念的引入时间

| 完整概念 | 首次引入 | 后续扩展 |
|---|---|---|
| Application / Conversation / Turn / Loop State | P1 | P5 Task、P7 Child Agent |
| ModelAdapter / ModelStreamEvent | P1 | P4 MCP 工具、P6 Async、P9 Desktop |
| QueryEngine | P1 | P5 Task、P6 后台任务、P7 Multi-Agent、P9 Desktop |
| ConfigManager / Profile | P2 | P3 沙箱、P4 MCP/Plugin、P10 托管配置 |
| Agent 指令文件 / 自动记忆 | P2 | P4 Skills、P5 WorkingMemory、P10 审计 |
| CLI command / exec / JSON event | P2 | P6 自动化、P8 CI、P9 App Server |
| TerminalRenderer / TUI Shell | P2 渲染协议 | P6 异步 TUI、P9 Desktop |
| Git / Review / PR workflow | P2 | P7 Worktree、P8 CI、P10 安全扫描 |
| Loop 状态机、`needs_follow_up` | P1 | P5 Planner、P6 Async |
| Hook、Stop Hook、`stop_hook_active` | P1 | P5 Task Hook、P7 Agent Hook、P10 Governance |
| Runtime / Pending / Persisted Messages | P1 | P5 WorkingMemory、P7 Agent 消息 |
| UsageState | P1 | P5 Task Usage、P6 Automation、P7 子 Agent 汇总、P10 成本报表 |
| PermissionState | P1 | P3 权限策略、P6 自动化、P7 Agent 边界 |
| OS 沙箱 / 网络权限 Profile | P3 | P4 MCP、P7 Worktree、P10 托管策略 |
| FileStateCache | P1 | P3 ChangeSet、P7 Worktree |
| MemoryLoadState | P1 | P2 指令文件、P4 Skill References、P5 WorkingMemory |
| CancellationController | P1 同步基础 | P6 异步传播、P7 子 Agent 树 |
| EventBus / Artifact / Checkpoint | P1 | 后续全部阶段复用 |
| ToolResult 展示分层 / ToolProgressEvent | P1 | P3 本地工具、P4 MCP/Skill、P5 Planner、P6/P7 并发与 Agent |
| ToolRenderer / RendererRegistry / DisplayMode | P1 | 各阶段注册对应的专属 Renderer |
| MCP / Skill / Plugin | P4 | P5 Task Skill、P7 Agent Skill、P9 Desktop |
| TaskState / WorkingMemory | P5 | P7 分布式子任务 |
| Async / Background / Automation | P6 | P7 Agent Team、P9 Desktop、P10 运维 |
| Browser / Computer Use / CI | P8 | P9 Desktop、P10 审计 |
| Client API / App Server | P9 | 桌面端、未来 Web UI、远程控制 |
| BaseAgent | P7 | 多 Agent 类型、SDK 化 |
| Orphaned Permission Recovery | P6 | P7 子 Agent 恢复 |
| Governance / Audit / Telemetry | P10 | P11 最终发布 |

---

## 5. P0：ReAct 可靠性与评测

### 5.1 ProgressMonitor 无进展检测

在现有重复工具调用检测之上，识别：

- 连续多次不可重试错误；
- 相同工具、参数和 Observation 重复；
- 参数变化但 Observation 基本不变；
- 多个工具之间形成固定循环；
- 连续多轮没有产生新事实、文件变化或有效结果。

ProgressMonitor 只负责判断执行是否停滞，不替代最大迭代上限。

完成情况：

- [x] 新增独立 `ProgressMonitor` 和结构化 `ProgressSignal`；
- [x] 检测变化参数连续得到相同 Observation；
- [x] 检测连续不可重试失败；
- [x] 检测固定工具轮次循环；
- [x] 新结果会重置连续重复结果计数；
- [x] AgentLoop 在检测到停滞后安全持久化结果并停止；
- [x] 保留现有最大迭代和相同调用熔断机制；
- [x] 单元测试、AgentLoop 集成测试和全量回归通过。

### 5.2 一次性 Reflection 纠偏

当执行没有进展时，在熔断前插入一次结构化纠偏提示，要求模型总结已尝试方法、判断失败原因，并更换工具、参数或执行路线。

约束：

- 每个用户轮次最多触发一次；
- 不重置 `max_tool_iterations`；
- 再次无进展时立即熔断；
- 正常成功任务不增加额外模型调用；
- 不要求模型输出或持久化完整隐藏思维链。

完成情况：

- [x] ProgressMonitor 首次检测停滞时注入结构化 `REFLECTION_REQUIRED` System 消息；
- [x] 相同工具盲目重复达到限制时也先获得一次 Reflection 机会；
- [x] Reflection 提示包含停滞原因、轮次数、换路要求和阻塞报告要求；
- [x] 每个用户 Turn 最多触发一次 Reflection；
- [x] Reflection 不重置 `max_tool_iterations`；
- [x] Reflection 后再次停滞会明确标记 `after reflection` 并安全停止；
- [x] Reflection 提示只存在于当前运行上下文，不写入持久会话历史；
- [x] 正常成功任务不产生额外 Reflection 调用；
- [x] 单元测试、AgentLoop 集成测试、Replay 测试和全量回归通过。

### 5.3 工具错误恢复增强

- 区分参数、权限、超时、环境、外部服务和业务错误；
- 根据错误类型决定是否允许重试；
- 支持 Retry-After 和指数退避；
- 对确定性失败禁止盲目重试；
- 将错误摘要、尝试次数和恢复建议统一反馈给模型。

完成情况：

- [x] `ToolError` 增加 `category`、`deterministic`、`attempts` 和 `retry_after_seconds` 字段，同时保留旧 JSON 字段兼容；
- [x] 将参数错误、权限错误、超时、环境错误、外部服务错误和业务错误映射为明确分类；
- [x] 对确定性参数错误、缺失工具、权限拒绝和文件缺失等失败禁止自动重试；
- [x] 对超时和外部服务错误允许有界自动重试；
- [x] 支持异常对象上的 `retry_after_seconds` / `retry_after` 以及响应头 `Retry-After`；
- [x] 自动重试优先遵守 Retry-After，否则使用指数退避；
- [x] 最终错误结果会反馈尝试次数、错误分类、是否确定性、是否可重试和恢复建议；
- [x] 新增无磁盘依赖的错误恢复目标测试，覆盖确定性参数错误、指数退避和 Retry-After。

### 5.4 回放基准扩充

增加权限错误、相同空结果、双工具循环、参数修正恢复、主动报告阻塞、Reflection 换路和安全停止等固定场景。

完成情况：

- [x] Replay Runner 增加扩展断言，支持检查模型调用次数、Reflection 次数、ProgressSignal 原因、工具调用序列、运行时 Prompt、Retry-After 等待和工具结果 JSON；
- [x] Replay Runner 支持从 YAML 声明工具异常，覆盖真实 `tool_error_from_exception()` 错误分类路径；
- [x] Replay Runner 使用内存 Store 运行 deterministic replay，避免磁盘临时目录影响 AgentLoop 行为验证；
- [x] 新增 `07_reflection_repeated_observation.yaml`，覆盖重复 Observation 触发 Reflection；
- [x] 新增 `08_error_category_recovery.yaml`，覆盖 FileNotFoundError 分类和换工具恢复；
- [x] 新增 `09_retry_after_external_service.yaml`，覆盖外部服务 Retry-After 和重试耗尽结构化错误；
- [x] 新增 `tests/test_eval_runner.py`，验证新增 benchmark 能通过，且 runner 能发现错误期望；
- [x] 新增 `docs/5.4-replay-benchmarks.md`，说明功能作用、代码位置、执行链路、测试位置和验证结果。

### 验收标准

- 相同失败调用不会持续到最大迭代次数；
- Reflection 不绕过权限确认和硬性熔断；
- 正常短任务的调用次数和延迟无明显退化；
- 新增行为均具备确定性回放测试。

完成情况：

- [x] 目标测试通过：`tests/test_progress_monitor.py`、`tests/test_tool_error_recovery.py`、`tests/test_eval_runner.py` 共 12 个测试通过；
- [x] Replay suite 通过：9 个 replay benchmark 全部通过，成功率 100%；
- [x] 验收期间修复 replay eval 对真实临时 workspace 的依赖，replay 模式改为无磁盘写入；
- [x] 新增 `docs/p0-acceptance.md`，记录验收项、对应代码、测试结果、已知限制和进入 P1 的迁移点。

---

## 6. P1：Conversation Runtime 与完整状态管理

本阶段建立类似 QueryEngine 的正式会话运行时。它不是临时包装层，而是跨用户 Turn 状态的最终所有者。后续权限、Skills、Planner、异步和多 Agent 都在这一状态体系上扩展。

### 6.1 状态生命周期与所有权模型

正式定义五层状态及生命周期：

```text
ApplicationState
└── ConversationState
    ├── TurnState
    │   └── LoopState
    └── TaskState
        └── WorkingMemory
```

- `ApplicationState`：进程级，保存配置、模型、工具、Skills、MCP 连接和活动会话；
- `ConversationState`：会话级，跨多个用户 Turn 累积；
- `TurnState`：单次用户输入级，从提交消息到最终完成；
- `LoopState`：单个 Turn 内部的 ReAct 状态；
- `TaskState`：长任务级，跨多个 Turn 和子步骤存在；
- `WorkingMemory`：Task 内的结构化临时记忆。

每种状态必须明确：创建者、唯一所有者、允许修改者、持久化边界、恢复策略和销毁时机。

完成情况：

- [x] 新增 `zzm_agent/core/state_lifecycle.py`，定义状态范围、生命周期、持久化边界、恢复策略和状态生命周期规则；
- [x] 定义 `Application / Conversation / Turn / Loop / Task / WorkingMemory` 的父子关系、所有者、允许修改者、创建者、销毁时机和用途；
- [x] 提供 `get_state_policy()`、`state_lineage()`、`state_children()` 和 `validate_state_lifecycle_policies()` 查询与校验函数；
- [x] 新增 `tests/test_state_lifecycle.py`，固定状态层级、所有权、持久化边界和恢复策略；
- [x] 新增 `docs/6.1-state-lifecycle-ownership.md`，说明整体背景、代码位置、执行链路、测试位置和后续 6.2 边界。

### 6.2 ApplicationState / ConversationState / TurnState / LoopState

正式状态至少包含：

```text
ApplicationState
├── configuration
├── model_registry
├── tool_registry
├── skill_registry
├── mcp_connections
└── active_session_id

ConversationState
├── session_id
├── messages
├── usage
├── permissions
├── file_reads
├── skills
├── memories
├── cancellation
├── active_turn
└── active_task

TurnState
├── turn_id
├── user_input
├── status
├── usage
├── discovered_skills
├── loaded_memory_paths
├── permission_requests
├── permission_denials
├── artifacts
├── loop
├── final_response
└── error

LoopState
├── phase
├── transition
├── model_iterations
├── tool_iterations
├── reflection_count
├── current_tool_calls
├── observations
├── progress_signal
├── needs_follow_up
├── stop_hook_active
└── stop_hook_attempts
```

当前 `AgentLoop` 中的局部计数器和 `last_*` 字段逐步迁移到这些有明确作用域的状态对象。

完成情况：

- [x] 新增 `zzm_agent/core/runtime_state.py`，实现 `ApplicationState`、`ConversationState`、`TurnState`、`LoopState` 四层运行时状态对象；
- [x] 新增 `TurnStatus` 和 6.2 过渡版 `LoopPhase`，先承载状态字段和基础阶段记录，正式状态机留到 6.3 完成；
- [x] 每个运行时状态对象都提供 `policy()`，与 6.1 的 `StateScope` / `StatePolicy` 生命周期规则对齐；
- [x] `ConversationState` 支持创建、完成、失败和防重入 Turn，避免同一会话中同时存在多个活动 Turn；
- [x] `TurnState` 记录用户输入、状态、用量、Skills、Memory 路径、权限请求/拒绝、Artifacts、最终回复和错误；
- [x] `LoopState` 记录模型调用次数、工具轮次、当前工具调用、Observation、无进展信号、Reflection 次数、follow-up 标记和 Stop Hook 标记；
- [x] `AgentLoop.run()` 开始创建并更新 `last_turn_state` / `last_loop_state`，让单轮 ReAct 执行过程能被结构化观察；
- [x] 新增 `tests/test_runtime_state.py`，覆盖状态对象字段、生命周期、防重入、Loop 观测记录，以及 `AgentLoop` 的基础集成；
- [x] 新增 `docs/6.2-runtime-state-objects.md`，用中文说明整体作用、代码位置、关键类、执行链路、测试位置和验证结果。

### 6.3 LoopPhase / LoopTransition 正式状态机

正式引入状态枚举：

```text
LoopPhase:
IDLE → PREPARING → CALLING_MODEL → STREAMING_RESPONSE
→ VALIDATING_TOOL_CALLS → AWAITING_PERMISSION
→ EXECUTING_TOOLS → PROCESSING_OBSERVATIONS
→ REFLECTING → RUNNING_STOP_HOOKS
→ COMPLETED / BLOCKED / CANCELLED / FAILED
```

转换原因至少包括：

- `next_turn`；
- `tool_follow_up`；
- `reflection_retry`；
- `stop_hook_retry`；
- `completed`；
- `no_progress`；
- `iteration_limit`；
- `duplicate_call_limit`；
- `permission_denied`；
- `blocked`；
- `cancelled`；
- `error`。

`needs_follow_up` 显式表示工具执行后是否需要再次调用模型；`stop_hook_active` 和 `stop_hook_attempts` 防止 Stop Hook 无限阻止结束。所有状态转换通过集中方法执行并验证非法转换。

完成情况：

- [x] 扩展 `LoopPhase`，正式覆盖 `PREPARING`、`STREAMING_RESPONSE`、`VALIDATING_TOOL_CALLS`、`AWAITING_PERMISSION`、`PROCESSING_OBSERVATIONS`、`RUNNING_STOP_HOOKS` 和终态；
- [x] 新增 `LoopTransition`，枚举 `next_turn`、`tool_follow_up`、`reflection_retry`、`stop_hook_retry`、`completed`、`no_progress`、`iteration_limit`、`duplicate_call_limit`、`permission_denied`、`blocked`、`cancelled`、`error` 等转换原因；
- [x] 新增 `LoopTransitionError` 和 `_ALLOWED_LOOP_TRANSITIONS`，通过 `LoopState.transition_to()` 集中校验非法状态跳转；
- [x] 新增 `transition_history`，记录每次状态转换的来源、目标和原因，方便后续回放、调试和 UI 展示；
- [x] 为 `LoopState` 增加 `prepare_next_turn()`、`record_streaming_response()`、`validate_tool_calls()`、`await_permission()`、`record_permission_denial()`、`record_tool_execution_start()`、`mark_cancelled()` 和 `mark_failed()` 等状态机方法；
- [x] `AgentLoop.run()` 接入工具校验、权限等待、权限拒绝、工具执行、Observation 处理和流式响应阶段；
- [x] `needs_follow_up` 在工具 Observation 后置为 `True`，在完成、阻塞、取消或失败时清理；
- [x] `stop_hook_active` / `stop_hook_attempts` 进入 `RUNNING_STOP_HOOKS` 阶段，为后续 6.10 Hook 系统预留正式状态入口；
- [x] 扩充 `tests/test_runtime_state.py`，覆盖合法状态流、非法转换、转换原因映射、权限拒绝路径和 AgentLoop 集成；
- [x] 新增 `docs/6.3-loop-state-machine.md`，说明整体作用、代码位置、关键类/函数、执行链路、测试位置和验证结果。

### 6.4 运行时消息、待提交消息与持久化消息分层

建立完整消息模型：

```text
ConversationMessageStore
├── persisted_messages
├── runtime_messages
├── pending_messages
└── model_context_messages
```

- `persisted_messages`：已经原子提交的完整历史；
- `runtime_messages`：当前会话运行视图；
- `pending_messages`：当前 Turn 尚未提交的消息；
- `model_context_messages`：经过压缩和预算分配后发送给模型的视图。

中断时只回滚 `pending_messages`，不得破坏已提交历史；上下文压缩不得覆盖原始完整消息。

完成情况：

- [x] 新增 `zzm_agent/core/runtime_messages.py`，实现 `ConversationMessageStore` 运行时消息账本；
- [x] 明确 `persisted_messages`、`runtime_messages`、`pending_messages`、`model_context_messages` 和 `committed_messages` 的职责；
- [x] 新增 `begin_turn()`，从压缩后的模型上下文和当前用户消息创建本轮消息账本；
- [x] 新增 `append_pending()`，把当前 Turn 应提交的 user、assistant、tool 消息同时加入运行视图和待提交缓冲；
- [x] 新增 `append_runtime_only()`，支持 Reflection 等只给模型看的运行时消息，不写入真实历史；
- [x] 新增 `prepare_model_context()`，在每次模型调用前冻结当前模型上下文快照；
- [x] 新增 `commit()` 和 `rollback_pending()`，正常完成时原子提交 pending，中断时只回滚 pending；
- [x] `AgentLoop.run()` 接入消息账本，替代裸 `messages` / `turn_messages` 提交流程；
- [x] `_request_reflection()` 改为写入 runtime-only 消息，避免 Reflection 提示污染持久化历史；
- [x] 新增 `tests/test_runtime_messages.py`，覆盖消息分层、提交、回滚和输入复制隔离；
- [x] 扩充 `tests/test_runtime_state.py`，覆盖 `AgentLoop` 简单回复、工具调用和 Reflection runtime-only 集成；
- [x] 新增 `docs/6.4-runtime-message-layers.md`，说明整体作用、代码位置、关键类/函数、执行链路、测试位置和验证结果。

### 6.5 完整 UsageState 及多作用域累计

UsageState 完整记录：

- input / output tokens；
- cache creation / cache read tokens；
- reasoning tokens；
- tool schema tokens；
- 模型调用次数和工具调用次数；
- 估算费用；
- 按 Model、Turn、Conversation、Task 和 Application 聚合。

Usage 必须随 Session 和 Task 持久化，进程重启后可恢复，切换 Session 时不能串账。

已完成：

- [x] 扩展 `TokenUsage`，记录 cache creation、cache read、reasoning、tool schema、模型调用次数和工具调用次数；
- [x] 新增 `UsageState`，支持 Turn、Conversation、Task、Application 和 Model 维度累计；
- [x] 为 `UsageState` 增加 `to_record()` / `from_record()`，为后续 Session / Task 持久化恢复预留结构；
- [x] `TurnState`、`ConversationState` 和 `ApplicationState` 接入 `usage_state`；
- [x] `MemoryStore` 支持把 `UsageState` 保存到当前 Session 的 `meta.json`，并在恢复或切换 Session 时读取对应账本；
- [x] `AgentLoop` 在每次模型调用后记录 usage，在工具调用时记录工具调用次数；
- [x] `_usage_from_sdk_object()` 兼容 cache 和 reasoning token 明细；
- [x] 保留 `last_turn_usage`、`cumulative_usage` 等旧字段，兼容 CLI 和已有调用方；
- [x] 扩充 `tests/test_runtime_state.py` 和 `tests/test_agent_loop.py`，覆盖多作用域累计、模型维度、序列化恢复和 AgentLoop 接入；
- [x] 新增 `docs/6.5-usage-state.md`，说明整体作用、代码位置、关键类/函数、执行链路、测试位置和验证结果。

### 6.5.1 Prompt 输出约束与结构化回复协议

本任务尽快补强 `PromptManager` 的输出约束，让模型在调用工具和输出最终答案时遵守统一协议，减少过程文本、工具调用标记和最终回答混杂的问题。

实现要求：

- 统一新增 `[Response Protocol]` prompt section；
- 明确 Tool call 与 Final response 两种输出模式；
- 文本 fallback 工具调用必须只输出 `<tool_call>...</tool_call>`，禁止前后夹杂解释；
- 最终回答不得暴露隐藏推理、私有计划、原始 prompt 规则或 tool-call 标记；
- coding / analysis / chat 按任务意图注入不同回答版式；
- 继续保留 native tool calling，不强制把最终回复包进 XML；
- 为后续非法输出校验和自动重试预留统一入口。

已完成：

- [x] 新增 `zzm_agent/prompt/output_protocol.py`，集中生成输出协议；
- [x] 新增 `PROMPT_SECTION_RESPONSE_PROTOCOL` 常量；
- [x] `PromptManager.build()` 在工具说明之后、模板输出格式之前注入 `Response Protocol`；
- [x] 扩充 `tests/test_prompt_manager.py`，覆盖协议注入和意图专属回答版式；
- [x] 新增 `docs/6.5.1-prompt-output-protocol.md`，说明整体作用、代码位置、执行链路、测试位置和边界。

### 6.6 完整 PermissionState 及权限生命周期

正式引入：

```text
PermissionState
├── pending_requests
├── decisions
├── denials
├── session_grants
├── task_grants
├── orphaned_requests
└── has_handled_orphaned_permission
```

权限状态包括 Pending、Approved Once、Approved for Session、Approved for Task、Denied、Expired、Orphaned 和 Cancelled。

每个权限决定记录工具、参数摘要、风险、作用域、原因、时间和关联 Tool Call。历史拒绝用于避免重复申请，但不得自动变成永久拒绝。

已完成：

- [x] 新增 `PermissionStatus` 和 `PermissionScope`，覆盖 pending、approved once、approved for session、approved for task、denied、expired、orphaned 和 cancelled；
- [x] 新增 `PermissionRequest`、`PermissionDecision` 和 `PermissionState`，记录请求、决定、拒绝、session/task grant、孤立请求和孤立处理标记；
- [x] 权限记录包含工具名、参数摘要、风险、作用域、原因、时间、Turn、Task 和 Tool Call 关联信息；
- [x] 支持 `request_permission()`、`approve_request()`、`deny_request()`、`expire_request()`、`cancel_request()`、`orphan_request()`、`handle_orphaned_permissions()` 和 `find_active_grant()`；
- [x] 支持 `to_record()` / `from_record()` 序列化恢复；
- [x] `TurnState` 和 `ConversationState` 接入正式 `PermissionState`，并保留旧 `permission_requests` / `permission_denials` 兼容字段；
- [x] `AgentLoop` 在工具确认路径中记录权限 request、approval 和 denial，拒绝仍回填 `User denied tool execution.`；
- [x] 扩充 `tests/test_runtime_state.py`，覆盖授权、拒绝、grant 查找、孤立请求和序列化恢复；
- [x] 扩充 AgentLoop 权限集成测试，覆盖拒绝与同意两条路径；
- [x] 新增 `docs/6.6-permission-state.md`，说明功能作用、代码位置、执行链路、测试位置和验证结果。

### 6.7-6.8 文件状态缓存与 Memory 加载去重

这两个任务合并开发。原因是二者都在解决“运行时上下文来源已经读过什么、版本是什么、是否还能复用”的问题：FileStateCache 面向文件内容，MemoryLoadState 面向项目 Memory、嵌套 Memory、Skill Reference 和长期记忆注入。合并后可以统一处理路径规范化、版本标识、重复加载防护、失效检测和上下文来源追踪。

#### 6.7 FileStateCache 文件状态缓存

每个文件状态至少记录：

- 规范化路径；
- 内容或内容引用；
- Hash、大小、mtime、编码和行数；
- 已读取范围；
- 摘要；
- 最后读取时间；
- Agent 最后修改时间；
- 文件版本。

支持重复读取复用、部分范围读取、外部修改检测、缓存失效、Agent 写入后的缓存更新，并与 ChangeSet 和 Artifact 联动。

#### 6.8 MemoryLoadState 与嵌套记忆去重

记录：

- 已加载项目 Memory 路径；
- 已加载嵌套目录 Memory 路径；
- 已加载 Skill Reference 路径；
- 已注入 Semantic / Episodic Memory ID；
- Memory 文件版本。

支持根目录到子目录的规则继承、重复加载防护、文件变化后重新加载和上下文来源追踪。

已完成：

- [x] 新增 `FileReadRange`、`FileState` 和 `FileStateCache`，记录规范化路径、内容 Hash、大小、mtime、编码、行数、已读范围、摘要、读取时间、Agent 修改时间和缓存版本；
- [x] `read_file` 接入文件状态缓存，支持缓存复用、读取范围追加和外部修改失效后重读；
- [x] `write_file`、`file_edit` 和 `file_append` 在 Agent 写入后刷新文件缓存；
- [x] 新增 `MemorySourceRecord` 和 `MemoryLoadState`，记录项目 Memory、嵌套 Memory、Skill Reference、Semantic / Episodic Memory ID、Memory 文件版本和重复来源；
- [x] `MemoryStore.build_memory_messages()` 在每轮构建上下文时生成新的 MemoryLoadState，避免本轮重复注入同一记忆来源；
- [x] `MemoryStore.build_turn_messages()` 在 compression 元数据里返回 `memory_load_state`，供后续 QueryEngine / CheckpointStore / UI 使用；
- [x] `ConversationState.file_reads` 和 `ConversationState.memories` 从占位 dict 升级为正式状态对象；
- [x] 扩充 `tests/test_runtime_state.py`、`tests/test_plugins.py` 和 `tests/test_memory_store.py`，覆盖缓存范围、外部修改、Agent 写入刷新、Memory 来源记录、重复去重和序列化恢复；
- [x] 新增 `docs/6.7-6.8-file-memory-state.md`，说明功能作用、代码位置、执行链路、测试位置和验证结果。

### 6.9 CancellationController 基础层级模型

建立会话、Turn、Task 和 Child Token 的层级取消模型：

```text
Session Token
├── Turn Token
├── Task Token
└── Child Tokens
```

Token 支持取消原因、取消时间、子 Token、回调注册和 `raise_if_cancelled()`。本阶段先完成同步执行链路接入；异步模型请求、并发工具、后台进程和子 Agent 的完整传播在 P6、P7 扩展。

已完成：

- [x] 新增 `CancellationToken`、`CancellationController` 和 `CancellationError`，支持 Session、Turn、Task 和 Child Token 的层级取消树；
- [x] Token 记录取消原因、取消时间、父子关系，支持回调注册、取消传播、`raise_if_cancelled()` 和序列化恢复；
- [x] `ConversationState.cancellation` 从占位 dict 升级为正式 controller，`start_turn()` 会为当前 Turn 绑定取消 token；
- [x] `AgentLoop` 支持外部传入 controller，并在同步模型调用前、工具执行前、工具 retry 前后接入取消检查点；
- [x] pre-cancelled session 不会继续调用模型；工具执行前取消会回滚 pending message，并将 `TurnState` / `LoopState` 标记为 cancelled；
- [x] 扩充 `tests/test_runtime_state.py`，覆盖取消传播、回调、序列化、ConversationState 绑定和 AgentLoop 同步取消链路；
- [x] 新增 `docs/6.9-cancellation-controller.md`，说明功能作用、具体使用场景、代码位置、执行链路、测试位置和验证结果。

### 6.10 Hook 系统、Stop Hook 与阻塞重试保护

正式 Hook 类型包括：

- Session Start / End；
- Turn Start / End；
- Before / After Model；
- Before / After Tool；
- Tool Error；
- Stop。

Hook 决策包括 Continue、Block、Retry、Modify 和 Stop。Stop Hook 可以阻止模型过早结束并要求继续，但必须通过 `stop_hook_active`、`stop_hook_attempts` 和最大次数防止无限阻塞。

已完成：

- [x] 新增 `HookType`、`HookDecision`、`HookContext`、`HookResult` 和 `HookRegistry`，建立同步进程内 Hook 基础协议；
- [x] 支持 Session Start / End、Turn Start / End、Before / After Model、Before / After Tool、Tool Error 和 Stop Hook；
- [x] Hook 决策支持 Continue、Block、Retry、Modify 和 Stop，其中 Block 可阻塞当前 Turn，Modify 可修改模型消息、工具参数、工具结果或最终回答；
- [x] Hook callback 异常会被记录为 `hook_error` 并按 Continue 处理，避免观察者破坏主流程；
- [x] `AgentLoop` 接入同步 Hook 链路，覆盖模型调用、工具执行、工具错误、最终回答和 Turn/Session 结束；
- [x] Stop Hook 支持 runtime-only retry prompt，要求模型继续补一轮回答，提示不会写入持久历史；
- [x] Stop Hook 通过 `stop_hook_active`、`stop_hook_attempts` 和 `max_stop_hook_attempts` 防止无限重试，超过上限时进入 blocked；
- [x] 扩充 `tests/test_runtime_state.py`，覆盖 HookRegistry 决策、异常隔离、Stop Hook 重试、重试上限阻塞、工具参数修改和工具结果修改；
- [x] 新增 `docs/6.10-hook-system-stop-hook.md`，说明功能作用、具体使用场景、代码位置、执行链路、测试位置和验证结果。

### 6.11 EventBus、ArtifactStore 与 CheckpointStore

- `EventBus`：统一发布状态转换、模型调用、工具、权限、Hook、Usage 和取消事件；
- `ArtifactStore`：保存长工具结果、报告、Diff、日志和生成文件；
- `CheckpointStore`：保存 Conversation、Turn、Task 和 WorkingMemory 检查点。

事件和状态持久化必须区分事实记录与 UI 展示，观察者异常不得改变 Agent 行为。

### 6.12-6.15 工具结果、进度事件与展示协议

这四个任务合并开发。原因是 ToolResult 决定工具结果的数据结构，ToolProgressEvent 决定执行中事件流，DisplayMode 决定长结果如何折叠，ToolRenderer / RendererRegistry 决定这些结构最终如何展示。分开开发会反复修改同一条工具结果与 UI 事件链路；合并开发可以一次确定模型内容、展示内容、Artifact、进度、Renderer 和折叠策略之间的边界。

#### 6.12 ToolResult 展示分层

工具执行结果不得继续只用一个字符串同时服务模型和终端。正式结构至少包括：

```text
ToolResult
├── model_content：发送给模型的机器可读内容
├── display_content：供用户界面渲染的结构化内容
├── artifacts：完整日志、文件、Diff 或超长结果引用
└── metadata：退出码、路径、命中数、耗时等工具元数据
```

模型内容、用户展示和完整原始结果可以采用不同预算与格式，但必须通过同一个 Tool Call ID 关联。

#### 6.13 ToolProgressEvent

在现有 Tool Start / End / Error 事件之间加入进度事件：

```text
ToolProgressEvent
├── tool_call_id
├── sequence
├── message
├── percent
├── stdout_chunk
├── stderr_chunk
└── metadata
```

- 支持有百分比和无百分比两类进度；
- 保证同一 Tool Call 内 sequence 单调递增；
- 慢消费者不得无限积压输出；
- UI 观察者异常不得中断工具；
- 完整日志进入 Artifact，终端只保留受预算控制的实时窗口。

#### 6.14 ToolRenderer / RendererRegistry

定义完整工具渲染协议：

```text
ToolRenderer
├── render_use()：工具准备执行时展示名称、参数和权限状态
├── render_progress()：执行期间展示实时进度
├── render_result()：成功后展示专属结果
└── render_error()：失败后展示错误、恢复建议和已产生 Artifact
```

`RendererRegistry` 根据工具名称、工具类别和来源选择 Renderer，并提供：

- 本地工具专属 Renderer；
- MCP、Skill、Planner、后台任务和 Agent Renderer；
- 通用默认 Renderer；
- 纯文本降级 Renderer；
- 插件注册和名称冲突处理。

Renderer 只能消费事件和结构化结果，不得直接执行工具或修改核心状态。

#### 6.15 DisplayMode 与折叠策略

正式定义展示模式：

```text
INLINE
COLLAPSED
STREAMING
SUMMARY_ONLY
HIDDEN
```

每种工具可以声明：

- 默认展示模式；
- 最大预览行数和字符数；
- 是否属于 Search / Read 类工具；
- 是否保留实时输出；
- 是否生成完整 Artifact；
- 用户是否可以切换展开状态。

终端暂不支持交互展开时，应显示摘要、被隐藏的数量和 Artifact 路径，不能静默丢弃内容。

完成情况：

- [x] 新增 `zzm_agent/core/tool_results.py`，定义 `ToolResult`、`ToolProgressEvent`、`ToolProgressEmitter`、`DisplayMode`、`DisplayPolicy`、`ToolRenderer`、`RendererRegistry` 和默认纯文本 Renderer；
- [x] `AgentLoop` 在工具回填前创建结构化 `ToolResult`，并通过 `to_model_message()` 保持原有模型 tool result 协议兼容；
- [x] `TurnState.tool_results` 和 `AgentLoop.last_tool_results` 可观察每次工具调用的模型内容、展示内容、状态和 metadata；
- [x] `ToolProgressEmitter` 保证同一 Tool Call 内进度 `sequence` 单调递增，并通过 `EventBus` 发布 `tool.progress` 事件，同时限制本地缓冲长度；
- [x] `RendererRegistry` 支持按工具名、类别、来源选择 Renderer，并提供纯文本降级 Renderer；
- [x] 新增 `tests/test_tool_results.py` 并扩充 `tests/test_runtime_state.py`，验证结构化结果、进度事件、Renderer 选择和 AgentLoop 兼容性；
- [x] 新增 `docs/6.12-6.15-tool-result-display-protocol.md`，说明使用场景、代码位置、执行链路、测试位置和已知边界。

### 6.16 状态序列化、版本迁移与恢复协议

- 所有持久状态具有 Schema Version；
- 支持向后兼容迁移；
- 使用原子写入和损坏文件隔离；
- 明确哪些运行中状态可以恢复；
- 不可恢复状态转换为 Blocked 或 Failed 并给出原因；
- 恢复时校验工作区、文件版本、权限和 Artifact。

完成情况：

- [x] 新增 `zzm_agent/core/state_serialization.py`，提供 `StateEnvelope`、`StateSnapshotStore`、`migrate_state_record()`、`RecoveryValidator` 和恢复判定结构；
- [x] 为 `LoopState`、`TurnState`、`ConversationState`、`ApplicationState` 增加 `to_record()` / `from_record()`，覆盖 Usage、权限、文件缓存、记忆、取消 token、事件、Artifact、Checkpoint 和 active turn；
- [x] 快照文件通过 `StorageIO` 写入，复用原子替换、`.bak` 备份和损坏 JSON 隔离；
- [x] 运行中 Turn 默认要求 checkpoint；中间阶段、缺失 Artifact、记忆文件版本变化、工作区缺失会分别返回 Blocked 或 Failed；
- [x] 新增 `tests/test_state_serialization.py`，覆盖 schema version、旧记录迁移、checksum 防篡改、损坏文件隔离、Conversation roundtrip 和恢复判定；
- [x] 新增 `docs/6.16-state-serialization-recovery.md`，说明使用场景、示例、执行链路、恢复规则、测试和当前边界。

### 6.17-6.20 QueryEngine、ModelAdapter、StreamEvent 与 CLI 主链路迁移

这四个任务合并开发。原因是 QueryEngine 会成为跨 Turn 会话入口，而模型适配、分层流事件和 CLI 主链路是同一条执行链路上的协议边界。如果先迁移 CLI，再补 ModelAdapter 和 StreamEvent，后续很容易重复改动流式输出、reasoning 展示、工具调用和恢复逻辑。合并开发时仍需要保留兼容入口，保证现有 REPL、Session、Slash Command 和 AgentLoop 测试持续可用。

#### 6.17 ModelAdapter 与模型能力声明

引入统一模型适配层，隔离 OpenAI、OpenRouter、Anthropic、本地模型等 provider 的响应差异：

```text
ModelAdapter
├── ModelCapabilities
├── request/response normalize
├── stream chunk normalize
├── reasoning/content/tool_call mapping
└── provider error mapping
```

主要职责：

- 统一模型请求、流式 chunk、工具调用、usage 和错误结构；
- 声明模型是否支持 reasoning、tool call、json schema、vision、parallel tool calls、prompt cache 等能力；
- 将 provider 原始响应转换为内部标准事件；
- 让 QueryEngine 和 CLI 不直接依赖某个 SDK 的响应形状；
- 为后续模型热切换、降级、回放和测试 fake model 提供稳定接口。

#### 6.18 ModelStreamEvent 分层协议

正式区分流式输出中的不同语义层：

```text
ModelStreamEvent
├── status
├── reasoning_summary
├── content_delta
├── tool_call_delta
├── tool_result
├── usage
├── final_message
└── error
```

主要职责：

- 将“思考摘要/推理说明”和“最终回答内容”分开传递；
- 将工具调用参数增量、工具结果、状态提示和最终消息分开；
- CLI 可以用不同样式渲染，桌面端可以放到不同面板；
- EventBus 可以继续发布内部运行事件，但模型流事件负责用户可见输出；
- 回放测试可以断言事件序列，而不是解析混在一起的字符串。

#### 6.19 QueryEngine 会话编排器

正式引入 QueryEngine：

```text
QueryEngine
├── ApplicationState
├── ConversationState
├── BaseAgent / AgentLoop
├── Model
├── ToolRegistry
├── SkillRegistry
├── HookRegistry
└── EventBus
```

主要职责：

- 接收用户消息并创建 TurnState；
- 管理跨 Turn 的 ConversationState；
- 调用 AgentLoop；
- 管理消息提交、Usage、权限、Skills、Memory 和取消；
- 处理 Stop Hook、孤立请求和恢复；
- 在 Turn 边界调用 StateSnapshotStore，完成 6.16 状态快照的真实落地；
- 为 Planner、后台任务和 Sub-Agent 提供统一入口。

AgentLoop 只负责一个 Turn 内部的 ReAct，不再承担跨 Turn 会话编排。

#### 6.20 CLI 主链路迁移到 QueryEngine

- REPL 通过 `QueryEngine.submit_message()` 运行；
- Session 切换、取消、模型切换和 Slash Commands 通过 QueryEngine 更新状态；
- CLI 不直接拼装多个核心对象的内部状态，也不再解析 provider 原始流；
- CLI 根据 ModelStreamEvent 分层渲染状态、reasoning、正文、工具调用和最终结果；
- 断点恢复、会话恢复和中断回滚通过 QueryEngine 调用 StateSnapshotStore；
- 保留兼容入口，迁移期间现有命令和测试持续可用。

#### 完成记录

- [x] 新增 `zzm_agent/core/model_adapter.py`，提供 `ModelCapabilities`、`ModelRequest`、`ModelResponse`、`ModelStreamChunk` 和 OpenAI-compatible adapter；
- [x] 新增 `zzm_agent/core/model_stream.py`，定义 status、reasoning_summary、content_delta、tool_call_delta、tool_result、usage、final_message 和 error 分层事件；
- [x] `AgentLoop.run()` 增加 `on_stream_event`，保留 `on_text_chunk` 兼容，并保证伪 XML 工具调用不会作为正文流式渲染；
- [x] 新增 `zzm_agent/core/query_engine.py`，提供 `QueryEngine.submit_message()`、`QueryResult` 和 Turn 边界 snapshot 保存；
- [x] CLI 主执行路径优先通过 QueryEngine 提交消息，保留旧 `loop.run()` 降级路径；
- [x] 新增 `tests/test_query_engine_streaming.py`，覆盖 adapter 归一化、分层流事件、原生 tool call delta、伪 XML 隐藏和 QueryEngine snapshot；
- [x] 新增 `docs/6.17-6.20-query-engine-model-stream.md`，说明使用场景、模块边界、执行链路、兼容策略和测试命令。

### 验收标准

- 五种状态作用域及其所有权清晰可测试；
- 非法 Loop 状态转换会被拒绝；
- `needs_follow_up`、Reflection、Stop Hook 和结束原因均可观察；
- Pending 消息可以原子提交或中断回滚；
- Usage、权限拒绝、文件缓存和 Memory 加载状态可持久化恢复；
- 取消能够从 Session 传播到同步 Turn 和工具检查点；
- ToolResult 的模型内容、展示内容和 Artifact 不再互相混用；
- ToolProgressEvent 可以按顺序驱动实时 UI；
- RendererRegistry 能选择专属、默认和纯文本降级 Renderer；
- DisplayMode 能控制长结果折叠而不丢失完整内容；
- ModelAdapter 能屏蔽 provider SDK 响应结构差异；
- ModelStreamEvent 能区分 status、reasoning、content、tool_call、final 和 error；
- QueryEngine 成为 CLI 的统一会话入口；
- StateSnapshotStore 被 QueryEngine 在真实 Turn 边界调用，不只停留在协议和单测层；
- 现有 ReAct、Session、Memory 和回放测试保持兼容。

---

## 7. P2：配置、指令文件与 CLI 产品化

P2 的目标是让终端版先成为可日常使用的产品，而不是只有一个能跑 AgentLoop 的入口。Claude Code 和 Codex 的共同经验是：用户每天依赖的是可恢复会话、清晰配置、项目指令、权限命令、review、git、脚本化和 CI 接口。没有这些，后面的桌面端也只是在不稳定内核上套 UI。

### 7.1 ConfigManager、Profile 与配置作用域

建立统一配置系统，覆盖全局、项目、本地和托管/管理员作用域。配置项至少包括模型、reasoning effort、权限 profile、沙箱 profile、MCP server、Skills、Plugin、CLI UI、日志、自动化和默认验证命令。

验收要求：

- [x] 配置加载有明确优先级和来源审计；
- [x] CLI 能显示当前生效配置；
- [x] 项目配置可以提交到仓库，本地配置默认不提交；
- [x] 托管配置可以声明不可被用户覆盖的安全要求。

完成记录：

- [x] 新增 `zzm_agent/core/config.py`，提供 `ConfigManager`、`ConfigScope`、`ConfigSource`、`ConfigOrigin` 和 `ConfigLoadResult`；
- [x] `load_config()` 迁移到 ConfigManager，同时保留旧函数入口和 `--config` / `ZZM_AGENT_CONFIG` 兼容行为；
- [x] 支持 global、project、local、managed 作用域合并，记录 `_config_sources`、`_config_origin` 和 `_config_locked`；
- [x] 支持 `${ENV:-default}` 展开和 `ZZM_AGENT_PROFILE` profile 覆盖；
- [x] 新增 `/config` 命令显示当前关键配置、来源和锁定信息；
- [x] 新增 `tests/test_config_manager.py`，扩充 `tests/test_cli.py`；
- [x] 新增 `docs/7.1-config-manager-profile.md`，说明问题、例子、链路、关键数据结构和验证结果。

### 7.2 Agent 指令文件与自动记忆

支持 `AGENTS.md` / `ZZM.md` 这类 repo 指令文件，按目录层级加载并允许就近覆盖。自动记忆用于保存跨会话稳定事实，例如构建命令、测试入口、常见故障和用户偏好；它不能替代指令文件，也不能静默覆盖显式指令。

验收要求：

- [x] 启动时能列出加载的指令文件和优先级；
- [x] 指令文件有大小预算和截断提示；
- [x] 自动记忆有创建、查看、删除、禁用和来源记录；
- [x] nested repo / monorepo 的就近规则可测试。

完成记录：

- [x] 新增 `zzm_agent/memory/instructions.py`，提供 `InstructionManager` 和 `InstructionFile`，支持 `AGENTS.md` / `ZZM.md` 从 workspace root 到当前目录的层级加载；
- [x] `MemoryStore.build_turn_messages()` 接入项目指令文件和自动记忆，并通过 `MemoryLoadState` 记录来源、版本、截断和重复信息；
- [x] 语义记忆新增 `source` 和 `enabled` 元数据，支持禁用后保留但不注入上下文；
- [x] 新增 `/instructions`、`/memory-disable`、`/memory-enable`，增强 `/semantic` 和 `/config` 的来源展示；
- [x] 默认配置新增 `memory.instruction_files`、`memory.instruction_max_chars` 和 `memory.auto_memory_enabled`；
- [x] 扩充 `tests/test_memory_store.py` 和 `tests/test_cli.py`，覆盖指令加载、预算截断、来源审计和自动记忆启停；
- [x] 新增 `docs/7.2-agent-instructions-auto-memory.md`，说明问题、例子、执行链路、关键数据结构、边界和验证结果。

### 7.3 Slash Command 与交互式 CLI

合并开发核心 CLI 命令：`/status`、`/resume`、`/sessions`、`/config`、`/permissions`、`/artifacts`、`/plan`、`/review`、`/undo`、`/tools`、`/skills`、`/mcp`。这些命令都应通过 QueryEngine 或统一服务层读取状态，而不是直接扒内部字段。

验收要求：

- [x] 用户能恢复最近会话、查看当前 Turn、切换权限和打开 Artifact；
- [x] `/review` 能对未提交改动、暂存区或指定 commit 做只读审查；
- [x] `/plan` 能在编辑前展示计划并允许用户确认；
- [x] 命令输出在无 Rich 环境下仍可读。

完成记录：

- [x] 新增 `/status`、`/resume`、`/permissions`、`/artifacts`、`/plan`、`/review`、`/undo`、`/skills` 和 `/mcp` 命令；
- [x] `/status` 展示 session、model、workspace、stream、tools、context window、active turn 和 usage；
- [x] `/resume` 支持无参数恢复最近历史会话，或指定 session id；
- [x] `/permissions` 通过 QueryEngine / runtime 权限账本展示 pending、decisions、denials 和 grants；
- [x] `/artifacts` 支持列表、预览和 `--full` 完整输出；
- [x] `/plan` 只读展示 active task 或本地 `task.md` / `implementation_plan.md`；
- [x] `/review` 读取 git diff，并通过 QueryEngine 发起只读审查请求；
- [x] `/undo`、`/skills`、`/mcp` 对尚未接入的后续能力给出明确占位提示；
- [x] 更新 help 和 slash completion；
- [x] 新增 `docs/7.3-slash-command-interactive-cli.md`，说明问题、例子、命令边界、执行链路和验证结果。

### 7.3A 终端输出分层与可降级渲染

先做轻量但关键的终端输出整理，不在这一阶段重构成全屏 TUI。目标是解决当前思考过程、工具执行、状态提示和最终总结混在一堆文本里的问题，并为后续 `exec --json`、P6 异步 TUI 和桌面端共用同一套展示语义打基础。

这一阶段应基于 `ModelStreamEvent`、`ToolProgressEvent`、`ToolResult` 和 `RendererRegistry` 建立 CLI 渲染边界。CLI 渲染层只能消费结构化事件，不应直接解析 AgentLoop 的自然语言输出，也不应修改核心运行状态。

验收要求：

- [x] 思考摘要、状态提示、工具执行记录、工具结果、正文增量和最终总结按事件类型分区渲染；
- [x] 最终总结前有清晰分隔线，用户能一眼区分执行过程和最终结论；
- [x] 工具执行使用统一样式展示 `Running`、`Ran`、`Failed`、`Cancelled`，命令正文弱化显示，关键状态更醒目；
- [x] 长工具输出走折叠、摘要或 Artifact 引用，不把 transcript 和模型上下文冲爆；
- [x] 支持 `TerminalRenderer` / `PlainTextRenderer` 分层：有 TTY 和 Rich 时使用增强样式，无 TTY、无 Rich、CI、管道或重定向环境自动降级为普通文本；
- [x] 降级文本仍保留事件顺序、工具状态、最终总结和错误信息，便于日志、CI 和脚本消费；
- [x] 本阶段不实现固定底部输入框、不在同步 AgentLoop 外包线程做完整 TUI，也不承诺 Esc 能立即终止所有同步工具；这些放到 11.6。

完成记录：

- [x] 新增 `PlainTextRenderer`、`TerminalRenderer` 和 `build_terminal_renderer()`；
- [x] `run_repl()` 的 `on_stream_event` 改为消费完整 `ModelStreamEvent`，不再只处理 `CONTENT_DELTA`；
- [x] 支持 `reasoning_summary`、`tool_call_delta`、`tool_result`、`content_delta`、`final_message` 和 `error` 分层渲染；
- [x] 最终消息前输出 Rich Rule 或纯文本 `---` 分隔线；
- [x] 非 Rich console 或非 TTY 输出自动选择 `PlainTextRenderer`；
- [x] 保留旧 `on_text_chunk` fallback；
- [x] 新增 `docs/7.3A-terminal-renderer.md`，说明问题、例子、事件链路、数据结构和验证结果。

### 7.3B 响应语言策略、系统语言检测与全局语言设置

让 Agent 的回答语言像 Codex / Claude Code 一样自然：普通问题跟随用户输入语言，`/review`、`/plan`、`exec` 这类没有自然语言正文的命令继承会话语言或系统语言，而不是每个命令各自硬编码“请用中文回答”。

参考方向：

- Claude Code 官方设置体系包含用户、项目、本地、托管等作用域，适合承载个人偏好；它的 Output style 属于 system prompt 的一部分，说明“回答风格/语言偏好”应该在模型指令层生效，而不是渲染层临时改文本。
- Codex 使用 `config.toml`、项目配置、`AGENTS.md`、rules、memories 等层级承载长期偏好和项目指令；响应语言也应采用“配置默认值 + 会话记忆 + 本轮输入覆盖”的链路。

语言决策优先级：

1. 用户本轮显式要求最高，例如“用英文回答”“下面用中文”；
2. 当前自然语言输入检测，例如中文字符占比高则本轮输出中文；
3. 当前会话最近一次明确语言，例如用户一直中文交流，则 `/review` 继承中文；
4. 全局配置 `ui.response_language`，支持 `auto`、`zh-CN`、`en-US` 等；
5. 系统 locale / 环境变量 fallback，例如 Windows UI 语言、`LANG`、`LC_ALL`；
6. 项目默认值 fallback，仍无法判断时用 `zh-CN` 或配置默认语言。

功能范围：

- 新增 `ResponseLanguagePolicy` 或同等模块，负责检测、解析显式语言指令、读取配置和输出本轮语言决策；
- 新增会话级 `response_language` 字段，记录最近一次自然语言偏好，供 slash command、非交互 `exec` 和自动任务继承；
- 在 `AgentLoop` / `QueryEngine` 构造本轮 runtime-only system message，明确“最终回答使用某语言，代码、命令、路径、错误信息保持原文”；
- 在 `config.yaml` / ConfigManager 中增加全局语言设置，例如 `ui.response_language: auto`、`ui.default_locale_language: zh-CN`；
- 在 `/config` 中展示当前语言策略、系统检测结果、会话继承语言和手动设置值；
- 支持用户在问题中临时覆盖语言，但只影响当前会话/当前轮，不自动写入全局配置，除非用户明确要求“以后都用某语言”；
- `/review` 不再单独指定中文或英文，只提交审查任务，由统一语言策略决定最终总结语言；
- `exec --json` 后续需要在事件 metadata 中输出 `response_language`，方便桌面端和日志回放解释语言来源。

验收标准：

- [x] 中文系统或中文会话中直接输入 `/review`，最终 review 总结为中文；
- [x] 英文问题触发的普通问答最终回答为英文；
- [x] 用户输入“用英文回答：解释项目结构”时，本轮使用英文，但不会永久覆盖全局设置；
- [x] 用户配置 `ui.response_language: zh-CN` 后，slash command、review、exec 默认中文；
- [x] 用户配置 `ui.response_language: auto` 时，优先根据本轮输入和会话语言自动判断；
- [x] `/config` 能看到语言来源：explicit / input_detected / session / config / system_locale / default；
- [x] 新增 `docs/7.3B-response-language-policy.md`，说明问题、例子、配置项、执行链路、数据结构和验证结果。

### 7.4 非交互 `exec`、stdin 管道与 JSON 输出

提供可脚本化入口，例如：

```text
zzm exec "fix CI failure"
git diff --name-only | zzm exec --stdin "review these files"
zzm exec --json "summarize repo"
```

验收要求：

- [x] 支持 stdin、非交互最终结果、JSON event stream 和退出码；
- [x] 支持输出最终消息到文件；
- [x] 非交互模式无法弹出新权限时必须失败并说明原因；
- [x] 支持 shell completion 和 prompt history。

已落地行为：

- 新增 `zzm-agent exec` 子命令，支持一次性提交任务，不进入 REPL；
- 新增 `--stdin`，将标准输入内容合并到任务 prompt，适合管道接收 `git diff`、日志、文件列表和测试输出；
- 新增 `--json`，按 JSONL 输出 `ModelStreamEvent`，最后输出 `type=result` 记录，包含最终回答和语言来源；
- 新增 `--output/-o`，将最终 assistant message 写入文件，适合生成 review 报告、commit message 草稿或 CI 摘要；
- `exec` 复用 `QueryEngine`、`AgentLoop`、配置、记忆、语言策略、状态快照和事件模型，与 REPL 使用同一运行时；
- `exec` 使用非交互权限确认回调，需要人工批准的工具不会弹出菜单或卡住 CI，而是被拒绝并回写给模型；
- 新增 `zzm-agent completion [bash|zsh|powershell]`，输出轻量 shell completion 脚本；
- REPL 继续使用 `prompt_toolkit` 文件历史；`exec` 不写交互输入历史，避免 CI 或脚本把大量日志污染人工 prompt history；
- 新增 `docs/7.4-non-interactive-exec-json.md`，说明使用场景、失败模式、执行链路、事件结构、对应代码和测试位置。

### 7.5 Git / Review / Commit / PR 工作流

把 Git 作为一等工作流，而不是 Shell 的偶然副作用。支持 diff review、stage/unstage、commit message、branch、PR description、CI failure analysis 和 release notes。

验收要求：

- 所有写 Git 操作都可确认和回滚；
- Review 默认只读，不修改工作区；
- commit/PR 描述引用测试结果和关键变更；
- CI 失败分析能关联日志 Artifact 和建议修复。

完成记录：

- [x] 新增 `GitWorkflow` 与 `GitSnapshot`，统一读取 branch、status、staged diff 和 unstaged diff；
- [x] 新增 `/git status|stage|unstage|undo`、`/stage` 和 `/unstage`，index 写操作复用运行时确认入口并支持最近一次操作反向回滚；
- [x] 新增 `/commit-message`、`/branch` 和 `/pr` 只读草稿入口，要求检查 diff 与测试证据，不直接创建 commit、分支或远程 PR；
- [x] 新增 `/ci <log-file>`，将完整日志保存为 `ci-log` Artifact，并生成包含根因、相关代码、最小修复和验证命令的分析；
- [x] Git 子进程使用参数数组和 `--` 路径分隔，拒绝以 `-` 开头的路径参数，避免 Shell 与 option 注入；
- [x] 新增 `tests/test_git_workflow.py` 并扩充 `tests/test_cli.py`，全量回归结果为 `310 passed, 2 skipped`；
- [x] 新增 `docs/7.5-git-review-commit-pr-workflow.md`，说明问题、端到端例子、执行链路、数据结构、安全边界和验证结果。

### 验收标准

- 终端用户能从启动、配置、执行、审查、提交到恢复形成闭环；
- CLI 与 QueryEngine 使用同一状态和权限系统；
- 用户能清楚区分执行过程、工具输出和最终结论；
- 脚本化入口可用于 CI；
- 配置、指令和记忆的来源可解释。

完成记录：

- [x] 新增 `tests/test_p2_acceptance.py`，从用户闭环验证配置来源、项目指令、会话恢复、JSONL、渲染分层、Git 回滚和 CI Artifact；
- [x] 确认 REPL、slash command 与非交互 `exec` 共享 QueryEngine、ConversationState 和权限入口；
- [x] 确认 Git index 写操作需要确认且可逆，review、commit/branch/PR 草稿和 CI 分析保持只读边界；
- [x] 新增 `docs/p2-acceptance.md`，说明用户痛点、端到端例子、执行链路、关键数据结构、边界和验证证据；
- [x] 阶段定向测试 `4 passed`，全量回归 `314 passed, 2 skipped`。

---

## 8. P3：本地执行安全、沙箱与上下文治理

P3 处理本地执行的硬边界。PermissionState 只是账本，不能替代 OS 级沙箱、路径边界、网络访问控制和工具参数校验。

### 8.1 工具生命周期、参数校验与权限网关

统一链路：

```text
tool call -> 参数解析 -> schema 校验 -> 风险分级 -> 权限确认 -> 执行 -> ToolResult -> EventBus / Artifact / Checkpoint
```

验收要求：

- 无效参数不会进入工具函数；
- 高风险工具必须经过权限策略；
- MCP、内置工具和未来插件工具都走同一网关；
- 工具调用前后都有可回放事件。

完成记录：

- [x] ToolRegistry 注册 schema 增加 `additionalProperties=false`，统一拒绝模型虚构的额外参数；
- [x] 新增 `validate_arguments()`，在函数执行前校验必填项、未知字段与基础 JSON 类型，且不做隐式类型转换；
- [x] `ToolRegistry.call()` 强制经过校验，使内置工具、插件工具和未来适配器不能绕过入口；
- [x] AgentLoop 在 BEFORE_TOOL hook 后、权限确认前执行校验，无效高风险调用不会请求授权或发出 `tool.start`；
- [x] 新增 `ToolArgumentValidationError` 结构化错误语义，并保持既有 argument recovery hint 兼容；
- [x] 扩充 `tests/test_tool_registry.py` 和 `tests/test_agent_loop.py`，最终定向回归 `56 passed`，全量回归 `317 passed, 2 skipped`；
- [x] 新增 `docs/8.1-tool-lifecycle-permission-gateway.md`，说明真实问题、执行链路、事件语义、代码位置与阶段边界。

### 8.2 文件系统与网络沙箱 Profile

支持 read/write/deny、workspace roots、敏感文件拒读、网络域名 allow/deny、localhost/private network 规则、Unix socket 或 Windows 特殊路径策略。Windows、WSL、macOS/Linux 的能力差异必须文档化。

验收要求：

- `.env`、密钥目录和显式 deny 路径不可读；
- 写入默认限制在 workspace roots；
- 网络默认关闭或按 profile 限制域名；
- 沙箱失败能请求受控升级而不是静默绕过。

完成记录：

- [x] 新增核心 `SandboxProfile`，统一 workspace roots、显式 deny、敏感路径和网络边界；
- [x] 文件与搜索插件统一复用 `authorize_path()`，默认拒读 `.env`、`.ssh`、云凭据目录和私钥名称；
- [x] 写操作默认限制在 workspace roots，并继续防止真实路径和符号链接父目录逃逸；
- [x] 新增 `authorize_url()`，网络默认关闭，支持域名 allow/deny、localhost、loopback、private/link-local 独立策略；
- [x] `SandboxViolation` 进入结构化 permission 错误，明确要求受控 Profile 变更或显式升级，禁止静默绕过；
- [x] 记录 Windows、WSL、Unix 路径分隔、符号链接、socket 与应用层/OS 层沙箱差异；
- [x] 新增 `tests/test_sandbox_profile.py` 并扩充插件安全测试，定向回归 `43 passed, 2 skipped`，全量回归 `323 passed, 2 skipped`；
- [x] 新增 `docs/8.2-filesystem-network-sandbox-profile.md`，说明场景、链路、配置、边界与验证结果。

### 8.3 工具超时、取消与资源清理

为模型请求、Shell、文件操作、MCP 工具和后台进程设置超时和取消机制。不能强制停止的同步工具必须在下一安全检查点停止，并报告无法立即停止的原因。

完成记录：

- [x] 工具注册元数据支持 `timeout_seconds`，并在统一调用入口使用单调时钟执行 deadline 检查；
- [x] `CancellationToken` 贯穿 AgentLoop 与 ToolRegistry，在执行前后和重试等待边界设置安全检查点；
- [x] 兼容不接受取消参数的自定义/测试注册器，通过能力检测在调用前后保留取消检查；
- [x] 支持按工具注册 cleanup callback，并保证成功、失败、超时或取消时均按 LIFO 顺序清理；
- [x] deadline 超时和 cleanup 失败具有明确、非静默的结构化错误语义；
- [x] 新增并扩充工具注册、AgentLoop、运行状态、错误恢复、Replay 与 Eval 测试，专项回归 `98 passed`，全量回归 `327 passed, 2 skipped`；
- [x] 新增 `docs/8.3-tool-timeout-cancellation-cleanup.md`，说明执行链路、配置方式、安全检查点、清理协议与阶段边界。

### 8.4 ChangeSet、Patch 与 `/undo`

所有受管写操作生成 ChangeSet，记录 before/after hash、Patch、tool call、Turn 和撤销状态。`/undo` 必须检测文件是否已被用户或外部工具改动。

完成记录：

- [x] 新增持久化 `ChangeSetStore`，在成功的 `file_edit`、`write_file`、`file_append` 工具调用后记录文件内容、前后 SHA-256 摘要、统一 diff、工具调用 ID、会话、Turn 和撤销状态；
- [x] ChangeSet 仅在文件实际变化后写入 `.zzm_agent/changesets.json`，重启后仍可读取；
- [x] 新增 `/undo` 和 `/undo <changeset-id>`，默认撤销当前会话最近一项仍生效的受管变更，支持恢复覆盖前内容或删除由 Agent 新建的文件；
- [x] 撤销前使用写入后摘要验证当前文件。检测到用户或外部工具后续修改时标记冲突、保留文件并明确报告，不静默覆盖；
- [x] 新增 `tests/test_change_set.py` 并更新 CLI 断言，覆盖补丁和元数据、持久化、恢复、创建文件删除、冲突保护和 slash command；定向回归 `88 passed`；
- [x] 新增 `docs/8.4-changeset-patch-undo.md`，说明用户可见行为、使用例子、限制、实现位置和验证结果。

### 8.4A 任务持续执行、完成判定与终止治理

解决 Agent 在大型任务中执行大量工具后无提示返回输入框，或把单段工具轮次耗尽误当成整个任务结束的问题。本任务先建立可靠的终止协议和自动续段基础；P5 再基于 TaskState、计划步骤和验收证据完成任务级 CompletionGate。

执行顺序按依赖调整为：`8.4A.1 → 8.4A.2 → 8.5 → 8.4A.3 → 8.4A.4 → 8.4A 阶段验收 → 8.6`。8.5 必须先提供工具结果 Artifact 化和自动压缩，否则自动续段会持续累积上下文。

#### 8.4A.1 统一终止原因与结束可观测性

- 定义 `completed`、`yielded`、`blocked`、`failed`、`cancelled`，不得继续用空字符串或普通最终回复隐含运行状态；
- 每条 AgentLoop、Hook、取消、Provider 和 QueryEngine 结束路径都必须携带结构化原因；
- CLI、JSON 事件、Checkpoint 和状态快照必须能解释任务为什么结束；
- `yielded` 只代表内部执行段结束，不得显示为任务完成或失败。

完成记录：已增加统一结束记录、五种状态协议、Provider 结束标记历史、结束事件、终端显示和按真实状态保存的会话快照。功能说明见 `docs/8.4A.1-termination-observability.md`。

#### 8.4A.2 空模型回复与异常完成恢复

- 模型同时没有文本和工具调用时，不得提交空 assistant message 或把 Turn 标记为 completed；
- ModelAdapter 保留 provider 的 finish reason、截断、流中断和空响应信息；
- 允许有限的结构化恢复提示，要求模型继续工具执行、提交有效最终回复或明确报告阻塞；
- 恢复仍为空时标记 blocked，展示已执行轮次、工具次数、上下文状态和继续入口，不得静默返回提示符。

完成记录：任何无文字且无工具调用的模型响应都不再完成任务；默认恢复两次，耗尽后以 `empty_model_response` 明确阻塞，并显示调用轮次、上下文估算、Provider 结束标记和继续入口。功能说明见 `docs/8.4A.2-empty-response-recovery.md`。

#### 8.4A.3 SegmentResult 与安全让出

- AgentLoop 增加结构化 `SegmentResult`，包含状态、原因、回复、工具轮次、工具调用数、检查点和剩余工作摘要；
- `max_tool_iterations` 改为单段安全边界，达到后生成 `yielded` 和检查点，不再直接终止整个任务；
- 重复调用、连续失败、权限拒绝和取消继续作为安全信号，但必须区分可恢复让出与不可恢复停止；
- 同步 `run()` 保持兼容，新的分段协议供 QueryEngine 和后续异步入口使用。

#### 8.4A.4 QueryEngine 自动续段与基础完成门禁

- QueryEngine 消费 `SegmentResult`；收到 yielded 时调用 8.5 压缩能力，保存事实来源并自动继续同一用户任务；
- 空回复、单段轮次耗尽、上下文压缩和一次工具批次完成都不能单独成为任务完成条件；
- 简单问答和明确的小修改仍走轻量路径，不产生额外 Planner 调用；
- 当前基础门禁负责协议完整性和显式阻塞；基于计划步骤与验收证据的完整完成判定由 10.2A 实现。

验收要求：

- 复现“大量 read/search/shell 后空回复”的场景时，系统会恢复或明确阻塞，不再静默回到输入框；
- 达到单段轮次边界后能压缩、检查点和自动续跑，用户不需要手动输入“继续”；
- 正常简单问答仍在单次模型回复内完成；
- 相同调用死循环、连续失败、用户取消和权限边界保持有效；
- Replay 固定覆盖正常完成、空回复恢复、provider 截断、yielded 续段、明确阻塞和安全停止。

### 8.5 Token Budget、自动压缩与上下文解释

预算分区至少包括 system prompt、指令文件、记忆、pinned context、历史消息、tool schema、tool result、reflection prompt 和 output reserve。大结果进入 Artifact，模型只接收摘要、关键片段和引用。自动 compact 必须保留事实来源。

### 8.6 本地工具 Renderer 合集

合并开发 FileRead、FileEdit、Search、Shell、动态活动描述和纯文本降级 Renderer。Renderer 消费 ToolResult / ToolProgressEvent，不直接解析自然语言输出。

### 验收标准

- 本地执行有确定性权限和沙箱边界；
- 文件修改可撤销；
- 长工具结果不会撑爆上下文；
- 用户能看懂工具做了什么、为什么被拒绝、如何恢复。

---

## 9. P4：MCP、Skills 与 Plugin 分发

P4 把扩展生态做成可安装、可禁用、可审计的系统。MCP 负责外部工具连接，Skills 负责可复用工作流，Plugin 是分发单元。

### 9.1 MCP Client 与连接治理

支持 stdio、HTTP、SSE、WebSocket MCP Server，包含能力发现、动态工具更新、鉴权、重连、限流、错误隔离、输出限制和权限治理。

### 9.2 Skills 模块化与发现状态

Skill 是任务知识包，包含触发描述、步骤、资源、示例、允许工具和可选脚本。SkillDiscoveryState 记录 available、discovered、activated、pinned、rejected、loaded resources、token cost 和 activation reason。

### 9.3 工具 Schema 按需装载与 Tool Search

根据任务、Skill、MCP server、阶段和用户显式选择延迟暴露工具，避免每轮塞入全部 schema。大型 MCP server 支持工具搜索和按需启用。

### 9.4 Plugin Manifest、安装与启停

Plugin 可以打包 Skills、MCP 配置、资源、UI 元数据、权限声明、依赖和版本。支持本地开发、安装、启用、禁用、卸载、版本检查和 marketplace 预留字段。

### 9.5 MCP / Skill / Plugin Renderer

展示来源、连接状态、激活原因、权限请求、工具进度、远程错误、token 成本和禁用原因。

### 验收标准

- 至少接入一个真实 MCP server；
- Skills 可显式/隐式触发并可禁用；
- Plugin 可本地安装和卸载；
- 外部工具不能绕过权限、沙箱和审计。

---

## 10. P5：长任务规划、工作记忆与任务恢复

P5 让 Agent 从“单轮 ReAct”扩展到可暂停、可恢复、可审查的长任务。Planner 在 AgentLoop 外层工作，不强迫简单任务进入重规划流程。

### 10.0 TaskRouter 与自动规划策略

在 QueryEngine 外层判断任务应走 simple、standard、planned 还是 durable 路径。`planning_mode=auto` 为默认：简单问答和小修改直接执行；新增模块、跨目录功能、重构、迁移、实现加测试、引用实施计划或预计跨多个执行段的任务先建立计划。`always` 强制先规划，`never` 跳过 Planner 但不绕过权限、安全与终止治理。

显式 `/plan` 进入只调查、澄清和生成计划的模式，在用户确认前不执行写操作。自动规划在需求明确且权限允许时可以展示计划后直接执行；存在关键歧义、高风险选择或不可逆操作时必须等待用户确认。

### 10.1 TaskState 与 WorkingMemory

TaskState 保存目标、步骤、状态、发现、Artifacts、阻塞和更新时间。WorkingMemory 保存任务内临时事实、计划、子步骤结果和当前阻塞；它与长期记忆分离，结束时可以选择性沉淀为 Episodic Memory。

### 10.2 外层 Planner、计划 Diff 与重规划

Planner 负责拆解目标、定义验收条件、调度步骤、接收结果、反思失败、调整计划并生成计划 Diff。正常完成且没有新信息的步骤不应额外消耗模型反思。计划必须是带 pending、running、completed、blocked 状态的运行时数据，不得只保存为一段 Markdown。

### 10.2A TaskRunner、计划感知 CompletionGate 与持续执行

TaskRunner 复用 8.4A 的自动续段协议，跨 Turn 推进 TaskState。模型没有工具调用或生成最终文本，只表示提出完成；CompletionGate 必须确认所有必要计划步骤完成、验收条件满足、测试或其他验证证据存在、没有未处理阻塞，才能把 Task 标记为 completed。若仍有未完成步骤则继续执行；若缺少用户输入、权限、凭据或外部条件则明确 blocked。

### 10.3 用户干预、暂停与恢复

支持确认、修改、跳过、重试、暂停和从检查点恢复。恢复时验证工作区、文件版本、Artifact、权限请求和后台任务。

### 10.4 PlannerRenderer 与 TaskProgressRenderer

展示目标、约束、步骤列表、当前步骤、计划变化、完成比例、Usage、耗时、Artifacts、阻塞原因和下一步选择。

### 验收标准

- 复杂任务能跨 Turn 执行；
- 自动路由能让简单任务保持轻量，让新增模块等复杂任务进入计划执行；
- 用户能修改计划并继续；
- 模型提前输出最终回复时，未完成计划不会被误判为任务完成；
- 中断后能从最近安全状态恢复或明确阻塞原因；
- 简单任务默认不启用 Planner；
- 可与纯 ReAct 基线比较成功率、耗时和 Token。

---

## 11. P6：异步、并发、后台任务与自动化

P6 解决长时间运行和重复运行的问题：异步模型流、并发工具、后台进程、定时任务、监控和失败重试。

### 11.1 Async Agent Loop 与只读工具并发

提供 `async_run()`，保留同步 `run()`。只并发低风险、只读、无依赖工具，写操作、Shell 和未知副作用工具默认串行。

### 11.2 ToolCallScheduler、后台进程与取消传播

ToolCallScheduler 根据依赖、风险、副作用和资源限制调度工具。后台进程支持启动、查询、停止、日志 Artifact 和退出清理。CancellationController 贯穿模型流、工具、后台进程和子任务。

### 11.3 Circuit Breaker 与外部依赖降级

为 Provider、MCP Server、网络工具和后台服务建立 Closed、Open、Half-Open 状态机，支持失败率阈值、冷却、半开探测、手动恢复和降级提示。

### 11.4 Automations、定时任务与事件触发

支持 recurring task、monitor、webhook/channel trigger、失败重试、通知、运行历史和手动暂停。自动化必须使用非交互权限策略，不能等待无人批准的权限请求。

### 11.5 ConcurrentToolsRenderer 与 BackgroundProcessRenderer

展示并发工具状态、完成顺序、耗时、失败、取消；展示后台进程 ID、命令、日志、退出码、运行时长和 Artifact。

### 11.6 基于 prompt_toolkit 的异步交互式终端 TUI

在 `async_run()`、ToolCallScheduler、后台进程和取消传播稳定后，再把交互式终端升级为真正的非阻塞 TUI。目标是接近 Codex / Claude Code 这类产品体验：Agent 执行时输入框和状态栏固定在底部，上方 transcript 可滚动，用户可以继续输入，按 Esc 或 Ctrl+C 能取消当前 Turn 或正在运行的后台任务。

这一阶段不应通过把同步 `AgentLoop.run()` 简单丢进线程池来完成最终架构。可以为了兼容旧同步入口保留适配层，但产品级 TUI 应以 QueryEngine 的异步 API、CancellationController、EventBus 和渲染协议为主链路。

验收要求：

- 终端布局分为 transcript、固定输入框和固定状态栏，Agent 输出不会把输入框顶走；
- Agent 执行中输入框仍可编辑，用户可排队输入下一条消息或执行 slash command；
- Esc / Ctrl+C 调用 `cancel_turn()` 或 `cancel_task()`，取消信号能传播到模型流、并发工具、后台进程和子任务；
- 权限确认、计划确认、工具进度、后台任务状态和最终总结不会互相覆盖或打乱布局；
- TUI 复用 7.3A 的 TerminalRenderer 语义，不重新发明一套输出协议；
- 无 TTY、无 Rich、CI 和管道环境继续走 PlainTextRenderer / `exec --json`，不启动全屏 TUI；
- Windows Terminal、PowerShell、CMD、WSL、macOS/Linux 终端兼容性有测试或手动验证记录。

### 验收标准

- 同步入口继续可用；
- 只读并发有明确收益；
- 后台任务可查询和停止；
- 自动化任务可审计、可暂停、可失败恢复；
- 交互式终端能在异步任务运行时保持输入可用，并能取消当前 Turn；
- 外部依赖持续失败时会熔断。

---

## 12. P7：多 Agent 协作与 Worktree 隔离

P7 处理并行探索和复杂任务分工。多 Agent 不是默认路径，只在能带来上下文隔离、并行收益或角色专业化时启用。

### 12.1 Sub-Agent / TaskTool 与子 Agent 状态

主 Agent 可委派边界清晰的任务。子 Agent 有独立上下文、工具权限、Usage、取消 token 和结构化结果。权限授权不得意外跨越 Agent 边界。

### 12.2 Git Worktree 隔离与合并审查

写操作子 Agent 在独立 worktree 和分支中工作，记录基线 commit、改动、测试和 Artifact。合并前必须生成 Diff、测试结果和冲突检查。

### 12.3 Swarm / Agent Team 编排

支持角色、依赖图、并行/串行调度、共享事实、冲突结论处理、重复劳动去重、部分失败收敛和资源上限。

### 12.4 SubAgentRenderer 与 SwarmRenderer

展示 Agent 拓扑、任务分配、当前动作、成本、阻塞、证据、完成比例和整体收敛状态。

### 验收标准

- 子 Agent 无法无限递归；
- 主 Agent 能获得可核验结果；
- worktree 不污染用户当前工作区；
- 多 Agent 在选定任务上相较串行有可衡量收益。

---

## 13. P8：浏览器、Computer Use、Web 测试与 CI 集成

P8 扩展到真实软件交付环境：网页调试、视觉证据、CI 失败分析、PR 审查和必要时的桌面应用操作。所有外部内容默认不可信。

### 13.1 Browser Controller 与网页调试

支持打开页面、点击、输入、截图、DOM 检查、控制台日志、网络错误和本地 Web App 冒烟测试。截图、HTML 片段和控制台日志进入 Artifact。

### 13.2 Computer Use 高风险能力边界

Computer Use 只在浏览器/MCP/CLI 无法完成时启用，必须有显式授权、敏感区域保护、截图证据、失败回退和审计记录。

### 13.3 Web / CI / GitHub 集成

支持 CI 日志分析、PR 自动审查、issue/PR 触发任务、状态回写和凭据边界。外部网页、issue、日志和评论均按不可信内容处理。

### 13.4 浏览器与 CI Renderer

展示页面状态、截图、测试结果、PR 评论、CI 日志、失败原因和复现步骤。

### 验收标准

- Agent 能完成本地 Web App 冒烟测试；
- CI 失败分析能定位日志和建议修复；
- 外部内容不会直接污染系统指令；
- 高风险 Computer Use 有授权和证据链。

---

## 14. P9：Client API、App Server 与桌面客户端

桌面端值得做，但必须建立在稳定 QueryEngine 和 Client API 之上。桌面端是可视化操作层，不重新实现 AgentLoop、权限、取消、记忆或工具执行。

### 14.1 App Server / 本地桥接协议

提供 `submit_message`、`cancel_turn`、`cancel_task`、`approve_permission`、`deny_permission`、`list_sessions`、`switch_session`、`list_background_tasks`、`open_artifact` 和事件订阅。协议支持进程内 Python API、localhost 服务或 WebSocket。

### 14.2 Desktop Client 边界与主工作台

第一屏是工作台：会话列表、当前 Turn、任务计划、运行状态、Usage、Artifacts、可恢复任务、错误和取消状态。桌面端不直接调用工具函数，不改内部状态。

### 14.3 桌面权限、取消与工具进度界面

权限确认、取消按钮和工具进度复用 PermissionState、CancellationController、EventBus、ToolProgressEvent 和 RendererRegistry。

### 14.4 Artifact / Diff / 日志 / Replay 查看器

长内容放在专门视图：完整工具输出、生成文件、Patch、Diff、测试日志、后台进程日志、多 Agent 报告和回放记录。

### 14.5 桌面端端到端验收

覆盖 CLI 与桌面行为一致性、重启恢复、权限请求、取消、后台任务、桥接层异常和 UI 崩溃不影响后端执行。

---

## 15. P10：企业治理、安全审计与可运维性

P10 让系统从个人工具走向团队和企业可用：策略、审计、安全、成本和运维。

### 15.1 Secret Redaction、外部内容隔离与 Prompt Injection 防护

识别并遮蔽密钥、token、`.env`、私钥和敏感路径。网页、MCP、CI、日志、issue、PR 评论和工具输出都标记来源，不能覆盖系统/开发者/项目指令。

### 15.2 Governance、Managed Config 与审计日志

支持组织级配置、不可覆盖策略、权限审计、数据保留、导出、用户/项目范围和安全事件记录。

### 15.3 Telemetry、性能指标与成本报表

记录任务成功率、失败类型、恢复次数、延迟、Token、费用、工具耗时、MCP 错误、自动化运行和阶段对比。

### 15.4 安全扫描与发布门禁

集成 SAST、依赖扫描、自定义安全 review 和发布前检查，输出可追踪发现、修复建议、验证命令和证据 Artifact。

### 验收标准

- 敏感数据不会进入普通日志和模型上下文；
- 管理员策略不能被项目配置绕过；
- 成本、失败和安全事件可审计；
- 发布门禁能复现安全发现和验证结果。

---

## 16. P11：最终产品验收与发布

P11 是最终路线的收口，不新增大架构，只验证终端版和桌面版是否真的可长期日常使用。

### 16.1 端到端基准与真实任务套件

覆盖代码理解、修改、测试、PR、Web 调试、长任务、多 Agent、恢复、自动化和桌面操作。每个场景记录成功率、耗时、Token、失败原因和人工干预次数。

### 16.2 兼容性与迁移验证

覆盖 Windows、WSL、macOS/Linux、PowerShell、bash/zsh、旧会话、旧配置、旧记忆、旧 Artifact 和旧 Checkpoint。

### 16.3 用户文档、示例项目与故障排查

补齐终端版、桌面版、MCP、Skills、Plugins、权限、沙箱、自动化、浏览器、CI、企业配置和安全说明。

### 16.4 发布检查清单

确认安装、升级、回滚、隐私、安全、性能、可观测性、支持流程、已知限制和版本说明。

### 验收标准

- 终端版和桌面版使用同一核心运行时；
- 关键场景具备回归基准；
- 旧数据可迁移或明确报告不可迁移；
- 用户能根据文档完成安装、配置、日常任务和故障排查。

---
## 17. 统一工程验收模板

每项路线任务在进入开发前都应补齐：

### 用户痛点

说明该能力解决的真实失败模式或使用障碍。

### 当前能力

说明仓库中已经存在的基础，避免重复建设。

### 本阶段范围

列出本次实现必须交付的行为。

### 非目标

明确当前阶段不解决的相邻问题，防止范围失控。

### 依赖与风险

说明依赖模块、兼容性、安全风险和迁移成本。

### 验收标准

使用用户可观察行为描述完成条件，而不是只检查是否新增了某个类或文件。

### 测试与指标

至少覆盖：

- 单元测试和集成测试；
- Replay / Benchmark；
- 成功率、Token 消耗和延迟；
- 错误恢复；
- 安全和兼容性回归。

### 功能说明文档

任务完成后必须新增或更新对应 `docs/*.md` 功能说明文档，并且不能只写“新增了哪些类/函数”。文档至少包含：

- **一句话说明**：这项能力到底是干什么的；
- **解决的问题**：没有这项能力时，用户、CLI、运行时、桌面端或后续开发会遇到什么真实问题；
- **具体例子**：至少给出 1 个端到端例子，包含用户输入、系统内部发生了什么、最终输出或状态变化；
- **使用场景**：说明用户什么时候会直接感知它，运行时什么时候会间接使用它；
- **执行链路**：用文本、列表或 Mermaid 说明从入口到关键模块再到结果的调用顺序；
- **关键事件或数据结构**：如果涉及 EventBus、ModelStreamEvent、ToolProgressEvent、Artifact、Checkpoint、StateSnapshot 等，必须说明谁发布、谁接收、每类事件或数据代表什么；
- **代码定位**：列出主要文件、关键类、关键函数，以及它们之间的职责边界；
- **边界与非目标**：说明当前阶段没有解决什么，避免后续误以为已经完整支持；
- **测试与验证**：列出新增或更新的测试文件、测试覆盖点、实际运行过的验证命令和结果。

文档结构建议：

```text
# 阶段编号 + 功能名

## 一句话说明
## 解决的问题
## 具体例子
## 使用场景
## 执行链路
## 关键事件或数据结构
## 代码定位
## 边界与非目标
## 测试与验证
```

---

## 18. 阶段完成定义

一个阶段只有同时满足以下条件才算完成：

- 目标能力已经实现；
- 关键路径具备自动化测试；
- 新行为进入回放基准；
- 配置和用户文档已经更新；
- 现有功能没有未解释的明显退化；
- 安全边界和已知限制已经记录；
- 可观测性能够解释执行过程和失败原因。

路线图中的后置能力均为正式计划的一部分。阶段顺序用于控制架构风险和实施依赖，不表示后续能力可以被永久搁置或删除。
