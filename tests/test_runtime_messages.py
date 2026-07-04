from zzm_agent.core.runtime_messages import ConversationMessageStore


def test_message_store_keeps_runtime_pending_and_model_context_separate():
    committed_batches: list[list[dict]] = []
    ledger = ConversationMessageStore.begin_turn(
        persisted_messages=[{"role": "user", "content": "old"}],
        model_context_messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "new"},
        ],
        user_message={"role": "user", "content": "new"},
    )

    ledger.append_runtime_only({"role": "system", "content": "reflection"})
    ledger.append_pending({"role": "assistant", "content": "answer"})
    model_context = ledger.prepare_model_context()
    committed = ledger.commit(committed_batches.append)

    assert [message["content"] for message in model_context] == [
        "sys",
        "new",
        "reflection",
        "answer",
    ]
    assert committed == [
        {"role": "user", "content": "new"},
        {"role": "assistant", "content": "answer"},
    ]
    assert committed_batches == [committed]
    assert ledger.pending_messages == []
    assert ledger.persisted_messages == [
        {"role": "user", "content": "old"},
        {"role": "user", "content": "new"},
        {"role": "assistant", "content": "answer"},
    ]


def test_message_store_rollback_drops_pending_without_touching_committed_history():
    ledger = ConversationMessageStore.begin_turn(
        persisted_messages=[{"role": "assistant", "content": "old"}],
        model_context_messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "new"},
        ],
        user_message={"role": "user", "content": "new"},
    )
    ledger.append_pending({"role": "assistant", "content": "partial"})

    rolled_back = ledger.rollback_pending()

    assert rolled_back == [
        {"role": "user", "content": "new"},
        {"role": "assistant", "content": "partial"},
    ]
    assert ledger.pending_messages == []
    assert ledger.persisted_messages == [{"role": "assistant", "content": "old"}]


def test_message_store_copies_inputs_to_prevent_external_mutation():
    user_message = {"role": "user", "content": "new"}
    model_context = [{"role": "system", "content": "sys"}, user_message]
    ledger = ConversationMessageStore.begin_turn(
        persisted_messages=[],
        model_context_messages=model_context,
        user_message=user_message,
    )

    user_message["content"] = "mutated"
    model_context[0]["content"] = "mutated"

    assert ledger.runtime_messages == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "new"},
    ]
    assert ledger.pending_messages == [{"role": "user", "content": "new"}]
