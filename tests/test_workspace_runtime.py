from __future__ import annotations

import json
import subprocess

import pytest

from zzm_agent.cli_support.git_workflow import GitWorkflow
from zzm_agent.workspace.runtime import WorkspaceRuntime


@pytest.fixture
def repo(tmp_path):
    """创建包含一个已修改跟踪文件的最小 Git 仓库。"""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    target = tmp_path / "tracked.txt"
    target.write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True)
    target.write_text("two\n", encoding="utf-8")
    return tmp_path


def test_workspace_runtime_records_denied_effect_without_running_action(tmp_path):
    """验证未授权副作用会留下拒绝事实且不会执行动作。"""
    called = []
    runtime = WorkspaceRuntime(
        tmp_path,
        authorize=lambda kind, operation, target, metadata: False,
    )

    with pytest.raises(PermissionError):
        runtime.execute(
            kind="process",
            operation="execute",
            target=str(tmp_path),
            action=lambda: called.append(True),
        )

    assert called == []
    assert runtime.effects[-1].status == "denied"
    assert runtime.effects[-1].authorized is False


def test_file_effect_creates_checkpoint_and_conflict_aware_undo(tmp_path):
    """验证文件 Effect 带检查点，可撤销且不会覆盖后续外部编辑。"""
    target = tmp_path / "demo.txt"
    target.write_text("before", encoding="utf-8")
    runtime = WorkspaceRuntime(tmp_path)

    runtime.execute_file_mutation(
        target,
        operation="write",
        action=lambda: target.write_text("after", encoding="utf-8"),
    )
    effect = runtime.effects[-1]

    assert effect.kind == "file"
    assert effect.checkpoint_id is not None
    assert effect.reversible is True
    assert runtime.undo(effect.effect_id).undone is True
    assert target.read_text(encoding="utf-8") == "before"

    runtime.execute_file_mutation(
        target,
        operation="write",
        action=lambda: target.write_text("agent", encoding="utf-8"),
    )
    target.write_text("user", encoding="utf-8")
    conflict = runtime.undo()

    assert conflict.undone is False
    assert conflict.effect is not None
    assert conflict.effect.status == "conflicted"
    assert target.read_text(encoding="utf-8") == "user"


def test_effect_journal_restores_file_undo_across_runtime_restart(tmp_path):
    """验证文件检查点持久化后可由新的 Runtime 实例恢复并撤销。"""
    journal = tmp_path / ".zzm-agent" / "effects.json"
    target = tmp_path / "created.txt"
    runtime = WorkspaceRuntime(tmp_path, journal_path=journal)
    runtime.execute_file_mutation(
        target,
        operation="write",
        action=lambda: target.write_text("created", encoding="utf-8"),
    )

    restored = WorkspaceRuntime(tmp_path, journal_path=journal)
    result = restored.undo()

    assert result.undone is True
    assert target.exists() is False
    records = json.loads(journal.read_text(encoding="utf-8"))
    assert records[-1]["status"] == "reverted"


def test_git_workflow_records_and_reverts_index_effect(repo):
    """验证 Git stage 通过统一边界记录，并由同一 Effect 完成撤销。"""
    runtime = WorkspaceRuntime(repo)
    workflow = GitWorkflow(repo, workspace_runtime=runtime)

    workflow.stage(["tracked.txt"], confirm=lambda _message: True)

    assert runtime.effects[-1].kind == "git"
    assert runtime.effects[-1].operation == "stage"
    assert runtime.effects[-1].status == "applied"

    workflow.undo_last_index_change(confirm=lambda _message: True)

    assert runtime.effects[-1].status == "reverted"
