from unittest.mock import MagicMock

from zzm_agent.core.agent_loop import AgentLoop
from zzm_agent.core.tool_registry import ToolRegistry
from zzm_agent.memory.store import MemoryStore
from zzm_agent.prompt.manager import PromptManager, detect_intent

from tests.test_agent_loop import make_response


def test_detect_intent_prefers_coding_for_paths_and_code_words():
    assert detect_intent("请实现 docs/example.md 里描述的功能") == "coding"
    assert detect_intent("修复这个 pytest 失败") == "coding"


def test_detect_intent_identifies_analysis_requests():
    assert detect_intent("查看这个项目目前执行到了哪一步") == "analysis"


def test_prompt_manager_injects_project_rules_environment_and_tools(tmp_path):
    rules_dir = tmp_path / ".zzm_agent"
    rules_dir.mkdir()
    (rules_dir / "rules.md").write_text("- 所有回答使用中文\n", encoding="utf-8")

    registry = ToolRegistry()

    @registry.tool(
        description="读取文件内容",
        risk_level="low",
        group="files",
        examples=["read_file(path='README.md')"],
    )
    def read_file(path: str) -> str:
        """Read a file.

        Args:
            path: File path to read.
        """
        return path

    manager = PromptManager(
        base_prompt="基础身份",
        workspace_root=tmp_path,
        registry=registry,
    )

    prompt = manager.build("请分析当前状态", history=[])

    assert "你是一个细致的代码与项目分析专家" in prompt
    assert "基础身份" in prompt
    assert "[Project Rules]" in prompt
    assert "- 所有回答使用中文" in prompt
    assert "[Environment]" in prompt
    assert f"Workspace: {tmp_path.resolve()}" in prompt
    assert "[Tools]" in prompt
    assert "read_file (low, group=files): 读取文件内容" in prompt
    assert "example: read_file(path='README.md')" in prompt
    assert "[Response Protocol]" in prompt
    assert "Mode A - Tool call" in prompt
    assert "Mode B - Final response" in prompt
    assert "Analysis response shape" in prompt
    assert "Do not expose hidden reasoning" in prompt
    assert "[Output Format]" in prompt


def test_prompt_manager_uses_intent_specific_response_protocol(tmp_path):
    manager = PromptManager(
        base_prompt="基础身份",
        workspace_root=tmp_path,
        registry=ToolRegistry(),
    )

    coding_prompt = manager.build("请修复 tests/test_demo.py 里的 bug", history=[])
    chat_prompt = manager.build("你好", history=[])

    assert "Coding response shape" in coding_prompt
    assert "Analysis response shape" not in coding_prompt
    assert "Chat response shape" in chat_prompt
    assert "Coding response shape" not in chat_prompt


def test_agent_loop_uses_prompt_manager_per_turn(tmp_path):
    registry = ToolRegistry()
    store = MemoryStore(path=tmp_path / "memory.json", max_history=10)
    manager = PromptManager(
        base_prompt="静态基础 prompt",
        workspace_root=tmp_path,
        registry=registry,
    )
    loop = AgentLoop(
        client=MagicMock(),
        model="test-model",
        system_prompt="旧 prompt",
        registry=registry,
        store=store,
        prompt_manager=manager,
    )
    loop.client.chat.completions.create.return_value = make_response(content="ok")

    assert loop.run("请查看当前状态", stream=False) == "ok"

    kwargs = loop.client.chat.completions.create.call_args.kwargs
    system_message = kwargs["messages"][0]["content"]
    assert "旧 prompt" not in system_message
    assert "静态基础 prompt" in system_message
    assert "细致的代码与项目分析专家" in system_message
