from __future__ import annotations

from zzm_agent.core.provider_headers import build_provider_default_headers


def test_openrouter_headers_include_project_url_and_display_name() -> None:
    """验证 OpenRouter 请求携带项目 URL 和名称，防止控制台继续显示 Unknown。"""
    headers = build_provider_default_headers(
        {
            "base_url": "https://openrouter.ai/api/v1",
            "openrouter_referer": "https://github.com/zhang4014439175/zzm-agent",
            "openrouter_title": "zzm-agent",
        }
    )

    assert headers == {
        "HTTP-Referer": "https://github.com/zhang4014439175/zzm-agent",
        "X-OpenRouter-Title": "zzm-agent",
    }


def test_openrouter_title_without_referer_is_not_sent() -> None:
    """验证缺少必需 URL 时不发送孤立名称，避免产生看似已归属的错误配置。"""
    assert build_provider_default_headers(
        {
            "base_url": "https://openrouter.ai/api/v1",
            "openrouter_title": "zzm-agent",
        }
    ) == {}


def test_openrouter_headers_are_not_sent_to_other_providers() -> None:
    """验证平台专属标识不会泄露给普通 OpenAI 兼容服务。"""
    assert build_provider_default_headers(
        {
            "base_url": "https://api.openai.com/v1",
            "openrouter_referer": "https://github.com/zhang4014439175/zzm-agent",
            "openrouter_title": "zzm-agent",
        }
    ) == {}
