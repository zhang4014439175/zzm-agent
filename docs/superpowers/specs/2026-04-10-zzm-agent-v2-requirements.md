# zzm-agent v0.2.0 任务卡片规格

**日期：** 2026-04-10  
**基线：** `v0.1.0-skeleton`  
**目标：** 用一份简洁文档明确 `v2` 范围，并能一眼判断开发进度。

---

## 1. 当前状态

**结论：v2 已开始开发，`P1.2/X1/P2.1/P2.2` 已验收完成，当前整体处于 `X2 测试与质量` 进行中。**

当前仓库已经完成 `P1.1`、`P1.2`、`P2.1`、`P2.2` 与 `X1`，并继续推进 `X2`。不得再把仓库整体描述为“v2 尚未开始”；后续每次更新时都必须按本文件的状态规则同步修正总览、任务卡片和里程碑。

### 已有基础能力

- [x] 基础 `AgentLoop`
- [x] 基础 `tool_calls` 循环
- [x] 单文件 `memory.json` 历史存储
- [x] 基础 `ToolRegistry`
- [x] 插件目录加载
- [x] 基础命令：`/tools` `/memory` `/evolve` `/help` `/exit`
- [x] 基础测试：`agent_loop`、`memory_store`、`tool_registry`

### 尚未完成的 v2 能力

- [x] 多会话管理
- [x] 分层记忆
- [x] 上下文压缩
- [ ] 记忆检索
- [ ] 风险分级与工具确认
- [ ] 插件热重载命令
- [x] `/sessions` `/session` `/new`
- [-] `/remember` `/forget` `/search`
- [ ] `/evolve status` `/evolve run` `/evolve diff` `/evolve apply` `/evolve rollback`
- [x] 数据迁移与回滚闭环
- [-] v2 对应测试

### 判定规则

- 本文档状态只表示 `v2` 规格进度
- `v0.1` 的旧骨架不等于 `v2` 已完成
- 不满足当前卡片要求时，统一保持 `[ ]`

### 执行纪律

- 每次开始工作前，先完整阅读本文件，再回答三个问题：当前做到哪一张卡、该卡当前状态是什么、下一步只应该推进哪一项。
- 每次开始编码前，先把将要推进的模块从 `[ ]` 改成 `[-]`；禁止写完代码后再回头补状态。
- 每次完成实现后，必须同步更新 3 个层级，缺一不可：`1. 当前状态` 总览、`3. v2 范围` 的总状态、`4. 任务卡片/8. 里程碑` 的对应条目。
- 每次更新状态时，必须同时更新“上面”和“下面”的状态，禁止只改任务卡片不改总览，或只改里程碑不改任务卡片。
- 只有在“实现 + 自动化测试/可重复验证 + 验收条件满足”三者都成立时，条目才能从 `[-]` 改成 `[x]`。
- 如果代码已写但测试未完成、环境验证受阻、或验收条件未全部满足，必须保持 `[-]`，并在对应卡片的“当前状态/说明”里写清楚缺口。
- 如果实现结果与文档不一致，优先修正文档中的状态；禁止模型为了省事跳过文档同步。
- 每次读取本文件后，默认动作不是直接编码，而是先判断“当前阶段 -> 当前卡片 -> 状态是否需要先更新”。
- 对入口参数、会话切换、迁移触发点、状态同步点这类“读代码不容易一眼看懂意图”的位置，必须补简洁注释，说明“何时生效、为什么存在、影响什么状态”。

### 对标 Agent 的规划约束

- 参照 `Claude Code` 这类 agent，文档必须先定义“当前处于哪个会话/阶段、下一步推进哪张卡”，而不是只列功能清单。
- 参照 `Claude Code` 与 `hermes-agent` 这类长期运行 agent，`session` 必须先于分层记忆、检索、压缩落地；否则后续记忆能力缺少稳定边界。
- 参照成熟 agent 的工程纪律，命令面、存储面、迁移面、测试面必须一起规划，不能只写功能不写状态同步和验收闭环。
- 本文档默认把“未验证完成的实现”视为 `[-]` 而不是 `[x]`；禁止把“代码已写”当作“能力已完成”。

---

## 2. 状态规则

- `[ ]` 未开始
- `[-]` 进行中
- `[x]` 已完成
- `[!]` 阻塞
- `[~]` 延后

更新规则：

- 开始开发前，把对应模块改成 `[-]`
- 每完成一个子任务就勾选
- 只有实现、测试、验收都完成后，模块才能改成 `[x]`
- 超出 `v0.2` 范围的项改成 `[~]`
- 阻塞时改成 `[!]`，并写原因
- 每次状态变更后，必须反向检查 `1. 当前状态`、`3. v2 范围`、`4. 任务卡片`、`8. 里程碑` 是否全部一致

---

## 3. v2 范围

### Must

- [x] P1.1 流式输出
- [x] P1.2 会话管理
- [x] P2.1 分层记忆
- [x] P2.2 上下文压缩
- [ ] P2.3 记忆检索
- [ ] P3.1 工具确认
- [ ] P3.2 插件热重载
- [ ] P4.1 对话评估记录
- [ ] P4.2 Prompt 候选闭环
- [x] X1 数据迁移与回滚
- [-] X2 测试与质量

### Should

- [ ] 输出折叠显示
- [ ] `/rename`
- [ ] `/search`
- [x] `/memory` 增强展示

### Not in v2

- [~] 全量 async agent loop
- [~] 并发 `tool_calls`
- [~] `@tool_chain`
- [~] 自动触发 evolve
- [~] “连续优化后分数持续提升”类效果承诺

---

## 4. 任务卡片

### P1.1 流式输出 `[x]`

当前状态：已完成  
说明：已支持 `AgentLoop.run(stream=True)`、流式文本渲染、分片 tool call 重组，以及流式中断时返回已收到文本且不写坏历史。

任务：
- [x] `AgentLoop.run(stream: bool = true)`
- [x] 文本 chunk 到达即渲染
- [x] tool call 分片重组
- [x] tool call 触发后暂停文本流并进入工具执行
- [x] 保留非流式回退路径
- [x] 处理中断、超时、取消时的历史安全写入

验收：
- [x] mock stream 收到首个文本 chunk 后开始渲染
- [x] 分片 tool call 可重组为完整调用
- [x] 用户中断后下一轮仍可继续
- [x] 中断场景下历史不损坏

---

### P1.2 会话管理 `[x]`

当前状态：已完成  
说明：已落地 session 目录结构、最近会话恢复、`--session`、`/sessions`、`/session <id>`、`/new` 和旧版 `memory.json` 首次迁移；相关 CLI / store / agent loop 测试已通过，具备验收条件。

任务：
- [x] 引入 `session_id`
- [x] 每个会话独立目录
- [x] 默认恢复最近会话
- [x] 支持 `--session <id>`
- [x] 支持 `/sessions`
- [x] 支持 `/session <id>`
- [x] 支持 `/new`
- [x] 首次迁移旧版 `memory.json`

目录结构：

```text
~/.zzm_agent/sessions/
├── index.json
├── last_session.txt
└── <session-id>/
    ├── meta.json
    └── history.json
```

验收：
- [x] 会话历史完全隔离
- [x] 切换后只基于目标会话继续
- [x] 启动时恢复最近会话
- [x] 首次启动自动迁移旧数据
- [x] 迁移逻辑幂等

备注：
- `/rename <name>` 放入 Should
- 异常退出不要求补做会话结束钩子
- 已通过定向自动化验证：`pytest tests/test_memory_store.py tests/test_cli.py tests/test_agent_loop.py -q --basetemp C:\Users\zhangzm\.codex\memories\zzm-agent-pytest`

---

### P2.1 分层记忆 `[x]`

当前状态：已完成  
说明：已补齐 `episodic` / `semantic` 两层持久化记忆、`/remember` / `/forget` 命令，以及长期记忆注入数量控制；新会话会自动注入上一会话摘要与长期事实。

任务：
- [x] `history.json` 保存完整会话历史
- [x] 会话摘要写入 `episodic.json`
- [x] `/remember <fact>`
- [x] `/forget <keyword>`
- [x] 支持长期记忆注入数量配置

边界：
- `Working Memory`: 当前会话原始历史
- `Runtime Compression Summary`: 仅运行时使用，不持久化为长期记忆
- `Episodic Memory`: 会话级摘要
- `Semantic Memory`: 跨会话稳定事实
- 同一事实不得被重复注入

验收：
- [x] 新会话可引用上一会话关键结论
- [x] `/remember` 信息可在后续会话检索到
- [x] 长期记忆注入条数受配置控制

备注：
- 当前通过定向自动化验证：`pytest tests/test_memory_store.py tests/test_cli.py tests/test_agent_loop.py -q --basetemp C:\Users\zhangzm\.codex\memories\zzm-agent-pytest`

---

### P2.2 上下文压缩 `[x]`

当前状态：已完成  
说明：已接入运行时上下文压缩：超过 `memory.max_context_tokens` 时保留最近 N 条原始消息，把更早历史折叠为单独 system summary message；`/memory` 也会展示当前压缩状态与 summary 预览。

任务：
- [x] 超过 `memory.max_context_tokens` 时触发压缩
- [x] 保留最近 N 条原始消息
- [x] 更早消息汇总为单独 system message
- [x] 支持 `/memory`

约束：
- v0.2 可用字符数估算 token
- `tiktoken` 可选，不作为强依赖

验收：
- [x] 超阈值时触发压缩
- [x] 压缩后消息体积低于配置上限
- [x] 保留用户指令、工具结果、最终结论

备注：
- 当前通过定向自动化验证：`pytest tests/test_memory_store.py tests/test_cli.py tests/test_agent_loop.py -q`

---

### P2.3 记忆检索 `[ ]`

当前状态：未开始  
说明：当前没有 `episodic/semantic` 检索，也没有 `/search`。

任务：
- [ ] 用关键词匹配检索 `episodic` 与 `semantic`
- [ ] 支持 `memory.retrieval_top_k`
- [ ] 支持 `/search <keyword>`
- [ ] 预留 `MemoryRetriever` 接口

注入顺序：
- system prompt
- 相关记忆
- 当前会话 history
- 用户输入

验收：
- [ ] 固定样本下 top-K 包含预期条目
- [ ] 1000 条样本 benchmark 满足目标阈值

---

### P3.1 工具确认 `[ ]`

当前状态：未开始  
说明：当前工具 schema 没有 `risk_level`，也没有确认流程。

任务：
- [ ] `@tool` 支持 `risk_level`
- [ ] `high` 风险执行前强制确认
- [ ] `medium` 风险在 `--safe` 下确认
- [ ] `low` 风险默认自动执行
- [ ] 支持 `agent.auto_approve: true`
- [ ] 拒绝后回写固定 tool message

交互：
- streaming 中触发高风险工具时先暂停渲染
- `--quiet` 下也必须显示确认
- 输入 `n` 或 `Ctrl+C` 视为拒绝
- 拒绝后返回 `User denied tool execution.`

验收：
- [ ] 默认模式下高风险工具会确认
- [ ] 用户拒绝后工具不执行且结果写回链路
- [ ] `auto_approve: true` 行为等价旧自动执行路径

---

### P3.2 插件热重载 `[ ]`

当前状态：未开始  
说明：当前只有启动时加载插件目录，没有 `/reload`。

任务：
- [ ] 提供 `/reload`
- [ ] 热重载后展示新增、删除、更新摘要
- [ ] 仅保证工具定义刷新

验收：
- [ ] 新增插件后 `/reload` 可发现并注册
- [ ] 修改工具描述后 `/tools` 可显示新描述

---

### P4.1 对话评估记录 `[ ]`

当前状态：未开始  
说明：`EvolutionOptimizer.optimize()` 仍为 stub。

任务：
- [ ] 从近期会话或情景记忆中采样
- [ ] 生成结构化评估结果
- [ ] 存储到 `~/.zzm_agent/evolution/evaluations.json`
- [ ] 支持 `/evolve status`

评估字段：
- 相关性评分
- 工具使用评分
- 简洁性评分
- 简短理由
- 总体结论

验收：
- [ ] 固定输入下能生成结构完整的评估记录
- [ ] `/evolve status` 能展示最近一次结果

备注：
- 不要求相同输入的分数波动小于固定阈值

---

### P4.2 Prompt 候选闭环 `[ ]`

当前状态：未开始  
说明：当前只有简化版 `/evolve`，没有 run/diff/apply/rollback 闭环。

任务：
- [ ] `/evolve run`
- [ ] `/evolve diff`
- [ ] `/evolve apply`
- [ ] `/evolve rollback`
- [ ] 保留最近 N 版 prompt 历史

约束：
- 自动生成不自动生效
- 应用动作必须由用户显式触发

验收：
- [ ] `/evolve run` 可生成候选或明确返回“无候选”
- [ ] diff / apply / rollback 流程可走通
- [ ] prompt 历史可追溯

备注：
- 不要求连续多次运行后平均分持续提升

---

### X1 数据迁移与回滚 `[x]`

当前状态：已完成  
说明：已支持旧版 `memory.json` 自动迁移到 session 结构，并加入避免重复迁移的幂等保护；迁移失败时会恢复 `index.json` / `last_session.txt`，并在启动前清理不完整 session / `.tmp` 残留。相关自动化测试已通过。

任务：
- [x] 旧版 `memory.json` 自动迁移
- [x] 迁移逻辑幂等
- [x] 迁移失败可回滚
- [x] 补充迁移测试

验收：
- [x] 重复启动不会重复迁移或破坏数据
- [x] 迁移异常时可恢复到安全状态

---

### X2 测试与质量 `[-]`

当前状态：进行中  
说明：已补充 session / migration / CLI 基础测试，并新增 streaming mock、迁移失败回滚、启动前清理残留，以及上下文压缩与 `/memory` 展示相关测试；但 retrieval / tool confirmation / evolve 闭环测试仍未完成，因此不能判定该项完成。

任务：
- [-] 新增核心模块补充单元测试
- [x] streaming mock 测试
- [x] session 测试
- [x] migration 测试
- [ ] retrieval 测试
- [ ] tool confirmation 测试
- [ ] evolve 最小闭环测试

验收：
- [ ] 核心链路均有自动化测试覆盖

---

## 5. 非功能要求

- NFR-1: `v0.1.0` 配置格式仍可加载，新字段提供默认值
- NFR-2: `memory.json` 可自动迁移到新会话结构
- NFR-3: 迁移逻辑应幂等
- NFR-4: 记录启动耗时
- NFR-5: 记录 1000 条样本下的检索延迟
- NFR-6: agent 层不应对 mock stream 引入明显额外缓冲
- NFR-7: 尽量控制新增依赖数量
- NFR-8: `tiktoken`、`jieba` 缺失时允许降级

---

## 6. 建议配置

```yaml
agent:
  auto_approve: false

memory:
  max_context_tokens: 8000
  retrieval_top_k: 3
  compression_keep_recent: 10

evolution:
  sample_size: 20
  history_versions: 5
```

预留但不承诺启用：

- `evolution.trigger`
- `evolution.auto_interval`
- `evolution.threshold`

---

## 7. 建议命令

### Must

- `/sessions`
- `/session <id>`
- `/new`
- `/memory`
- `/reload`
- `/remember <fact>`
- `/forget <keyword>`
- `/evolve status`
- `/evolve run`
- `/evolve diff`
- `/evolve apply`
- `/evolve rollback`

### Should

- `/rename <name>`
- `/search <keyword>`

---

## 8. 里程碑

- [x] M1 Streaming + 输出改进
- [x] M2 Session + 旧数据迁移
- [-] M3 分层记忆 + 压缩 + 检索
- [ ] M4 工具确认 + 插件热重载
- [ ] M5 Evolve 最小闭环
- [ ] M6 集成测试 + 迁移验证 + 文档更新 + 发布
