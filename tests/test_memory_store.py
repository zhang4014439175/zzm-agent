import json

import pytest

from zzm_agent.core.observability import TokenUsage, UsageState
from zzm_agent.memory.io import StorageCorruptionError, StorageIO
from zzm_agent.memory.token_counter import TokenCounter
from zzm_agent.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    """Fixture to provide a MemoryStore instance with a temporary path."""
    return MemoryStore(path=tmp_path / "memory.json", max_history=10)


def test_append_and_load(store):
    msgs = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    store.append(msgs)
    loaded = store.load_history()
    assert len(loaded) == 2
    assert loaded[0]["content"] == "hello"
    assert loaded[1]["role"] == "assistant"


def test_max_history_truncation(store):
    for i in range(15):
        store.append([{"role": "user", "content": str(i)}])

    loaded = store.load_history()
    assert len(loaded) == 10
    assert loaded[-1]["content"] == "14"
    assert loaded[0]["content"] == "5"


def test_load_history_drops_tool_result_orphaned_by_truncation(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=1)
    store.append(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "echo", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ]
    )

    assert store.load_history() == []


def test_persists_across_instances(tmp_path):
    path = tmp_path / "memory.json"
    store1 = MemoryStore(path=path, max_history=50)
    store1.append([{"role": "user", "content": "persistent"}])

    store2 = MemoryStore(path=path, max_history=50)
    loaded = store2.load_history()
    assert len(loaded) == 1
    assert loaded[0]["content"] == "persistent"


def test_empty_store_returns_empty_list(store):
    assert store.load_history() == []


def test_session_history_is_isolated(tmp_path):
    # Session switching should change the backing history file rather than
    # merging turns from different conversations into one log.
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50)
    first_session = store.session_id
    store.append([{"role": "user", "content": "first"}])

    second_session = store.create_session()["id"]
    store.append([{"role": "user", "content": "second"}])

    assert second_session != first_session
    assert store.load_history()[-1]["content"] == "second"

    store.switch_session(first_session)
    assert store.load_history()[-1]["content"] == "first"


def test_restores_last_session_on_startup(tmp_path):
    # Restart behavior should follow `last_session.txt`, which is the minimum
    # contract needed for a CLI agent to feel persistent between launches.
    path = tmp_path / "memory.json"
    store1 = MemoryStore(path=path, max_history=50)
    first_session = store1.session_id
    store1.append([{"role": "user", "content": "first"}])

    second_session = store1.create_session()["id"]
    store1.append([{"role": "user", "content": "second"}])

    store2 = MemoryStore(path=path, max_history=50)
    assert store2.session_id == second_session
    assert store2.load_history()[-1]["content"] == "second"

    store2.switch_session(first_session)
    assert store2.load_history()[-1]["content"] == "first"


def test_explicit_session_id_resumes_or_creates_target_session(tmp_path):
    # An explicit `--session` style selection must be stable across restarts so
    # external callers can pin a workflow to a named conversation.
    path = tmp_path / "memory.json"
    store = MemoryStore(path=path, max_history=50, session_id="alpha")
    assert store.session_id == "alpha"

    store.append([{"role": "user", "content": "hello"}])

    resumed = MemoryStore(path=path, max_history=50, session_id="alpha")
    assert resumed.load_history()[-1]["content"] == "hello"


def test_usage_state_persists_per_session_without_crossing_accounts(tmp_path):
    path = tmp_path / "memory.json"
    alpha = MemoryStore(path=path, max_history=50, session_id="alpha")
    usage_state = UsageState(conversation_id="alpha")
    usage_state.record_model_call(
        TokenUsage(prompt_tokens=10, completion_tokens=4, total_tokens=14, source="api"),
        model="demo-model",
        tool_schema_tokens=3,
    )
    usage_state.record_tool_calls(1)

    alpha.save_usage_state(usage_state)

    resumed_alpha = MemoryStore(path=path, max_history=50, session_id="alpha")
    beta = MemoryStore(path=path, max_history=50, session_id="beta")

    alpha_usage = resumed_alpha.load_usage_state()
    beta_usage = beta.load_usage_state()

    assert alpha_usage.conversation_id == "alpha"
    assert alpha_usage.conversation.total_tokens == 14
    assert alpha_usage.conversation.tool_calls == 1
    assert alpha_usage.snapshot_for_model("demo-model").tool_schema_tokens == 3
    assert beta_usage.conversation_id == "beta"
    assert beta_usage.conversation.total_tokens == 0
    assert beta_usage.snapshot_for_model("demo-model").total_tokens == 0


def test_explicit_session_id_rejects_path_traversal(tmp_path):
    path = tmp_path / "memory.json"

    with pytest.raises(ValueError):
        MemoryStore(path=path, max_history=50, session_id="../escape")


def test_legacy_memory_is_migrated_once(tmp_path):
    # Migration should preserve the old history and then become a no-op on
    # later startups to avoid duplicated imported sessions.
    path = tmp_path / "memory.json"
    legacy_history = [{"role": "user", "content": "legacy"}]
    path.write_text(json.dumps(legacy_history), encoding="utf-8")

    store = MemoryStore(path=path, max_history=50)
    sessions = store.list_sessions()

    assert len(sessions) == 1
    assert store.load_history() == legacy_history
    assert sessions[0]["name"] == "Migrated Session"

    migrated_session = store.session_id
    again = MemoryStore(path=path, max_history=50)
    assert len(again.list_sessions()) == 1
    assert again.session_id == migrated_session
    assert again.load_history() == legacy_history


def test_migration_failure_rolls_back_to_safe_state(tmp_path, monkeypatch):
    # A partial migration must not leave behind a broken sessions index or a
    # dangling last-session pointer, otherwise the next startup cannot recover.
    path = tmp_path / "memory.json"
    legacy_history = [{"role": "user", "content": "legacy"}]
    path.write_text(json.dumps(legacy_history), encoding="utf-8")

    original_write_text = StorageIO.write_text

    def fail_on_last_session(self, target, value):
        if target.name == "last_session.txt":
            raise OSError("simulated write failure")
        return original_write_text(self, target, value)

    monkeypatch.setattr(StorageIO, "write_text", fail_on_last_session)

    with pytest.raises(OSError):
        MemoryStore(path=path, max_history=50)

    sessions_dir = tmp_path / "sessions"
    assert not (sessions_dir / "index.json").exists()
    assert not (sessions_dir / "last_session.txt").exists()
    assert list(sessions_dir.iterdir()) == []
    assert json.loads(path.read_text(encoding="utf-8")) == legacy_history


def test_startup_cleans_partial_session_state_before_migration(tmp_path):
    # A previous failed migration can leave behind orphan tmp files or an
    # incomplete session directory. Startup should clean those artifacts first
    # so legacy migration can be retried safely.
    path = tmp_path / "memory.json"
    legacy_history = [{"role": "user", "content": "legacy"}]
    path.write_text(json.dumps(legacy_history), encoding="utf-8")

    sessions_dir = tmp_path / "sessions"
    broken_session = sessions_dir / "migrated-broken"
    broken_session.mkdir(parents=True)
    (broken_session / "history.json.tmp").write_text("[]", encoding="utf-8")
    (sessions_dir / "index.json.tmp").write_text("[]", encoding="utf-8")

    store = MemoryStore(path=path, max_history=50)

    assert not broken_session.exists()
    assert not (sessions_dir / "index.json.tmp").exists()
    assert len(store.list_sessions()) == 1
    assert store.load_history() == legacy_history


def test_corrupt_history_file_is_quarantined_and_restored_from_backup(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50, session_id="alpha")
    store.append([{"role": "user", "content": "hello"}])

    history_path = store.history_path
    history_path.write_text("{bad json", encoding="utf-8")

    with pytest.raises(StorageCorruptionError):
        store.load_history()

    quarantined = list(history_path.parent.glob("history.json.corrupt.*"))
    assert quarantined
    assert store.load_history() == [{"role": "user", "content": "hello"}]


def test_corrupt_semantic_file_is_quarantined_and_reinitialized(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50)
    store.semantic_path.write_text("{bad json", encoding="utf-8")

    with pytest.raises(StorageCorruptionError):
        store.load_semantic_memory()

    quarantined = list(store.base_dir.glob("semantic.json.corrupt.*"))
    assert quarantined
    assert store.load_semantic_memory() == []


def test_remember_and_forget_semantic_memory(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50)

    store.remember_fact("User prefers concise answers.")
    store.remember_fact("User prefers concise answers.")
    store.remember_fact("Project language is Python.")

    memories = store.load_semantic_memory()
    assert len(memories) == 2
    assert memories[0]["fact"] == "Project language is Python."

    removed = store.forget_fact("concise")
    assert removed == 1
    assert [entry["fact"] for entry in store.load_semantic_memory()] == [
        "Project language is Python."
    ]


def test_list_semantic_facts_returns_all_long_term_memories(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50)

    store.remember_fact("User prefers concise answers.")
    store.remember_fact("Project language is Python.")

    assert store.list_semantic_facts() == [
        "Project language is Python.",
        "User prefers concise answers.",
    ]


def test_switching_sessions_persists_episodic_summary(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50)
    first_session = store.session_id
    store.append(
        [
            {"role": "user", "content": "What did we decide?"},
            {"role": "assistant", "content": "We decided to ship the Python CLI first."},
        ]
    )

    store.create_session()

    episodic = store.load_episodic(first_session)
    assert episodic is not None
    assert "ship the Python CLI first" in episodic["summary"]


def test_build_memory_messages_respects_injection_limit(tmp_path):
    store = MemoryStore(
        path=tmp_path / "memory.json",
        max_history=50,
        retrieval_top_k=1,
        session_id="alpha",
    )
    store.remember_fact("User prefers concise answers.")
    store.remember_fact("Project language is Python.")
    store.append(
        [
            {"role": "user", "content": "Summarize the current plan."},
            {"role": "assistant", "content": "The plan is to ship session support first."},
        ]
    )
    store.create_session(make_current=True)

    messages = store.build_memory_messages()
    assert len(messages) == 2
    assert "Project language is Python." in messages[0]["content"]
    assert "User prefers concise answers." not in messages[0]["content"]
    assert "ship session support first" in messages[1]["content"]


def test_search_memories_returns_related_semantic_and_episodic_entries(tmp_path):
    store = MemoryStore(
        path=tmp_path / "memory.json",
        max_history=50,
        retrieval_top_k=2,
        session_id="alpha",
    )
    store.remember_fact("Project language is Python.")
    store.remember_fact("User prefers concise answers.")
    store.append(
        [
            {"role": "user", "content": "What should we build?"},
            {"role": "assistant", "content": "Build the Python CLI first."},
        ]
    )
    store.create_session(make_current=True)

    results = store.search_memories("python cli")

    assert [entry["fact"] for entry in results["semantic"]] == [
        "Project language is Python."
    ]
    assert len(results["episodic"]) == 1
    assert "Python CLI" in results["episodic"][0]["summary"]


def test_build_turn_messages_uses_related_memory_retrieval(tmp_path):
    store = MemoryStore(
        path=tmp_path / "memory.json",
        max_history=50,
        retrieval_top_k=2,
        session_id="alpha",
    )
    store.remember_fact("Project language is Python.")
    store.remember_fact("User prefers concise answers.")
    store.append(
        [
            {"role": "user", "content": "What should we build?"},
            {"role": "assistant", "content": "Build the Python CLI first."},
        ]
    )
    store.create_session(make_current=True)

    messages, _compression = store.build_turn_messages(
        system_prompt="sys",
        user_input="Need the Python CLI plan",
    )

    contents = [message["content"] for message in messages if message.get("content")]
    assert any("Project language is Python." in content for content in contents)
    assert any("Build the Python CLI first." in content for content in contents)
    assert not any("User prefers concise answers." in content for content in contents)
    memory_state = _compression["memory_load_state"]
    assert len(memory_state["injected_semantic_memory_ids"]) == 1
    assert len(memory_state["injected_episodic_memory_ids"]) == 1
    assert str(store.semantic_path.resolve(strict=False)) in memory_state["memory_file_versions"]


def test_build_turn_messages_loads_project_instruction_files_with_sources(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Use pytest for verification.", encoding="utf-8")
    nested = tmp_path / "packages" / "api"
    nested.mkdir(parents=True)
    (nested / "ZZM.md").write_text("API package overrides root guidance.", encoding="utf-8")
    store = MemoryStore(
        path=tmp_path / ".zzm_agent" / "memory.json",
        max_history=50,
        workspace_root=tmp_path,
        instruction_max_chars=1000,
    )

    instruction_files = store.list_instruction_files(cwd=nested)
    assert [item.name for item in instruction_files] == ["AGENTS.md", "ZZM.md"]
    assert [item.priority for item in instruction_files] == [0, 1]

    messages = store.build_instruction_messages(cwd=nested)

    assert len(messages) == 1
    content = messages[0]["content"]
    assert "Use pytest for verification." in content
    assert "API package overrides root guidance." in content
    assert content.index("AGENTS.md") < content.index("ZZM.md")
    source_types = [source.source_type for source in store.memory_load_state.sources]
    assert source_types == ["project_instruction", "project_instruction"]


def test_instruction_files_respect_character_budget_and_report_truncation(tmp_path):
    (tmp_path / "AGENTS.md").write_text("A" * 20, encoding="utf-8")
    store = MemoryStore(
        path=tmp_path / ".zzm_agent" / "memory.json",
        max_history=50,
        workspace_root=tmp_path,
        instruction_max_chars=8,
    )

    files = store.list_instruction_files()
    messages = store.build_instruction_messages()

    assert files[0].truncated is True
    assert files[0].loaded_chars == 8
    assert "truncated: loaded 8/20 chars" in messages[0]["content"]


def test_disabled_auto_memory_is_not_injected(tmp_path):
    store = MemoryStore(path=tmp_path / "memory.json", max_history=50)
    store.remember_fact("Project language is Python.")

    disabled = store.set_memory_enabled("python", enabled=False)
    messages = store.build_memory_messages(query="python")

    assert disabled == 1
    assert messages == []
    all_entries = store.list_semantic_memory(include_disabled=True)
    assert all_entries[0]["enabled"] is False


def test_build_memory_messages_deduplicates_retrieved_memory_sources(tmp_path):
    store = MemoryStore(
        path=tmp_path / "memory.json",
        max_history=50,
        retrieval_top_k=2,
        session_id="alpha",
    )
    semantic = store.remember_fact("Project language is Python.")

    class DuplicateRetriever:
        def search(self, query, semantic_entries, episodic_entries, limit):
            return {"semantic": [semantic, semantic], "episodic": []}

    store.retriever = DuplicateRetriever()

    messages = store.build_memory_messages(query="python", limit=2)

    assert len(messages) == 1
    assert messages[0]["content"].count("Project language is Python.") == 1
    assert len(store.memory_load_state.duplicate_sources) == 1


def test_compress_history_preserves_recent_messages_and_adds_summary(tmp_path):
    store = MemoryStore(
        path=tmp_path / "memory.json",
        max_history=50,
        max_context_tokens=40,
        compression_keep_recent=2,
    )
    store.append(
        [
            {"role": "user", "content": "A" * 80},
            {"role": "assistant", "content": "B" * 80},
            {"role": "user", "content": "recent user"},
            {"role": "assistant", "content": "recent assistant"},
        ]
    )

    compressed = store.preview_context_window()

    assert compressed["applied"] is True
    assert compressed["kept_recent_count"] == 2
    assert compressed["messages"][0]["role"] == "system"
    assert "Runtime compression summary" in compressed["messages"][0]["content"]
    assert compressed["messages"][-2]["content"] == "recent user"
    assert compressed["messages"][-1]["content"] == "recent assistant"


def test_compress_history_drops_tool_result_orphaned_by_budget_trim(tmp_path):
    store = MemoryStore(
        path=tmp_path / "memory.json",
        max_history=50,
        max_context_tokens=1,
        compression_keep_recent=2,
    )
    store.append(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "echo", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "x" * 80},
        ]
    )

    compressed = store.preview_context_window()

    assert all(message["role"] != "tool" for message in compressed["messages"])


def test_build_turn_messages_respects_context_budget(tmp_path):
    store = MemoryStore(
        path=tmp_path / "memory.json",
        max_history=50,
        max_context_tokens=45,
        compression_keep_recent=1,
    )
    store.append(
        [
            {"role": "user", "content": "first " * 30},
            {"role": "assistant", "content": "second " * 30},
            {"role": "user", "content": "latest raw message"},
        ]
    )

    messages, compression = store.build_turn_messages(
        system_prompt="sys",
        user_input="new input",
    )

    assert compression["applied"] is True
    assert messages[0]["content"] == "sys"
    assert messages[-1]["content"] == "new input"
    assert any(
        message["role"] == "system"
        and "Runtime compression summary" in message["content"]
        for message in messages[1:-1]
    )
    assert any(
        message["role"] == "user" and message["content"] == "latest raw message"
        for message in messages[1:-1]
    )


def test_token_counter_uses_model_specific_tokenizer_before_fallback():
    counter = TokenCounter(
        model="demo-model",
        model_tokenizers={"demo-model": lambda text: 7},
    )

    counted = counter.count("hello world")

    assert counted.tokens == 7
    assert counted.source == "model"


def test_token_counter_falls_back_to_len_over_four_when_tiktoken_missing(monkeypatch):
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "tiktoken":
            raise ImportError
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    counter = TokenCounter(model="unknown-model")

    counted = counter.count("abcd efgh")

    assert counted.tokens == 3
    assert counted.source == "len/4"


def test_build_turn_messages_injects_pinned_context_before_compressed_history(tmp_path):
    store = MemoryStore(
        path=tmp_path / "memory.json",
        max_history=50,
        max_context_tokens=60,
        compression_keep_recent=1,
    )
    store.append(
        [
            {"role": "user", "content": "old " * 80},
            {"role": "assistant", "content": "older " * 80},
            {"role": "user", "content": "latest raw message"},
        ]
    )

    messages, compression = store.build_turn_messages(
        system_prompt="sys",
        user_input="Fix zzm_agent/memory/store.py and do not remove current behavior",
    )

    contents = [message["content"] for message in messages if message.get("content")]
    pinned = next(content for content in contents if "[Pinned Context]" in content)
    assert "zzm_agent/memory/store.py" in pinned
    assert "do not remove current behavior" in pinned
    assert compression["pinned_context"] == pinned
    assert compression["applied"] is True


def test_compress_history_reports_strategy(tmp_path):
    """验证历史压缩结果会报告所用策略，便于状态诊断和自动续段观察。"""
    store = MemoryStore(
        path=tmp_path / "memory.json",
        max_history=50,
        max_context_tokens=30,
        compression_keep_recent=1,
    )
    store.append(
        [
            {"role": "user", "content": "alpha " * 60},
            {"role": "assistant", "content": "beta " * 60},
            {"role": "user", "content": "recent"},
        ]
    )

    preview = store.preview_context_window()

    assert preview["applied"] is True
    assert preview["compression_strategy"] in {"light", "medium", "heavy"}
    assert "Runtime compression summary" in preview["summary"]
def test_context_budget_explains_all_reserved_sources(tmp_path):
    """验证上下文预算包含全部固定与动态来源，且分类之和等于总占用。"""
    store = MemoryStore(
        path=tmp_path / "memory.json",
        max_history=10,
        max_context_tokens=200,
    )
    store.append(
        [
            {"role": "user", "content": "earlier question"},
            {"role": "assistant", "content": "earlier answer"},
        ]
    )

    messages, context = store.build_turn_messages(
        system_prompt="system",
        user_input="current",
        tool_schema_tokens=11,
        output_reserve_tokens=23,
        prompt_cache_strategy="provider_native",
    )

    breakdown = context["budget_breakdown"]
    assert messages[-1]["content"] == "current"
    assert {
        "system_prompt",
        "instruction_files",
        "memory",
        "pinned_context",
        "history_messages",
        "tool_result",
        "user_input",
        "tool_schema",
        "reflection_prompt",
        "output_reserve",
    } <= set(breakdown)
    assert breakdown["tool_schema"] == 11
    assert breakdown["output_reserve"] == 23
    assert context["total_tokens"] == sum(breakdown.values())
    assert context["prompt_cache_strategy"] == "provider_native"
