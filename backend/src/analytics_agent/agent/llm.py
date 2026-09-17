from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlparse

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import SecretStr

from analytics_agent.config import settings

# ── Per-provider factory functions ────────────────────────────────────────────


def _make_anthropic(model: str, streaming: bool) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic

    if not settings.anthropic_api_key:
        raise ValueError("模型尚未配置，请先在“模型与设置”中保存并验证 API Key。")
    kwargs: dict = {"model_name": model, "streaming": streaming}
    if settings.anthropic_api_key:
        kwargs["api_key"] = SecretStr(settings.anthropic_api_key)
    if settings.anthropic_base_url:
        kwargs["anthropic_api_url"] = settings.anthropic_base_url
    return ChatAnthropic(**kwargs)  # type: ignore[call-arg]


def _is_openai_reasoning_model(model: str) -> bool:
    """True for OpenAI reasoning families (gpt-5*, o-series) that require the
    Responses API for function tools and reject a non-default temperature."""
    m = model.lower()
    return m.startswith(("gpt-5", "o1", "o3", "o4"))


def _make_openai(model: str, streaming: bool) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    if not settings.openai_api_key:
        raise ValueError("模型尚未配置，请先在“模型与设置”中保存并验证 API Key。")
    kwargs: dict = {"model": model, "temperature": 0, "streaming": streaming}
    if settings.openai_api_key:
        kwargs["api_key"] = SecretStr(settings.openai_api_key)
    if settings.openai_reasoning_effort and _is_openai_reasoning_model(model):
        # Reasoning models refuse function tools on /v1/chat/completions unless
        # reasoning_effort is "none", and they reject a non-default temperature.
        # The Responses API supports tools and reasoning together. Gate on the
        # model so a single global setting only touches reasoning models — the
        # cheaper non-reasoning tiers (e.g. gpt-4o-mini) keep the standard path
        # and don't 400 on an unsupported reasoning.effort parameter.
        kwargs["use_responses_api"] = True
        kwargs["reasoning_effort"] = settings.openai_reasoning_effort
        kwargs.pop("temperature")
    return ChatOpenAI(**kwargs)


def _make_google(model: str, streaming: bool) -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI

    if not settings.google_api_key:
        raise ValueError("模型尚未配置，请先在“模型与设置”中保存并验证 API Key。")
    kwargs: dict = {"model": model, "streaming": streaming}
    if settings.google_api_key:
        kwargs["google_api_key"] = SecretStr(settings.google_api_key)
    return ChatGoogleGenerativeAI(**kwargs)


def _make_bedrock(model: str, streaming: bool) -> BaseChatModel:
    from langchain_aws import ChatBedrockConverse

    kwargs: dict = {"model": model, "region_name": settings.aws_region}
    # Explicit creds override the default AWS credential chain when provided.
    if settings.aws_access_key_id and settings.aws_secret_access_key:
        kwargs["aws_access_key_id"] = SecretStr(settings.aws_access_key_id)
        kwargs["aws_secret_access_key"] = SecretStr(settings.aws_secret_access_key)
        if settings.aws_session_token:
            kwargs["aws_session_token"] = SecretStr(settings.aws_session_token)
    return ChatBedrockConverse(**kwargs)


def _api_key_from_headers(headers: dict) -> str:
    """Extract an API key from an Authorization header, stripping the Bearer prefix."""
    auth_value = headers.get("Authorization", "")
    if not auth_value:
        return ""
    return auth_value[7:] if auth_value.startswith("Bearer ") else auth_value


def _is_deepseek_v4_endpoint(model: str, url: str) -> bool:
    """Detect the official DeepSeek V4 Chat Completions endpoint.

    DeepSeek V4 enables thinking by default. Its tool-call protocol requires
    every reasoning_content field to be replayed on later requests, while the
    generic LangChain ChatOpenAI adapter currently drops that provider-specific
    field. Explicit non-thinking mode keeps the supported tool-call path
    deterministic instead of failing after the first tool result.
    """
    try:
        hostname = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return hostname == "api.deepseek.com" and model.lower().startswith("deepseek-v4-")


def _build_openai_compatible(
    model: str,
    url: str,
    headers: dict,
    *,
    api_key: str = "",
    streaming: bool = False,
    max_tokens: int | None = None,
) -> BaseChatModel:
    """Construct a ChatOpenAI instance pointed at an OpenAI-compatible endpoint."""
    from langchain_openai import ChatOpenAI

    effective_api_key = _api_key_from_headers(headers) or api_key
    kwargs: dict = {
        "model": model,
        "base_url": url.rstrip("/"),
        "api_key": SecretStr(effective_api_key or ""),
        "streaming": streaming,
        "temperature": 0,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if _is_deepseek_v4_endpoint(model, url):
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    if headers:
        kwargs["default_headers"] = {str(k): str(v) for k, v in headers.items()}
    return ChatOpenAI(**kwargs)


def _make_openai_compatible(model: str, streaming: bool) -> BaseChatModel:
    import json

    url = settings.openai_compatible_base_url
    if not url:
        raise ValueError(
            "OPENAI_COMPATIBLE_BASE_URL is required for the openai-compatible provider"
        )
    if not settings.openai_compatible_api_key and not settings.openai_compatible_headers:
        raise ValueError("模型尚未配置，请先在“模型与设置”中保存并验证 API Key。")

    headers = {}
    if settings.openai_compatible_headers:
        try:
            headers = json.loads(settings.openai_compatible_headers)
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"Invalid OPENAI_COMPATIBLE_HEADERS JSON: {e}")

    return _build_openai_compatible(
        model,
        url,
        headers,
        api_key=settings.openai_compatible_api_key,
        streaming=streaming,
    )


# Registry — adding a provider means adding one entry here.
_FACTORIES: dict[str, Callable[[str, bool], BaseChatModel]] = {
    "anthropic": _make_anthropic,
    "openai": _make_openai,
    "google": _make_google,
    "bedrock": _make_bedrock,
    "openai-compatible": _make_openai_compatible,
}


def _make_llm(model: str, streaming: bool = False) -> BaseChatModel:
    factory = _FACTORIES.get(settings.llm_provider)
    if factory is None:
        raise ValueError(
            f"Unknown LLM provider {settings.llm_provider!r}. Valid providers: {sorted(_FACTORIES)}"
        )
    return factory(model, streaming)


# ── Public accessors (one per model tier) ─────────────────────────────────────


def get_llm(streaming: bool = True) -> BaseChatModel:
    return _make_llm(settings.get_llm_model(), streaming=streaming)


def get_chart_llm() -> BaseChatModel:
    return _make_llm(settings.get_chart_llm_model())


def get_quality_llm() -> BaseChatModel:
    return _make_llm(settings.get_quality_llm_model())


def get_delight_llm() -> BaseChatModel:
    return _make_llm(settings.get_delight_llm_model())
