from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

from zzm_agent.cli_support.commands import handle_slash
from zzm_agent.cli_support.git_workflow import GitWorkflow
from zzm_agent.cli_support.rendering import PlainTextRenderer
from zzm_agent.cli_support.runtime import parse_args, run_exec
from zzm_agent.core.config import ConfigManager, ConfigScope, ConfigSource
from zzm_agent.core.model_stream import ModelStreamEvent
from zzm_agent.core.runtime_records import ArtifactStore
from zzm_agent.core.runtime_state import PermissionState
from zzm_agent.memory.store import MemoryStore


class Console:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, value="") -> None:
        self.lines.append(str(value))


class Registry:
    def get_schemas(self):
        return []


class Optimizer:
    pass


class QueryEngine:
    def __init__(self) -> None:
        self.submitted = []
        self.conversation_state = type("Conversation", (), {})()
        self.conversation_state.permissions = PermissionState()
        self.conversation_state.artifacts = ArtifactStore()
        self.conversation_state.active_turn = None

    def submit_message(self, prompt, **kwargs):
        self.submitted.append((prompt, kwargs))
        callback = kwargs.get("on_stream_event")
        if callback is not None:
            callback(ModelStreamEvent.status("turn.started"))
        return type("Result", (), {"reply": "验收完成", "response_language": None})()


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=path, capture_output=True, text=True, check=True
    )
    return result.stdout


def test_p2_configuration_instructions_and_sessions_are_explainable_and_recoverable(tmp_path):
    global_path = _write(tmp_path / "global.yaml", "agent:\n  stream: true\n")
    project_path = _write(
        tmp_path / "project.yaml",
        "model:\n  model_name: project-model\nmemory:\n  path: memory.json\n",
    )
    result = ConfigManager(cwd=tmp_path, repo_root=tmp_path).load(
        sources=[
            ConfigSource(global_path, ConfigScope.GLOBAL),
            ConfigSource(project_path, ConfigScope.PROJECT),
        ]
    )
    _write(tmp_path / "AGENTS.md", "Run pytest before completion.")
    store = MemoryStore(
        path=tmp_path / "memory.json",
        max_history=20,
        session_id="first",
        workspace_root=tmp_path,
    )
    store.append([{"role": "user", "content": "first turn"}])
    second = store.create_session(name="second", make_current=True)["id"]
    store.switch_session("first")

    assert result.config["model"]["model_name"] == "project-model"
    assert result.origins["model.model_name"].scope is ConfigScope.PROJECT
    assert store.list_instruction_files()[0].name == "AGENTS.md"
    assert store.load_history()[-1]["content"] == "first turn"
    assert second in {item["id"] for item in store.list_sessions()}


def test_p2_exec_json_and_terminal_renderer_keep_machine_and_human_output_separate():
    engine = QueryEngine()
    stdout = io.StringIO()
    code = run_exec(
        {"query_engine": engine},
        parse_args(["exec", "--json", "inspect status"]),
        stdout=stdout,
    )
    records = [json.loads(line) for line in stdout.getvalue().splitlines()]

    console = Console()
    renderer = PlainTextRenderer(console)
    renderer.render_event(ModelStreamEvent.reasoning_summary("checking"))
    renderer.render_event(ModelStreamEvent.content_delta("draft"))
    renderer.render_event(ModelStreamEvent.final_message("final"))

    assert code == 0
    assert [record["type"] for record in records] == ["event", "result"]
    assert records[-1]["reply"] == "验收完成"
    assert console.lines == ["Reasoning: checking", "---", "final"]


def test_p2_git_index_write_is_confirmed_and_reversible(tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    _write(tmp_path / "demo.txt", "base\n")
    _git(tmp_path, "add", "demo.txt")
    _git(tmp_path, "commit", "-m", "initial")
    _write(tmp_path / "demo.txt", "changed\n")
    approvals: list[str] = []
    workflow = GitWorkflow(tmp_path)

    workflow.stage(["demo.txt"], confirm=lambda message: approvals.append(message) or True)
    assert "changed" in _git(tmp_path, "diff", "--cached")
    workflow.undo_last_index_change(
        confirm=lambda message: approvals.append(message) or True
    )

    assert _git(tmp_path, "diff", "--cached") == ""
    assert len(approvals) == 2


def test_p2_review_pr_and_ci_flow_remains_read_only_and_preserves_log_artifact(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ZZM_AGENT_WORKSPACE_ROOT", str(tmp_path))
    _write(tmp_path / "ci.log", "FAILED tests/test_api.py::test_response\n")
    engine = QueryEngine()
    store = MemoryStore(path=tmp_path / "memory.json", max_history=20)
    console = Console()
    runtime = {"query_engine": engine}

    assert handle_slash("/review --cached", Registry(), store, Optimizer(), console, runtime)
    assert handle_slash("/pr", Registry(), store, Optimizer(), console, runtime)
    assert handle_slash("/ci ci.log", Registry(), store, Optimizer(), console, runtime)

    prompts = [item[0] for item in engine.submitted]
    artifacts = list(engine.conversation_state.artifacts.records.values())
    assert "Do not modify files" in prompts[0]
    assert "Do not modify the worktree" in prompts[1]
    assert len(artifacts) == 1 and artifacts[0].kind == "ci-log"
    assert artifacts[0].artifact_id in prompts[2]
