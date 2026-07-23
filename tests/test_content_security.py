from __future__ import annotations

from zzm_agent.core.observability import summarize_arguments, tool_error_event
from zzm_agent.core.tool_results import ToolResult
from zzm_agent.runtime.events import RuntimeEvent
from zzm_agent.security.content import ContentTrust, REDACTED, redact_secrets


def test_recursive_redaction_masks_keys_and_inline_credentials() -> None:
    value = {
        "api_key": "sk-super-secret-value",
        "nested": ["Authorization: Bearer abcdefghijklmnop", "safe text"],
    }

    redacted = redact_secrets(value)

    assert redacted["api_key"] == REDACTED
    assert "abcdefghijklmnop" not in redacted["nested"][0]
    assert redacted["nested"][1] == "safe text"


def test_runtime_event_redacts_only_when_serialized() -> None:
    event = RuntimeEvent(
        event_type="provider.request",
        payload={"token": "secret-token-value", "model": "demo"},
    )

    assert event.payload["token"] == "secret-token-value"
    assert event.to_record()["payload"] == {"token": REDACTED, "model": "demo"}


def test_tool_observability_redacts_arguments_and_errors() -> None:
    arguments = summarize_arguments({"password": "hunter2", "path": "README.md"})
    event = tool_error_event(
        tool_name="run_shell",
        tool_call_id="call-1",
        arguments={"command": "echo token=abcdef123456"},
        risk_level="high",
        duration_ms=1,
        attempts=1,
        error_type="RuntimeError",
        error_message="Bearer abcdefghijklmnop",
    )

    assert arguments["password"] == REDACTED
    record = event.to_record()
    assert "abcdef123456" not in str(record)
    assert "abcdefghijklmnop" not in str(record)


def test_tool_results_are_untrusted_and_redacted_for_model_and_records() -> None:
    result = ToolResult.from_text(
        tool_call_id="call-1",
        tool_name="fetch_web",
        status="ok",
        content="page token=abcdef123456",
    )

    assert result.content_trust is ContentTrust.UNTRUSTED
    assert result.content_source == "fetch_web"
    assert result.metadata["content_trust"] == "untrusted"
    assert "abcdef123456" not in result.to_model_message()["content"]
    assert "abcdef123456" not in str(result.to_record())


def test_old_tool_result_records_default_to_untrusted() -> None:
    result = ToolResult.from_record(
        {
            "tool_call_id": "call-old",
            "tool_name": "legacy_tool",
            "status": "ok",
            "model_content": "legacy",
        }
    )

    assert result.content_trust is ContentTrust.UNTRUSTED
    assert result.content_source == "legacy_tool"

