"""
LLM gateway configuration.

make_llm() is a custom factory — not an official LangChain API.
It wraps langchain_openai.ChatOpenAI with your gateway's base_url and api_key
so every skill and the orchestrator share the same connection config.

Swap the gateway by changing LLM_GATEWAY_URL.
Swap a model by changing the MODEL_* env var — zero other code changes needed.
"""

import os

from langchain_openai import ChatOpenAI

LLM_GATEWAY_URL: str = os.getenv("LLM_GATEWAY_URL", "http://10.0.0.224:11434/v1")
LLM_GATEWAY_KEY: str = os.getenv("LLM_GATEWAY_KEY", "ollama")

MODEL_GENERAL: str = os.getenv("MODEL_GENERAL", "qwen3:8b")
MODEL_CODE: str    = os.getenv("MODEL_CODE",    "qwen2.5-coder:14b")
MODEL_FAST: str    = os.getenv("MODEL_FAST",    "qwen2.5:1.5b")


def make_llm(model: str = MODEL_GENERAL, temperature: float = 0.1) -> ChatOpenAI:
    """Return a ChatOpenAI instance pointed at the internal LLM gateway."""
    return ChatOpenAI(
        model=model,
        base_url=LLM_GATEWAY_URL,
        api_key=LLM_GATEWAY_KEY,
        temperature=temperature,
    )
