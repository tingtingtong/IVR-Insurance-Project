"""Dual-provider LLM factory — Groq (default) or Amazon Bedrock."""

from config import settings


def get_llm(*, temperature: float = 0.3, max_tokens: int = 200):
    """Return a ChatModel for service nodes (policy, faq, payment, etc.)."""
    if settings.llm_provider == "bedrock":
        from langchain_aws import ChatBedrockConverse

        return ChatBedrockConverse(
            model=settings.bedrock_model,
            temperature=temperature,
            max_tokens=max_tokens,
            region_name=settings.aws_region,
        )
    else:
        from langchain_groq import ChatGroq

        return ChatGroq(
            model=settings.groq_model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=settings.groq_api_key,
        )


def get_router_llm():
    """Return a ChatModel tuned for single-word intent classification."""
    if settings.llm_provider == "bedrock":
        from langchain_aws import ChatBedrockConverse

        return ChatBedrockConverse(
            model=settings.router_bedrock_model,
            temperature=0,
            max_tokens=20,
            region_name=settings.aws_region,
        )
    else:
        from langchain_groq import ChatGroq

        return ChatGroq(
            model=settings.router_model,
            temperature=0,
            max_tokens=20,
            api_key=settings.groq_api_key,
        )
