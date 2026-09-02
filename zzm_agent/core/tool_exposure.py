from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


_EXPLICIT_MCP = re.compile(r"@mcp:([A-Za-z0-9_.-]+)", re.IGNORECASE)
_SEARCH_WORD = re.compile(r"[A-Za-z0-9_\-\u4e00-\u9fff]+")
_TASK_STOP_WORDS = {
    "a", "an", "and", "for", "from", "in", "of", "on", "or", "please",
    "the", "this", "to", "use", "with", "一个", "一下", "使用", "工具", "请",
}


@dataclass
class ToolExposureState:
    """记录一轮任务中工具 Schema 的候选、暴露原因与预算收益。

    ``available`` 包含可被搜索的延迟工具，``exposed`` 是实际发给模型的完整工具
    名，``hidden`` 则用于说明本轮省略了什么。Token 字段只描述 Schema 成本，
    不代表工具调用授权；实际执行仍由 Registry 和权限账本决定。
    """

    available: set[str] = field(default_factory=set)
    exposed: set[str] = field(default_factory=set)
    hidden: set[str] = field(default_factory=set)
    activation_reasons: dict[str, str] = field(default_factory=dict)
    search_queries: list[str] = field(default_factory=list)
    full_schema_tokens: int = 0
    exposed_schema_tokens: int = 0
    schema_tokens_saved: int = 0

    def to_record(self) -> dict[str, Any]:
        """生成稳定、可 JSON 序列化的诊断记录，不改变当前暴露集合。"""
        return {
            "available": sorted(self.available),
            "exposed": sorted(self.exposed),
            "hidden": sorted(self.hidden),
            "activation_reasons": dict(sorted(self.activation_reasons.items())),
            "search_queries": list(self.search_queries),
            "full_schema_tokens": self.full_schema_tokens,
            "exposed_schema_tokens": self.exposed_schema_tokens,
            "schema_tokens_saved": self.schema_tokens_saved,
        }


class ToolExposureManager:
    """为模型维护按任务启用的工具 Schema 视图和可搜索目录。

    常驻工具保持兼容并直接暴露；带 ``lazy_schema`` 元数据的工具默认只进入轻量
    目录。用户的 ``@mcp:`` 选择、任务关键词、Skill ``allowed_tools`` 或模型调用
    ``tool_search`` 后，命中项才进入本轮 Schema。管理器从不直接执行被搜索出的
    业务工具，因此不会绕过参数校验、风险等级或人工确认。
    """

    SEARCH_TOOL_NAME = "tool_search"

    def __init__(
        self,
        registry: Any,
        *,
        token_counter: Callable[[str], int] | None = None,
    ) -> None:
        """绑定注册表并准备空状态；存在延迟工具时注册低风险搜索入口。"""
        self.registry = registry
        self.token_counter = token_counter or (lambda text: max(1, len(text) // 4))
        self.state = ToolExposureState()
        self._enabled: set[str] = set()
        self._ensure_search_tool()

    def prepare_for_turn(
        self,
        user_input: str,
        *,
        allowed_tools: Iterable[str] = (),
        continuation: bool | None = None,
    ) -> ToolExposureState:
        """根据当前任务、Skill 声明和续段阶段建立新的暴露状态。

        普通新任务会清空上一轮动态选择；自动续段默认保留已启用项，防止模型在
        使用工具后的安全换段丢失同一能力。显式前缀只接受完整 MCP 公共标识，
        自然语言匹配使用长度至少为三的关键词以减少泛化误暴露。
        """
        self._ensure_search_tool()
        is_continuation = (
            "[CONTINUE_TASK_FROM_CHECKPOINT]" in user_input
            if continuation is None
            else bool(continuation)
        )
        previous = set(self._enabled) if is_continuation else set()
        lazy_names = set(self._lazy_tool_names())
        self._enabled = previous & lazy_names
        self.state = ToolExposureState(available=lazy_names)
        for name in sorted(self._enabled):
            self.state.activation_reasons[name] = "stage:continuation"

        for match in _EXPLICIT_MCP.finditer(user_input):
            name = self._resolve_public_name(match.group(1), source="mcp")
            if name is not None:
                self._enable(name, f"explicit:{match.group(0)}")

        for name in allowed_tools:
            resolved = self._resolve_allowed_name(str(name))
            if resolved is not None:
                self._enable(resolved, "skill:allowed_tools")

        for name in self._task_matches(user_input):
            self._enable(name, f"task:{self._public_name(name)}")
        self._refresh_state()
        return self.state

    def get_schemas(self) -> list[dict[str, Any]]:
        """返回当前可见 Schema，并同步统计隐藏数量和 Token 节省。"""
        schemas = [
            metadata["schema"]
            for name, metadata in self.registry.tools.items()
            if not metadata.get("lazy_schema", False) or name in self._enabled
        ]
        self._refresh_state(schemas)
        return schemas

    def is_exposed(self, name: str) -> bool:
        """判断模型本轮是否获知指定工具，供文本工具调用兼容路径做边界校验。"""
        metadata = self.registry.tools.get(name)
        if metadata is None:
            return False
        return not metadata.get("lazy_schema", False) or name in self._enabled

    def search_and_enable(
        self,
        query: str,
        source: str = "mcp",
        limit: int = 5,
    ) -> dict[str, Any]:
        """搜索延迟目录并启用最相关候选，返回给模型紧凑的选择证据。

        ``source`` 当前接受 ``mcp`` 或 ``all``；非法来源返回空结果而不扩大暴露
        面。结果只改变后续模型请求携带的 Schema，真正调用命中工具时仍会进入
        原有权限确认链路。
        """
        normalized_source = source.strip().casefold()
        if normalized_source not in {"mcp", "all"}:
            return {"query": query, "source": normalized_source, "enabled": [], "matches": []}
        bounded_limit = max(1, min(20, int(limit)))
        matches = self._search(query, source=normalized_source, limit=bounded_limit)
        self.state.search_queries.append(query.strip())
        enabled: list[str] = []
        records: list[dict[str, str]] = []
        for name in matches:
            self._enable(name, f"tool_search:{query.strip()}")
            enabled.append(name)
            metadata = self.registry.tools[name]
            records.append({
                "name": name,
                "selector": f"@mcp:{self._public_name(name)}" if self._is_mcp(name) else name,
                "server": str(metadata.get("server_name") or ""),
                "description": str(metadata.get("description") or ""),
            })
        self._refresh_state()
        return {
            "query": query,
            "source": normalized_source,
            "enabled": enabled,
            "matches": records,
        }

    def completion_candidates(self, query: str, *, limit: int = 20) -> list[dict[str, str]]:
        """为 ``@mcp:`` 菜单返回模糊候选；该只读操作不会启用或调用工具。"""
        records: list[dict[str, str]] = []
        for name in self._search(query, source="mcp", limit=max(1, min(50, limit))):
            metadata = self.registry.tools[name]
            records.append({
                "name": name,
                "insert_text": f"@mcp:{self._public_name(name)}",
                "server": str(metadata.get("server_name") or self._mcp_server(name)),
                "description": str(metadata.get("description") or ""),
            })
        return records

    def _ensure_search_tool(self) -> None:
        """在存在延迟候选时注册唯一的模型搜索工具，重复调用保持幂等。"""
        if not self._lazy_tool_names() or self.SEARCH_TOOL_NAME in self.registry.tools:
            return
        self.registry.register_external_tool(
            name=self.SEARCH_TOOL_NAME,
            description=(
                "Search the deferred tool catalog and enable relevant schemas for the "
                "next model step. This does not execute the selected tools."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Capability to find"},
                    "source": {
                        "type": "string",
                        "enum": ["mcp", "all"],
                        "description": "Catalog source; use mcp for remote tools",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
            },
            handler=self.search_and_enable,
            risk_level="low",
            group="Runtime",
            source="runtime",
            lazy_schema=False,
        )

    def _lazy_tool_names(self) -> list[str]:
        """按注册顺序返回延迟 Schema 工具名，不包含搜索工具自身。"""
        return [
            name for name, metadata in self.registry.tools.items()
            if name != self.SEARCH_TOOL_NAME and metadata.get("lazy_schema", False)
        ]

    def _enable(self, name: str, reason: str) -> None:
        """把合法延迟工具加入本轮集合，并保留首次选择原因以便审计。"""
        if name not in self.registry.tools or not self.registry.tools[name].get("lazy_schema", False):
            return
        self._enabled.add(name)
        self.state.activation_reasons.setdefault(name, reason)

    def _refresh_state(self, schemas: list[dict[str, Any]] | None = None) -> None:
        """依据注册表与当前集合刷新可见性及完整/精简 Schema Token 统计。"""
        visible = schemas if schemas is not None else [
            metadata["schema"]
            for name, metadata in self.registry.tools.items()
            if not metadata.get("lazy_schema", False) or name in self._enabled
        ]
        full = [metadata["schema"] for metadata in self.registry.tools.values()]
        self.state.available = set(self._lazy_tool_names())
        self.state.exposed = {
            str(item.get("function", {}).get("name", "")) for item in visible
            if item.get("function", {}).get("name")
        }
        self.state.hidden = self.state.available - self._enabled
        self.state.full_schema_tokens = self._schema_tokens(full)
        self.state.exposed_schema_tokens = self._schema_tokens(visible)
        self.state.schema_tokens_saved = max(
            0, self.state.full_schema_tokens - self.state.exposed_schema_tokens
        )

    def _schema_tokens(self, schemas: list[dict[str, Any]]) -> int:
        """使用运行时计数器估算稳定 JSON Schema 文本成本，空目录成本为零。"""
        if not schemas:
            return 0
        payload = json.dumps(schemas, ensure_ascii=False, sort_keys=True, default=str)
        return max(0, int(self.token_counter(payload)))

    def _task_matches(self, user_input: str) -> list[str]:
        """用有辨识度的任务词匹配名称和说明，并按得分返回延迟候选。"""
        if _EXPLICIT_MCP.search(user_input):
            cleaned = _EXPLICIT_MCP.sub(" ", user_input)
        else:
            cleaned = user_input
        terms = {
            word.casefold() for word in _SEARCH_WORD.findall(cleaned)
            if len(word) >= 3 and word.casefold() not in _TASK_STOP_WORDS
        }
        if not terms:
            return []
        scored: list[tuple[int, str]] = []
        for name in self._lazy_tool_names():
            metadata = self.registry.tools[name]
            searchable = " ".join([
                self._public_name(name).replace("_", " ").replace(".", " "),
                str(metadata.get("description") or ""),
            ]).casefold()
            score = sum(2 if term in self._public_name(name).casefold() else 1 for term in terms if term in searchable)
            if score >= 2 or sum(1 for term in terms if term in searchable) >= 2:
                scored.append((score, name))
        return [name for _, name in sorted(scored, key=lambda item: (-item[0], item[1]))[:5]]

    def _search(self, query: str, *, source: str, limit: int) -> list[str]:
        """对名称、服务名和说明执行词组/子序列模糊排序，返回完整注册名。"""
        compact_query = "".join(query.casefold().split())
        query_terms = [item.casefold() for item in _SEARCH_WORD.findall(query) if item]
        scored: list[tuple[int, str]] = []
        for name in self._lazy_tool_names():
            if source == "mcp" and not self._is_mcp(name):
                continue
            metadata = self.registry.tools[name]
            public = self._public_name(name).casefold()
            description = str(metadata.get("description") or "").casefold()
            compact_public = public.replace(".", "").replace("_", "").replace("-", "")
            if not compact_query:
                score = 1
            elif compact_query in public or compact_query in description:
                score = 100
            elif query_terms and all(term in f"{public} {description}" for term in query_terms):
                score = 80 + len(query_terms)
            elif self._is_subsequence(compact_query.replace(".", "").replace("_", ""), compact_public):
                score = 50
            else:
                continue
            scored.append((score, name))
        return [name for _, name in sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]]

    def _resolve_public_name(self, public_name: str, *, source: str) -> str | None:
        """把用户可见选择器映射为唯一完整工具名，不对部分输入做执行期猜测。"""
        normalized = public_name.casefold()
        for name in self._lazy_tool_names():
            if source == "mcp" and not self._is_mcp(name):
                continue
            if self._public_name(name).casefold() == normalized:
                return name
        return None

    def _resolve_allowed_name(self, allowed_name: str) -> str | None:
        """解析 Skill 工具声明，支持完整名、MCP 公共名和无歧义远端短名。"""
        normalized = allowed_name.strip().casefold()
        matches = [
            name for name in self._lazy_tool_names()
            if normalized in {
                name.casefold(),
                self._public_name(name).casefold(),
                name.rsplit(".", 1)[-1].casefold(),
            }
        ]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _public_name(name: str) -> str:
        """把内部 ``mcp.服务.工具`` 名转换成 ``@mcp:`` 后使用的公共标识。"""
        return name[4:] if name.casefold().startswith("mcp.") else name

    @staticmethod
    def _is_mcp(name: str) -> bool:
        """根据稳定命名空间判断候选是否来自 MCP 服务。"""
        return name.casefold().startswith("mcp.")

    @staticmethod
    def _mcp_server(name: str) -> str:
        """从完整 MCP 工具名提取服务名，格式异常时返回空字符串。"""
        parts = name.split(".", 2)
        return parts[1] if len(parts) == 3 and parts[0].casefold() == "mcp" else ""

    @staticmethod
    def _is_subsequence(needle: str, haystack: str) -> bool:
        """判断查询字符是否按顺序出现在候选中，空查询由调用方单独处理。"""
        iterator = iter(haystack)
        return bool(needle) and all(character in iterator for character in needle)
