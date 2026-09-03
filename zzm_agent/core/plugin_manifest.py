from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


_PLUGIN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RISK_LEVELS = {"low", "medium", "high"}
_MANIFEST_NAMES = ("plugin.yaml", "plugin.yml", "plugin.json")


class PluginManifestError(ValueError):
    """表示插件清单格式、字段或包内路径不合法。

    该异常只描述静态清单问题；调用方应隔离单个插件并继续加载其他插件，不能
    因第三方包损坏而中止整个 Agent。异常文本可以进入诊断界面，但不得包含凭据。
    """


@dataclass(frozen=True)
class PluginManifest:
    """保存经过验证的本地插件描述及其运行时贡献。

    ``entry`` 与 ``skill_directories`` 都已经解析为插件根目录内的绝对路径；MCP
    配置仍沿用应用已有的 stdio 配置结构。``permissions`` 是可审计的能力声明，
    不是授权凭证，工具调用仍必须经过 Registry、确认策略与 Workspace 沙箱。
    """

    schema_version: int
    name: str
    version: str
    description: str
    root: Path
    manifest_path: Path
    enabled: bool
    entry: Path | None
    entry_label: str
    namespace: str
    group: str
    default_risk_level: str | None
    config_key: str
    skill_directories: tuple[Path, ...]
    skill_labels: tuple[str, ...]
    mcp_servers: tuple[dict[str, Any], ...]
    permissions: dict[str, Any]
    raw: dict[str, Any]

    def to_record(self, *, enabled: bool, status: str) -> dict[str, Any]:
        """生成稳定、可序列化且不包含插件私有配置的诊断记录。

        记录只展示名称、来源、贡献和权限声明，不包含 ``plugins.<name>`` 下可能
        存在的令牌或其他运行时配置，适合后续 Renderer 与状态快照直接消费。
        """
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "path": str(self.root),
            "enabled": enabled,
            "status": status,
            "entry": self.entry_label,
            "skills": list(self.skill_labels),
            "mcp_servers": [str(item["name"]) for item in self.mcp_servers],
            "permissions": _jsonable_permissions(self.permissions),
        }


def find_plugin_manifest(root: Path) -> Path | None:
    """查找插件根目录中的唯一清单文件。

    支持 YAML 与旧版 JSON 文件名。若同一个目录出现多个候选清单则拒绝猜测优先
    级，避免编辑残留文件导致启动行为随实现细节变化。
    """
    matches = [root / name for name in _MANIFEST_NAMES if (root / name).is_file()]
    if len(matches) > 1:
        names = ", ".join(path.name for path in matches)
        raise PluginManifestError(f"multiple plugin manifests found: {names}")
    return matches[0] if matches else None


def load_plugin_manifest(path: str | Path) -> PluginManifest:
    """读取并验证一个本地 Plugin Manifest。

    JSON 用于兼容已有插件，YAML 是新包的推荐格式。所有可执行入口和 Skill 搜索
    目录都必须留在插件根目录内；MCP 仅接受当前客户端支持的 stdio 命令数组。
    权限字段采用有限结构进行验证，以免拼写错误被误认为有效声明。
    """
    manifest_path = Path(path).expanduser().resolve()
    root = manifest_path.parent.resolve()
    try:
        text = manifest_path.read_text(encoding="utf-8")
        data = json.loads(text) if manifest_path.suffix.casefold() == ".json" else yaml.safe_load(text)
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise PluginManifestError(f"unable to read manifest: {exc}") from exc
    if not isinstance(data, dict):
        raise PluginManifestError("plugin manifest must contain a mapping")

    schema_version = data.get("schema_version", 1)
    if schema_version != 1:
        raise PluginManifestError(f"unsupported schema_version: {schema_version}")
    name = str(data.get("name") or root.name).strip()
    if not _PLUGIN_NAME.fullmatch(name):
        raise PluginManifestError(f"invalid plugin name: {name}")
    version = str(data.get("version") or "0.0.0").strip()
    if not version or len(version) > 128:
        raise PluginManifestError("plugin version must be a non-empty string")

    entry_label = str(data.get("entry") or "").strip()
    if not entry_label and (root / "__init__.py").is_file():
        entry_label = "__init__.py"
    entry = _resolve_inside(root, entry_label, "Plugin entry") if entry_label else None
    if entry is not None and not entry.is_file():
        raise PluginManifestError(f"Plugin entry not found: {entry_label}")

    raw_skills = data.get("skills", [])
    skill_labels = _string_list(raw_skills, "skills")
    skill_dirs: list[Path] = []
    for label in skill_labels:
        directory = _resolve_inside(root, label, "Skill directory")
        if not directory.is_dir():
            raise PluginManifestError(f"Skill directory not found: {label}")
        skill_dirs.append(directory)

    risk = data.get("risk_level")
    default_risk = str(risk).strip().casefold() if risk is not None else None
    if default_risk is not None and default_risk not in _RISK_LEVELS:
        raise PluginManifestError(f"unsupported risk_level: {risk}")

    return PluginManifest(
        schema_version=1,
        name=name,
        version=version,
        description=str(data.get("description") or "").strip(),
        root=root,
        manifest_path=manifest_path,
        enabled=_boolean(data.get("enabled"), default=True, field="enabled"),
        entry=entry,
        entry_label=entry_label,
        namespace=str(data.get("namespace") or "").strip(),
        group=str(data.get("group") or "").strip(),
        default_risk_level=default_risk,
        config_key=str(data.get("config_key") or name).strip(),
        skill_directories=tuple(skill_dirs),
        skill_labels=tuple(skill_labels),
        mcp_servers=_mcp_servers(data, name),
        permissions=_permissions(data.get("permissions")),
        raw=dict(data),
    )


def _resolve_inside(root: Path, label: str, field: str) -> Path:
    """解析插件相对路径，并拒绝绝对路径或 ``..``、符号链接造成的目录逃逸。"""
    candidate = Path(label)
    if candidate.is_absolute():
        raise PluginManifestError(f"{field} must stay inside the plugin directory")
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise PluginManifestError(f"{field} must stay inside the plugin directory")
    return resolved


def _string_list(value: Any, field: str) -> list[str]:
    """把清单字符串数组规范化为非空字符串列表，并对错误类型快速失败。"""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PluginManifestError(f"{field} must be a list of strings")
    return [item.strip() for item in value if item.strip()]


def _boolean(value: Any, *, default: bool, field: str) -> bool:
    """严格读取布尔字段，防止字符串 ``false`` 被 Python 当作真值。"""
    if value is None:
        return default
    if not isinstance(value, bool):
        raise PluginManifestError(f"{field} must be a boolean")
    return value


def _mcp_servers(data: dict[str, Any], plugin_name: str) -> tuple[dict[str, Any], ...]:
    """验证插件打包的 stdio MCP 列表并附加不可伪造的插件来源。"""
    mcp = data.get("mcp", {})
    if mcp is None:
        mcp = {}
    if not isinstance(mcp, dict):
        raise PluginManifestError("mcp must be a mapping")
    servers = mcp.get("servers", data.get("mcp_servers", []))
    if servers is None:
        servers = []
    if not isinstance(servers, list):
        raise PluginManifestError("mcp.servers must be a list")
    result: list[dict[str, Any]] = []
    names: set[str] = set()
    for raw in servers:
        if not isinstance(raw, dict):
            raise PluginManifestError("each MCP server must be a mapping")
        name = raw.get("name")
        command = raw.get("command")
        if not isinstance(name, str) or not name.strip():
            raise PluginManifestError("MCP server name must be a non-empty string")
        if name.casefold() in names:
            raise PluginManifestError(f"duplicate MCP server name: {name}")
        if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
            raise PluginManifestError(f"MCP server {name} command must be a non-empty string list")
        try:
            timeout = float(raw.get("timeout_seconds", 15))
        except (TypeError, ValueError) as exc:
            raise PluginManifestError(
                f"MCP server {name} timeout_seconds must be numeric"
            ) from exc
        if timeout <= 0:
            raise PluginManifestError(f"MCP server {name} timeout_seconds must be positive")
        names.add(name.casefold())
        result.append({
            "name": name.strip(),
            "command": list(command),
            "timeout_seconds": timeout,
            "enabled": _boolean(raw.get("enabled"), default=True, field=f"MCP server {name} enabled"),
            "plugin_name": plugin_name,
        })
    return tuple(result)


def _permissions(value: Any) -> dict[str, Any]:
    """校验最小权限声明结构；声明只供审计与风险展示，不产生运行时授权。"""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise PluginManifestError("permissions must be a mapping")
    unknown = set(value) - {"filesystem", "network", "subprocess", "secrets"}
    if unknown:
        raise PluginManifestError("unknown permission field(s): " + ", ".join(sorted(unknown)))
    result: dict[str, Any] = {}
    for section, keys in (("filesystem", {"read", "write"}), ("network", {"connect"})):
        raw = value.get(section)
        if raw is None:
            continue
        if not isinstance(raw, dict) or set(raw) - keys:
            raise PluginManifestError(f"permissions.{section} contains unsupported fields")
        result[section] = {key: tuple(_string_list(raw.get(key, []), f"permissions.{section}.{key}")) for key in sorted(keys)}
    if "subprocess" in value:
        result["subprocess"] = _boolean(value["subprocess"], default=False, field="permissions.subprocess")
    if "secrets" in value:
        result["secrets"] = tuple(_string_list(value["secrets"], "permissions.secrets"))
    return result


def _jsonable_permissions(value: dict[str, Any]) -> dict[str, Any]:
    """递归把内部元组转换成列表，生成稳定的 JSON 兼容权限视图。"""
    result: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, dict):
            result[key] = _jsonable_permissions(item)
        elif isinstance(item, tuple):
            result[key] = list(item)
        else:
            result[key] = item
    return result
