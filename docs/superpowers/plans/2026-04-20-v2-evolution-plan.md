# zzm-agent 深度改进计划

**日期:** 2026-04-20（最后修改：2026-04-22）  
**范围:** 逐模块审视现有代码的具体问题，提出详细改进方案  
**原则:** 不是"加新功能"而是"让现有的每个模块从 demo 级升级到工程级"

### 变更记录

| 日期 | 修改内容 |
|------|----------|
| 2026-04-20 | 初版，E1-E10 路线图 |
| 2026-04-22 | 代码审计补充：E3 拆分为 E3a/E3b；新增前置修复任务 E0.5；E4 补充对话成本追踪、结构化输出；E5 补充 tokenizer fallback 链；E8 补充 async 兼容层策略和 Git 工具；E9 补充 Planner 架构约束；E10 补充 auto-evolve 安全红线；新增目标目录结构 |
| 2026-04-22 | 完成 E4 CLI 可观测性：工具事件、Rich Live 状态、缓冲 Markdown、diff 预览、工具事件日志、token/费用展示和审批选项 |
| 2026-04-22 | E4 CLI 体验增强：授权菜单接入 Questionary 上下键选择，REPL 输入接入 prompt_toolkit 历史记录，Rich 面板统一圆角、padding、柔和配色和 JSON 高亮 |

---

## 0. 文档阅读指南

这份文档分为三层：

- **模块改进方案**：第一章到第七章，按现有模块拆解问题和改进方案。
- **测试与评估体系**：第八章定义如何验证 Agent 是否真的变好。
- **实施路线图**：第九章把前面的改进项整理成可执行的 E1-E10 阶段计划。

建议阅读顺序：

1. 先看第九章，按路线图确定当前应该开发的阶段。
2. 再回到第一章到第八章，查看该阶段关联的模块细节和测试要求。
3. 开发时按每个阶段的任务清单、验收标准和回归测试推进。

执行纪律：

- 必须按第九章 E1-E10 的阶段顺序推进开发；跳过阶段或调整顺序时，需要先在本文档记录原因。
- 开发一个阶段前，先确认该阶段任务清单是否覆盖本次工作；如果实际要做的事项不在清单中，必须补充到对应阶段后再开发。
- 每完成一个实现项、测试项或验收项，都必须在本文档中把对应 checklist 标记为 `[x]`；未完全验证的项保持 `[ ]`，阶段性完成但仍有剩余工作的项在阶段说明中写清楚。
- 每次阶段状态变化，都要同步更新第 9.1 总体开发顺序、对应阶段任务清单、验收标准和必要的变更记录，避免代码进度和路线图脱节。
- 只有实现、测试或人工验证都完成后，才允许把任务项标记为 `[x]`。

模块与阶段的对应关系：

| 模块方向 | 主要章节 | 对应阶段 |
|----------|----------|----------|
| Prompt 管理 | 第一章 | E6 |
| 记忆与检索 | 第二章 | E5、E7、E9 |
| Agent Loop | 第三章 | E2、E4、E9 |
| 工具注册、插件与索引 | 第四章 | E1、E2、E8 |
| CLI / UI | 第五章 | E4、E8 |
| 配置与模型 | 第六章 | E10 |
| 错误处理与可观测性 | 第七章 | E2、E4、E10 |
| 测试与评估 | 第八章 | E3a、E3b、E10 |

---

## 一、Prompt 系统

**当前状态:** 无独立模块，需新建。

### 现状问题

当前 system_prompt 是一句硬编码的字符串：

```yaml
system_prompt: "你是 zzm-agent，一个简洁高效的个人助理。"
```

这导致 Agent 在所有场景下使用同一个身份和指令，无法根据任务类型调整行为。
Claude Code 等先进 Agent 会根据上下文动态组装 prompt，包含：项目规则、文件上下文、
工具使用指南、输出格式约束等。

### 改进方案：构建 `PromptManager` 模块

```
zzm_agent/
└── prompt/
    ├── __init__.py
    ├── manager.py          # PromptManager: 动态组装最终 prompt
    ├── templates.py        # 预定义 prompt 模板库
    └── context_builder.py  # 上下文感知的 prompt 片段生成
```

#### 1.1 Prompt 模板系统

不同任务类型使用不同的 prompt 模板，通过意图检测自动选择：

```python
TEMPLATES = {
    "coding": {
        "role": "你是一个精通编程的 AI 助手，擅长代码编写、调试和重构。",
        "rules": [
            "修改文件前必须先用 read_file 查看当前内容",
            "使用 file_edit 精确修改，不要用 write_file 覆盖整个文件",
            "修改后用 grep_search 验证改动是否正确",
        ],
        "output_format": "先说明思路，再执行操作，最后总结改动",
    },
    "analysis": {
        "role": "你是一个细致的代码分析专家。",
        "rules": ["先用 list_directory 和 find_files 确认项目结构再分析"],
        "output_format": "输出结构化分析报告",
    },
    "chat": {
        "role": "你是一个简洁高效的个人助理。",
        "rules": [],
        "output_format": "简洁回答",
    },
}
```

#### 1.2 动态 Prompt 组装

每次调用 LLM 前，根据以下信息组装最终 prompt：

```
最终 Prompt = 基础身份 + 项目规则 + 工具使用指南 + 记忆上下文 + 输出格式约束
```

具体来说：

- **基础身份**：根据任务类型选择模板
- **项目规则**：从 `.zzm_agent/rules.md` 自动加载，用户可自定义项目级规约
- **工具使用指南**：根据已注册工具动态生成使用说明（"你有以下工具可用..."）
- **上下文窗口状态**：告知模型剩余可用 token 数，引导其控制输出长度
- **输出格式约束**：如 "代码修改请使用 file_edit"、"回答问题时使用 Markdown"

#### 1.3 意图检测

在 Agent Loop 的 `run()` 入口做轻量级意图分类：

```python
def detect_intent(user_input: str, history: list[dict]) -> str:
    """基于关键词和历史上下文判断任务类型"""
    # 简单规则：包含文件路径/代码关键词 → coding
    # 包含 "分析"/"review" → analysis
    # 其他 → chat
    # 后期可替换为 LLM 分类
```

#### 1.4 项目级规则文件

支持用户在项目根目录下放置 `.zzm_agent/rules.md`，Agent 启动时自动读取并注入 system prompt：

```markdown
# 项目规则
- 本项目使用 Python 3.11+
- 代码风格遵循 PEP 8
- 测试框架使用 pytest
- 所有函数必须写 docstring
```

类似 Claude Code 的 `CLAUDE.md` 机制。

#### 1.5 环境适配上下文

PromptManager 还应注入当前运行环境，避免模型输出不适用于本机的命令或路径格式。

环境上下文由 `environment_info` 或运行时检测生成，至少包含：

- 操作系统：Windows / Linux / macOS
- 默认 shell：PowerShell / cmd / bash / zsh
- 工作区根目录
- 路径分隔符和换行符
- 已安装的常用开发工具
- 当前 shell 下推荐使用的命令风格

示例注入片段：

```text
[Environment]
OS: Windows
Shell: PowerShell
Workspace: E:\PythonProject\study\zzm-agent

执行 shell 命令时优先使用 PowerShell cmdlet，例如 Get-ChildItem、Select-String。
不要假设 bash 工具可用；需要跨平台时优先使用 Python 或项目已有脚本。
```

这能降低 `ls` / `grep` / `rm -rf` 等 Unix 命令在 Windows 环境下误用的概率。

#### 1.6 Prompt 片段命名空间

Prompt 中的 section 标签不应散落硬编码，例如 `[Environment]`、`[Working Memory]`、`[Pinned Context]`。这些标签会被压缩、评估、回放测试和日志系统共同依赖，一旦到处写死，后续很难统一修改。

建议集中定义：

```python
PROMPT_SECTION_ENVIRONMENT = "Environment"
PROMPT_SECTION_WORKING_MEMORY = "Working Memory"
PROMPT_SECTION_PINNED_CONTEXT = "Pinned Context"
PROMPT_SECTION_TOOL_GUIDE = "Tools"
```

同时集中定义内部路径、事件名和配置 key：

- `.zzm_agent`
- `.zzm_agent/index/project_structure.json`
- `tool.start` / `tool.end` / `tool.error`
- `context.environment`
- `agent.max_parallel_tools`

这类常量可以放在 `zzm_agent/prompt/constants.py`、`zzm_agent/schema/` 或统一的 `zzm_agent/constants.py` 中。原则是：跨模块共享的命名必须集中定义，避免后续出现多个近似但不一致的标签。

---

## 二、记忆系统

**当前模块:** `memory/`

### 现状问题

#### 问题 1：Episodic 摘要是字符串拼接，不是真正的摘要

```python
def _build_summary(self, history: list[dict]) -> str:
    # 只是截取最后 4 条消息的前 160 字符拼在一起
    return " | ".join(excerpts[-4:])
```

这不是"摘要"，是"截断拼接"。一个 100 轮的会话最终只保留最后 4 条消息的片段，
丢失了所有关键决策和结论。

#### 问题 2：History 压缩是本地字符串拼接，不是 LLM 摘要

```python
def _build_compression_summary(self, messages, token_budget):
    # 逐条 message 截取前 160 字符拼成 bullet list
    line = self._summary_line(message)  # -> "User: xxx..." / "Tool result: xxx..."
```

这意味着压缩后的"摘要"只是一堆截断的消息前缀，丢失了语义连贯性。

#### 问题 3：语义记忆只能手动管理

必须用 `/remember` 手动添加，Agent 无法从对话中自动提取值得记忆的信息。

#### 问题 4：无工作记忆 / 草稿本

Agent 在复杂任务中无法保存中间状态（如"我已经检查了 A 文件，发现问题 X，
接下来需要检查 B 文件"）。

### 改进方案

#### 2.1 LLM 驱动的 Episodic 摘要

用 LLM 生成会话级摘要，而不是字符串截断：

```python
def _build_summary_with_llm(self, history: list[dict]) -> str:
    """调用 LLM 生成一段结构化会话摘要"""
    prompt = (
        "请为以下对话生成一段简洁的摘要（100字以内），"
        "重点提取：用户的目标、关键决策、最终结论、未解决的问题。\n\n"
        f"{self._format_history(history)}"
    )
    # 调用 LLM 生成...
```

摘要结构如下：
```json
{
  "session_id": "session-abc",
  "goal": "用户想修复登录页面的 CSS 问题",
  "decisions": ["决定使用 flexbox 替代 float 布局"],
  "outcome": "成功修复，但移动端适配还需要调整",
  "open_items": ["移动端响应式布局待处理"],
  "updated_at": "2026-04-20T08:00:00Z"
}
```

#### 2.2 LLM 驱动的 History 压缩

当历史消息超出上下文窗口时，不是做字符串截断，而是调用 LLM 把旧消息压缩成摘要：

```python
def _compress_with_llm(self, older_messages: list[dict]) -> str:
    """用 LLM 将多条历史消息压缩为一段连贯摘要"""
    prompt = (
        "以下是一段对话历史的早期部分，请将其压缩为一段简洁的上下文摘要，"
        "保留关键信息（文件路径、决策、错误信息等），供后续对话参考。\n\n"
        f"{self._format_messages(older_messages)}"
    )
```

压缩策略分级：
1. **轻量压缩**：移除 tool_call 的参数细节，保留工具名和结果
2. **中量压缩**：合并连续的 user-assistant 对为一行摘要
3. **重度压缩**：调用 LLM 生成全局摘要

#### 2.3 自动记忆提取

在每轮对话结束后，让 LLM 判断是否有值得长期记忆的信息：

```python
def auto_extract_memories(self, turn_messages: list[dict]) -> list[str]:
    """从本轮对话中自动提取应该记住的事实"""
    prompt = (
        "分析以下对话，提取值得长期记忆的关键事实（如用户偏好、项目约定、"
        "重要决策等）。如果没有，返回空列表。\n"
        "返回 JSON 格式: {\"facts\": [\"事实1\", \"事实2\"]}\n\n"
        f"{self._format_messages(turn_messages)}"
    )
```

提取到的事实自动写入 semantic memory，无需用户手动 `/remember`。

#### 2.4 工作记忆 / Scratchpad

新增 `WorkingMemory` 模块——Agent 可在任务执行过程中保存中间状态：

```python
class WorkingMemory:
    """临时工作记忆，存活于单个任务生命周期"""

    def __init__(self):
        self.notes: list[str] = []      # Agent 的思考笔记
        self.findings: dict = {}        # 已发现的关键信息
        self.plan: list[str] = []       # 当前任务计划
        self.completed: list[str] = []  # 已完成的步骤
```

工作记忆在每轮 LLM 调用时注入到 prompt 中：

```
[Working Memory]
计划：1. 读取 config.py  2. 修改数据库连接字符串  3. 运行测试
进度：步骤 1 已完成，发现连接字符串在第 42 行
待办：执行步骤 2
```

#### 2.5 记忆重要性评分

每条记忆增加 importance 字段，影响检索优先级：

```json
{
  "fact": "用户偏好简洁的代码风格",
  "importance": 0.8,
  "access_count": 5,
  "last_accessed_at": "2026-04-20T08:00:00Z"
}
```

重要性随被引用次数递增，随时间衰减，模拟人类记忆的遗忘曲线。

#### 2.6 关键上下文 Pinning

History 压缩不能只做“摘要化”。有些信息一旦丢失，后续推理会直接偏离任务，应进入受保护上下文。

建议把上下文分为四类：

- **Pinned Context**：必须保留，不参与压缩
- **Active Context**：当前任务强相关，优先保留
- **Compressible Context**：可摘要压缩
- **Discardable Context**：可丢弃或仅保留统计信息

典型需要 pin 的信息：

- 用户最初提出的核心目标
- 用户明确给出的约束和禁止事项
- 当前正在修改或排查的文件路径
- 已经确认的关键决策
- 最近一次错误堆栈的核心行
- 当前计划中尚未完成的步骤

压缩流程应先抽取 pinned 信息，再对剩余历史执行轻量 / 中量 / 重度压缩，避免“摘要正确但任务目标丢失”。

---

## 三、Agent Loop

**当前模块:** `core/agent_loop.py`

### 现状问题

#### 问题 1：无循环保护

```python
while True:  # 没有最大迭代次数限制
    assistant_content, tool_calls_raw, interrupted = self._stream_once(...)
```

如果模型持续请求工具调用（幻觉循环），Agent 会无限执行下去。

#### 问题 2：无工具调用反思

模型调用工具后，结果直接追加到 messages，没有让模型评估工具是否真正解决了问题。
先进 Agent 会在工具结果后加入 "反思提示"，引导模型判断是否需要调整策略。

#### 问题 3：无并行工具调用

模型可能在一次回复中请求多个工具调用（例：同时读取 3 个文件），但当前是串行执行。

#### 问题 4：无回调/事件系统

工具执行过程对外部完全不可见——CLI 层无法显示 "正在执行 run_shell..." 之类的进度。

### 改进方案

#### 3.1 迭代上限与死循环检测

```python
MAX_TOOL_ITERATIONS = 20

while iteration < MAX_TOOL_ITERATIONS:
    iteration += 1
    # ...
    if self._detect_loop(tool_calls_raw, recent_calls):
        messages.append({
            "role": "system",
            "content": "检测到重复工具调用模式，请换一种方法或直接回答用户。"
        })
```

#### 3.2 工具执行事件回调

```python
class AgentLoop:
    def __init__(self, ..., on_tool_start=None, on_tool_end=None):
        self.on_tool_start = on_tool_start  # fn(tool_name, args) -> None
        self.on_tool_end = on_tool_end      # fn(tool_name, result, duration) -> None
```

CLI 层可以利用回调显示工具执行状态：

```
🔧 Executing: grep_search("TODO", path="src/")...
✅ grep_search completed in 0.3s (found 12 matches)
```

#### 3.3 工具结果验证

在工具结果返回给模型前，做基本验证：

```python
def _validate_tool_result(self, name: str, result: str) -> str:
    """验证工具结果是否表示成功，如果失败则添加提示"""
    if result.startswith("Error"):
        return result + "\n[系统提示：工具执行失败，请检查参数后重试或换一种方法。]"
    return result
```

#### 3.4 思维链引导

在工具调用前后注入引导提示，鼓励模型进行推理：

```python
THINK_BEFORE_ACT = (
    "在调用工具前，请先简要说明你的计划和理由。"
    "调用工具后，请评估结果是否符合预期。"
)
```

#### 3.5 全量 Async Agent Loop

当前 Agent Loop 如果以同步方式串联 LLM stream、工具调用、CLI 渲染和中断处理，后续会很难支持后台任务、并发工具、取消执行和实时状态更新。

建议将 Agent Loop 主路径升级为 async：

```python
class AgentLoop:
    async def run(self, user_input: str) -> AgentResult:
        ...

    async def _stream_once(self, messages: list[dict]) -> StreamResult:
        ...

    async def _execute_tool_call(self, call: ToolCall) -> ToolResult:
        ...
```

关键要求：

- LLM streaming、工具执行、事件回调统一走 async 接口
- 支持 cancellation token，用户中断时能停止未完成工具
- 同步工具通过线程池或包装器接入，不阻塞主事件循环
- 现有同步工具必须通过 `asyncio.to_thread()`（Python 3.9+）或显式线程池执行，不能直接在 event loop 中运行
- 文件 IO、shell 执行、索引构建等潜在阻塞操作都应走 sync-to-async 包装，保证 CLI Spinner、事件回调和取消信号仍能及时响应
- CLI 渲染只订阅事件，不直接耦合 Agent Loop 内部状态
- 测试中可用 fake async LLM / fake async tools 做确定性回放

不要为了 async 改写所有工具实现；第一阶段应先让 loop 和 registry 支持 async/sync 混合执行。

#### 3.6 并发 Tool Calls

模型一次回复中可能返回多个工具调用。并发执行可以显著提升效率，但必须受调度器约束。

建议增加 `ToolCallScheduler`：

```python
class ToolCallScheduler:
    async def execute_batch(self, calls: list[ToolCall]) -> list[ToolResult]:
        ...
```

并发规则：

- 只并发无依赖、低风险、只读工具，例如 `read_file`、`grep_search`、`file_info`
- 写文件、执行 shell、修改配置等有副作用工具默认串行执行
- 同一路径的多个写操作必须串行
- 工具结果按原 tool_call 顺序回填 messages，避免上下文顺序混乱
- 并发上限可配置，例如 `agent.max_parallel_tools: 4`

并发 tool_calls 是性能优化，不应牺牲可解释性和安全性。

---

## 四、Tool Registry

**当前模块:** `core/tool_registry.py`

### 现状问题

#### 问题 1：无参数描述

当前 JSON Schema 中每个参数只有 `type`，没有 `description`：

```python
properties[name] = {"type": json_type}  # 缺少 "description" 字段
```

模型不知道每个参数代表什么意思，可能传入错误参数。

#### 问题 2：无使用示例

工具没有 few-shot example，模型需要猜测如何正确调用。

### 改进方案

#### 4.1 参数描述从 docstring 提取

增强 `@tool` 装饰器，自动从函数 docstring 的 Args 部分提取参数描述：

```python
def decorator(fn):
    sig = inspect.signature(fn)
    param_docs = _parse_param_docs(fn.__doc__)  # 从 docstring 解析 Args 部分

    for name, param in sig.parameters.items():
        json_type = _TYPE_MAP.get(param.annotation, "string")
        prop = {"type": json_type}
        if name in param_docs:
            prop["description"] = param_docs[name]
        properties[name] = prop
```

这样 `grep_search` 的 schema 就会包含：

```json
{
  "pattern": {
    "type": "string",
    "description": "The text or regex pattern to search for."
  }
}
```

#### 4.2 工具使用示例

在 `@tool` 装饰器中新增 `examples` 参数：

```python
@tool(
    description="在文件中精确查找目标文本并替换",
    risk_level="medium",
    examples=[
        {"path": "main.py", "target": "old_func()", "replacement": "new_func()"},
    ],
)
```

示例可注入 prompt 作为 few-shot 引导。

#### 4.3 工具分组

```python
@tool(description="...", risk_level="low", group="filesystem")
```

`/tools` 命令按分组展示，复杂场景下可按分组启用/禁用工具。

#### 4.4 插件生命周期与隔离

`zzm_agent/plugins/` 不应只是简单 import 目录。随着用户自定义插件增多，需要明确插件边界，避免命名冲突、初始化顺序混乱和单个插件异常拖垮主流程。

建议引入 `BasePlugin` 基类：

```python
class BasePlugin:
    name: str
    version: str

    def initialize(self, context: PluginContext) -> None:
        """插件初始化，读取插件级配置、准备资源"""

    def register_tools(self, registry: ToolRegistry) -> None:
        """向 Tool Registry 注册工具"""

    def shutdown(self) -> None:
        """释放文件句柄、后台进程、网络连接等资源"""
```

插件加载机制应支持：

- 插件 manifest，声明名称、版本、入口模块、工具分组和风险级别
- 插件级配置，例如 `plugins.filesystem.max_file_size`
- 工具命名空间，例如 `filesystem.read_file`，避免不同插件工具重名
- 加载失败隔离：某个插件失败不影响其他插件和核心 Agent 启动
- Agent 退出时调用 `shutdown()`，释放插件资源

第一阶段不需要做进程级沙箱，先把生命周期、配置和命名空间稳定下来。

#### 4.5 轻量级项目索引

大型项目中，Agent 反复调用 `list_directory` 和 `grep_search` 会浪费时间和上下文。应增加轻量级项目索引，作为搜索工具的前置加速层。

建议缓存到：

```text
.zzm_agent/index/project_structure.json
```

索引内容包括：

- 文件路径、大小、mtime、扩展名
- 语言类型
- 可选符号信息：class / function / method 名称
- 忽略规则：`.gitignore`、`.zzm_agentignore` 或配置项

索引更新策略：

- 启动时按需构建
- 文件 mtime 变化时增量更新
- 用户可通过 `/index rebuild` 手动重建

项目索引不是为了替代 `grep_search`，而是减少“先找文件在哪里”的重复工具调用。

#### 4.6 `@tool_chain` 工具链

单个工具适合表达原子能力，但 Agent 经常需要执行固定的多步工具流程，例如“先查找文件，再读取，再精确替换，再验证”。如果每次都让模型自由组合，容易增加工具调用次数和失败概率。

建议引入 `@tool_chain`，把常见流程注册为可审计、可测试的工具链：

```python
@tool_chain(
    name="safe_replace",
    description="查找目标文本，执行精确替换，并验证替换结果",
    steps=[
        "grep_search",
        "read_file",
        "file_edit",
        "grep_search",
    ],
    risk_level="medium",
)
async def safe_replace(ctx, path: str, target: str, replacement: str):
    ...
```

工具链设计原则：

- `@tool_chain` 是确定性工作流，不是隐藏的二次 Agent
- 每一步都必须产生事件，CLI 和日志能看到完整执行过程
- 工具链内部仍遵守风险等级、确认策略和路径沙箱
- 工具链失败时返回结构化错误和已完成步骤
- 工具链必须可以被回放测试覆盖

适合第一批内置的工具链：

- `safe_replace`：搜索、读取、替换、验证
- `inspect_symbol`：查项目索引、定位文件、读取相关片段
- `run_test_and_summarize`：执行测试、提取失败摘要、给出恢复建议

`@tool_chain` 的目标是降低重复工具编排成本，而不是绕过 Agent Loop 的安全控制。

---

## 五、CLI / UI

**当前模块:** `cli_support/rendering.py`

### 现状问题

#### 问题 1：流式输出无 Markdown 渲染

```python
def stream_reply_chunk(console, chunk):
    console.print(chunk, end="")  # 原始文本输出，无格式化
```

非流式回复会用 `rich.Markdown` 渲染，但流式（默认模式）完全是纯文本。
这意味着用户看到的是原始的 `**bold**` 和 `\`code\`` 标记，而不是格式化的文字。

#### 问题 2：工具执行无可视反馈

Agent 调用工具时 CLI 完全静默，用户不知道 Agent 在做什么、要等多久。

#### 问题 3：无上下文使用量指示器

用户不知道当前会话已使用多少 token，距离上下文窗口上限还有多少空间。

#### 问题 4：错误显示过于简陋

所有错误都是 `[red]Error: {exc}[/red]`，没有区分严重程度和恢复建议。

### 改进方案

#### 5.1 流式 Markdown 渲染

实现一个缓冲型的流式 Markdown 渲染器：

```python
class StreamMarkdownRenderer:
    """缓冲流式文本，在自然断点处（段落/代码块结束）触发 Markdown 渲染"""

    def __init__(self, console):
        self.console = console
        self.buffer = ""
        self.in_code_block = False

    def feed(self, chunk: str):
        self.buffer += chunk
        # 检测到完整段落或代码块结束时，渲染并刷新缓冲区
        if self._has_renderable_boundary():
            self._flush_rendered()

    def finalize(self):
        """流结束时渲染剩余内容"""
        if self.buffer:
            self.console.print(Markdown(self.buffer))
```

#### 5.2 工具执行状态面板

```
╭──────────────────────────────────╮
│ 🔧 grep_search                  │
│   pattern: "TODO"               │
│   path: "src/"                  │
│   ⏳ Running... (1.2s)          │
╰──────────────────────────────────╯
```

使用 Rich 的 `Live` 组件实现实时更新：

```python
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner

def on_tool_start(name, args):
    panel = Panel(
        Spinner("dots", text=f"Executing {name}..."),
        title=f"🔧 {name}",
        subtitle="Press Ctrl+C to cancel"
    )
    live.update(panel)
```

#### 5.3 状态栏

在每次 prompt 后显示当前上下文使用情况：

```
you> 请帮我重构这个函数
[tokens: 2,340 / 8,000 | session: alpha | tools: 10]
```

#### 5.4 Diff 预览

`file_edit` 工具执行后在终端显示彩色 diff：

```python
from rich.syntax import Syntax

def render_file_diff(console, path, old_content, new_content):
    diff_text = unified_diff(old_content.splitlines(), new_content.splitlines(), ...)
    console.print(Syntax("\n".join(diff_text), "diff", theme="monokai"))
```

#### 5.5 多行输入支持

当前只支持单行输入。应支持：
- 多行模式：以 `"""` 开始多行输入，以 `"""` 结束
- 文件输入：`@file:path/to/prompt.txt` 从文件读取输入
- 管道输入：支持 `echo "question" | zzm-agent`

---

## 六、配置系统

**当前模块:** `config.yaml` + `runtime.py`

### 现状问题

- 无配置校验，拼写错误不会报错
- 无默认值管理，缺少的字段可能导致 KeyError
- API Key 明文存储

### 改进方案

#### 6.1 配置 Schema 校验

```python
from dataclasses import dataclass

@dataclass
class ModelConfig:
    base_url: str
    api_key: str          # 支持 ${ENV_VAR} 引用
    model_name: str
    temperature: float = 0.7
    max_tokens: int = 4096

@dataclass
class AgentConfig:
    system_prompt: str = "你是 zzm-agent"
    auto_approve: bool = False
    plugin_dirs: list[str] = field(default_factory=lambda: ["zzm_agent/plugins"])
    max_tool_iterations: int = 20  # 新增：循环保护

@dataclass
class AppConfig:
    model: ModelConfig
    agent: AgentConfig
    memory: MemoryConfig
    evolution: EvolutionConfig
```

启动时自动校验，缺少必填项或类型不匹配立刻报错，附带修复建议。

#### 6.2 Secret Store 支持

`${ENV_VAR}` 是默认推荐方式，但工程级工具可以进一步支持 OS 级 Secret Store，避免 API Key 长期以明文形式出现在配置文件或 shell 历史中。

建议支持三种来源：

```yaml
model:
  api_key: ${OPENAI_API_KEY}
  # 或
  api_key: keyring://openai/default
  # 或
  api_key: plain://仅用于本地临时测试
```

优先级建议：

1. `${ENV_VAR}`：默认方案，适合本地和 CI
2. `keyring://service/account`：可选增强，适合长期本机使用
3. `plain://...`：仅允许显式配置，启动时给出安全提示

Secret Store 属于生产安全增强，不应阻塞前期核心 Agent 能力建设。

---

## 七、错误处理与自愈（贯穿所有模块）

### 现状问题

```python
except Exception as e:
    result_str = f"Error executing tool: {e}"  # 没有分类、没有重试、没有恢复建议
```

### 改进方案

#### 7.1 结构化错误类型

```python
class ToolError(Exception):
    """工具执行错误的基类"""
    def __init__(self, message, recovery_hint=None, retryable=False):
        self.recovery_hint = recovery_hint
        self.retryable = retryable

class FileNotFoundError(ToolError):
    def __init__(self, path):
        super().__init__(
            f"文件未找到: {path}",
            recovery_hint="使用 find_files 或 list_directory 查找正确的文件路径",
            retryable=False,
        )

class CommandTimeoutError(ToolError):
    def __init__(self, command, timeout):
        super().__init__(
            f"命令超时 ({timeout}s): {command}",
            recovery_hint="增加 timeout 参数或使用 run_background 执行耗时命令",
            retryable=True,
        )
```

#### 7.2 自动重试策略

```python
def _execute_tool_with_retry(self, name, args, max_retries=2):
    for attempt in range(max_retries + 1):
        try:
            return self.registry.call(name, args)
        except ToolError as e:
            if not e.retryable or attempt >= max_retries:
                return f"Error: {e}\nRecovery hint: {e.recovery_hint}"
            # 等待后重试
            time.sleep(1 * (attempt + 1))
```

---

## 八、测试与评估体系

Agent 的质量不能只靠传统单元测试判断。Loop、Prompt、Memory、Planner 的改动经常表现为“行为变化”，必须建立可回放、可比较、可回归的评估体系。

### 8.1 测试分层

| 层级 | 目标 | 适用对象 | 是否依赖真实 LLM |
|------|------|----------|:--------------:|
| 单元测试 | 验证纯逻辑正确性 | Tool Registry、配置解析、TokenCounter、错误类型、路径安全 | 否 |
| 工具回放测试 | 验证 Agent 面对固定工具结果时的决策 | Agent Loop、工具错误处理、上下文压缩 | 否 |
| 集成测试 | 验证 CLI、工具、记忆、配置能串起来 | CLI 命令、插件加载、后台任务 | 可选 |
| 固定任务基准 | 防止 Prompt / Loop / Memory 退化 | 编程任务、分析任务、问答任务 | 是 |
| LLM-as-a-Judge | 评估开放式输出质量 | Evolution、复杂规划、回答质量 | 是 |

### 8.2 工具回放测试

工具回放测试用于隔离 LLM 和真实环境的不确定性。测试用例记录一段固定的工具调用输入与返回结果，然后验证 Agent 下一步是否符合预期。

示例场景：

- `read_file` 返回目标文本不存在，Agent 应改用 `grep_search` 或 `find_files`
- `run_shell` 返回超时，Agent 应使用恢复建议或改用后台命令
- 连续两次相同工具调用无新信息，Agent 应停止重复调用并调整策略

建议测试数据格式：

```yaml
name: file-edit-target-not-found
user: "把 old_func 改成 new_func"
tool_results:
  - tool: read_file
    result: "file content without old_func"
expected:
  should_call: grep_search
  should_not_call: file_edit
```

### 8.3 固定任务基准集

建立一组稳定的“金标准任务”，每次修改 Prompt、Agent Loop、Memory 或工具执行逻辑后运行，防止能力退化。

建议初始基准集包括：

- 代码阅读：给定文件，回答函数职责
- 精确修改：修改一个小函数并保持其他内容不变
- 错误恢复：工具失败后换一种方法继续
- 长上下文：历史压缩后仍记得用户原始目标
- 项目规则：`.zzm_agent/rules.md` 生效
- 安全策略：高风险 shell 命令被拦截或要求确认

每个任务至少记录：

- 输入 prompt
- 初始文件状态或工具回放数据
- 期望结果
- 禁止行为
- 评分规则

### 8.4 LLM-as-a-Judge

LLM-as-a-Judge 适合评估开放式输出，但不应作为所有 CI 的强依赖。建议只在 Evolution、Prompt 变更和发布前评估中使用。

评分维度：

- 是否完成用户目标
- 是否遵守项目规则
- 是否正确使用工具
- 是否避免危险操作
- 是否保留关键上下文
- 回答是否简洁、可执行

Judge 模型应尽量强于被评估模型，并保留原始回答、评分理由和最终分数，便于回溯。

### 8.5 回归指标

每次评估至少记录以下指标：

- 任务成功率
- 平均工具调用次数
- 工具失败恢复率
- 重复工具调用次数
- 上下文压缩后 pinned 信息保留率
- 高风险操作拦截率
- 平均响应耗时

这些指标会成为 E10 Evolution 的输入，不应等到 Evolution 阶段才开始建设。

### 8.6 评估结论边界

不要在文档或产品行为中承诺“连续优化后分数持续提升”。Agent 评估受任务集、Judge 模型、采样参数和工具环境影响，分数可能波动。

合理的表达方式：

- 观察多次评估的趋势，而不是承诺单次必然提升
- 设置回归门禁，例如“新版本不得低于 baseline 3%”
- 对关键任务设置 hard gate，例如安全拦截、pinned 信息保留、文件修改正确性
- Evolution 生成的候选 prompt 必须经过评估和人工确认后再应用

不合理的表达方式：

- “每次 evolve 后分数都会提升”
- “连续优化必然越来越强”
- “自动 evolve 可以无监督长期运行”

评估体系的职责是降低退化风险、帮助发现改进方向，而不是给出不可验证的效果承诺。

### 8.7 评估成本控制

评估体系应默认优先使用不花钱、可重复的测试，避免每次本地开发或 CI 都触发真实 LLM 调用。

建议分层执行：

- **默认本地测试**：单元测试、工具回放测试、mock LLM，不访问真实模型
- **Smoke LLM Eval**：仅在 Prompt、Memory、Planner、Evolution 等核心行为变化时手动或 CI 条件触发
- **Full LLM Eval**：发布前、重要版本合并前或夜间任务运行
- **Judge Eval**：只用于 Evolution、Prompt 变更评审和发布前质量评估

建议命令拆分：

```text
zzm-agent eval --suite replay
zzm-agent eval --suite smoke --llm
zzm-agent eval --suite full --llm
```

默认 `zzm-agent eval --suite smoke` 不应隐式调用真实 LLM；需要真实模型时必须显式传入 `--llm` 或配置开关。

---

## 九、实施路线图

本章是实际开发入口。前面第一章到第八章描述的是“问题、方案和验证方法”，这里把它们合并成一条可执行路线。

三个概念的关系如下：

- **单项改进**：来自第一章到第八章，例如 `3.1 循环保护`、`4.1 参数描述从 docstring 提取`、`8.2 工具回放测试`。它们是具体能力点。
- **演进阶段**：E1-E10，每个阶段把多个相关能力点打包成一次可交付版本。
- **任务清单**：每个阶段下面的 checklist，是开发时逐项完成和验收的执行步骤。

开发时不要按“章节顺序”开发，也不要按“单项改进优先级表”孤立开发。应按下面的阶段顺序推进：每完成一个阶段，就得到一个可以独立测试、可以回滚、可以发布的小版本。

### 9.1 总体开发顺序

| 顺序 | 阶段 | 优先级 | 目标 | 合并的关键改进 | 依赖 |
|:---:|------|:------:|------|----------------|------|
| 0 | E1 工具集补齐 | 已完成 | 让 Agent 具备基本编程工具能力 | 文件工具、搜索工具、Shell 工具、路径安全 | 无 |
| 1 | E2 Agent 执行安全底座 | 已完成 | 防止失控，保证工具调用可控、可解释 | 3.1 循环保护、4.1 参数描述、4.4 插件生命周期、7.1 结构化错误、7.2 自动重试 | E1 |
| 1.5 | E0.5 安全与卫生修复 | P0 | 修复明文 API Key 等不应带入后续阶段的安全和卫生问题 | API Key 环境变量化、.env.example、.gitignore 补全 | E2 |
| 2 | E3a 回放测试底座 | P0 | 让 Agent Loop 的行为可在零 LLM 调用下被测试 | MockLLM/ReplayLLM、核心回放用例、fixtures 目录 | E2 |
| 2.5 | E3b 基准集与评估命令 | P0 | 建立可比较、可回归的质量基线 | 固定任务基准集、eval 命令行、回归指标记录 | E3a |
| 3 | E4 CLI 可观测性 | 已完成 | 让用户看得见 Agent 正在做什么 | 3.2 工具事件回调、5.2 工具状态面板、5.1 流式 Markdown、5.4 Diff 预览、对话成本追踪、日志 | E2、E3a |
| 4 | E5 上下文与 Token 管理 | P0 | 让长会话不再靠粗略估算和硬截断 | 2.2 History 压缩、2.6 Pinning、Token 精确计算（含 fallback 链）、5.3 状态栏 | E2、E3a |
| 5 | E6 PromptManager 与环境适配 | P1 | 让 Agent 能根据项目、任务和运行环境调整行为 | 1.1 Prompt 模板、1.2 动态组装、1.3 意图检测、1.4 项目规则、1.5 环境适配 | E5 |
| 6 | E7 记忆与检索升级 | P1 | 让记忆真正能被沉淀、召回和利用 | 2.1 Episodic 摘要、2.3 自动记忆、2.5 重要性评分、语义/混合检索 | E5、E6 |
| 7 | E8 异步执行、项目索引与后台任务 | P2 | 支持并发工具、减少大型项目探索成本，并管理长时间运行任务 | 3.5 async loop（保留 sync 入口）、3.6 并发 tool_calls、4.5 项目索引、4.6 `@tool_chain`、后台命令、Git 工具 | E4 |
| 8 | E9 多轮规划与工作记忆 | P2 | 让 Agent 能拆解复杂任务并保留中间状态 | 2.4 WorkingMemory、3.4 思维链引导、Planner（AgentLoop 外层编排）、plan-execute | E6、E7、E8 |
| 9 | E10 Evolution、多模型、Web 与安全加固 | P3 | 扩展边界能力，同时补齐生产级约束 | gated auto-evolve（仅候选生成，禁止自动应用）、LLM-as-a-Judge、多 Provider、Web 工具、安全审计、配置 Schema、Secret Store | E3b-E9 |

### 9.2 开发原则

1. **先稳住执行环，再做智能化。** 循环保护、工具 schema、错误类型、可观测性优先于复杂规划和自进化。
2. **先建评估基线，再改智能行为。** 没有回归测试，Prompt、Memory 和 Planner 的改动无法判断是增强还是退化。
3. **先让上下文可靠，再做记忆。** 没有准确 token 计量、pinning 和压缩策略，记忆系统会污染上下文而不是增强能力。
4. **先做 PromptManager，再做 Evolution。** Evolution 需要稳定的 prompt 输出载体，否则只能改一段硬编码字符串。
5. **目录结构随阶段自然演进。** 不先做大搬家，需要某类能力时再引入 `prompt/`、`schema/`、`utils/` 等目录。
6. **每个阶段必须可测试。** 阶段完成标准不是“代码写完”，而是有 CLI 行为、单元测试、回放测试或基准测试证明它工作。
7. **评估默认低成本。** 本地和常规 CI 默认跑 mock / replay / deterministic tests，真实 LLM 评估必须显式开启。
8. **共享命名必须集中定义。** Prompt section、事件名、内部路径和配置 key 不允许散落硬编码。
9. **Planner 不修改 AgentLoop。** 多轮规划在 AgentLoop 外层编排，核心循环只关注单轮 ReAct。
10. **auto-evolve 永远只生成候选。** 任何 prompt 变更必须经过人工确认或回归门禁，禁止后台静默切换。

### 9.3 目标目录结构

随着 E3-E6 的推进，代码目录会自然演进。以下是 E6 完成后的预期结构：

```
zzm_agent/
├── cli_support/     # CLI 渲染 + 工具状态面板 + 成本展示
├── core/            # AgentLoop + ToolRegistry + Plugin + Errors
├── eval/            # 评估框架：ReplayLLM、基准集、eval 命令（E3 新增）
├── evolution/       # 优化器
├── memory/          # 记忆系统
├── plugins/         # 工具实现
├── prompt/          # PromptManager + 模板 + 上下文构建器（E6 新增）
├── constants.py     # 共享常量
└── schema.py        # 配置 dataclass（可选，E10 时引入）
```

原则：不提前做大搬家，需要某类能力时再引入对应目录。

---

### E1: 工具集补齐（已完成）

**目标:** 让 Agent 具备基本编程助手能力。  
**状态:** 已完成。  
**关联章节:** 第四章 Tool Registry、现有工具模块。

已将内置工具从 2 个扩展至 10 个，覆盖编程助手的核心能力：

- [x] `read_file` 增强：带行号输出、支持 start_line / end_line 分页读取
- [x] `file_edit`：精确搜索-替换编辑，避免全量覆写丢失内容
- [x] `file_append`：向文件末尾追加内容
- [x] `list_directory`：目录浏览，支持递归、隐藏文件过滤
- [x] `file_info`：获取文件元信息（大小、行数、修改时间）
- [x] `grep_search`：跨文件内容搜索，支持正则、大小写、文件类型过滤
- [x] `find_files`：按文件名模式查找文件
- [x] `run_shell` 增强：可配超时（最大 300s）、自定义工作目录、返回退出码
- [x] `environment_info`：检测操作系统和已安装开发工具
- [x] 路径安全统一修复：相对路径基于 workspace root 解析，防止 symlink 逃逸

---

### E2: Agent 执行安全底座

**目标:** 先保证 Agent 不会失控，并提升工具调用准确率。  
**为什么先做:** 没有循环保护、工具 schema、插件边界和结构化错误，后续任何复杂能力都会放大风险。  
**关联章节:** 3.1、4.1、4.4、7.1、7.2。  
**依赖:** E1。

**当前状态:** 已完成。已实现 Agent Loop 迭代上限、重复调用检测、工具参数描述提取、结构化工具错误返回、执行安全阈值配置化、插件 manifest / 生命周期 / 失败隔离，以及 retryable 工具错误的有限自动重试。

任务清单：

- [x] 在 `AgentLoop` 中增加最大工具迭代次数，例如 `max_tool_iterations = 20`
- [x] 增加重复工具调用检测，发现循环时注入系统提示并停止盲目执行
- [x] 从工具 docstring 的 Args 部分提取参数描述，写入 JSON Schema
- [x] 将 `max_tool_iterations` 和 `duplicate_tool_call_limit` 接入 `config.yaml` 的 `agent` 配置，并保留默认值
- [x] 引入插件 manifest、插件级配置和工具命名空间
- [x] 定义 `BasePlugin.initialize()`、`register_tools()`、`shutdown()` 生命周期
- [x] 插件加载失败时隔离错误，不影响核心工具和其他插件
- [x] 定义 `ToolError`、`CommandTimeoutError` 等结构化错误类型
- [x] 工具执行异常以 JSON payload 返回模型，包含 `error_type`、`message`、`recovery_hint`、`retryable`
- [x] 为可重试错误增加有限次数自动重试和 recovery hint
- [x] 为已完成能力补充单元测试和至少一个端到端工具循环保护测试

验收标准：

- [x] 模型连续请求相同工具时，Agent 能在上限内停止并给出明确提示
- [x] `/tools` 或工具 schema 中能看到参数描述
- [x] 单个插件加载失败时，Agent 仍能启动并加载其他插件
- [x] 工具失败时返回包含错误类型和恢复建议的结果，而不是裸字符串异常

---

### E0.5: 安全与卫生修复

**目标:** 修复不应带入后续开发阶段的安全和卫生问题。  
**为什么现在做:** `config.yaml` 中 API Key 明文暴露，如果仓库公开或被其他人 fork 会立刻泄漏。这个问题不需要等 E10 的 Secret Store，现在就应该修复。  
**依赖:** E2。

任务清单：

- [x] 将 `config.yaml` 中的 `api_key` 改为 `${OPENAI_API_KEY}` 或 `${DASHSCOPE_API_KEY}` 环境变量引用
- [x] 在配置加载逻辑中实现 `${ENV_VAR}` 占位符解析（如尚未支持）
- [x] 创建 `.env.example` 文件，说明需要设置的环境变量
- [x] 确认 `.gitignore` 已包含 `.env`、`.zzm_agent/` 等敏感路径
- [x] 已提交的历史中如有明文 Key，在文档中记录并建议用户轮换 Key

验收标准：

- [x] `config.yaml` 中不再包含任何明文 API Key
- [x] 新用户 clone 后，根据 `.env.example` 设置环境变量即可启动
- [x] CI 环境可通过注入环境变量运行测试

---

### E3a: 回放测试底座

**目标:** 让 Agent Loop 的核心行为（工具调用、循环保护、错误恢复）在零 LLM 调用下可被确定性测试。  
**为什么拆分:** 原 E3 范围过大，MockLLM 是后续所有测试的地基，应优先独立交付。  
**关联章节:** 第八章 8.1-8.2。  
**依赖:** E2。

#### MockLLM / ReplayLLM 设计

回放测试的核心是一个可替代真实 LLM client 的 mock 对象。它按预定义的 turn 序列返回固定 response，从而让 Agent Loop 的行为完全确定。

```python
@dataclass
class ReplayTurn:
    """One model response in a replay sequence."""
    content: str = ""                       # 模型文本回复
    tool_calls: list[dict] | None = None    # 模型请求的工具调用（可选）

class ReplayLLM:
    """Replays a fixed sequence of LLM responses for deterministic testing."""

    def __init__(self, turns: list[ReplayTurn]):
        self.turns = turns
        self._cursor = 0

    def create(self, **kwargs) -> MockResponse:
        """Compatible with `client.chat.completions.create()`."""
        if self._cursor >= len(self.turns):
            return MockResponse(content="(no more replay turns)")
        turn = self.turns[self._cursor]
        self._cursor += 1
        return turn.to_response(stream=kwargs.get("stream", False))
```

关键设计约束：

- ReplayLLM 必须同时兼容 `stream=True` 和 `stream=False` 两种调用路径
- 工具调用结果通过 `ToolRegistry.call()` 真实执行或通过 fixture mock
- 每个测试用例是一个 `(ReplayLLM turns, mock tool results, assertions)` 三元组

任务清单：

- [x] 建立 `tests/fixtures/agent_cases/` 目录
- [x] 实现 `ReplayTurn` 和 `ReplayLLM`，兼容 `client.chat.completions.create()` 的流式和非流式接口
- [x] 实现工具结果 mock 机制：给定 `(tool_name, args) → result` 映射表
- [x] 编写第一批核心回放测试用例：

| # | 用例名 | 验证点 |
|---|--------|--------|
| 1 | `test_normal_tool_flow` | LLM 请求 `read_file` → 工具返回结果 → LLM 给出最终回复 |
| 2 | `test_tool_error_recovery` | `read_file` 返回 ToolError → LLM 换用 `grep_search` |
| 3 | `test_duplicate_call_stop` | 连续 3 次相同 `grep_search` → 被截断并输出提示 |
| 4 | `test_iteration_limit` | 超过 `max_tool_iterations` → 被截断 |
| 5 | `test_user_deny_high_risk` | `run_shell(rm ...)` 被拦截 → LLM 收到 "User denied" |

- [x] 所有回放测试可通过 `pytest tests/` 直接运行，不需要网络或 API Key

验收标准：

- `ReplayLLM` 可以替代 `OpenAI` client 驱动 `AgentLoop.run()`
- 5 个核心回放用例全部通过
- 任何修改 Agent Loop 工具调用逻辑的 PR 都可以靠这些测试快速回归
- 测试运行耗时 < 5 秒，无外部依赖

---

### E3b: 基准集与评估命令

**目标:** 在回放测试之上，建立可比较、可回归的质量基线和评估入口。  
**为什么单独拆:** 基准集涉及真实 LLM 调用，与 E3a 的零成本测试有本质区别；eval 命令行是独立的 CLI 入口，适合单独交付。  
**关联章节:** 第八章 8.3-8.7。  
**依赖:** E3a。

任务清单：

- [x] 建立第一批固定任务基准集（YAML 格式）：
  - 代码阅读：给定文件，回答函数职责
  - 精确修改：修改一个小函数并保持其他内容不变
  - 错误恢复：工具失败后换一种方法继续
  - 长上下文：历史压缩后仍记得用户原始目标
  - 项目规则：`.zzm_agent/rules.md` 生效
  - 安全策略：高风险 shell 命令被拦截或要求确认
- [x] 增加评估命令入口：`zzm-agent eval --suite replay|smoke|full`
- [x] `replay` 套件默认不访问真实 LLM，`smoke` 和 `full` 需要 `--llm` 显式开启
- [x] 真实 LLM 评估必须显式传入 `--llm` 或由发布流程触发
- [x] 记录回归指标：成功率、工具调用次数、重复调用次数、失败恢复率
- [x] 在 CI 或本地测试命令中区分 deterministic tests 和 LLM evals

验收标准：

- `zzm-agent eval --suite replay` 可以稳定运行，运行成本为零
- 每次修改 Agent Loop 或工具执行逻辑后，都能运行 replay 套件
- `zzm-agent eval --suite smoke` 默认不产生真实模型调用成本
- 评估输出包含可比较的指标，而不是只有自然语言总结

---

### E4: CLI 可观测性

**目标:** 让用户清楚看到 Agent 当前正在执行什么、是否成功、耗时多久、花了多少钱。  
**为什么现在做:** Agent 一旦具备执行能力，静默执行就是最影响信任感的问题。  
**关联章节:** 3.2、5.1、5.2、5.4、7.1。  
**依赖:** E2、E3a。  
**当前状态:** 已完成。已通过工具事件回调、Rich Live 状态面板、缓冲 Markdown、diff 预览、JSONL 工具事件日志、token/费用汇总和三选项工具授权覆盖验收标准；medium / high 风险工具默认弹出授权卡，`auto_approve` 显式开启时才跳过。CLI 体验已补充 Questionary 授权菜单、prompt_toolkit 输入历史，以及圆角面板、padding、柔和配色和 JSON 高亮。

任务清单：

- [x] 在 `AgentLoop` 增加 `on_tool_start`、`on_tool_end`、`on_tool_error` 事件回调
- [x] CLI 层用 Rich `Live` 渲染工具执行状态面板
- [x] 流式输出改为缓冲型 Markdown 渲染，避免默认模式显示原始 `**bold**`
- [x] `file_edit` 执行后显示彩色 diff 预览
- [x] 多文件修改场景支持一次性展示所有变更的 diff
- [x] 日志系统接入工具调用事件，记录工具名、参数摘要、耗时和结果状态
- [x] 对话成本追踪：累计每轮 prompt_tokens、completion_tokens，转换为预估费用
- [x] 在状态栏或会话结束时展示本轮和累计 token 用量及预估费用
- [x] 批准授权使用工具时显示同意，再次会话中始终同意，拒绝三个选择，用户直接选择即可，不需要用户输入yes，no等

验收标准：

- 执行工具时 CLI 会显示工具名、关键参数、运行状态和耗时
- 流式回复中的 Markdown 能在自然段落边界正确渲染
- 文件修改后用户能看到 diff，而不是只能看最终回复
- 工具执行日志能被测试和评估系统读取
- 用户能在每轮对话后看到 token 用量

---

### E5: 上下文与 Token 管理

**现状:** 使用 `len(text) / 4` 粗略估算 token 数，误差较大（尤其中文场景）。  
**目标:** 引入精确 token 计算、关键上下文 pinning，并让历史压缩从字符串截断升级为语义摘要。  
**为什么现在做:** Prompt 管理、记忆检索和规划能力都依赖可靠上下文预算。  
**关联章节:** 2.2、2.6、5.3。  
**依赖:** E2、E3a。

**⚠️ 风险提示：Tokenizer Fallback 链**

`tiktoken` 不支持所有模型的编码器（例如当前使用的 `qwen3.5-plus`）。建议实现 fallback 链：

```
模型专用 tokenizer（如 qwen-tokenizer）→ tiktoken cl100k_base → len(text) / 4
```

不要假设 tiktoken 一定能命中当前模型的编码器；fallback 必须是显式设计，而不是 catch-all 异常处理。

任务清单：

- [x] 在 `requirements.txt` 中添加 `tiktoken` 可选依赖
- [x] 实现 `TokenCounter` 类，支持 tokenizer fallback 链：模型专用 → tiktoken cl100k_base → `len/4`
- [x] `MemoryStore.estimate_text_tokens` 优先使用 `TokenCounter`，不可用时回退到字符估算
- [x] 调大默认 `max_context_tokens`，充分利用现代模型的长上下文窗口
- [x] 实现 `PinnedContext`，保存用户目标、关键约束、当前文件、错误核心行、未完成计划
- [x] History 超出预算时，优先压缩旧消息，而不是简单截断
- [x] 增加轻量 / 中量 / 重度压缩策略
- [x] CLI 状态栏展示当前 token 使用情况

验收标准：

- token 估算逻辑可针对不同模型选择编码器，不支持的模型自动 fallback
- 压缩前后 pinned 信息不会丢失
- 长会话超过预算时，旧消息会被压缩为保留文件路径、决策、错误信息的摘要
- 用户能在 CLI 中看到当前上下文使用量

---

### E6: PromptManager 与环境适配

**目标:** 从硬编码 `system_prompt` 升级为可按任务、项目、工具和运行环境动态组装的 PromptManager。  
**为什么现在做:** 有了可靠上下文预算后，prompt 才能稳定注入项目规则、工具说明、环境约束和记忆上下文。  
**关联章节:** 1.1、1.2、1.3、1.4、1.5、1.6、4.2、4.3。  
**依赖:** E5。

任务清单：

- [ ] 新建 `zzm_agent/prompt/` 模块，包含 `manager.py`、`templates.py`、`context_builder.py`
- [ ] 实现 coding / analysis / chat 三类基础模板
- [ ] 实现轻量意图检测，按用户输入和历史上下文选择模板
- [ ] 支持读取 `.zzm_agent/rules.md` 并注入 system prompt
- [ ] 注入环境上下文：OS、shell、workspace、路径规则、推荐命令风格
- [ ] 集中定义 Prompt section 标签、事件名、内部路径和配置 key，避免硬编码散落
- [ ] 根据 Tool Registry 动态生成工具使用指南
- [ ] 支持工具 examples 和 group 信息注入 prompt 或 `/tools` 展示

验收标准：

- 不同任务类型会生成不同 system prompt
- 项目根目录存在 `.zzm_agent/rules.md` 时，规则会自动生效
- Windows / PowerShell 环境下，Agent 会优先生成适配当前 shell 的命令
- `[Environment]`、`[Working Memory]`、`[Pinned Context]` 等标签来自统一常量定义
- 工具说明和参数描述来自注册表，而不是散落在硬编码 prompt 中

---

### E7: 记忆与检索升级

**现状:** `KeywordMemoryRetriever` 仅做关键词子串匹配，召回率低。  
**目标:** 让长期记忆可以自动沉淀、准确召回，并参与 prompt 组装。  
**为什么现在做:** PromptManager 已经能承载记忆上下文，下一步应提升记忆质量。  
**关联章节:** 2.1、2.3、2.5。  
**依赖:** E5、E6。

- [ ] 添加 `sentence-transformers` 可选依赖
- [ ] 用 LLM 生成 Episodic 会话摘要，替代最后 4 条消息拼接
- [ ] 每轮对话结束后自动提取值得长期保存的 semantic memory
- [ ] 实现 `EmbeddingMemoryRetriever`：对 semantic / episodic 条目做 embedding 编码
- [ ] 使用余弦相似度排序替代关键词计分
- [ ] 支持混合检索：关键词匹配 + 语义相似度加权融合
- [ ] embedding 缓存持久化到磁盘，避免重复计算
- [ ] 配置项控制：`memory.retriever_type: keyword | embedding | hybrid`
- [ ] 为记忆增加 importance、access_count、last_accessed_at 字段

验收标准：

- 长会话结束后能生成结构化摘要，包含目标、决策、结果和未完成事项
- 用户偏好、项目约定等事实可自动进入 semantic memory
- 相同语义但不同关键词的查询能召回相关记忆

---

### E8: 异步执行、项目索引与后台任务

**现状:** `run_shell` 同步阻塞，无法启动 dev server 后继续其他操作。  
**目标:** 升级为 async agent loop，支持并发只读工具调用，建立轻量项目索引，并支持后台进程管理。  
**为什么现在做:** 有了可观测性后，async loop 和并发工具才有足够的状态反馈；有了项目索引后，后续 Planner 才不必反复遍历目录。  
**关联章节:** 3.5、3.6、4.5、4.6、5.5。  
**依赖:** E4。

**⚠️ 风险提示：Async 迁移策略**

从 sync 到 async 是一次影响面大的改动，会波及所有测试。建议采用以下策略降低风险：

1. **保留 `run()` 同步入口**：对外 API 不变，内部用 `asyncio.run(self._async_run(...))` 包装
2. 现有测试不需要改成 async 就能继续工作
3. 新增 `async_run()` 方法供需要 async 上下文的调用方使用
4. 工具注册时标记 `is_async=True/False`，async 工具直接 await，sync 工具走 `asyncio.to_thread()`

任务清单：

- [ ] 将 Agent Loop 主路径升级为 async，保留 `run()` 同步入口以兼容现有调用方
- [ ] 同步工具通过 `asyncio.to_thread()` 或线程池执行，不能直接阻塞 event loop
- [ ] 文件 IO、shell 执行、索引构建等阻塞操作统一走 sync-to-async 包装器
- [ ] 增加 cancellation token，用户中断时能取消未完成工具
- [ ] 实现 `ToolCallScheduler`，只并发低风险、无依赖、只读工具
- [ ] 增加 `agent.max_parallel_tools` 配置项，限制并发工具数量
- [ ] 同一路径写操作和 shell 操作默认串行执行
- [ ] 工具结果按原 tool_call 顺序回填 messages
- [ ] 引入 `@tool_chain`，注册可审计、可回放的固定工具流程
- [ ] 实现第一批工具链：`safe_replace`、`inspect_symbol`、`run_test_and_summarize`
- [ ] 新增 `.zzm_agent/index/project_structure.json` 缓存
- [ ] 索引文件路径、大小、mtime、扩展名和语言类型
- [ ] 可选提取 class / function / method 等符号信息
- [ ] 支持 `.gitignore`、`.zzm_agentignore` 或配置项过滤
- [ ] 文件 mtime 变化时增量更新索引
- [ ] 新增 `/index rebuild` 命令手动重建索引
- [ ] 新增 `run_background` 工具：启动后台进程并返回进程 ID
- [ ] 新增 `check_process` 工具：查询后台进程的状态和最近输出
- [ ] 新增 `stop_process` 工具：终止后台进程
- [ ] 进程注册表管理，Agent 退出时自动清理子进程
- [ ] 新增 Git 集成工具：`git_status`、`git_diff`、`git_commit`（对编程助手是核心能力）
- [ ] 支持多行输入：`"""` 多行模式、`@file:path` 文件输入、管道输入

验收标准：

- Agent Loop 可以在 async 模式下运行，并兼容现有同步工具
- `run()` 同步入口仍然可用，现有测试无需改为 async
- 慢文件 IO 或慢 shell 执行期间，CLI Spinner、事件回调和取消信号仍能响应
- 多个只读工具调用可以并发执行，写操作和 shell 操作仍保持安全串行
- 用户中断时，未完成工具能被取消或进入明确的停止状态
- `@tool_chain` 每一步都有事件、日志和回放测试覆盖
- Agent 可以先查项目索引，再决定是否需要 `grep_search`
- 大型项目中重复目录遍历次数明显下降
- 能启动一个长时间运行命令，并在后续轮次继续查询状态
- Agent 退出时不会留下不可控子进程
- CLI 能展示后台进程的运行状态和最近输出
- `git_status`、`git_diff` 能正确工作并返回结构化结果

---

### E9: 多轮规划与工作记忆

**现状:** Agent 只做单轮 ReAct（思考-行动），无法分解复杂任务。  
**目标:** 支持 Agent 自主规划多步骤任务，并保存任务内中间状态。  
**为什么现在做:** 规划依赖 PromptManager、记忆和工具可观测性，不应过早实现。  
**关联章节:** 2.4、3.4。  
**依赖:** E6、E7、E8。

**🔴 架构约束：Planner 必须在 AgentLoop 外层编排**

plan-execute 模式与当前 ReAct 循环是两套不同的执行模型。如果直接在 `agent_loop.py` 中嵌入 Planner 逻辑，核心循环会变得极其复杂且难以测试。

正确的做法是让 Planner 在 AgentLoop **外部**做编排：

```
Planner.plan(user_goal) → [step1, step2, step3]
for step in steps:
    result = AgentLoop.run(step.instruction)  # 每个子任务走现有 AgentLoop
    Planner.reflect(step, result)             # 回顾并动态调整后续步骤
```

AgentLoop 本身只负责单轮 ReAct 循环，不需要知道自己是被 Planner 调度的。

任务清单：

- [ ] 实现 `WorkingMemory`：保存 notes、findings、plan、completed
- [ ] 每轮 LLM 调用时注入当前 Working Memory
- [ ] 实现 `Planner` 模块：接收用户目标，输出步骤列表
- [ ] `Planner` 在 AgentLoop 外部编排子任务，不修改 AgentLoop 核心循环
- [ ] 每步执行后调用 `Planner.reflect()` 回顾结果，动态调整后续步骤
- [ ] 支持用户中途干预：确认 / 修改 / 跳过某步骤

验收标准：

- 对复杂任务，Agent 能生成可见计划并逐步更新进度
- 中途工具失败后，Agent 能根据 findings 调整后续步骤
- 用户可以修改计划，Agent 后续执行会基于修改后的计划继续
- `AgentLoop.run()` 的接口和内部逻辑不因 Planner 引入而改变

---

### E10: Evolution、多模型、Web 与安全加固

**现状:** `optimizer.optimize()` 是空函数，`/evolve` 只能做评估不能真正优化。  
**目标:** 完成 "评估 → 分析 → 生成新 prompt → 应用" 的完整闭环，并扩展模型、联网和生产安全边界。  
**为什么最后做:** 这些能力价值高，但会显著扩大配置复杂度和安全面，应该在核心闭环稳定后推进。  
**关联章节:** 第一章 Prompt 系统、第六章配置系统、第七章错误处理、第八章测试与评估、Evolution 模块、Web 能力、安全加固。  
**依赖:** E3b-E9。

**🔴 安全红线：auto-evolve 仅做候选生成，禁止自动应用**

LLM 自己优化自己的 prompt 在学术界仍无定论，生产环境中容易退化且难以调试。必须遵守以下红线：

1. auto-evolve 只自动生成候选 prompt + 评估报告，**永远不自动写入生产配置**
2. 任何 prompt 变更必须经过人工确认或至少通过固定基准集的回归门禁
3. `/evolve apply` 必须是显式的用户操作，不允许后台静默切换
4. 每次变更保留回滚点，`/evolve rollback` 必须能一键恢复

- [ ] 实现 `optimize()`：基于历史评估记录，让 LLM 自省并生成改进后的 system_prompt
- [ ] 加入 prompt A/B 对比验证：新旧 prompt 分别回答相同问题，选择更好的
- [ ] 引入 LLM-as-a-Judge，仅用于 Evolution、Prompt 变更和发布前评估
- [ ] 支持 gated auto-evolve：达到触发条件时自动生成候选优化，但不自动应用
- [ ] 触发条件包括：连续失败、固定基准退化、用户显式请求、评估分数低于阈值
- [ ] 自动触发后必须生成候选变更、评估报告和回滚点，等待用户确认
- [ ] 禁止承诺“连续优化后分数持续提升”，只展示趋势、置信度和回归风险
- [ ] 实现 prompt 版本历史管理：保留最近 N 个版本，支持回滚
- [ ] `/evolve status` 展示趋势：多次评估的得分变化曲线
- [ ] config.yaml 支持多个 model profile 定义
- [ ] 配置 Schema 校验：缺少必填项或类型错误时启动即报错
- [ ] `/model <name>` 命令运行时切换模型
- [ ] 不同工具调用使用不同模型（如规划用强模型、执行用快模型）
- [ ] API 调用失败自动重试 + 指数退避
- [ ] 支持 Anthropic / Google / 本地 Ollama 等非 OpenAI 格式 API
- [ ] API Key 默认支持 `${ENV_VAR}`，可选支持 `keyring://service/account`
- [ ] 新增 `web_search` 工具：调用搜索 API 获取摘要结果
- [ ] 新增 `read_url` 工具：抓取网页正文并转为 Markdown
- [ ] 搜索结果自动注入上下文，帮助 Agent 回答时效性问题
- [ ] 配置项控制：`agent.web_enabled: true/false`
- [ ] 命令黑名单：禁止 `rm -rf /`、`format`、`shutdown` 等破坏性命令
- [ ] 文件操作审计日志：记录所有文件修改到 `.zzm_agent/audit.log`
- [ ] 路径沙箱加固：递归解析 symlink 后再做边界检查
- [ ] 工具执行前的 dry-run 预览模式

验收标准：

- `/evolve` 不只输出评估分数，还能生成候选 prompt 变更
- 新 prompt 应经过 A/B 对比、固定任务基准或 Judge 评估后再应用
- auto-evolve 只能自动生成候选方案，默认不自动写入生产配置
- `/evolve status` 显示趋势和回归门禁，不做单调提升承诺
- 用户可以查看 prompt 版本历史并回滚
- 配置错误能在启动时暴露，并给出明确修复建议
- 用户能在运行时切换模型 profile
- API Key 可通过环境变量或可选 keyring 读取
- Web 工具默认可关闭，开启后搜索结果会带来源摘要进入上下文
- 文件和命令类高风险操作有审计和预览机制

---
