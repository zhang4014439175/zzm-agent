"""Plugin Manifest 核心模型与运行时装配测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from zzm_agent.cli_support import bootstrap
from zzm_agent.cli_support.bootstrap import _resolve_skill_dirs
from zzm_agent.core.plugin_manifest import PluginManifestError, load_plugin_manifest
from zzm_agent.core.tool_registry import ToolRegistry


def _write_manifest_plugin(root: Path, *, enabled: bool = True) -> Path:
    """创建同时贡献工具、Skill 和 MCP 配置的最小测试插件。"""
    plugin = root / "demo-plugin"
    (plugin / "skills" / "reviewer").mkdir(parents=True)
    (plugin / "plugin.yaml").write_text(
        "\n".join(
            [
                "schema_version: 1",
                "name: demo-plugin",
                "version: 1.2.0",
                "description: Demo packaged extension",
                f"enabled: {'true' if enabled else 'false'}",
                "entry: tools.py",
                "namespace: demo",
                "group: Demo",
                "risk_level: medium",
                "skills:",
                "  - skills",
                "mcp:",
                "  servers:",
                "    - name: helper",
                "      command: [python, -m, demo_helper]",
                "      timeout_seconds: 9",
                "permissions:",
                "  filesystem:",
                "    read: [workspace]",
                "    write: []",
                "  network:",
                "    connect: [api.example.com]",
                "  subprocess: true",
                "  secrets: [DEMO_TOKEN]",
            ]
        ),
        encoding="utf-8",
    )
    (plugin / "tools.py").write_text(
        "from zzm_agent.core.tool_registry import tool\n\n"
        "@tool(description='demo echo')\n"
        "def echo(text: str) -> str:\n"
        "    return text\n",
        encoding="utf-8",
    )
    (plugin / "skills" / "reviewer" / "SKILL.md").write_text(
        "---\nname: reviewer\ndescription: Review changes\n---\nReview carefully.\n",
        encoding="utf-8",
    )
    return plugin


def test_manifest_parses_packaged_contributions_and_permissions(tmp_path):
    """Manifest 应规范化包内路径、MCP 配置和权限声明。"""
    plugin = _write_manifest_plugin(tmp_path)

    manifest = load_plugin_manifest(plugin / "plugin.yaml")

    assert manifest.name == "demo-plugin"
    assert manifest.entry == (plugin / "tools.py").resolve()
    assert manifest.skill_directories == ((plugin / "skills").resolve(),)
    assert manifest.mcp_servers[0]["name"] == "helper"
    assert manifest.mcp_servers[0]["plugin_name"] == "demo-plugin"
    assert manifest.permissions["filesystem"]["read"] == ("workspace",)
    assert manifest.permissions["subprocess"] is True


def test_manifest_rejects_paths_outside_plugin_root(tmp_path):
    """插件入口和 Skill 目录不得使用相对路径逃逸插件根目录。"""
    plugin = tmp_path / "unsafe"
    plugin.mkdir()
    (plugin / "plugin.json").write_text(
        '{"name":"unsafe","version":"1.0.0","entry":"../outside.py"}',
        encoding="utf-8",
    )

    with pytest.raises(PluginManifestError, match="inside the plugin directory"):
        load_plugin_manifest(plugin / "plugin.json")


def test_registry_discovers_manifest_packages_and_exposes_state(tmp_path):
    """插件目录的直接子包应被发现，并把贡献与权限状态暴露给启动层。"""
    plugin = _write_manifest_plugin(tmp_path)
    registry = ToolRegistry()
    registry.configure_plugin_dirs([tmp_path])

    registry.load_configured_plugins()

    assert registry.call("demo.echo", {"text": "ok"}) == "ok"
    assert registry.get_plugin_skill_dirs() == [(plugin / "skills").resolve()]
    assert registry.get_plugin_mcp_servers()[0]["name"] == "helper"
    assert registry.get_plugin_states() == [
        {
            "name": "demo-plugin",
            "version": "1.2.0",
            "description": "Demo packaged extension",
            "path": str(plugin.resolve()),
            "enabled": True,
            "status": "loaded",
            "entry": "tools.py",
            "skills": ["skills"],
            "mcp_servers": ["helper"],
            "permissions": {
                "filesystem": {"read": ["workspace"], "write": []},
                "network": {"connect": ["api.example.com"]},
                "subprocess": True,
                "secrets": ["DEMO_TOKEN"],
            },
        }
    ]


def test_plugin_config_can_disable_and_enable_manifest(tmp_path):
    """配置项应覆盖 Manifest 默认启停值，禁用时不得装配任何贡献。"""
    _write_manifest_plugin(tmp_path)
    disabled = ToolRegistry()
    disabled.configure_plugin_dirs(
        [tmp_path], plugin_config={"demo-plugin": {"enabled": False}}
    )
    disabled.load_configured_plugins()

    assert "demo.echo" not in disabled.tools
    assert disabled.get_plugin_skill_dirs() == []
    assert disabled.get_plugin_mcp_servers() == []
    assert disabled.get_plugin_states()[0]["status"] == "disabled_by_config"

    plugin = tmp_path / "default-off"
    plugin.mkdir()
    (plugin / "plugin.yaml").write_text(
        "name: default-off\nversion: 1.0.0\nenabled: false\nentry: tools.py\n",
        encoding="utf-8",
    )
    (plugin / "tools.py").write_text(
        "from zzm_agent.core.tool_registry import tool\n"
        "@tool(description='enabled override')\n"
        "def active() -> str:\n    return 'yes'\n",
        encoding="utf-8",
    )
    enabled = ToolRegistry()
    enabled.configure_plugin_dirs(
        [plugin], plugin_config={"default-off": {"enabled": True}}
    )
    enabled.load_configured_plugins()

    assert enabled.call("active", {}) == "yes"


def test_runtime_skill_dirs_include_only_enabled_plugin_contributions(tmp_path):
    """运行时 Skill 搜索路径应合并配置目录与已启用插件目录并稳定去重。"""
    plugin = _write_manifest_plugin(tmp_path)
    configured = tmp_path / "configured-skills"
    registry = ToolRegistry()
    registry.configure_plugin_dirs([tmp_path])
    registry.load_configured_plugins()
    cfg = {
        "_config_dir": str(tmp_path),
        "skills": {"directories": ["configured-skills", "configured-skills"]},
    }

    assert _resolve_skill_dirs(cfg, registry) == [
        configured.resolve(),
        (plugin / "skills").resolve(),
    ]


def test_packaged_mcp_uses_existing_stdio_loader(tmp_path, monkeypatch):
    """插件 MCP 配置应交给既有客户端连接，而不是建立绕过权限的新执行路径。"""
    _write_manifest_plugin(tmp_path)
    registry = ToolRegistry()
    registry.configure_plugin_dirs([tmp_path])
    registry.load_configured_plugins()
    created: list[tuple[str, list[str], float]] = []

    class FakeClient:
        """记录构造和连接参数的无进程 MCP 测试替身。"""

        def __init__(self, name: str, command: list[str], *, timeout_seconds: float):
            """保存传给现有客户端入口的服务配置。"""
            created.append((name, command, timeout_seconds))

        def connect(self, target: ToolRegistry) -> None:
            """模拟成功连接，并确认目标仍是统一工具注册表。"""
            assert target is registry

    monkeypatch.setattr(bootstrap, "StdioMCPClient", FakeClient)

    bootstrap._load_mcp_servers(registry, {"mcp": {"servers": []}})

    assert created == [("helper", ["python", "-m", "demo_helper"], 9.0)]
    assert len(registry.mcp_clients) == 1
