from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


_SKILL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_EXPLICIT_SKILL = re.compile(
    r"(?:\$|@skill:)([A-Za-z0-9][A-Za-z0-9_-]{0,63})\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SkillDefinition:
    """保存发现阶段所需的轻量 Skill 元数据，不提前读取正文或资源。

    对象来自 ``SKILL.md`` 的 YAML 头部，包含名称、触发描述、允许工具和资源
    清单。``path`` 只定位包入口；正文要等任务真正激活该 Skill 后才读取。
    """

    name: str
    description: str
    path: Path
    triggers: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    scripts: tuple[str, ...] = ()
    enabled: bool = True


@dataclass
class SkillDiscoveryState:
    """记录一次任务的 Skill 发现、选择、加载成本和拒绝原因。

    ``available`` 是本地可识别目录，``discovered`` 是本轮匹配或固定启用的候选，
    ``activated`` 才代表正文进入模型上下文。资源路径和 Token 成本用于诊断预算；
    被禁用、越界或超预算的内容会进入拒绝记录而不是静默消失。
    """

    available: set[str] = field(default_factory=set)
    discovered: set[str] = field(default_factory=set)
    activated: set[str] = field(default_factory=set)
    pinned: set[str] = field(default_factory=set)
    rejected: dict[str, str] = field(default_factory=dict)
    loaded_resources: list[str] = field(default_factory=list)
    rejected_resources: list[str] = field(default_factory=list)
    activation_reasons: dict[str, str] = field(default_factory=dict)
    token_cost: int = 0

    def to_record(self) -> dict[str, Any]:
        """返回稳定、可 JSON 序列化的状态快照，不改变当前发现结果。"""
        return {
            "available": sorted(self.available),
            "discovered": sorted(self.discovered),
            "activated": sorted(self.activated),
            "pinned": sorted(self.pinned),
            "rejected": dict(sorted(self.rejected.items())),
            "loaded_resources": list(self.loaded_resources),
            "rejected_resources": list(self.rejected_resources),
            "activation_reasons": dict(sorted(self.activation_reasons.items())),
            "token_cost": self.token_cost,
        }


class SkillFormatError(ValueError):
    """表示 Skill 入口缺少合法头部；发现会隔离该包并继续处理其他目录。"""


class SkillManager:
    """发现本地 Skill，并按当前请求渐进加载匹配的知识包。

    管理器只在发现阶段读取 ``SKILL.md`` 的 YAML 头部。显式名称、配置固定项或
    元数据关键词命中后，才读取正文和声明资源；所有读取都限制在 Skill 自身目录
    且受 Token 预算约束。脚本仅作为可审计元数据展示，本阶段不会自动执行。
    """

    def __init__(
        self,
        directories: list[str | Path] | tuple[str | Path, ...],
        *,
        disabled: set[str] | None = None,
        pinned: set[str] | None = None,
        max_skill_tokens: int = 2000,
        max_resource_tokens: int = 1000,
        token_counter: Callable[[str], int] | None = None,
    ) -> None:
        """保存目录和预算策略；目录不存在时视为空目录，单个坏包不会阻断启动。"""
        self.directories = tuple(Path(item).expanduser().resolve() for item in directories)
        self.disabled = {item.casefold() for item in disabled or set()}
        self.configured_pinned = {item.casefold() for item in pinned or set()}
        self.max_skill_tokens = max(0, int(max_skill_tokens))
        self.max_resource_tokens = max(0, int(max_resource_tokens))
        self.token_counter = token_counter or (lambda text: max(1, len(text) // 4))
        self.catalog: dict[str, SkillDefinition] = {}
        self.discovery_errors: dict[str, str] = {}
        self.state = SkillDiscoveryState()

    def discover(self) -> dict[str, SkillDefinition]:
        """扫描直接子目录的 ``SKILL.md``，只读取 YAML 头部并建立轻量目录。

        目录顺序代表优先级：同名 Skill 以先发现者为准，后续重复项进入错误记录。
        缺失目录被忽略；格式错误、读取错误和非法名称只隔离当前包。
        """
        catalog: dict[str, SkillDefinition] = {}
        errors: dict[str, str] = {}
        for root in self.directories:
            if not root.is_dir():
                continue
            for entry in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
                skill_file = entry / "SKILL.md"
                if not entry.is_dir() or not skill_file.is_file():
                    continue
                try:
                    definition = self._read_definition(skill_file)
                except (OSError, SkillFormatError, ValueError) as exc:
                    errors[str(skill_file)] = str(exc)
                    continue
                key = definition.name.casefold()
                if key in catalog:
                    errors[str(skill_file)] = f"duplicate_skill:{definition.name}"
                    continue
                catalog[key] = definition
        self.catalog = catalog
        self.discovery_errors = errors
        self.state = SkillDiscoveryState(
            available={item.name for item in catalog.values()},
        )
        return {item.name: item for item in catalog.values()}

    def build_messages(self, user_input: str) -> list[dict[str, str]]:
        """为当前请求选择并加载 Skill，返回仅对本轮模型可见的系统消息。

        显式 ``$name`` / ``@skill:name`` 优先，其次是配置固定项和头部触发短语。
        被禁用或包内声明为关闭的 Skill 会记录拒绝原因。正文共享总 Skill 预算，
        声明资源共享独立资源预算；预算耗尽时停止继续加载并保留诊断信息。
        """
        self.discover()
        selected = self._select(user_input)
        blocks: list[str] = []
        remaining_skill_tokens = self.max_skill_tokens
        remaining_resource_tokens = self.max_resource_tokens
        for key, reason in selected:
            definition = self.catalog.get(key)
            display_name = definition.name if definition is not None else key
            self.state.discovered.add(display_name)
            if definition is None:
                self.state.rejected[display_name] = "not_found"
                continue
            if key in self.disabled:
                self.state.rejected[definition.name] = "disabled_by_config"
                continue
            if not definition.enabled:
                self.state.rejected[definition.name] = "disabled_by_manifest"
                continue
            if remaining_skill_tokens <= 0:
                self.state.rejected[definition.name] = "skill_budget_exhausted"
                continue
            try:
                body = self._read_body(definition.path)
            except (OSError, SkillFormatError) as exc:
                self.state.rejected[definition.name] = f"load_failed:{exc}"
                continue
            header = self._render_activation_header(definition, reason)
            fitted, used, truncated = self._fit_text(
                f"{header}\n{body.strip()}", remaining_skill_tokens
            )
            if not fitted:
                self.state.rejected[definition.name] = "skill_budget_exhausted"
                continue
            remaining_skill_tokens -= used
            self.state.token_cost += used
            self.state.activated.add(definition.name)
            self.state.activation_reasons[definition.name] = reason
            self.state.loaded_resources.append(str(definition.path.resolve()))
            if truncated:
                self.state.rejected_resources.append(
                    f"{definition.name}:skill_body_truncated"
                )
            resource_blocks, resource_used = self._load_resources(
                definition, remaining_resource_tokens
            )
            remaining_resource_tokens -= resource_used
            self.state.token_cost += resource_used
            blocks.append("\n".join([fitted, *resource_blocks]))
        if not blocks:
            return []
        return [{
            "role": "system",
            "content": (
                "Activated local Skills. Follow these task-specific instructions while "
                "keeping higher-priority system and project instructions authoritative.\n\n"
                + "\n\n---\n\n".join(blocks)
            ),
        }]

    def _select(self, user_input: str) -> list[tuple[str, str]]:
        """按显式、固定、隐式顺序去重候选，并保留每项首次激活原因。"""
        selected: dict[str, str] = {}
        for match in _EXPLICIT_SKILL.finditer(user_input):
            raw_name = match.group(1)
            selected.setdefault(raw_name.casefold(), f"explicit:{match.group(0)}")
        for key in sorted(self.configured_pinned):
            selected.setdefault(key, "pinned_by_config")
            definition = self.catalog.get(key)
            self.state.pinned.add(definition.name if definition else key)
        normalized_input = " ".join(user_input.casefold().split())
        for key, definition in self.catalog.items():
            for trigger in definition.triggers:
                normalized_trigger = " ".join(trigger.casefold().split())
                if normalized_trigger and normalized_trigger in normalized_input:
                    selected.setdefault(key, f"implicit:{trigger}")
                    break
        return list(selected.items())

    def _read_definition(self, path: Path) -> SkillDefinition:
        """读取入口文件开头直到 YAML 结束标记，避免发现阶段装入正文。"""
        lines: list[str] = []
        with path.open(encoding="utf-8") as handle:
            if handle.readline().strip() != "---":
                raise SkillFormatError("missing_yaml_front_matter")
            for index, line in enumerate(handle, start=1):
                if line.strip() == "---":
                    break
                if index > 200:
                    raise SkillFormatError("front_matter_too_long")
                lines.append(line)
            else:
                raise SkillFormatError("unterminated_yaml_front_matter")
        try:
            import yaml
        except ImportError as exc:
            raise SkillFormatError("PyYAML is required for SKILL.md") from exc
        metadata = yaml.safe_load("".join(lines)) or {}
        if not isinstance(metadata, dict):
            raise SkillFormatError("front_matter_must_be_mapping")
        name = str(metadata.get("name") or path.parent.name).strip()
        description = str(metadata.get("description") or "").strip()
        if not _SKILL_NAME.fullmatch(name):
            raise SkillFormatError(f"invalid_skill_name:{name}")
        if not description:
            raise SkillFormatError("missing_description")
        return SkillDefinition(
            name=name,
            description=description,
            path=path,
            triggers=self._string_tuple(metadata.get("triggers")),
            resources=self._string_tuple(metadata.get("resources")),
            allowed_tools=self._string_tuple(metadata.get("allowed_tools")),
            scripts=self._string_tuple(metadata.get("scripts")),
            enabled=self._as_bool(metadata.get("enabled"), default=True),
        )

    def _read_body(self, path: Path) -> str:
        """激活后读取正文；头部不完整时失败，避免把元数据误当执行步骤。"""
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            raise SkillFormatError("missing_yaml_front_matter")
        parts = text.split("---", 2)
        if len(parts) != 3:
            raise SkillFormatError("unterminated_yaml_front_matter")
        return parts[2].strip()

    def _load_resources(
        self,
        definition: SkillDefinition,
        budget_tokens: int,
    ) -> tuple[list[str], int]:
        """读取包内声明资源；越界、缺失和预算不足均记录后跳过，不读取任意路径。"""
        blocks: list[str] = []
        used = 0
        skill_root = definition.path.parent.resolve()
        for raw_path in definition.resources:
            resource = (skill_root / raw_path).resolve()
            if not resource.is_relative_to(skill_root):
                self.state.rejected_resources.append(
                    f"{definition.name}:{raw_path}:outside_skill_directory"
                )
                continue
            if not resource.is_file():
                self.state.rejected_resources.append(
                    f"{definition.name}:{raw_path}:missing_resource"
                )
                continue
            remaining = budget_tokens - used
            if remaining <= 0:
                self.state.rejected_resources.append(
                    f"{definition.name}:{raw_path}:resource_budget_exhausted"
                )
                continue
            try:
                content = resource.read_text(encoding="utf-8")
            except OSError as exc:
                self.state.rejected_resources.append(
                    f"{definition.name}:{raw_path}:read_failed:{exc}"
                )
                continue
            fitted, cost, truncated = self._fit_text(content, remaining)
            if not fitted:
                self.state.rejected_resources.append(
                    f"{definition.name}:{raw_path}:resource_budget_exhausted"
                )
                continue
            blocks.append(f"[Resource: {raw_path}]\n{fitted}")
            used += cost
            self.state.loaded_resources.append(str(resource))
            if truncated:
                self.state.rejected_resources.append(
                    f"{definition.name}:{raw_path}:resource_truncated"
                )
        return blocks, used

    def _fit_text(self, text: str, budget_tokens: int) -> tuple[str, int, bool]:
        """用实际计数器把文本压入预算；超限时按字符二分截断并返回真实成本。"""
        cleaned = text.strip()
        if not cleaned or budget_tokens <= 0:
            return "", 0, bool(cleaned)
        cost = max(0, int(self.token_counter(cleaned)))
        if cost <= budget_tokens:
            return cleaned, cost, False
        low, high = 0, len(cleaned)
        while low < high:
            middle = (low + high + 1) // 2
            if int(self.token_counter(cleaned[:middle])) <= budget_tokens:
                low = middle
            else:
                high = middle - 1
        fitted = cleaned[:low].rstrip()
        fitted_cost = int(self.token_counter(fitted)) if fitted else 0
        return fitted, fitted_cost, True

    def _render_activation_header(self, definition: SkillDefinition, reason: str) -> str:
        """渲染模型可理解且可审计的激活说明，工具和脚本仅声明不授予权限。"""
        lines = [
            f"[Skill: {definition.name}]",
            f"Description: {definition.description}",
            f"Activation reason: {reason}",
        ]
        if definition.allowed_tools:
            lines.append("Allowed tool guidance: " + ", ".join(definition.allowed_tools))
        if definition.scripts:
            lines.append(
                "Declared scripts (do not execute automatically): "
                + ", ".join(definition.scripts)
            )
        return "\n".join(lines)

    def _string_tuple(self, value: Any) -> tuple[str, ...]:
        """把 YAML 标量或列表规范成去空白字符串元组，其他类型视为空。"""
        if isinstance(value, str):
            return (value.strip(),) if value.strip() else ()
        if isinstance(value, list):
            return tuple(str(item).strip() for item in value if str(item).strip())
        return ()

    def _as_bool(self, value: Any, *, default: bool) -> bool:
        """解析 YAML/文本布尔值；未知值回退默认策略而不意外启用或禁用。"""
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().casefold()
            if normalized in {"true", "yes", "on", "1"}:
                return True
            if normalized in {"false", "no", "off", "0"}:
                return False
        return default
