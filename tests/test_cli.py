from zzm_agent.cli_support.commands import handle_slash
from zzm_agent.cli_support.observability import CliObserver
from zzm_agent.cli_support.rendering import (
    PROMPT_COMPLETION_MENU_RESERVED_LINES,
    SlashCommandCompleter,
    PlainTextRenderer,
    TerminalRenderer,
    _plain_terminal_reply,
    build_bottom_toolbar,
    build_terminal_renderer,
)
from zzm_agent.core.model_stream import ModelStreamEvent
from zzm_agent.cli_support.runtime import (
    _build_working_footer,
    build_tool_confirmation_callback,
    _ask_tool_approval_choice,
    _config_bool,
    _format_repl_exception,
    _format_repl_exception_with_runtime,
    _resolve_plugin_dirs,
    _start_working_status,
    _stop_working_status,
    get_agent_loop_policy,
    load_config,
    parse_args,
    run_repl,
)
from zzm_agent.core.model_metadata import resolve_model_context_limit
from zzm_agent.core.observability import TokenUsage, tool_end_event, tool_start_event
from zzm_agent.core.runtime_records import ArtifactStore
from zzm_agent.core.runtime_state import PermissionState
from zzm_agent.core.tool_registry import ToolRegistry
from zzm_agent.core.agent_loop import AgentLoop
from zzm_agent.memory.store import MemoryStore


class DummyQueryEngine:
    def __init__(self):
        self.submitted = []
        self.conversation_state = type("Conversation", (), {})()
        self.conversation_state.permissions = PermissionState()
        self.conversation_state.artifacts = ArtifactStore()
        self.conversation_state.active_turn = None
        self.agent_loop = type(
            "Loop",
            (),
            {"tool_choice": "auto", "auto_approve": True},
        )()

    def submit_message(self, message, **kwargs):
        self.submitted.append((message, kwargs))
        return type("Result", (), {"reply": "review result"})()


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


class DummyModelsClient:
    def __init__(self, model_ids):
        self.model_ids = model_ids

    class _Models:
        def __init__(self, model_ids):
            self.model_ids = model_ids

        def list(self):
            class Response:
                pass

            response = Response()
            response.data = []
            for item in self.model_ids:
                if isinstance(item, tuple):
                    model_id, created = item
                    response.data.append(type("Model", (), {"id": model_id, "created": created})())
                else:
                    response.data.append(type("Model", (), {"id": item})())
            return response

    @property
    def models(self):
        return self._Models(self.model_ids)

    class _Chat:
        class _Completions:
            def create(self, **kwargs):
                raise AssertionError("chat completions should not be called")

        completions = _Completions()

    chat = _Chat()


def test_tool_approval_choice_default_is_valid(monkeypatch):
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "prompt_toolkit":
            raise ImportError
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    assert _ask_tool_approval_choice(DummyConsole()) == "1"


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
    assert cfg["_config_dir"] == str(config_path.parent)


def test_parse_args_accepts_debug_flag():
    args = parse_args(["repl", "--debug"])

    assert args.command == "repl"
    assert args.debug is True


def test_prompt_session_reserves_only_one_completion_menu_line():
    assert PROMPT_COMPLETION_MENU_RESERVED_LINES == 1


def test_plain_terminal_reply_does_not_invent_bullets():
    reply = (
        "# 结论\n"
        "你好！这是普通段落。\n"
        "\n"
        "1. 第一项\n"
        "- 第二项\n"
    )

    rendered = _plain_terminal_reply(reply)

    assert rendered == "结论\n你好！这是普通段落。\n\n1. 第一项\n- 第二项"
    assert "\u2022" not in rendered


def test_format_repl_exception_summarizes_sdk_error_payload():
    exc = RuntimeError(
        "Error code: 400 - {'error': {'message': 'openai_error', "
        "'type': 'bad_response_status_code', 'param': '', "
        "'code': 'bad_response_status_code'}}"
    )

    message = _format_repl_exception(exc)

    assert message == (
        "模型接口请求失败：openai_error "
        "(code: bad_response_status_code)"
    )


def test_working_status_resets_elapsed_for_each_new_request(monkeypatch):
    class FakeConsole:
        pass

    class FakeLive:
        def __init__(self, status, console, refresh_per_second, transient):
            self.status = status

        def start(self):
            return None

        def stop(self):
            return None

    times = iter([10.0, 20.0])
    console = FakeConsole()
    monkeypatch.setattr("zzm_agent.cli_support.runtime.time.monotonic", lambda: next(times))
    monkeypatch.setattr("rich.live.Live", FakeLive)

    assert _start_working_status(console) is True
    first_status = console._zzm_working_status
    assert first_status.started_at == 10.0
    assert _stop_working_status(console) is True

    assert _start_working_status(console) is True
    second_status = console._zzm_working_status

    assert second_status is not first_status
    assert second_status.started_at == 20.0


def test_working_status_resume_can_keep_elapsed_timer(monkeypatch):
    class FakeConsole:
        pass

    class FakeLive:
        def __init__(self, status, console, refresh_per_second, transient):
            self.status = status

        def start(self):
            return None

        def stop(self):
            return None

    console = FakeConsole()
    monkeypatch.setattr("zzm_agent.cli_support.runtime.time.monotonic", lambda: 10.0)
    monkeypatch.setattr("rich.live.Live", FakeLive)

    assert _start_working_status(console) is True
    first_status = console._zzm_working_status
    assert _stop_working_status(console) is True
    assert _start_working_status(console, reset_elapsed=False) is True

    assert console._zzm_working_status is first_status


def test_working_footer_matches_bottom_toolbar_runtime_data(tmp_path, monkeypatch):
    class DummyLoop:
        model = "demo-model"
        last_context_window = {"max_context_tokens": 64000}
        last_turn_usage = TokenUsage(prompt_tokens=1000, completion_tokens=80, total_tokens=1080)

    store = MemoryStore(path=tmp_path / "memory.json", max_history=1, max_context_tokens=64000)
    monkeypatch.setenv("ZZM_AGENT_WORKSPACE_ROOT", str(tmp_path))

    footer = _build_working_footer({"loop": DummyLoop(), "store": store})
    rendered = str(footer)

    assert str(tmp_path) in rendered
    assert "Model: demo-model" in rendered
    assert "Context: 1000/64000" in rendered


def test_plugin_dirs_resolve_relative_to_config_file(tmp_path, monkeypatch):
    config_dir = tmp_path / "agent"
    config_dir.mkdir()
    config_path = config_dir / "config.yaml"
    config_path.write_text(
        "agent:\n"
        "  plugin_dirs:\n"
        "    - zzm_agent/plugins\n",
        encoding="utf-8",
    )
    other_cwd = tmp_path / "other-project"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)

    cfg = load_config(config_path)
    plugin_dirs = _resolve_plugin_dirs(cfg)

    assert plugin_dirs == [(config_dir / "zzm_agent" / "plugins").resolve()]


def test_load_config_uses_config_manager_metadata(tmp_path, monkeypatch):
    monkeypatch.delenv("ZZM_AGENT_TEST_BASE_URL", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "model:\n"
        "  base_url: ${ZZM_AGENT_TEST_BASE_URL:-https://example.com/v1}\n"
        "  model_name: demo\n"
        "agent:\n"
        "  stream: true\n",
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert cfg["model"]["base_url"] == "https://example.com/v1"
    assert cfg["_config_path"] == str(config_path.resolve())
    assert cfg["_config_sources"][0]["scope"] == "project"
    assert cfg["_config_origin"]["model.model_name"]["scope"] == "project"


def test_config_command_shows_effective_config(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50)
    console = DummyConsole()
    runtime = {
        "config": {
            "model": {
                "base_url": "https://example.com/v1",
                "model_name": "demo-model",
            },
            "agent": {"stream": True},
            "memory": {
                "path": ".zzm_agent/memory.json",
                "max_context_tokens": 32000,
            },
            "_config_profile": "default",
            "_config_sources": [
                {"scope": "project", "path": str(tmp_path / "config.yaml")},
            ],
            "_config_origin": {
                "model.model_name": {
                    "scope": "project",
                    "path": str(tmp_path / "config.yaml"),
                    "locked": False,
                }
            },
            "_config_locked": [],
        }
    }

    assert handle_slash("/config", DummyRegistry(), store, DummyOptimizer(), console, runtime) is True

    rendered = "\n".join(console.lines)
    assert "Effective config" in rendered
    assert "model.model_name: demo-model" in rendered
    assert "Sources" in rendered


def test_run_repl_prints_traceback_in_debug_mode(monkeypatch):
    class FakeConsole:
        def __init__(self):
            self.exception_called = False
            self.printed = []

        def print(self, *args, **kwargs):
            self.printed.append((args, kwargs))

        def print_exception(self):
            self.exception_called = True

        def input(self, prompt):
            return "hello"

    class FakePromptSession:
        pass

    class FakeLoop:
        model = "demo"
        last_turn_usage = None
        cumulative_usage = None
        last_context_window = {}

        def run(self, user_input, stream=True, on_text_chunk=None):
            raise ValueError("boom")

    class FakeStore:
        session_id = "session-1"

    class FakeRegistry:
        def get_schemas(self):
            return []

    class FakeOptimizer:
        pass

    fake_console = FakeConsole()
    runtime = {
        "console": fake_console,
        "registry": FakeRegistry(),
        "store": FakeStore(),
        "optimizer": FakeOptimizer(),
        "loop": FakeLoop(),
        "observer": None,
        "stream": False,
        "debug": True,
    }

    monkeypatch.setattr("zzm_agent.cli_support.runtime.build_prompt_session", lambda **kwargs: FakePromptSession())
    monkeypatch.setattr("zzm_agent.cli_support.rendering.render_welcome", lambda *args, **kwargs: None)
    inputs = iter(["hello", KeyboardInterrupt()])

    def fake_read_repl_input(console, prompt_session):
        value = next(inputs)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr("zzm_agent.cli_support.runtime.read_repl_input", fake_read_repl_input)

    result = run_repl(runtime)

    assert result == 0
    assert fake_console.exception_called is True


def test_format_repl_exception_with_runtime_explains_404_chat_endpoint_errors():
    exc = RuntimeError("Error code: 404 - 404 page not found")
    runtime = {
        "config": {
            "model": {
                "base_url": "https://example.com/v1",
                "model_name": "demo-model",
            }
        }
    }

    message = _format_repl_exception_with_runtime(exc, runtime)

    assert "404 page not found" in message
    assert "model.base_url" in message
    assert "/chat/completions" in message
    assert "https://example.com/v1" in message
    assert "demo-model" in message


def test_format_repl_exception_with_runtime_explains_missing_choices_errors():
    exc = RuntimeError("Chat completion failed: response did not include choices.")
    runtime = {
        "config": {
            "model": {
                "base_url": "https://example.com/v1",
                "model_name": "demo-model",
            }
        }
    }

    message = _format_repl_exception_with_runtime(exc, runtime)

    assert "choices" in message
    assert "OpenAI-compatible" in message
    assert "https://example.com/v1" in message
    assert "demo-model" in message


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


def test_tool_confirmation_allows_by_default():
    console = DummyConsole()
    confirm = build_tool_confirmation_callback(console)

    assert confirm("wipe", {"target": "demo"}, "high") is True


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


def test_models_command_lists_current_base_url_models(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50)
    console = DummyConsole()
    registry = DummyRegistry()
    loop = type("Loop", (), {"model": "demo-a"})()
    runtime = {
        "client": DummyModelsClient(["demo-b", "demo-a"]),
        "loop": loop,
    }

    handled = handle_slash("/models", registry, store, DummyOptimizer(), console, runtime)

    assert handled is True
    assert any("2 model(s)" in line for line in console.lines)
    assert any("* [cyan]demo-a" in line for line in console.lines)
    assert any("demo-b" in line for line in console.lines)


def test_models_command_sorts_by_created_oldest_to_newest(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50)
    console = DummyConsole()
    runtime = {
        "client": DummyModelsClient([
            ("new-model", 300),
            ("old-model", 100),
            ("middle-model", 200),
        ]),
        "loop": type("Loop", (), {"model": "middle-model"})(),
    }

    handled = handle_slash("/models", DummyRegistry(), store, DummyOptimizer(), console, runtime)

    assert handled is True
    rendered = "\n".join(console.lines)
    assert rendered.index("old-model") < rendered.index("middle-model") < rendered.index("new-model")


def test_models_command_filters_by_model_name_substring(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50)
    console = DummyConsole()
    runtime = {
        "client": DummyModelsClient(["demo-a", "demo-a:free", "demo-b:free"]),
        "loop": type("Loop", (), {"model": "demo-a:free"})(),
    }

    handled = handle_slash("/models :free", DummyRegistry(), store, DummyOptimizer(), console, runtime)

    assert handled is True
    assert any("2 model(s)" in line for line in console.lines)
    assert any("* [cyan]demo-a:free" in line for line in console.lines)
    assert any("demo-b:free" in line for line in console.lines)
    assert not any("demo-a[/cyan]" in line for line in console.lines)


def test_model_command_switches_runtime_model(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50, max_context_tokens=32000)
    registry = ToolRegistry()
    loop = AgentLoop(
        client=DummyModelsClient(["demo-a", "demo-b"]),
        model="demo-a",
        system_prompt="sys",
        registry=registry,
        store=store,
    )
    optimizer = DummyOptimizer()
    optimizer.model = "demo-a"
    console = DummyConsole()
    runtime = {
        "client": DummyModelsClient(["demo-a", "demo-b"]),
        "config": {
            "model": {"model_name": "demo-a", "context_window_tokens": 64000},
            "memory": {"max_context_tokens": 32000},
        },
        "loop": loop,
        "store": store,
        "optimizer": optimizer,
    }

    handled = handle_slash("/model demo-b", registry, store, optimizer, console, runtime)

    assert handled is True
    assert loop.model == "demo-b"
    assert loop.token_counter.model == "demo-b"
    assert store.token_counter.model == "demo-b"
    assert store.max_context_tokens == 64000
    assert optimizer.model == "demo-b"
    assert any("Switched model" in line for line in console.lines)


def test_model_command_rejects_unknown_model(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50)
    console = DummyConsole()
    loop = type("Loop", (), {"model": "demo-a"})()
    runtime = {
        "client": DummyModelsClient(["demo-a"]),
        "loop": loop,
        "store": store,
        "config": {"model": {"model_name": "demo-a"}, "memory": {"max_context_tokens": 32000}},
    }

    handled = handle_slash("/model missing", DummyRegistry(), store, DummyOptimizer(), console, runtime)

    assert handled is True
    assert loop.model == "demo-a"
    assert any("Model not found" in line for line in console.lines)


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


def test_handle_slash_status_reports_runtime_summary(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50, session_id="alpha")
    console = DummyConsole()
    runtime = {
        "loop": type("Loop", (), {"model": "demo", "cumulative_usage": TokenUsage(total_tokens=12)})(),
        "query_engine": DummyQueryEngine(),
        "stream": True,
        "config": {"memory": {"max_context_tokens": 32000}},
    }

    handled = handle_slash("/status", DummyRegistry(), store, DummyOptimizer(), console, runtime)

    assert handled is True
    rendered = "\n".join(console.lines)
    assert "Status" in rendered
    assert "session: alpha" in rendered
    assert "model: demo" in rendered


def test_handle_slash_resume_without_id_switches_latest_other_session(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50, session_id="alpha")
    beta = store.create_session(name="beta", make_current=False)["id"]
    console = DummyConsole()

    handled = handle_slash("/resume", DummyRegistry(), store, DummyOptimizer(), console)

    assert handled is True
    assert store.session_id == beta
    assert any("Resumed session" in line for line in console.lines)


def test_handle_slash_permissions_lists_permission_state(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50)
    query_engine = DummyQueryEngine()
    request = query_engine.conversation_state.permissions.request_permission(
        tool_name="shell",
        arguments={"cmd": "pytest"},
        risk_level="high",
    )
    query_engine.conversation_state.permissions.approve_request(request.request_id)
    console = DummyConsole()

    handled = handle_slash(
        "/permissions",
        DummyRegistry(),
        store,
        DummyOptimizer(),
        console,
        {"query_engine": query_engine},
    )

    assert handled is True
    rendered = "\n".join(console.lines)
    assert "Permissions" in rendered
    assert "decisions: 1" in rendered
    assert "shell" in rendered


def test_handle_slash_artifacts_lists_and_previews_artifact(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50)
    query_engine = DummyQueryEngine()
    artifact = query_engine.conversation_state.artifacts.save_text(
        "\n".join(f"line {index}" for index in range(45)),
        kind="log",
        summary="test log",
    )
    console = DummyConsole()
    runtime = {"query_engine": query_engine}

    assert handle_slash("/artifacts", DummyRegistry(), store, DummyOptimizer(), console, runtime)
    assert handle_slash(
        f"/artifacts {artifact.artifact_id}",
        DummyRegistry(),
        store,
        DummyOptimizer(),
        console,
        runtime,
    )

    rendered = "\n".join(console.lines)
    assert artifact.artifact_id in rendered
    assert "line 0" in rendered
    assert "use /artifacts <id> --full" in rendered


def test_handle_slash_plan_reads_local_plan_file(tmp_path, monkeypatch):
    monkeypatch.setenv("ZZM_AGENT_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "task.md").write_text("Plan from file", encoding="utf-8")
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50)
    console = DummyConsole()

    handled = handle_slash("/plan", DummyRegistry(), store, DummyOptimizer(), console, {})

    assert handled is True
    assert any("Plan from file" in line for line in console.lines)


def test_handle_slash_review_submits_read_only_diff(monkeypatch, tmp_path):
    monkeypatch.setenv("ZZM_AGENT_WORKSPACE_ROOT", str(tmp_path))
    query_engine = DummyQueryEngine()
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50)
    console = DummyConsole()

    class Completed:
        returncode = 0
        stdout = "diff --git a/a.py b/a.py\n+print('hi')\n"
        stderr = ""

    monkeypatch.setattr(
        "zzm_agent.cli_support.commands.subprocess.run",
        lambda *args, **kwargs: Completed(),
    )

    handled = handle_slash(
        "/review",
        DummyRegistry(),
        store,
        DummyOptimizer(),
        console,
        {"query_engine": query_engine},
    )

    assert handled is True
    assert query_engine.submitted
    prompt = query_engine.submitted[0][0]
    submitted_kwargs = query_engine.submitted[0][1]
    assert "只读代码审查" in prompt
    assert "不要修改文件" in prompt
    assert submitted_kwargs["stream"] is True
    assert submitted_kwargs["on_stream_event"] is not None
    assert query_engine.agent_loop.tool_choice == "auto"
    assert query_engine.agent_loop.auto_approve is True
    assert "review result" in "\n".join(console.lines)


def test_handle_slash_review_reports_empty_model_reply(monkeypatch, tmp_path):
    monkeypatch.setenv("ZZM_AGENT_WORKSPACE_ROOT", str(tmp_path))
    query_engine = DummyQueryEngine()
    query_engine.submit_message = lambda message, **kwargs: type("Result", (), {"reply": ""})()
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50)
    console = DummyConsole()

    class Completed:
        returncode = 0
        stdout = "diff --git a/a.py b/a.py\n+print('hi')\n"
        stderr = ""

    monkeypatch.setattr(
        "zzm_agent.cli_support.commands.subprocess.run",
        lambda *args, **kwargs: Completed(),
    )

    handled = handle_slash(
        "/review",
        DummyRegistry(),
        store,
        DummyOptimizer(),
        console,
        {"query_engine": query_engine},
    )

    assert handled is True
    assert any("returned no textual findings" in line for line in console.lines)


def test_handle_slash_reserved_commands_report_unavailable_state(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50)
    console = DummyConsole()

    assert handle_slash("/skills", DummyRegistry(), store, DummyOptimizer(), console, {})
    assert handle_slash("/mcp", DummyRegistry(), store, DummyOptimizer(), console, {})
    assert handle_slash("/undo", DummyRegistry(), store, DummyOptimizer(), console, {})

    rendered = "\n".join(console.lines)
    assert "Skills state is not connected yet" in rendered
    assert "MCP state is not connected yet" in rendered
    assert "暂无受管的文件变更可撤销" in rendered


def test_plain_text_renderer_separates_process_from_final_answer():
    console = DummyConsole()
    renderer = PlainTextRenderer(console)

    renderer.render_event(ModelStreamEvent.reasoning_summary("checking files"))
    renderer.render_event(ModelStreamEvent.tool_call_delta(tool_name="rg", arguments_delta="pattern"))
    renderer.render_event(ModelStreamEvent.content_delta("draft"))
    renderer.render_event(ModelStreamEvent.final_message("final answer"))

    assert console.lines == [
        "Reasoning: checking files",
        "Running rg",
        "---",
        "final answer",
    ]


def test_plain_text_renderer_buffers_chunked_reasoning_and_tool_arguments():
    console = DummyConsole()
    renderer = PlainTextRenderer(console)

    renderer.render_event(ModelStreamEvent.reasoning_summary("The "))
    renderer.render_event(ModelStreamEvent.reasoning_summary("user "))
    renderer.render_event(ModelStreamEvent.reasoning_summary("asked."))
    renderer.render_event(ModelStreamEvent.tool_call_delta(arguments_delta="{"))
    renderer.render_event(ModelStreamEvent.tool_call_delta(tool_name="list_directory", tool_call_id="1"))
    renderer.render_event(ModelStreamEvent.tool_call_delta(arguments_delta='"path": "."}'))
    renderer.render_event(ModelStreamEvent.content_delta("answer"))

    assert console.lines == [
        "Reasoning: The user asked.",
        "Running list_directory",
        "---",
    ]


def test_plain_text_renderer_keeps_working_status_for_status_events():
    renderer = PlainTextRenderer(DummyConsole())

    assert renderer.should_stop_working_status(ModelStreamEvent.status("turn.started")) is False
    assert renderer.should_stop_working_status(ModelStreamEvent.reasoning_summary("thinking")) is True


def test_terminal_renderer_dims_reasoning_content_only():
    console = DummyConsole()
    renderer = TerminalRenderer(console)

    renderer.render_event(ModelStreamEvent.reasoning_summary("Need inspect"))
    renderer.render_event(ModelStreamEvent.content_delta("answer"))

    assert console.lines[0] == "[black]Reasoning:[/black] [dim]Need inspect[/dim]"


def test_build_terminal_renderer_uses_plain_text_for_non_rich_console():
    renderer = build_terminal_renderer(DummyConsole())

    assert isinstance(renderer, PlainTextRenderer)


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


def test_handle_slash_instructions_lists_loaded_instruction_files(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Use pytest.", encoding="utf-8")
    store = MemoryStore(
        path=tmp_path / ".zzm_agent" / "memory.json",
        max_history=50,
        session_id="alpha",
        workspace_root=tmp_path,
    )
    console = DummyConsole()

    handled = handle_slash("/instructions", DummyRegistry(), store, DummyOptimizer(), console)

    assert handled is True
    assert any("Loaded 1 instruction file" in line for line in console.lines)
    assert any("AGENTS.md" in line for line in console.lines)


def test_handle_slash_memory_enable_disable_updates_semantic_metadata(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50, session_id="alpha")
    store.remember_fact("Project language is Python.")
    console = DummyConsole()

    assert (
        handle_slash("/memory-disable python", DummyRegistry(), store, DummyOptimizer(), console)
        is True
    )
    assert store.load_semantic_memory() == []
    assert store.list_semantic_memory(include_disabled=True)[0]["enabled"] is False

    assert handle_slash("/semantic", DummyRegistry(), store, DummyOptimizer(), console) is True
    assert any("disabled" in line for line in console.lines)
    assert any("source=manual" in line for line in console.lines)

    assert (
        handle_slash("/memory-enable python", DummyRegistry(), store, DummyOptimizer(), console)
        is True
    )
    assert store.load_semantic_memory()[0]["enabled"] is True


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
