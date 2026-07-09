# zzm-agent

A local-first personal coding agent and CLI for OpenAI-compatible chat models.

`zzm-agent` gives you an interactive REPL for project work, a non-interactive `exec` mode for scripts and CI, persistent sessions, semantic memory, prompt snapshots, and plugin-based tools for files, search, and shell commands.

## Install

```bash
pip install zzm-agent
```

Requires Python 3.11 or newer.

## First Run

Start the REPL:

```bash
zzm-agent
```

On the first interactive run, `zzm-agent` asks for:

- Base URL, for example `https://api.openai.com/v1`
- Model name, for example `gpt-4o-mini`
- LLM API key

It creates user-level configuration:

```text
~/.zzm_agent/config.yaml
~/.zzm_agent/.env
```

Secrets are written to `.env`; `config.yaml` keeps references such as `${LLM_API_KEY}`.

You can also use existing environment variables:

```bash
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL_NAME=gpt-4o-mini
LLM_API_KEY=your_api_key_here
```

## Usage

Interactive REPL:

```bash
zzm-agent
zzm-agent repl --session my-session
zzm-agent repl --config path/to/config.yaml
```

Non-interactive one-shot tasks:

```bash
zzm-agent exec "summarize this repository"
zzm-agent exec --stdin < prompt.txt
zzm-agent exec --json "review the latest changes"
zzm-agent exec -o answer.md "write release notes"
```

In `exec` mode, medium and high risk tools are denied instead of prompting, so CI jobs and scripts do not hang on interactive approvals.

Shell completion:

```bash
zzm-agent completion bash
zzm-agent completion zsh
zzm-agent completion powershell
```

## Configuration

Config resolution:

1. `--config <path>`
2. `ZZM_AGENT_CONFIG`
3. `~/.zzm_agent/config.yaml`
4. repository `config.yaml`
5. `./config.yaml`
6. `./.zzm_agent/config.local.yaml`

`.env` files are loaded from the current directory and next to each loaded config file before `${ENV_NAME}` placeholders are expanded.

Minimal config:

```yaml
model:
  base_url: "${LLM_BASE_URL:-https://api.openai.com/v1}"
  api_key: "${LLM_API_KEY}"
  model_name: "${LLM_MODEL_NAME:-gpt-4o-mini}"

agent:
  stream: true
  auto_approve: false

memory:
  path: "~/.zzm_agent/memory.json"
  max_history: 50
```

## REPL Commands

Useful commands inside the REPL:

- `/help`
- `/tools`
- `/models [filter]`
- `/model [id]`
- `/config`
- `/memory`
- `/sessions`
- `/session <id>`
- `/new`
- `/remember <fact>`
- `/search <keyword>`
- `/evolve run`
- `/exit`

## Project Links

Source repository and full documentation are available in the project README.
