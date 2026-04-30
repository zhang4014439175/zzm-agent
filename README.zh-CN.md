<div align="center">

# zzm-agent

**面向本地项目工作的轻量级个人 Agent 和交互式 REPL。**

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![OpenAI Compatible](https://img.shields.io/badge/API-OpenAI%20Compatible-111827?style=flat-square)](https://platform.openai.com/docs/api-reference)
[![CLI](https://img.shields.io/badge/Interface-REPL-2563EB?style=flat-square)](#使用)
[![Memory](https://img.shields.io/badge/Memory-Session%20%2B%20Semantic-7C3AED?style=flat-square)](#记忆与会话)

[English](README.md) · [简体中文](README.zh-CN.md)

</div>

---

`zzm-agent` 适合在本地代码项目里使用：和模型连续对话，让它通过工具查看或修改工作区，跨会话保存有用记忆，并在调试时查看实际发送给模型的完整 prompt payload。

## 一眼看懂

| 能力 | 价值 |
| --- | --- |
| 交互式 REPL | 像结对工作一样持续和 Agent 对话。 |
| OpenAI-compatible 运行时 | 可接入 OpenAI、OpenRouter、DashScope 兼容端点或本地网关。 |
| 持久会话 | 每个会话都有独立历史目录。 |
| 语义与摘要记忆 | 保存长期事实，并回忆其他会话的结论。 |
| 插件工具 | 支持文件、搜索、shell 和自定义工具。 |
| Prompt 快照 | 通过 `latest_context.json` 查看最近一次真实模型请求。 |

## 目录

- [快速开始](#快速开始)
- [配置](#配置)
- [使用](#使用)
- [REPL 命令](#repl-命令)
- [记忆与会话](#记忆与会话)
- [Prompt 快照](#prompt-快照)
- [工具与插件](#工具与插件)
- [开发](#开发)

## 快速开始

```bash
git clone <your-repo-url>
cd zzm-agent
python -m venv .venv
.\.venv\Scripts\activate
pip install -e .
pip install -r requirements.txt
```

创建 `.env`：

```bash
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=your_api_key_here
LLM_MODEL_NAME=gpt-4o-mini
```

启动 REPL：

```bash
zzm-agent
```

## 配置

默认读取 `config.yaml`。也可以通过 `--config` 或 `ZZM_AGENT_CONFIG` 指定配置文件。

| 优先级 | 来源 |
| --- | --- |
| 1 | `--config <path>` |
| 2 | `ZZM_AGENT_CONFIG` |
| 3 | `./config.yaml` |
| 4 | 仓库默认 `config.yaml` |

最小配置示例：

```yaml
model:
  base_url: "${LLM_BASE_URL}"
  api_key: "${LLM_API_KEY}"
  model_name: "${LLM_MODEL_NAME}"
  temperature: 0.7
  max_tokens: 4096

agent:
  system_prompt: "You are zzm-agent, a concise and efficient personal assistant."
  auto_approve: false
  stream: true
  tool_choice: "auto"
  plugin_dirs:
    - "zzm_agent/plugins"

memory:
  path: ".zzm_agent/memory.json"
  max_history: 50
  retrieval_top_k: 3
  max_context_tokens: 32000
  compression_keep_recent: 10
```

## 使用

| 任务 | 命令 |
| --- | --- |
| 启动默认 REPL | `zzm-agent` |
| 显式启动 REPL | `zzm-agent repl` |
| 指定或创建会话 | `zzm-agent repl --session my-session` |
| 使用自定义配置 | `zzm-agent repl --config path/to/config.yaml` |
| 运行 replay 评估 | `zzm-agent eval --suite replay` |
| 用真实 LLM 跑 smoke 评估 | `zzm-agent eval --suite smoke --llm` |
| 用真实 LLM 跑 full 评估 | `zzm-agent eval --suite full --llm` |

## REPL 命令

| 命令 | 说明 |
| --- | --- |
| `/help` | 显示帮助。 |
| `/tools` | 查看已注册工具。 |
| `/reload` | 从磁盘重新加载插件工具。 |
| `/models [filter]` | 从当前 base URL 获取模型列表。 |
| `/model [id]` | 查看或切换当前模型。 |
| `/stream [on, off, toggle, status]` | 查看或切换当前 REPL 会话的流式输出。 |
| `/memory` | 查看最近历史和上下文压缩状态。 |
| `/sessions` | 查看已知会话。 |
| `/session <id>` | 切换到指定会话。 |
| `/new` | 创建并切换到新会话。 |
| `/remember <fact>` | 添加长期语义记忆。 |
| `/forget <keyword>` | 删除匹配关键字的长期记忆。 |
| `/search <keyword>` | 检索语义记忆和跨会话摘要。 |
| `/semantic` | 列出所有长期语义记忆。 |
| `/evolve run` | 基于当前历史生成 prompt 候选。 |
| `/evolve status` | 查看最近一次 prompt 评估状态。 |
| `/evolve diff [id]` | 查看 prompt 候选 diff。 |
| `/evolve apply [id]` | 应用 prompt 候选。 |
| `/evolve rollback` | 回滚 prompt 修改。 |
| `/exit`, `/quit` | 退出 REPL。 |

## 流式输出

默认开启：

```yaml
agent:
  stream: true
```

运行时修改只影响当前 REPL 会话，不会改写 `config.yaml`：

```text
/stream status
/stream off
/stream on
/stream toggle
```

## 记忆与会话

记忆会保存在 `memory.path` 所在目录下。

```text
.zzm_agent/
|-- semantic.json
`-- sessions/
    |-- index.json
    |-- last_session.txt
    `-- <session-id>/
        |-- meta.json
        |-- history.json
        |-- episodic.json
        `-- latest_context.json
```

| 文件 | 作用 |
| --- | --- |
| `history.json` | 当前会话原始对话历史。 |
| `episodic.json` | 当前会话摘要，用于跨会话回忆。 |
| `semantic.json` | 通过 `/remember` 添加的长期事实记忆。 |
| `latest_context.json` | 每次请求模型前保存的最新完整 prompt 快照。 |

每次调用模型前，上下文按以下顺序组装：

1. 系统提示词
2. 检索到的语义记忆
3. 检索到的跨会话摘要记忆
4. 从当前轮推断的 pinned context
5. 当前会话历史，必要时压缩
6. 当前用户输入

## Prompt 快照

每次模型请求前都会写入：

```text
sessions/<session-id>/latest_context.json
```

快照包含最近一次请求的元信息和 payload：

| 字段 | 含义 |
| --- | --- |
| `created_at` | 快照创建时间。 |
| `session_id` | 当前会话 id。 |
| `model` | 当前模型名。 |
| `latest_user_input` | 当前用户输入。 |
| `stream` | 本次请求是否使用流式输出。 |
| `tool_iteration` | 工具循环轮次。 |
| `context_window` | token 和压缩元信息。 |
| `request.messages` | 实际发送给模型的完整 messages。 |
| `request.tools` | 本次请求携带的工具 schema。 |

如果一轮对话包含工具调用，后续模型请求前会再次覆盖这个文件。因此它始终代表“最新一次请求”。

## 工具与插件

工具通过 `ToolRegistry` 注册，并暴露为 OpenAI-compatible function schema。

| 模块 | 作用 |
| --- | --- |
| `file_ops` | 在工作区内读取、写入和编辑文件。 |
| `search` | 在工作区内搜索文件和文件内容。 |
| `shell` | 在工作区内执行 shell 命令。 |

插件目录在 `config.yaml` 中配置：

```yaml
agent:
  plugin_dirs:
    - "zzm_agent/plugins"
```

| 风险等级 | 含义 |
| --- | --- |
| `low` | 通常是安全的查看或只读操作。 |
| `medium` | 可能修改文件或状态的操作。 |
| `high` | 例如 shell 执行等需要更强确认的操作。 |

除非显式开启自动批准，中高风险工具默认需要确认。

## 开发

```bash
pytest tests -q
pytest tests/test_agent_loop.py -q
python -B -m compileall -q zzm_agent tests
```

## 项目结构

```text
zzm_agent/
|-- cli_support/       # CLI 运行时、渲染和 slash commands
|-- core/              # Agent loop、工具注册、错误和观测
|-- eval/              # Replay 和评估运行器
|-- evolution/         # Prompt 优化和候选管理
|-- memory/            # 会话、历史、语义记忆和摘要记忆
`-- plugins/           # 内置工具

tests/                 # Pytest 测试
docs/                  # 设计说明和变更记录
config.yaml            # 默认配置
```

## License

当前仓库还没有包含 License 文件。发布或分发前建议补充。
