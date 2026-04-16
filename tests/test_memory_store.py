import json

import pytest

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
