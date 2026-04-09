import pytest
from pathlib import Path
from zzm_agent.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    """Fixture to provide a MemoryStore instance with a temporary path."""
    return MemoryStore(path=tmp_path / "memory.json", max_history=10)


def test_append_and_load(store):
    """Test that messages can be appended to and loaded from the store."""
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
    """Test that the store correctly truncates history based on max_history."""
    # Append 15 messages, but max_history is 10
    for i in range(15):
        store.append([{"role": "user", "content": str(i)}])
    
    loaded = store.load_history()
    assert len(loaded) == 10
    # The last message should be "14"
    assert loaded[-1]["content"] == "14"
    # The first message in the truncated list should be "5"
    assert loaded[0]["content"] == "5"


def test_persists_across_instances(tmp_path):
    """Test that data is persisted to disk and can be loaded by a new instance."""
    path = tmp_path / "memory.json"
    store1 = MemoryStore(path=path, max_history=50)
    store1.append([{"role": "user", "content": "persistent"}])
    
    # New instance pointing to the same file
    store2 = MemoryStore(path=path, max_history=50)
    loaded = store2.load_history()
    assert len(loaded) == 1
    assert loaded[0]["content"] == "persistent"


def test_empty_store_returns_empty_list(store):
    """Test that an empty store (or non-existent file) returns an empty list."""
    assert store.load_history() == []
