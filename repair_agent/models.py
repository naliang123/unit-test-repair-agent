"""Model protocol and OpenAI-compatible tool-calling adapter."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import Settings


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    parse_error: str | None = None


@dataclass(slots=True)
class ModelResponse:
    assistant_message: dict[str, Any]
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


class ChatModel(Protocol):
    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelResponse: ...


class OpenAICompatibleModel:
    def __init__(self, settings: Settings) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=settings.api_key, base_url=settings.base_url)
        self._model = settings.model

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelResponse:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0,
        )
        message = response.choices[0].message
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": message.content,
        }
        calls: list[ToolCall] = []
        if message.tool_calls:
            assistant_message["tool_calls"] = [
                call.model_dump(exclude_none=True) for call in message.tool_calls
            ]
            for call in message.tool_calls:
                raw = call.function.arguments or "{}"
                parse_error = None
                try:
                    arguments = json.loads(raw)
                    if not isinstance(arguments, dict):
                        raise ValueError("tool arguments must be a JSON object")
                except (json.JSONDecodeError, ValueError) as exc:
                    arguments = {}
                    parse_error = str(exc)
                calls.append(
                    ToolCall(call.id, call.function.name, arguments, parse_error)
                )
        usage = {
            "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(response.usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(response.usage, "total_tokens", 0) or 0,
        }
        return ModelResponse(
            assistant_message=assistant_message,
            content=message.content or "",
            tool_calls=calls,
            usage=usage,
        )
