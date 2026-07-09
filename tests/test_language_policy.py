from zzm_agent.core.language_policy import resolve_response_language


def test_response_language_detects_explicit_chinese():
    decision = resolve_response_language("请用中文回答：explain this file")

    assert decision.language == "zh-CN"
    assert decision.source == "explicit"
    assert "简体中文" in decision.instruction


def test_response_language_detects_english_input():
    decision = resolve_response_language("Please explain the project structure")

    assert decision.language == "en-US"
    assert decision.source == "input_detected"


def test_response_language_inherits_session_for_slash_command():
    decision = resolve_response_language(
        "/review",
        previous_language="zh-CN",
        config={"ui": {"response_language": "auto"}},
    )

    assert decision.language == "zh-CN"
    assert decision.source == "session"


def test_response_language_uses_config_when_auto_has_no_signal(monkeypatch):
    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.delenv("LC_ALL", raising=False)
    decision = resolve_response_language(
        "/review",
        config={"ui": {"response_language": "en-US"}},
    )

    assert decision.language == "en-US"
    assert decision.source == "config"
