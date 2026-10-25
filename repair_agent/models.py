"""Model protocol and LangChain-powered tool-calling adapter."""

from __future__ import annotations

import uuid
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
    assistant_message: Any
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


class ChatModel(Protocol):
    def complete(
        self, messages: list[Any], tools: list[dict[str, Any]]
    ) -> ModelResponse: ...


class LangChainModel:
    def __init__(self, settings: Settings) -> None:
        from langchain_openai import ChatOpenAI

        self._model = ChatOpenAI(
            model=settings.model,
            api_key=settings.api_key,
            base_url=settings.base_url,
            temperature=0,
        )

    def complete(
        self, messages: list[Any], tools: list[dict[str, Any]]
    ) -> ModelResponse:
        response = self._model.bind_tools(tools).invoke(messages)
        calls: list[ToolCall] = []
        for index, call in enumerate(response.tool_calls or []):
            arguments = call.get("args", {})
            parse_error = None
            if not isinstance(arguments, dict):
                arguments = {}
                parse_error = "tool arguments must be a JSON object"
            calls.append(
                ToolCall(
                    id=call.get("id") or f"tool-{index}-{uuid.uuid4().hex[:8]}",
                    name=call.get("name", ""),
                    arguments=arguments,
                    parse_error=parse_error,
                )
            )
        for index, call in enumerate(getattr(response, "invalid_tool_calls", []) or []):
            calls.append(
                ToolCall(
                    id=call.get("id") or f"invalid-{index}-{uuid.uuid4().hex[:8]}",
                    name=call.get("name") or "unknown",
                    arguments={},
                    parse_error=call.get("error") or "invalid tool call",
                )
            )
        usage = self._usage(response)
        content = response.content if isinstance(response.content, str) else str(response.content)
        return ModelResponse(
            assistant_message=response,
            content=content,
            tool_calls=calls,
            usage=usage,
        )

    @staticmethod
    def _usage(response: Any) -> dict[str, int]:
        metadata = getattr(response, "usage_metadata", None) or {}
        usage = {
            "prompt_tokens": int(metadata.get("input_tokens", 0) or 0),
            "completion_tokens": int(metadata.get("output_tokens", 0) or 0),
            "total_tokens": int(metadata.get("total_tokens", 0) or 0),
        }
        if usage["total_tokens"]:
            return usage
        raw = (getattr(response, "response_metadata", None) or {}).get("token_usage") or {}
        return {
            "prompt_tokens": int(raw.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(raw.get("completion_tokens", 0) or 0),
            "total_tokens": int(raw.get("total_tokens", 0) or 0),
        }


OpenAICompatibleModel = LangChainModel
