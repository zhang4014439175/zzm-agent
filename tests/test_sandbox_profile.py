import pytest

from zzm_agent.core.sandbox import SandboxProfile, SandboxViolation
from zzm_agent.core.errors import tool_error_from_exception


def test_filesystem_profile_blocks_sensitive_and_explicit_deny_paths(tmp_path):
    denied = tmp_path / "private"
    denied.mkdir()
    profile = SandboxProfile(workspace_roots=(tmp_path,), deny_paths=(denied,))

    assert profile.authorize_path("src/app.py") == tmp_path / "src" / "app.py"
    with pytest.raises(SandboxViolation, match="Sensitive"):
        profile.authorize_path(tmp_path / ".env")
    with pytest.raises(SandboxViolation, match="explicitly denied"):
        profile.authorize_path(denied / "secret.txt")


def test_filesystem_profile_blocks_escape_and_symlink_parent(tmp_path):
    profile = SandboxProfile(workspace_roots=(tmp_path,))
    with pytest.raises(SandboxViolation, match="escapes workspace"):
        profile.authorize_path(tmp_path.parent / "outside.txt", access="write")


def test_network_is_default_deny_and_allowlist_supports_subdomains(tmp_path):
    disabled = SandboxProfile(workspace_roots=(tmp_path,))
    with pytest.raises(SandboxViolation, match="disabled"):
        disabled.authorize_url("https://api.example.com/v1")

    enabled = SandboxProfile(
        workspace_roots=(tmp_path,),
        network_enabled=True,
        network_allow_domains=("example.com",),
    )
    assert enabled.authorize_url("https://api.example.com/v1").startswith("https://")
    with pytest.raises(SandboxViolation, match="not allowlisted"):
        enabled.authorize_url("https://example.org")


def test_network_denies_localhost_private_and_explicit_domains(tmp_path):
    profile = SandboxProfile(
        workspace_roots=(tmp_path,),
        network_enabled=True,
        network_deny_domains=("blocked.example",),
    )
    with pytest.raises(SandboxViolation, match="Localhost"):
        profile.authorize_url("http://localhost:8000")
    with pytest.raises(SandboxViolation, match="Private"):
        profile.authorize_url("http://192.168.1.10")
    with pytest.raises(SandboxViolation, match="denied"):
        profile.authorize_url("https://api.blocked.example")


def test_sandbox_denial_requests_controlled_escalation():
    error = tool_error_from_exception(SandboxViolation("blocked"))

    assert error.error_type == "SandboxViolation"
    assert error.category == "permission"
    assert error.retryable is False
    assert "controlled sandbox profile change" in error.recovery_hint
