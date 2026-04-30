from zzm_agent.cli_support.commands import handle_slash
from zzm_agent.cli_support.observability import CliObserver
from zzm_agent.cli_support.rendering import SlashCommandCompleter, build_bottom_toolbar
from zzm_agent.cli_support.runtime import (
    build_tool_confirmation_callback,
    _ask_tool_approval_choice,
    _config_bool,
    get_agent_loop_policy,
    load_config,
    parse_args,
)
from zzm_agent.core.model_metadata import resolve_model_context_limit
from zzm_agent.core.observability import TokenUsage, tool_end_event, tool_start_event
from zzm_agent.core.tool_registry import ToolRegistry
from zzm_agent.memory.store import MemoryStore


class DummyRegistry:
    def get_schemas(self):
        return []


class DummyOptimizer:
    def __init__(self):
        self.candidate = None
        self.diff_text = ""
        self.applied = None
        self.restored = None

    def run(self, history):
        return self.candidate

    def optimize(self, history):
        return ""

    def apply(self, new_prompt):
        return None

    def apply_candidate(self, candidate_id=None):
        return self.applied

    def diff(self, candidate_id=None):
        return self.diff_text

    def rollback(self):
        return self.restored

    def get_latest_evaluation(self):
        return None

    def evaluate(self, history):
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


def test_tool_approval_choice_default_is_valid(monkeypatch):
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "prompt_toolkit":
            raise ImportError
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    assert _ask_tool_approval_choice(DummyConsole()) == "3"


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


def test_slash_command_completer_highlights_selected_command():
    pytest = __import__("pytest")
    document_module = pytest.importorskip("prompt_toolkit.document")
    completion_module = pytest.importorskip("prompt_toolkit.completion")

    completer = SlashCommandCompleter({"/help": "Show help"})

    completions = list(
        completer.get_completions(
            document_module.Document("/"),
            completion_module.CompleteEvent(),
        )
    )

    assert completions[0].text == "/help"
    assert completions[0].display_meta_text == "Show help"
    assert completions[0].style == ""
    assert completions[0].selected_style == ""


def test_slash_command_completer_uses_prefix_fuzzy_matching():
    pytest = __import__("pytest")
    document_module = pytest.importorskip("prompt_toolkit.document")
    completion_module = pytest.importorskip("prompt_toolkit.completion")

    completer = SlashCommandCompleter({
        "/search": "Find memories",
        "/session": "Switch sessions",
        "/memory": "Find memories",
    })

    completions = list(
        completer.get_completions(
            document_module.Document("/srch"),
            completion_module.CompleteEvent(),
        )
    )

    assert [completion.text for completion in completions] == ["/search"]


def test_slash_command_completer_does_not_match_description_or_middle():
    pytest = __import__("pytest")
    document_module = pytest.importorskip("prompt_toolkit.document")
    completion_module = pytest.importorskip("prompt_toolkit.completion")

    completer = SlashCommandCompleter({
        "/search": "Find memories",
        "/memory": "Search previous messages",
    })

    description_matches = list(
        completer.get_completions(
            document_module.Document("/previous"),
            completion_module.CompleteEvent(),
        )
    )
    middle_matches = list(
        completer.get_completions(
            document_module.Document("/ear"),
            completion_module.CompleteEvent(),
        )
    )

    assert description_matches == []
    assert middle_matches == []


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


def test_config_bool_accepts_common_values():
    assert _config_bool(True, default=False) is True
    assert _config_bool("off", default=True) is False
    assert _config_bool("yes", default=False) is True
    assert _config_bool(None, default=True) is True


def test_model_context_limit_prefers_explicit_config():
    resolved = resolve_model_context_limit({
        "model": {"context_window_tokens": 64000},
        "memory": {"max_context_tokens": 32000},
    })

    assert resolved.tokens == 64000
    assert resolved.source == "config"


def test_model_context_limit_reads_openrouter_models(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return (
                b'{"data":[{"id":"tencent/hy3-preview:free",'
                b'"context_length":131072,'
                b'"top_provider":{"context_length":65536}}]}'
            )

    def fake_urlopen(request, timeout):
        assert "models" in request.full_url
        return FakeResponse()

    monkeypatch.setattr("zzm_agent.core.model_metadata.urlopen", fake_urlopen)

    resolved = resolve_model_context_limit({
        "model": {
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "key",
            "model_name": "tencent/hy3-preview:free",
        },
        "memory": {"max_context_tokens": 32000},
    })

    assert resolved.tokens == 131072
    assert resolved.source == "openrouter"


def test_model_context_limit_falls_back_to_memory_config(monkeypatch):
    def fail_urlopen(request, timeout):
        raise OSError("offline")

    monkeypatch.setattr("zzm_agent.core.model_metadata.urlopen", fail_urlopen)

    resolved = resolve_model_context_limit({
        "model": {
            "base_url": "https://openrouter.ai/api/v1",
            "model_name": "missing/model",
        },
        "memory": {"max_context_tokens": 24000},
    })

    assert resolved.tokens == 24000
    assert resolved.source == "memory"


def test_agent_loop_policy_uses_defaults_for_legacy_config():
    policy = get_agent_loop_policy({"agent": {}})

    assert policy == {
        "max_tool_iterations": 20,
        "duplicate_tool_call_limit": 3,
        "max_tool_retries": 1,
    }


def test_agent_loop_policy_reads_configured_values():
    policy = get_agent_loop_policy({
        "agent": {
            "max_tool_iterations": 8,
            "duplicate_tool_call_limit": 2,
            "max_tool_retries": 4,
        }
    })

    assert policy == {
        "max_tool_iterations": 8,
        "duplicate_tool_call_limit": 2,
        "max_tool_retries": 4,
    }


def test_agent_loop_policy_clamps_values_to_at_least_one():
    policy = get_agent_loop_policy({
        "agent": {
            "max_tool_iterations": 0,
            "duplicate_tool_call_limit": -5,
            "max_tool_retries": -1,
        }
    })

    assert policy == {
        "max_tool_iterations": 1,
        "duplicate_tool_call_limit": 1,
        "max_tool_retries": 0,
    }


def test_bottom_toolbar_shows_model_context_usage_only(tmp_path, monkeypatch):
    pytest = __import__("pytest")
    pytest.importorskip("prompt_toolkit.formatted_text")

    class DummyLoop:
        model = "demo-model"
        last_context_window = {"total_tokens": 1200, "max_context_tokens": 64000}
        last_turn_usage = TokenUsage(prompt_tokens=1000, completion_tokens=80, total_tokens=1080)
        cumulative_usage = TokenUsage(prompt_tokens=2000, completion_tokens=160, total_tokens=2160)

    store = MemoryStore(path=tmp_path / "memory.json", max_history=1, max_context_tokens=64000)
    monkeypatch.setenv("ZZM_AGENT_WORKSPACE_ROOT", str(tmp_path))

    toolbar = build_bottom_toolbar({"loop": DummyLoop(), "store": store})
    rendered = str(toolbar)

    assert "Context:" in rendered
    assert "1000/64000" in rendered
    assert "Ctx:" not in rendered
    assert "Last:" not in rendered
    assert "Session:" not in rendered


def test_tool_confirmation_supports_allow_once_choice():
    console = DummyConsole()
    console.inputs = ["1"]
    confirm = build_tool_confirmation_callback(console)

    assert confirm("wipe", {"target": "demo"}, "high") is True
    assert any("Tool approval required" in line for line in console.lines)
    assert any("Allow once" in line and "Deny" in line for line in console.lines)


def test_tool_confirmation_supports_session_allow_choice():
    console = DummyConsole()
    console.inputs = ["2"]
    confirm = build_tool_confirmation_callback(console)

    assert confirm("wipe", {"target": "demo"}, "high") is True
    assert confirm("wipe", {"target": "demo"}, "high") is True
    assert console.inputs == []
    assert any("remembered approval" in line for line in console.lines)


def test_tool_confirmation_denies_by_default():
    console = DummyConsole()
    confirm = build_tool_confirmation_callback(console)

    assert confirm("wipe", {"target": "demo"}, "high") is False


def test_stream_command_reports_and_updates_runtime_state(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50)
    console = DummyConsole()
    runtime = {"stream": True}

    assert handle_slash("/stream", DummyRegistry(), store, DummyOptimizer(), console, runtime) is True
    assert any("Streaming:" in line and "on" in line for line in console.lines)

    assert handle_slash("/stream off", DummyRegistry(), store, DummyOptimizer(), console, runtime) is True
    assert runtime["stream"] is False

    assert handle_slash("/stream toggle", DummyRegistry(), store, DummyOptimizer(), console, runtime) is True
    assert runtime["stream"] is True


def test_stream_command_handles_missing_runtime(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50)
    console = DummyConsole()

    assert handle_slash("/stream off", DummyRegistry(), store, DummyOptimizer(), console) is True
    assert any("unavailable" in line for line in console.lines)


def test_cli_observer_collects_file_edit_diff(tmp_path):
    path = tmp_path / "demo.txt"
    path.write_text("old\n", encoding="utf-8")
    observer = CliObserver(DummyConsole(), workspace_root=tmp_path)

    observer.on_tool_start(
        tool_start_event(
            tool_name="file_edit",
            tool_call_id="call_1",
            arguments={"path": "demo.txt", "target": "old", "replacement": "new"},
            risk_level="medium",
        )
    )
    path.write_text("new\n", encoding="utf-8")
    observer.on_tool_end(
        tool_end_event(
            tool_name="file_edit",
            tool_call_id="call_1",
            arguments={"path": "demo.txt", "target": "old", "replacement": "new"},
            risk_level="medium",
            status="success",
            duration_ms=1.0,
            result="ok",
            attempts=1,
        )
    )

    assert len(observer._diffs) == 1
    assert "-old" in observer._diffs[0][1]
    assert "+new" in observer._diffs[0][1]


def test_cli_observer_renders_usage_with_configured_pricing():
    console = DummyConsole()
    observer = CliObserver(
        console,
        workspace_root=".",
        input_price_per_1m=1.0,
        output_price_per_1m=2.0,
    )

    observer.render_usage(
        TokenUsage(prompt_tokens=1000, completion_tokens=1000, total_tokens=2000, source="api"),
        TokenUsage(prompt_tokens=1000, completion_tokens=1000, total_tokens=2000, source="api"),
    )

    assert console.lines


def test_cli_observer_finish_turn_does_not_render_usage_table():
    console = DummyConsole()
    observer = CliObserver(console, workspace_root=".")

    observer.finish_turn(
        TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15, source="api"),
        TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15, source="api"),
    )

    assert console.lines == []


def test_cli_observer_finish_turn_does_not_render_context_status():
    console = DummyConsole()
    observer = CliObserver(console, workspace_root=".")

    observer.finish_turn(
        TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15, source="api"),
        TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15, source="api"),
        context_window={
            "total_tokens": 120,
            "max_context_tokens": 32000,
            "tool_schema_tokens": 30,
            "applied": False,
            "compression_strategy": "none",
        },
    )

    assert console.lines == []


def test_cli_observer_edit_summary_colors_counts():
    pytest = __import__("pytest")
    text_module = pytest.importorskip("rich.text")
    observer = CliObserver(DummyConsole(), workspace_root=".")

    summary = observer._format_edit_summary(
        observer.workspace_root / ".env",
        "--- .env\n+++ .env\n+hello\n",
        text_module.Text,
    )

    assert str(summary).startswith("\u2022Edited: .env  (+1 -0)")
    spans_by_text = {
        str(summary)[span.start:span.end]: span.style
        for span in summary.spans
    }
    assert spans_by_text["+1"] == "#2EA043"
    assert spans_by_text["-0"] == "#CF222E"


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


def test_handle_slash_evolve_run_generates_candidate(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50, session_id="alpha")
    store.append([{"role": "user", "content": "help"}])
    optimizer = DummyOptimizer()
    optimizer.candidate = {
        "id": "candidate-1",
        "candidate_prompt": "new prompt",
        "rationale": "better boundaries",
    }
    console = DummyConsole()

    handled = handle_slash("/evolve run", DummyRegistry(), store, optimizer, console)

    assert handled is True
    assert any("candidate-1" in line for line in console.lines)
    assert any("better boundaries" in line for line in console.lines)


def test_handle_slash_evolve_diff_apply_and_rollback(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50, session_id="alpha")
    optimizer = DummyOptimizer()
    optimizer.diff_text = "--- current\n+++ candidate\n-new\n+old\n"
    optimizer.applied = {"id": "candidate-1"}
    optimizer.restored = {"id": "prompt-1"}
    console = DummyConsole()

    assert handle_slash("/evolve diff", DummyRegistry(), store, optimizer, console) is True
    assert any("+++ candidate" in line for line in console.lines)

    assert handle_slash("/evolve apply", DummyRegistry(), store, optimizer, console) is True
    assert any("candidate-1" in line for line in console.lines)

    assert handle_slash("/evolve rollback", DummyRegistry(), store, optimizer, console) is True
    assert any("prompt-1" in line for line in console.lines)
