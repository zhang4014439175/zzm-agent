# zzm-agent 代码参考

本文档包含 zzm-agent 各个模块的实现参考代码。

---

## 1. 配置文件与依赖

### pyproject.toml
```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "zzm-agent"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "openai>=1.30",
    "pyyaml>=6.0",
    "rich>=13.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-mock>=3.14"]

[tool.setuptools.packages.find]
where = ["."]
include = ["zzm_agent*"]
```

### config.yaml
```yaml
model:
  base_url: "http://localhost:11434/v1"
  api_key: "ollama"
  model_name: "qwen2.5:14b"
  temperature: 0.7
  max_tokens: 4096

agent:
  system_prompt: "你是 zzm-agent，一个简洁高效的个人助理。"
  plugin_dirs:
    - "zzm_agent/plugins"

memory:
  path: "~/.zzm_agent/memory.json"
  max_history: 50

evolution:
  enabled: false
  trigger: "manual"
  sample_size: 20
```

---

## 2. Tool Registry (工具注册表)

### tests/test_tool_registry.py
```python
from zzm_agent.core.tool_registry import tool, ToolRegistry


def test_tool_decorator_registers_function():
    registry = ToolRegistry()

    @registry.tool(description="加两个数")
    def add(a: int, b: int) -> int:
        return a + b

    assert "add" in registry.tools
    assert registry.tools["add"]["fn"] is add


def test_schema_generation():
    registry = ToolRegistry()

    @registry.tool(description="加两个数")
    def add(a: int, b: int) -> int:
        return a + b

    schemas = registry.get_schemas()
    assert len(schemas) == 1
    schema = schemas[0]
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "add"
    assert schema["function"]["description"] == "加两个数"
    props = schema["function"]["parameters"]["properties"]
    assert props["a"]["type"] == "integer"
    assert props["b"]["type"] == "integer"
    assert "a" in schema["function"]["parameters"]["required"]
    assert "b" in schema["function"]["parameters"]["required"]


def test_call_tool():
    registry = ToolRegistry()

    @registry.tool(description="加两个数")
    def add(a: int, b: int) -> int:
        return a + b

    result = registry.call("add", {"a": 3, "b": 4})
    assert result == 7


def test_supported_types():
    registry = ToolRegistry()

    @registry.tool(description="test")
    def fn(s: str, i: int, f: float, b: bool) -> str:
        return s

    schemas = registry.get_schemas()
    props = schemas[0]["function"]["parameters"]["properties"]
    assert props["s"]["type"] == "string"
    assert props["i"]["type"] == "integer"
    assert props["f"]["type"] == "number"
    assert props["b"]["type"] == "boolean"
```

### zzm_agent/core/tool_registry.py
```python
import importlib
import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Any, Callable

_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, dict] = {}

    def tool(self, description: str) -> Callable:
        def decorator(fn: Callable) -> Callable:
            sig = inspect.signature(fn)
            properties = {}
            required = []
            for name, param in sig.parameters.items():
                annotation = param.annotation
                json_type = _TYPE_MAP.get(annotation, "string")
                properties[name] = {"type": json_type}
                if param.default is inspect.Parameter.empty:
                    required.append(name)

            schema = {
                "type": "function",
                "function": {
                    "name": fn.__name__,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            }
            self.tools[fn.__name__] = {"fn": fn, "schema": schema}
            return fn
        return decorator

    def get_schemas(self) -> list[dict]:
        return [v["schema"] for v in self.tools.values()]

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        if name not in self.tools:
            raise KeyError(f"Tool not found: {name}")
        return self.tools[name]["fn"](**arguments)

    def load_plugin_dir(self, directory: str | Path) -> None:
        path = Path(directory).expanduser().resolve()
        if not path.exists():
            return
        for py_file in sorted(path.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module_name = f"_zzm_plugin_{py_file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

_active_registry: ToolRegistry | None = None


def set_active_registry(registry: ToolRegistry) -> None:
    global _active_registry
    _active_registry = registry


def tool(description: str) -> Callable:
    if _active_registry is None:
        raise RuntimeError("Active ToolRegistry is not set")
    return _active_registry.tool(description)
```

---

## 3. Memory Store (持久化记忆)

### tests/test_memory_store.py
```python
import pytest
from zzm_agent.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(path=tmp_path / "memory.json", max_history=10)


def test_append_and_load(store):
    msgs = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    store.append(msgs)
    loaded = store.load_history()
    assert len(loaded) == 2
    assert loaded[0]["content"] == "hello"


def test_max_history_truncation(store):
    for i in range(15):
        store.append([{"role": "user", "content": str(i)}])
    loaded = store.load_history()
    assert len(loaded) == 10
    assert loaded[-1]["content"] == "14"


def test_persists_across_instances(tmp_path):
    path = tmp_path / "memory.json"
    store1 = MemoryStore(path=path, max_history=50)
    store1.append([{"role": "user", "content": "persistent"}])
    store2 = MemoryStore(path=path, max_history=50)
    loaded = store2.load_history()
    assert loaded[0]["content"] == "persistent"


def test_empty_store_returns_empty_list(store):
    assert store.load_history() == []
```

### zzm_agent/memory/store.py
```python
import json
from pathlib import Path


class MemoryStore:
    def __init__(self, path: str | Path, max_history: int = 50):
        self.path = Path(path).expanduser()
        self.max_history = max_history
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_history(self) -> list[dict]:
        if not self.path.exists():
            return []
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        return data[-self.max_history:]

    def append(self, messages: list[dict]) -> None:
        existing = []
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                existing = json.load(f)
        existing.extend(messages)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
```

---

## 4. Agent Loop (核心循环)

### tests/test_agent_loop.py
```python
import json
import pytest
from unittest.mock import MagicMock
from zzm_agent.core.agent_loop import AgentLoop
from zzm_agent.core.tool_registry import ToolRegistry
from zzm_agent.memory.store import MemoryStore


@pytest.fixture
def registry():
    r = ToolRegistry()

    @r.tool(description="返回固定字符串")
    def echo(text: str) -> str:
        return f"ECHO:{text}"

    return r


@pytest.fixture
def store(tmp_path):
    return MemoryStore(path=tmp_path / "memory.json", max_history=10)


def make_response(content=None, tool_calls=None):
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def test_simple_reply(registry, store):
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="You are helpful.",
        registry=registry,
        store=store,
    )
    loop.client.chat.completions.create.return_value = make_response(content="Hello!")
    result = loop.run("Hi")
    assert result == "Hello!"


def test_tool_call_then_reply(registry, store):
    tool_call = MagicMock()
    tool_call.id = "call_1"
    tool_call.function.name = "echo"
    tool_call.function.arguments = json.dumps({"text": "world"})

    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="You are helpful.",
        registry=registry,
        store=store,
    )
    loop.client.chat.completions.create.side_effect = [
        make_response(tool_calls=[tool_call]),
        make_response(content="Done!"),
    ]
    result = loop.run("call echo")
    assert result == "Done!"
    assert loop.client.chat.completions.create.call_count == 2


def test_history_loaded_on_run(registry, store):
    store.append([{"role": "user", "content": "previous"}])
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="sys",
        registry=registry,
        store=store,
    )
    loop.client.chat.completions.create.return_value = make_response(content="ok")
    loop.run("new message")
    call_args = loop.client.chat.completions.create.call_args
    messages = call_args.kwargs["messages"]
    contents = [m["content"] for m in messages if m["role"] == "user"]
    assert "previous" in contents
    assert "new message" in contents
```

### zzm_agent/core/agent_loop.py
```python
import json
from openai import OpenAI
from zzm_agent.core.tool_registry import ToolRegistry
from zzm_agent.memory.store import MemoryStore


class AgentLoop:
    def __init__(
        self,
        client: OpenAI,
        model: str,
        system_prompt: str,
        registry: ToolRegistry,
        store: MemoryStore,
    ):
        self.client = client
        self.model = model
        self.system_prompt = system_prompt
        self.registry = registry
        self.store = store

    def run(self, user_input: str) -> str:
        history = self.store.load_history()
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_input})

        tools = self.registry.get_schemas()
        new_messages = [{"role": "user", "content": user_input}]

        while True:
            kwargs = {"model": self.model, "messages": messages}
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            response = self.client.chat.completions.create(**kwargs)
            msg = response.choices[0].message

            if not msg.tool_calls:
                final_reply = msg.content or ""
                new_messages.append({"role": "assistant", "content": final_reply})
                self.store.append(new_messages)
                return final_reply

            tool_calls_raw = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
            assistant_msg = {
                "role": "assistant",
                "content": msg.content,
                "tool_calls": tool_calls_raw,
            }
            messages.append(assistant_msg)
            new_messages.append(assistant_msg)

            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                    result = self.registry.call(tc.function.name, args)
                    result_str = str(result)
                except Exception as e:
                    result_str = f"Error: {e}"

                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                }
                messages.append(tool_msg)
                new_messages.append(tool_msg)
```

---

## 5. Built-in Plugins (内置插件)

### zzm_agent/plugins/shell.py
```python
import subprocess
from zzm_agent.core.tool_registry import tool


@tool(description="在本机执行 shell 命令，返回 stdout 和 stderr 合并输出（最多 4096 字符）")
def run_shell(command: str) -> str:
    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr
    return output[:4096] if output else "(no output)"
```

### zzm_agent/plugins/file_ops.py
```python
from pathlib import Path
from zzm_agent.core.tool_registry import tool


@tool(description="读取文件内容，返回文本（最多 8192 字符）")
def read_file(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"Error: file not found: {path}"
    content = p.read_text(encoding="utf-8", errors="replace")
    return content[:8192]


@tool(description="将文本写入文件，文件不存在则创建，存在则覆盖")
def write_file(path: str, content: str) -> str:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Written {len(content)} chars to {path}"
```

---

## 6. Evolution Optimizer (进化优化器)

### zzm_agent/evolution/optimizer.py
```python
from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai import OpenAI


class EvolutionOptimizer:
    def __init__(self, client: "OpenAI", config_path: str | Path, sample_size: int = 20):
        self.client = client
        self.config_path = Path(config_path)
        self.sample_size = sample_size

    def optimize(self, history: list[dict]) -> str:
        # 待实现：读取轨迹 → 模型自评 → 生成新 prompt
        return ""

    def apply(self, new_prompt: str) -> None:
        if not new_prompt:
            return
        import yaml
        with open(self.config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        cfg["agent"]["system_prompt"] = new_prompt
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True)
```

---

## 7. CLI Entry (命令行入口)

### cli.py
```python
#!/usr/bin/env python3
import sys
from pathlib import Path

import yaml
from openai import OpenAI
from rich.console import Console
from rich.markdown import Markdown

from zzm_agent.core.agent_loop import AgentLoop
from zzm_agent.core.tool_registry import ToolRegistry
from zzm_agent.memory.store import MemoryStore
from zzm_agent.evolution.optimizer import EvolutionOptimizer

console = Console()
CONFIG_PATH = Path("config.yaml")


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_registry(cfg: dict) -> ToolRegistry:
    registry = ToolRegistry()
    for plugin_dir in cfg["agent"].get("plugin_dirs", []):
        registry.load_plugin_dir(plugin_dir)
    return registry


def handle_slash(cmd: str, registry: ToolRegistry, store: MemoryStore, optimizer: EvolutionOptimizer) -> bool:
    cmd = cmd.strip()
    if cmd == "/tools":
        schemas = registry.get_schemas()
        if not schemas:
            console.print("[yellow]No tools registered.[/yellow]")
        for s in schemas:
            fn = s["function"]
            console.print(f"  [cyan]{fn['name']}[/cyan] — {fn['description']}")
        return True
    if cmd == "/memory":
        history = store.load_history()
        console.print(f"[yellow]{len(history)} messages in memory.[/yellow]")
        for m in history[-5:]:
            role = m.get("role", "?")
            content = str(m.get("content", ""))[:80]
            console.print(f"  [{role}] {content}")
        return True
    if cmd == "/evolve":
        history = store.load_history()
        console.print("[yellow]Running evolution optimizer...[/yellow]")
        new_prompt = optimizer.optimize(history)
        if new_prompt:
            optimizer.apply(new_prompt)
            console.print("[green]System prompt updated.[/green]")
        else:
            console.print("[dim]Optimizer stub: no changes.[/dim]")
        return True
    if cmd in ("/exit", "/quit"):
        console.print("[dim]Bye.[/dim]")
        sys.exit(0)
    if cmd == "/help":
        console.print("Commands: /tools  /memory  /evolve  /help  /exit")
        return True
    return False


def main():
    cfg = load_config()
    client = OpenAI(base_url=cfg["model"]["base_url"], api_key=cfg["model"]["api_key"])
    registry = build_registry(cfg)
    store = MemoryStore(path=cfg["memory"]["path"], max_history=cfg["memory"]["max_history"])
    optimizer = EvolutionOptimizer(client=client, config_path=CONFIG_PATH, sample_size=cfg["evolution"]["sample_size"])
    loop = AgentLoop(
        client=client,
        model=cfg["model"]["model_name"],
        system_prompt=cfg["agent"]["system_prompt"],
        registry=registry,
        store=store,
    )

    console.print("[bold green]zzm-agent[/bold green] started. Type [cyan]/help[/cyan] for commands.")
    console.print(f"[dim]{len(registry.get_schemas())} tools loaded.[/dim]\n")

    while True:
        try:
            user_input = console.input("[bold blue]you>[/bold blue] ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Bye.[/dim]")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            if not handle_slash(user_input, registry, store, optimizer):
                console.print(f"[red]Unknown command: {user_input}[/red]")
            continue

        try:
            reply = loop.run(user_input)
            console.print(Markdown(reply))
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")


if __name__ == "__main__":
    main()
```
