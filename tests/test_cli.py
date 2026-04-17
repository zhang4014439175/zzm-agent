from cli import handle_slash, load_config, parse_args
from zzm_agent.core.tool_registry import ToolRegistry
from zzm_agent.memory.store import MemoryStore


class DummyRegistry:
    def get_schemas(self):
        return []


class DummyOptimizer:
    def optimize(self, history):
        return ""

    def apply(self, new_prompt):
        return None


class DummyConsole:
    def __init__(self):
        self.lines = []
        self.inputs = []

    def print(self, *args, **kwargs):
        self.lines.append(" ".join(str(arg) for arg in args))

    def input(self, prompt):
        self.lines.append(str(prompt))
        if self.inputs:
            return self.inputs.pop(0)
        return ""


def test_parse_args_supports_session_flag():
    args = parse_args(["--session", "alpha"])
    assert args.session_id == "alpha"
    assert args.config_path is None


def test_parse_args_supports_config_flag():
    args = parse_args(["--config", "custom.yaml"])
    assert args.config_path == "custom.yaml"


def test_parse_args_supports_safe_flag():
    args = parse_args(["--safe"])
    assert args.safe is True


def test_load_config_expands_env_placeholders(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "model:\n"
        '  api_key: "${ZZM_AGENT_API_KEY}"\n'
        '  base_url: "https://example.com"\n'
        '  model_name: "demo"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ZZM_AGENT_API_KEY", "secret")

    cfg = load_config(config_path)

    assert cfg["model"]["api_key"] == "secret"


def test_handle_slash_new_and_switch_session(tmp_path):
    # The slash-command layer is responsible for wiring operator intent into
    # MemoryStore state changes without needing a live model client.
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50)
    console = DummyConsole()
    registry = DummyRegistry()
    optimizer = DummyOptimizer()

    initial_session = store.session_id
    assert handle_slash("/new", registry, store, optimizer, console) is True
    assert store.session_id != initial_session

    created_session = store.session_id
    assert (
        handle_slash(f"/session {initial_session}", registry, store, optimizer, console)
        is True
    )
    assert store.session_id == initial_session

    assert handle_slash("/sessions", registry, store, optimizer, console) is True
    assert any(created_session in line for line in console.lines)


def test_handle_slash_memory_mentions_current_session(tmp_path):
    # `/memory` output should expose the active session id so users can verify
    # which conversation they are inspecting after switching sessions.
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50, session_id="alpha")
    store.append([{"role": "user", "content": "hello"}])
    console = DummyConsole()

    handled = handle_slash("/memory", DummyRegistry(), store, DummyOptimizer(), console)

    assert handled is True
    assert any("alpha" in line for line in console.lines)


def test_handle_slash_memory_shows_compression_summary_when_active(tmp_path):
    store = MemoryStore(
        path=tmp_path / "memory.json",
        max_history=50,
        session_id="alpha",
        max_context_tokens=35,
        compression_keep_recent=1,
    )
    store.append(
        [
            {"role": "user", "content": "A" * 80},
            {"role": "assistant", "content": "B" * 80},
            {"role": "user", "content": "recent"},
        ]
    )
    console = DummyConsole()

    handled = handle_slash("/memory", DummyRegistry(), store, DummyOptimizer(), console)

    assert handled is True
    assert any("Context compression active" in line for line in console.lines)
    assert any("Runtime compression summary" in line for line in console.lines)


def test_handle_slash_remember_and_forget(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50, session_id="alpha")
    console = DummyConsole()

    assert (
        handle_slash(
            "/remember User prefers concise answers.",
            DummyRegistry(),
            store,
            DummyOptimizer(),
            console,
        )
        is True
    )
    assert store.load_semantic_memory()[0]["fact"] == "User prefers concise answers."

    assert (
        handle_slash(
            "/forget concise",
            DummyRegistry(),
            store,
            DummyOptimizer(),
            console,
        )
        is True
    )
    assert store.load_semantic_memory() == []


def test_handle_slash_semantic_lists_all_long_term_memories(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50, session_id="alpha")
    store.remember_fact("User prefers concise answers.")
    store.remember_fact("Project language is Python.")
    console = DummyConsole()

    handled = handle_slash("/semantic", DummyRegistry(), store, DummyOptimizer(), console)

    assert handled is True
    assert any("2 long-term memories" in line for line in console.lines)
    assert any("Project language is Python." in line for line in console.lines)
    assert any("User prefers concise answers." in line for line in console.lines)


def test_handle_slash_search_lists_memory_matches(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50, session_id="alpha")
    store.remember_fact("Project language is Python.")
    store.append(
        [
            {"role": "user", "content": "What should we build?"},
            {"role": "assistant", "content": "Build the Python CLI first."},
        ]
    )
    store.create_session(make_current=True)
    console = DummyConsole()

    handled = handle_slash("/search python", DummyRegistry(), store, DummyOptimizer(), console)

    assert handled is True
    assert any("Memory matches for 'python'" in line for line in console.lines)
    assert any("Project language is Python." in line for line in console.lines)
    assert any("Python CLI first" in line for line in console.lines)


def test_handle_slash_reload_reports_plugin_changes(tmp_path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    plugin_file = plugin_dir / "demo.py"
    plugin_file.write_text(
        "from zzm_agent.core.tool_registry import tool\n\n"
        '@tool(description="first version")\n'
        "def alpha(text: str) -> str:\n"
        "    return text\n",
        encoding="utf-8",
    )

    registry = ToolRegistry()
    registry.configure_plugin_dirs([plugin_dir])
    registry.load_configured_plugins()

    plugin_file.write_text(
        "from zzm_agent.core.tool_registry import tool\n\n"
        '@tool(description="second version")\n'
        "def alpha(text: str) -> str:\n"
        "    return text\n\n"
        '@tool(description="new tool")\n'
        "def beta() -> str:\n"
        '    return "ok"\n',
        encoding="utf-8",
    )

    console = DummyConsole()
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50, session_id="alpha")

    handled = handle_slash("/reload", registry, store, DummyOptimizer(), console)

    assert handled is True
    assert any("Plugins reloaded." in line for line in console.lines)
    assert any("added" in line and "beta" in line for line in console.lines)
    assert any("updated" in line and "alpha" in line for line in console.lines)


def test_tools_command_reflects_updated_plugin_description_after_reload(tmp_path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    plugin_file = plugin_dir / "demo.py"
    plugin_file.write_text(
        "from zzm_agent.core.tool_registry import tool\n\n"
        '@tool(description="first version")\n'
        "def alpha(text: str) -> str:\n"
        "    return text\n",
        encoding="utf-8",
    )

    registry = ToolRegistry()
    registry.configure_plugin_dirs([plugin_dir])
    registry.load_configured_plugins()

    plugin_file.write_text(
        "from zzm_agent.core.tool_registry import tool\n\n"
        '@tool(description="updated version")\n'
        "def alpha(text: str) -> str:\n"
        "    return text\n",
        encoding="utf-8",
    )

    store = MemoryStore(path=tmp_path / "memory.json", max_history=50, session_id="alpha")
    console = DummyConsole()

    assert handle_slash("/reload", registry, store, DummyOptimizer(), console) is True
    console.lines.clear()

    assert handle_slash("/tools", registry, store, DummyOptimizer(), console) is True
    assert any("updated version" in line for line in console.lines)
