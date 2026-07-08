from pathlib import Path

from zzm_agent.core.config import ConfigManager, ConfigScope, ConfigSource


def write_yaml(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_config_manager_merges_scopes_and_records_origins(tmp_path):
    global_path = write_yaml(
        tmp_path / "global.yaml",
        """
model:
  model_name: global-model
agent:
  stream: true
memory:
  max_context_tokens: 16000
""",
    )
    project_path = write_yaml(
        tmp_path / "project.yaml",
        """
model:
  model_name: project-model
memory:
  path: .zzm_agent/memory.json
""",
    )
    local_path = write_yaml(
        tmp_path / "local.yaml",
        """
agent:
  stream: false
""",
    )
    manager = ConfigManager(cwd=tmp_path, repo_root=tmp_path)

    result = manager.load(
        sources=[
            ConfigSource(global_path, ConfigScope.GLOBAL),
            ConfigSource(project_path, ConfigScope.PROJECT),
            ConfigSource(local_path, ConfigScope.LOCAL),
        ]
    )

    assert result.config["model"]["model_name"] == "project-model"
    assert result.config["agent"]["stream"] is False
    assert result.config["memory"]["max_context_tokens"] == 16000
    assert result.origins["model.model_name"].scope is ConfigScope.PROJECT
    assert result.origins["agent.stream"].scope is ConfigScope.LOCAL
    assert result.config["_config_dir"] == str(project_path.parent)
    assert len(result.config["_config_sources"]) == 3


def test_config_manager_applies_profile_and_expands_env(tmp_path):
    config_path = write_yaml(
        tmp_path / "config.yaml",
        """
model:
  api_key: "${LLM_API_KEY:-fallback}"
  model_name: base-model
profiles:
  fast:
    model:
      model_name: fast-model
agent:
  stream: true
""",
    )
    manager = ConfigManager(
        cwd=tmp_path,
        repo_root=tmp_path,
        env={"LLM_API_KEY": "secret", "ZZM_AGENT_PROFILE": "fast"},
    )

    result = manager.load(explicit_path=config_path)

    assert result.profile == "fast"
    assert result.config["model"]["api_key"] == "secret"
    assert result.config["model"]["model_name"] == "fast-model"
    assert result.config["_config_profile"] == "fast"


def test_managed_config_locks_keys_against_later_sources(tmp_path):
    project_path = write_yaml(
        tmp_path / "project.yaml",
        """
model:
  base_url: https://project.example/v1
  model_name: project-model
""",
    )
    managed_path = write_yaml(
        tmp_path / "managed.yaml",
        """
managed:
  locked_keys:
    - model.base_url
model:
  base_url: https://managed.example/v1
""",
    )
    local_path = write_yaml(
        tmp_path / "local.yaml",
        """
model:
  base_url: https://local.example/v1
  model_name: local-model
""",
    )
    manager = ConfigManager(cwd=tmp_path, repo_root=tmp_path)

    result = manager.load(
        sources=[
            ConfigSource(project_path, ConfigScope.PROJECT),
            ConfigSource(managed_path, ConfigScope.MANAGED),
            ConfigSource(local_path, ConfigScope.LOCAL),
        ]
    )

    assert result.config["model"]["base_url"] == "https://managed.example/v1"
    assert result.config["model"]["model_name"] == "local-model"
    assert "model.base_url" in result.locked_keys
    assert result.origins["model.base_url"].locked is True
