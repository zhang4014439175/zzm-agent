from __future__ import annotations

import subprocess

import pytest

from zzm_agent.cli_support.git_workflow import GitWorkflow, GitWorkflowError


def _git(path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=path, capture_output=True, text=True, check=True
    )
    return result.stdout


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "demo.txt").write_text("base\n", encoding="utf-8")
    _git(tmp_path, "add", "demo.txt")
    _git(tmp_path, "commit", "-m", "initial")
    return tmp_path


def test_snapshot_separates_staged_and_unstaged_changes(repo):
    (repo / "demo.txt").write_text("base\nstaged\n", encoding="utf-8")
    _git(repo, "add", "demo.txt")
    (repo / "demo.txt").write_text("base\nstaged\nunstaged\n", encoding="utf-8")

    snapshot = GitWorkflow(repo).snapshot()

    assert "demo.txt" in snapshot.status
    assert "+staged" in snapshot.staged_diff
    assert "+unstaged" in snapshot.unstaged_diff


def test_stage_requires_confirmation_and_can_be_undone(repo):
    (repo / "demo.txt").write_text("changed\n", encoding="utf-8")
    workflow = GitWorkflow(repo)

    with pytest.raises(GitWorkflowError, match="not approved"):
        workflow.stage(["demo.txt"], confirm=lambda _message: False)
    assert _git(repo, "diff", "--cached") == ""

    workflow.stage(["demo.txt"], confirm=lambda _message: True)
    assert "changed" in _git(repo, "diff", "--cached")
    workflow.undo_last_index_change(confirm=lambda _message: True)
    assert _git(repo, "diff", "--cached") == ""


def test_unstage_can_be_undone(repo):
    (repo / "demo.txt").write_text("changed\n", encoding="utf-8")
    _git(repo, "add", "demo.txt")
    workflow = GitWorkflow(repo)

    workflow.unstage(["demo.txt"], confirm=lambda _message: True)
    assert _git(repo, "diff", "--cached") == ""
    workflow.undo_last_index_change(confirm=lambda _message: True)
    assert "changed" in _git(repo, "diff", "--cached")


def test_git_paths_reject_option_injection(repo):
    with pytest.raises(GitWorkflowError, match="cannot start"):
        GitWorkflow(repo).stage(["--all"], confirm=lambda _message: True)
