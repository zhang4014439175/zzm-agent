from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


ENV_VALUE_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*))?\}$")


class ConfigScope(str, Enum):
    """Supported configuration scopes from low to high user specificity."""

    GLOBAL = "global"
    PROJECT = "project"
    LOCAL = "local"
    MANAGED = "managed"


@dataclass(frozen=True)
class ConfigSource:
    """One configuration input file and its scope."""

    path: Path
    scope: ConfigScope
    name: str | None = None
    required: bool = False


@dataclass(frozen=True)
class ConfigOrigin:
    """Audit record for the source that produced one effective config value."""

    key: str
    scope: ConfigScope
    path: str
    locked: bool = False

    def to_record(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "scope": self.scope.value,
            "path": self.path,
            "locked": self.locked,
        }


@dataclass(frozen=True)
class ConfigLoadResult:
    """Effective config plus source and lock metadata."""

    config: dict[str, Any]
    sources: list[ConfigSource] = field(default_factory=list)
    origins: dict[str, ConfigOrigin] = field(default_factory=dict)
    locked_keys: set[str] = field(default_factory=set)
    profile: str = "default"


class ConfigManager:
    """Load and merge scoped YAML config files with source audit metadata."""

    def __init__(
        self,
        *,
        cwd: str | Path | None = None,
        repo_root: str | Path | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.cwd = Path(cwd or Path.cwd()).resolve()
        self.repo_root = Path(repo_root or Path(__file__).resolve().parents[2]).resolve()
        self.env = env if env is not None else os.environ

    def resolve_default_sources(
        self,
        explicit_path: str | Path | None = None,
    ) -> list[ConfigSource]:
        """Return existing default config sources in merge order."""
        if explicit_path is not None:
            return [
                ConfigSource(
                    path=Path(explicit_path).expanduser().resolve(),
                    scope=ConfigScope.PROJECT,
                    name="explicit",
                    required=True,
                )
            ]

        sources: list[ConfigSource] = []
        env_config = self.env.get("ZZM_AGENT_CONFIG")
        if env_config:
            sources.append(
                ConfigSource(
                    path=Path(env_config).expanduser().resolve(),
                    scope=ConfigScope.PROJECT,
                    name="env",
                    required=True,
                )
            )
            return sources

        for path, scope, name in (
            (Path.home() / ".zzm_agent" / "config.yaml", ConfigScope.GLOBAL, "global"),
            (self.repo_root / "config.yaml", ConfigScope.PROJECT, "repo"),
            (self.cwd / "config.yaml", ConfigScope.PROJECT, "workspace"),
            (self.cwd / ".zzm_agent" / "config.local.yaml", ConfigScope.LOCAL, "local"),
        ):
            resolved = path.expanduser().resolve()
            if resolved.exists() and all(source.path != resolved for source in sources):
                sources.append(ConfigSource(path=resolved, scope=scope, name=name))
        return sources

    def load(
        self,
        *,
        explicit_path: str | Path | None = None,
        sources: list[ConfigSource] | None = None,
        profile: str | None = None,
    ) -> ConfigLoadResult:
        """Load config sources and return the effective config with audit metadata."""
        active_profile = profile or self.env.get("ZZM_AGENT_PROFILE") or "default"
        active_sources = list(sources or self.resolve_default_sources(explicit_path))
        if not active_sources:
            raise FileNotFoundError(
                "config.yaml not found. Use --config or set ZZM_AGENT_CONFIG."
            )

        effective: dict[str, Any] = {}
        origins: dict[str, ConfigOrigin] = {}
        locked_keys: set[str] = set()
        loaded_sources: list[ConfigSource] = []

        for source in active_sources:
            if not source.path.exists():
                if source.required:
                    raise FileNotFoundError(f"Config file not found: {source.path}")
                continue
            data = self._read_yaml(source.path)
            data = self._select_profile(data, active_profile)
            managed_keys = self._managed_locked_keys(data)
            data.pop("managed", None)
            data = self._expand_env_value(data)
            self._merge_mapping(
                effective,
                data,
                source=source,
                origins=origins,
                locked_keys=locked_keys,
                force=source.scope is ConfigScope.MANAGED,
            )
            for key in managed_keys:
                if self._has_path(effective, key):
                    locked_keys.add(key)
                    origin = origins.get(key)
                    if origin is not None:
                        origins[key] = ConfigOrigin(
                            key=origin.key,
                            scope=origin.scope,
                            path=origin.path,
                            locked=True,
                        )
            loaded_sources.append(source)

        primary = self._primary_config_source(loaded_sources)
        if primary is not None:
            effective["_config_path"] = str(primary.path)
            effective["_config_dir"] = str(primary.path.parent)
        effective["_config_profile"] = active_profile
        effective["_config_sources"] = [
            {
                "path": str(source.path),
                "scope": source.scope.value,
                "name": source.name or source.scope.value,
            }
            for source in loaded_sources
        ]
        effective["_config_origin"] = {
            key: origin.to_record()
            for key, origin in sorted(origins.items())
        }
        effective["_config_locked"] = sorted(locked_keys)
        return ConfigLoadResult(
            config=effective,
            sources=loaded_sources,
            origins=origins,
            locked_keys=locked_keys,
            profile=active_profile,
        )

    def _read_yaml(self, path: Path) -> dict[str, Any]:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("PyYAML is required to load config.yaml.") from exc

        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        if not isinstance(data, dict):
            raise ValueError(f"Config file must contain a mapping: {path}")
        return dict(data)

    def _select_profile(self, data: dict[str, Any], profile: str) -> dict[str, Any]:
        profiles = data.get("profiles")
        base = {key: value for key, value in data.items() if key != "profiles"}
        if not isinstance(profiles, dict):
            return base
        selected = profiles.get(profile)
        if selected is None and profile != "default":
            selected = profiles.get("default")
        if isinstance(selected, dict):
            self._deep_merge(base, selected)
        return base

    def _managed_locked_keys(self, data: dict[str, Any]) -> list[str]:
        managed = data.get("managed")
        if not isinstance(managed, dict):
            return []
        locked = managed.get("locked_keys") or managed.get("locked") or []
        if isinstance(locked, str):
            return [locked]
        if isinstance(locked, list):
            return [str(item) for item in locked]
        return []

    def _merge_mapping(
        self,
        target: dict[str, Any],
        incoming: dict[str, Any],
        *,
        source: ConfigSource,
        origins: dict[str, ConfigOrigin],
        locked_keys: set[str],
        force: bool = False,
        prefix: str = "",
    ) -> None:
        for key, value in incoming.items():
            dotted = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                current = target.setdefault(key, {})
                if isinstance(current, dict):
                    self._merge_mapping(
                        current,
                        value,
                        source=source,
                        origins=origins,
                        locked_keys=locked_keys,
                        force=force,
                        prefix=dotted,
                    )
                    continue
            if dotted in locked_keys and not force:
                continue
            target[key] = value
            origins[dotted] = ConfigOrigin(
                key=dotted,
                scope=source.scope,
                path=str(source.path),
                locked=dotted in locked_keys or source.scope is ConfigScope.MANAGED,
            )
            if source.scope is ConfigScope.MANAGED:
                locked_keys.add(dotted)

    def _deep_merge(self, target: dict[str, Any], incoming: dict[str, Any]) -> None:
        for key, value in incoming.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                self._deep_merge(target[key], value)
            else:
                target[key] = value

    def _expand_env_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._expand_env_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._expand_env_value(item) for item in value]
        if not isinstance(value, str):
            return value

        match = ENV_VALUE_PATTERN.fullmatch(value.strip())
        if match is None:
            return value
        env_name, default = match.groups()
        return self.env.get(env_name, default or "")

    def _has_path(self, data: dict[str, Any], dotted: str) -> bool:
        current: Any = data
        for part in dotted.split("."):
            if not isinstance(current, dict) or part not in current:
                return False
            current = current[part]
        return True

    def _primary_config_source(self, sources: list[ConfigSource]) -> ConfigSource | None:
        for scope in (ConfigScope.PROJECT, ConfigScope.GLOBAL, ConfigScope.LOCAL, ConfigScope.MANAGED):
            for source in reversed(sources):
                if source.scope is scope:
                    return source
        return sources[-1] if sources else None
