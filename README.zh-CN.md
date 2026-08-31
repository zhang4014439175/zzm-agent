<div align="center">

# zzm-agent

**面向本地项目工作的个人编码 Agent 和命令行 REPL。**

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![OpenAI Compatible](https://img.shields.io/badge/API-OpenAI%20Compatible-111827?style=flat-square)](https://platform.openai.com/docs/api-reference)
[![CLI](https://img.shields.io/badge/Interface-REPL-2563EB?style=flat-square)](#使用)
[![Memory](https://img.shields.io/badge/Memory-Session%20%2B%20Semantic-7C3AED?style=flat-square)](#记忆与会话)

[English](README.md) | [简体中文](README.zh-CN.md)

</div>

---

`zzm-agent` 适合在本地代码项目里持续使用：你可以和模型对话，让它通过工具查看、搜索、编辑工作区，跨会话保留有用记忆，并在调试时查看真实发送给模型的完整 prompt payload。

## 功能概览

| 能力 | 价值 |
| --- | --- |
| 交互式 REPL | 像结对工作一样持续和 Agent 对话。 |
| 非交互 exec | 在脚本、CI 或 shell 管道里执行一次性 Agent 任务。 |
| OpenAI-compatible 运行时 | 可接入 OpenAI、OpenRouter、DashScope 兼容端点或本地网关。 |
| 首次启动配置引导 | 第一次运行时自动创建用户配置并提示填写模型凭证。 |
| 持久会话 | 每个会话都有独立历史目录。 |
| 语义与情景记忆 | 保存长期事实，并回忆其他会话的摘要。 |
| 插件工具 | 支持文件、搜索、shell 和自定义工具。 |
| Prompt 快照 | 通过 `latest_context.json` 查看最近一次真实模型请求。 |

## 目录

- [快速开始](#快速开始)
- [配置](#配置)
- [使用](#使用)
- [非交互 Exec](#非交互-exec)
- [REPL 命令](#repl-命令)
- [记忆与会话](#记忆与会话)
- [Prompt 快照](#prompt-快照)
- [工具与插件](#工具与插件)
- [开发](#开发)

## 快速开始

从 PyPI 安装：

```bash
pip install zzm-agent
zzm-agent
```

首次交互式运行时，`zzm-agent` 会提示填写：

- Base URL，例如 `https://api.openai.com/v1`
- 模型名，例如 `gpt-4o-mini`
- LLM API Key

随后会自动创建：

```text
~/.zzm_agent/config.yaml
~/.zzm_agent/.env
```

API Key 会写入 `.env`；`config.yaml` 中只保留 `${LLM_API_KEY}` 这类环境变量引用。

从源码本地开发：

```bash
git clone <your-repo-url>
cd zzm-agent
python -m venv .venv
.\.venv\Scripts\activate
pip install -e .
pip install -r requirements.txt
zzm-agent
```

## 配置

`zzm-agent` 会先加载 YAML 配置和 `.env` 文件，再展开 `${ENV_NAME}` 占位符。你可以通过 `--config` 或 `ZZM_AGENT_CONFIG` 指定配置文件。

| 优先级 | 来源 |
| --- | --- |
| 1 | `--config <path>` |
| 2 | `ZZM_AGENT_CONFIG` |
| 3 | `~/.zzm_agent/config.yaml` |
| 4 | 仓库默认 `config.yaml` |
| 5 | `./config.yaml` |
| 6 | `./.zzm_agent/config.local.yaml` |

环境变量会从这些位置加载：

1. `./.env`
2. 每个配置文件同目录下的 `.env`，例如 `~/.zzm_agent/.env`

最小配置示例：

```yaml
model:
  base_url: "${LLM_BASE_URL:-https://api.openai.com/v1}"
  api_key: "${LLM_API_KEY}"
  model_name: "${LLM_MODEL_NAME:-gpt-4o-mini}"
  # 使用 OpenRouter 时，新请求会以此 URL 和名称显示在调用记录中。
  openrouter_referer: "${OPENROUTER_APP_URL:-https://github.com/zhang4014439175/zzm-agent}"
  openrouter_title: "${OPENROUTER_APP_NAME:-zzm-agent}"
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
  path: "~/.zzm_agent/memory.json"
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
| 恢复或创建指定会话 | `zzm-agent repl --session my-session` |
| 使用自定义配置 | `zzm-agent repl --config path/to/config.yaml` |
| 运行一次性非交互任务 | `zzm-agent exec "review this repo"` |
| 从 stdin 读取任务 | `type prompt.txt \| zzm-agent exec --stdin` |
| 输出 JSONL 事件 | `zzm-agent exec --json "summarize changes"` |
| 将最终回答写入文件 | `zzm-agent exec -o answer.md "write release notes"` |
| 输出 shell 补全脚本 | `zzm-agent completion powershell` |
| 运行 replay 评估 | `zzm-agent eval --suite replay` |
| 使用真实 LLM 运行 smoke 评估 | `zzm-agent eval --suite smoke --llm` |
| 使用真实 LLM 运行 full 评估 | `zzm-agent eval --suite full --llm` |

在 `exec` 模式下，中高风险工具会被拒绝，而不是进入交互式确认。这样 CI 和脚本不会因为等待用户授权而挂起。

## 非交互 Exec

`exec` 适合自动化、CI 检查、shell 脚本和编辑器集成：

```bash
zzm-agent exec "summarize the current project"
zzm-agent exec --stdin < prompt.txt
zzm-agent exec --json "review the latest changes"
zzm-agent exec -o report.md "write a concise project report"
```

使用 `--json` 时，输出格式为 JSONL：

```jsonl
{"type":"event","kind":"status","text":"turn.started","metadata":{"response_language":"zh-CN","language_source":"config"}}
{"type":"result","reply":"最终回答","response_language":"zh-CN","language_source":"config"}
```

## REPL 命令

| 命令 | 说明 |
| --- | --- |
| `/help` | 显示帮助。 |
| `/tools` | 查看已注册工具。 |
| `/reload` | 从磁盘重新加载插件工具。 |
| `/models [filter]` | 从当前 base URL 获取模型列表。 |
| `/model [id]` | 查看或切换当前模型。 |
| `/config` | 查看当前生效配置和来源。 |
| `/stream [on, off, toggle, status]` | 查看或切换当前 REPL 会话的流式输出。 |
| `/memory` | 查看最近历史和上下文压缩状态。 |
| `/sessions` | 查看已知会话。 |
| `/session <id>` | 切换到指定会话。 |
| `/new` | 创建并切换到新会话。 |
| `/remember <fact>` | 添加长期语义记忆。 |
| `/forget <keyword>` | 删除匹配关键词的长期记忆。 |
| `/search <keyword>` | 检索语义记忆和跨会话摘要。 |
| `/semantic` | 列出所有长期语义记忆。 |
| `/evolve run` | 基于当前历史生成 prompt 候选。 |
| `/evolve status` | 查看最近一次 prompt 评估状态。 |
| `/evolve diff [id]` | 查看 prompt 候选 diff。 |
| `/evolve apply [id]` | 应用 prompt 候选。 |
| `/evolve rollback` | 回滚 prompt 修改。 |
| `/exit`, `/quit` | 退出 REPL。 |

## 流式输出

默认开启流式输出：

```yaml
agent:
  stream: true
```

运行时修改只影响当前 REPL 会话：

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
| `latest_context.json` | 每次模型调用前保存的完整 prompt 快照。 |

上下文组装顺序：

1. System prompt
2. 检索到的语义记忆
3. 检索到的情景记忆
4. 从当前轮推断出的 pinned context
5. 当前会话历史，必要时压缩
6. 当前用户输入

## Prompt 快照

每次模型请求前，当前会话都会写入：

```text
sessions/<session-id>/latest_context.json
```

快照包含最近一次请求的元数据和 payload：

| 字段 | 含义 |
| --- | --- |
| `created_at` | 快照创建时间。 |
| `session_id` | 当前会话 ID。 |
| `model` | 当前模型名。 |
| `latest_user_input` | 当前用户输入。 |
| `stream` | 是否使用流式请求。 |
| `tool_iteration` | 工具循环轮次。 |
| `context_window` | Token 与压缩元数据。 |
| `request.messages` | 发送给模型的完整消息。 |
| `request.tools` | 本次请求包含的工具 schema。 |

如果一轮对话包含工具调用，该文件会在每次后续模型请求前覆盖写入，始终代表最近一次请求。

## 工具与插件

工具通过 `ToolRegistry` 注册，并以 OpenAI-compatible function schemas 暴露给模型。

| 模块 | 作用 |
| --- | --- |
| `file_ops` | 读取、写入和编辑工作区内文件。 |
| `search` | 搜索工作区内文件和文件内容。 |
| `shell` | 在工作区内执行 shell 命令。 |

插件目录在 `config.yaml` 中配置。首次运行生成的用户配置会自动指向已安装包里的内置插件目录；自定义配置可以追加更多目录：

```yaml
agent:
  plugin_dirs:
    - "zzm_agent/plugins"
```

| 风险级别 | 含义 |
| --- | --- |
| `low` | 通常是安全检查或只读操作。 |
| `medium` | 可能修改文件或状态的操作。 |
| `high` | shell 执行等需要更强确认的操作。 |

除非显式自动批准，否则中高风险工具会要求确认。

## 开发

```bash
pytest tests -q
pytest tests/test_agent_loop.py -q
python -B -m compileall -q zzm_agent tests
```

## 项目结构

```text
zzm_agent/
|-- cli_support/       # CLI runtime、渲染和 slash commands
|-- core/              # Agent loop、工具注册、错误和观测
|-- eval/              # Replay 和评估运行器
|-- evolution/         # Prompt 优化和候选管理
|-- memory/            # 会话、历史、语义和情景记忆
`-- plugins/           # 内置工具

tests/                 # Pytest 测试套件
docs/                  # 设计说明和变更记录
config.yaml            # 默认配置
```

## License

当前仓库尚未包含 license 文件。正式发布或分发前建议补充。
