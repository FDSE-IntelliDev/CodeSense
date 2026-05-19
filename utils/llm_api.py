"""Shared helpers for calling OpenAI-compatible LLM APIs."""

from __future__ import annotations

from typing import Any, Optional

from openai import OpenAI

from definition import API_KEY, BASE_URL



def get_llm_client(
    client: Optional[Any] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Any:
    """Return an existing client or create a new OpenAI-compatible client."""
    if client is not None:
        return client
    return OpenAI(
        base_url=base_url or BASE_URL,
        api_key=api_key or API_KEY,
    )



def call_chat_llm(
    messages: Any,
    model: str,
    *,
    temperature: float = 0.0,
    client: Optional[Any] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    **kwargs: Any,
) -> str:
    """Call chat.completions.create and return the assistant message content."""
    llm_client = get_llm_client(client=client, base_url=base_url, api_key=api_key)
    chat_messages: Any = messages
    resp = llm_client.chat.completions.create(
        model=model,
        messages=chat_messages,
        temperature=temperature,
        **kwargs,
    )
    return getattr(resp.choices[0].message, "content", "") or ""



def call_response_llm(
    input: Any,
    model: str,
    *,
    temperature: float = 0.0,
    client: Optional[Any] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    **kwargs: Any,
) -> str:
    """Call responses.create and return the output_text field."""
    llm_client = get_llm_client(client=client, base_url=base_url, api_key=api_key)
    response_input: Any = input
    resp = llm_client.responses.create(
        model=model,
        temperature=temperature,
        input=response_input,
        **kwargs,
    )
    return getattr(resp, "output_text", "") or ""




