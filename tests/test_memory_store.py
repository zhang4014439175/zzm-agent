import json

import pytest

from zzm_agent.memory.io import StorageCorruptionError, StorageIO
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
