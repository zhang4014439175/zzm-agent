<div align="center">

# zzm-agent

**A local-first personal coding agent and REPL for OpenAI-compatible chat models.**

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![OpenAI Compatible](https://img.shields.io/badge/API-OpenAI%20Compatible-111827?style=flat-square)](https://platform.openai.com/docs/api-reference)
[![CLI](https://img.shields.io/badge/Interface-REPL-2563EB?style=flat-square)](#usage)
[![Memory](https://img.shields.io/badge/Memory-Session%20%2B%20Semantic-7C3AED?style=flat-square)](#memory-and-sessions)

[English](README.md) · [简体中文](README.zh-CN.md)

</div>

---

`zzm-agent` is built for hands-on project work: talk to a model, let it inspect and edit your workspace through tools, keep useful memories across sessions, and inspect the exact prompt payload sent to the model when debugging.

## At A Glance

| You get | Why it matters |
| --- | --- |
| Interactive REPL | Work with the agent continuously instead of sending one-off prompts. |
| OpenAI-compatible runtime | Use OpenAI, OpenRouter, DashScope-compatible endpoints, or local gateways. |
| Persistent sessions | Keep each conversation's history under its own session directory. |
| Semantic and episodic memory | Store explicit facts and recall summaries from previous sessions. |
| Tool plugins | Add file, search, shell, or custom tools with generated schemas. |
| Prompt snapshots | Inspect `latest_context.json` to see the exact latest model request. |

## Contents

- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Usage](#usage)
- [REPL Commands](#repl-commands)
- [Memory and Sessions](#memory-and-sessions)
- [Prompt Snapshots](#prompt-snapshots)
- [Tools and Plugins](#tools-and-plugins)
- [Development](#development)

## Quick Start

```bash
git clone <your-repo-url>
cd zzm-agent
python -m venv .venv
.\.venv\Scripts\activate
pip install -e .
pip install -r requirements.txt
```

Create `.env`:

```bash
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=your_api_key_here
LLM_MODEL_NAME=gpt-4o-mini
```

Start the REPL:

```bash
zzm-agent
```

## Configuration

`zzm-agent` loads `config.yaml` by default. You can override it with `--config` or `ZZM_AGENT_CONFIG`.

| Priority | Source |
| --- | --- |
| 1 | `--config <path>` |
| 2 | `ZZM_AGENT_CONFIG` |
| 3 | `./config.yaml` |
| 4 | Repository default `config.yaml` |

Minimal config:

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

## Usage

| Task | Command |
| --- | --- |
| Start the default REPL | `zzm-agent` |
| Start explicitly | `zzm-agent repl` |
| Resume or create a session | `zzm-agent repl --session my-session` |
| Use a custom config | `zzm-agent repl --config path/to/config.yaml` |
| Run replay eval | `zzm-agent eval --suite replay` |
| Run smoke eval with real LLM | `zzm-agent eval --suite smoke --llm` |
| Run full eval with real LLM | `zzm-agent eval --suite full --llm` |

## REPL Commands

| Command | Description |
| --- | --- |
| `/help` | Show command help. |
| `/tools` | List registered tools. |
| `/reload` | Reload plugin tools from disk. |
| `/models [filter]` | List models from the configured base URL. |
| `/model [id]` | Show or switch the active model. |
| `/stream [on, off, toggle, status]` | Show or change streaming output for this REPL session. |
| `/memory` | Show recent history and compression state. |
| `/sessions` | List known conversation sessions. |
| `/session <id>` | Switch to a specific session. |
| `/new` | Start a clean session. |
| `/remember <fact>` | Add a long-term semantic memory fact. |
| `/forget <keyword>` | Remove semantic memories matching a keyword. |
| `/search <keyword>` | Search semantic and episodic memories. |
| `/semantic` | List all semantic memory facts. |
| `/evolve run` | Generate a prompt candidate from current history. |
| `/evolve status` | Show the latest prompt evaluation status. |
| `/evolve diff [id]` | Show a prompt candidate diff. |
| `/evolve apply [id]` | Apply a prompt candidate. |
| `/evolve rollback` | Roll back prompt changes. |
| `/exit`, `/quit` | Exit the REPL. |

## Streaming

Streaming is enabled by default:

```yaml
agent:
  stream: true
```

Runtime changes apply only to the current REPL session:

```text
/stream status
/stream off
/stream on
/stream toggle
```

## Memory and Sessions

Memory is stored under the directory that contains `memory.path`.

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

| File | Purpose |
| --- | --- |
| `history.json` | Raw transcript for the active session. |
| `episodic.json` | Lightweight session summary for cross-session recall. |
| `semantic.json` | Explicit long-term facts added with `/remember`. |
| `latest_context.json` | Latest full model prompt snapshot saved before each model call. |

Context is assembled in this order:

1. System prompt
2. Retrieved semantic memory
3. Retrieved episodic memory
4. Pinned context inferred from the current turn
5. Current session history, compressed if needed
6. Current user input

## Prompt Snapshots

Before every model request, the active session receives:

```text
sessions/<session-id>/latest_context.json
```

The snapshot includes the latest request metadata and payload:

| Field | Meaning |
| --- | --- |
| `created_at` | Snapshot creation time. |
| `session_id` | Active session id. |
| `model` | Active model name. |
| `latest_user_input` | Current user input. |
| `stream` | Whether the request used streaming. |
| `tool_iteration` | Tool-loop iteration number. |
| `context_window` | Token and compression metadata. |
| `request.messages` | Full message payload sent to the model. |
| `request.tools` | Tool schemas included in the request. |

If a turn includes tool calls, the file is overwritten before each follow-up model call. It always represents the latest request.

## Tools and Plugins

Tools are registered through `ToolRegistry` and exposed as OpenAI-compatible function schemas.

| Module | Purpose |
| --- | --- |
| `file_ops` | Read, write, and edit files inside the workspace. |
| `search` | Search files and file contents inside the workspace. |
| `shell` | Run shell commands inside the workspace. |

Configure plugin directories in `config.yaml`:

```yaml
agent:
  plugin_dirs:
    - "zzm_agent/plugins"
```

| Risk level | Meaning |
| --- | --- |
| `low` | Usually safe inspection or read-only work. |
| `medium` | Actions that may modify files or state. |
| `high` | Actions such as shell execution that require stronger confirmation. |

Medium and high risk tools require confirmation unless explicitly auto-approved.

## Development

```bash
pytest tests -q
pytest tests/test_agent_loop.py -q
python -B -m compileall -q zzm_agent tests
```

## Project Structure

```text
zzm_agent/
|-- cli_support/       # CLI runtime, rendering, and slash commands
|-- core/              # Agent loop, tool registry, errors, observability
|-- eval/              # Replay and evaluation runner
|-- evolution/         # Prompt optimization and candidate management
|-- memory/            # Sessions, history, semantic and episodic memory
`-- plugins/           # Built-in tools

tests/                 # Pytest suite
docs/                  # Design notes and change records
config.yaml            # Default configuration
```

## License

No license file is currently included. Add one before publishing or distributing the project.
