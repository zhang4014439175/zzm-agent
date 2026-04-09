# zzm-agent 设计文档

**日期：** 2026-04-08  
**参考：** [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)  
**语言：** Python  
**模型接口：** OpenAI 兼容（可接 Ollama、本地代理、任意兼容端点）

---

## 1. 目标

构建一个简洁的个人 AI Agent，具备：
- 多轮对话 + 自动 tool_calling 循环
- 动态插件/工具系统（`@tool` 装饰器自动注册）
- 持久记忆（跨会话保存对话历史）
- CLI 交互界面（REPL + 斜杠命令）
- 自我进化模块（自动优化 system prompt，框架阶段为空实现）

### MVP 边界

当前文档定义的是 `v0.1.0-skeleton`，只要求完成最小可运行闭环，不要求在这一版实现下面这些高级能力：
- 不要求插件热重载；首次启动时完成插件扫描和注册即可
- 不要求按 session 检索历史；`memory.json` 仅保存最近若干条消息
- 不要求安全沙箱；`shell.py` 和 `file_ops.py` 视为本地可信工具
- 不要求自动演化；`evolution/optimizer.py` 在该阶段允许为空实现或手动触发 stub

### 安全边界

当前版本默认面向本地个人使用，不是多租户或远程托管 agent：
- `shell.py` 和 `file_ops.py` 仅用于本机可信环境，不应直接暴露给外部用户或不可信输入
- 若后续要提供 Web/API/多用户访问，必须补充命令白名单、目录访问限制、超时/资源限制和审计日志
- 在未实现上述约束前，文档中的内置工具只能视为开发期能力，不视为生产安全能力

---

## 2. 目录结构

```
zzm-agent/
├── zzm_agent/
│   ├── __init__.py
│   ├── core/
│   │   ├── agent_loop.py       # 主循环：多轮对话 + tool_calling 调度
│   │   └── tool_registry.py    # 扫描并注册工具，生成 OpenAI tools schema
│   ├── memory/
│   │   └── store.py            # 持久记忆：读写 JSON，存/查历史消息
│   ├── evolution/
│   │   └── optimizer.py        # 自我进化：收集轨迹，自动优化 system prompt
│   └── plugins/                # 内置工具，每个 .py 暴露 @tool 装饰的函数
│       ├── shell.py            # 执行 shell 命令
│       └── file_ops.py         # 读写文件
├── cli.py                      # 入口：REPL 交互界面
├── config.yaml                 # 模型、agent、memory、evolution 配置
└── pyproject.toml
```

---

## 3. 模块职责

| 模块 | 职责 |
|------|------|
| `core/agent_loop.py` | 维护 messages 列表，调用模型，检测 tool_calls，分发执行，循环直到无 tool_call |
| `core/tool_registry.py` | 遍历配置的 plugin 目录，自动提取函数签名生成 JSON schema，并在启动阶段完成注册 |
| `memory/store.py` | 每轮对话后追加写入 `~/.zzm_agent/memory.json`，按 `max_history` 截断后加载最近消息 |
| `evolution/optimizer.py` | 读取历史轨迹 → 模型自评估 → 生成改进建议 → 写入 config.yaml 的 system_prompt |
| `cli.py` | `rich` 渲染的 REPL，支持 `/tools`、`/memory`、`/evolve` 斜杠命令 |

---

## 4. 数据流

```
用户输入
    │
    ▼
cli.py  ──── /tools /memory /evolve 斜杠命令直接处理
    │
    ▼ 普通消息
core/agent_loop.py
    │
    ├─1─► memory/store.py        加载历史消息，拼入 messages[]
    │
    ├─2─► core/tool_registry.py  获取当前可用工具的 JSON schema
    │
    ├─3─► OpenAI 兼容 API        POST /chat/completions
    │         ▲
    │         │ 返回 tool_calls?
    │         │   是 ──► 逐个调用对应 plugin 函数
    │         │          结果追加为 tool message
    │         │          再次调用 API（循环）
    │         │   否 ──► 最终文本回复
    │
    ├─4─► memory/store.py        保存本轮完整消息
    │
    └─5─► cli.py                 渲染输出给用户


evolution/optimizer.py（手动触发 /evolve 或自动触发）
    │
    ├─► 读取 memory.json 中最近 N 轮轨迹
    ├─► 让模型自评：哪些回复不好？为什么？
    ├─► 生成新 system_prompt 候选
    └─► 写回 config.yaml，下次启动时生效
```

---

## 5. Plugin 接口规范

每个 plugin 文件放在 `zzm_agent/plugins/` 或用户自定义目录，用 `@tool` 装饰器标注：

```python
from zzm_agent.core.tool_registry import tool

@tool(description="在本机执行 shell 命令，返回 stdout/stderr")
def run_shell(command: str) -> str:
    ...
```

- plugin 注册必须绑定到同一个 `ToolRegistry` 实例；不要同时维护“全局 registry”和“局部 registry”两套来源，否则 `build_registry()` 加载出的工具和 `@tool` 装饰器注册结果会不一致
- `tool_registry` 从函数签名 + 类型注解自动生成 JSON schema
- 支持参数类型：`str`、`int`、`float`、`bool`、`list`、`dict`
- plugin 目录可在 `config.yaml` 中配置多个，运行时合并注册
- 对 `run_shell`、`read_file`、`write_file` 这类高权限工具，当前阶段只定义功能，不定义隔离机制；生产化前必须增加访问控制

---

## 6. config.yaml 格式

```yaml
model:
  base_url: "http://localhost:11434/v1"
  api_key: "sk-xxx"
  model_name: "qwen2.5:14b"
  temperature: 0.7
  max_tokens: 4096

agent:
  system_prompt: "你是 zzm-agent，一个简洁高效的个人助理。"
  plugin_dirs:
    - "zzm_agent/plugins"
    - "~/.zzm_agent/plugins"

memory:
  path: "~/.zzm_agent/memory.json"
  max_history: 50

evolution:
  enabled: false
  trigger: "manual"        # manual | auto
  sample_size: 20
```

---

## 7. 实现顺序（逐步填充）

1. **框架骨架** — 所有文件创建，核心函数空实现，`cli.py` 能启动
2. **tool_registry** — `@tool` 装饰器 + 自动 schema 生成 + plugin 加载
3. **memory/store** — JSON 持久化，历史加载/保存
4. **agent_loop** — 多轮对话循环 + tool_calling 逻辑
5. **内置 plugins** — `shell.py`、`file_ops.py`
6. **evolution/optimizer** — 轨迹收集 + prompt 优化
