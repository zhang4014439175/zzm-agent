from __future__ import annotations

from typing import Any


def build_provider_default_headers(model_config: dict[str, Any]) -> dict[str, str]:
    """根据模型服务配置生成安全的客户端级默认请求头。

    当前只有 OpenRouter 需要应用归属信息：``HTTP-Referer`` 提供稳定的项目 URL，
    ``X-OpenRouter-Title`` 决定控制台显示名称。只有服务地址属于 OpenRouter 且配置
    了 URL 时才返回这些头，避免把平台专属元数据发送给其他 OpenAI 兼容服务。
    名称为空时仍保留 URL 归属；只有名称而没有 URL 时返回空字典，因为 OpenRouter
    不会据此创建应用记录。
    """
    base_url = str(model_config.get("base_url") or "").strip().casefold()
    if "openrouter.ai" not in base_url:
        return {}

    referer = str(model_config.get("openrouter_referer") or "").strip()
    if not referer:
        return {}

    headers = {"HTTP-Referer": referer}
    title = str(model_config.get("openrouter_title") or "").strip()
    if title:
        headers["X-OpenRouter-Title"] = title
    return headers
