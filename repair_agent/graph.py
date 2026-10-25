"""LangGraph state machine for the repair workflow."""

from __future__ import annotations

import json
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .config import Settings
from .models import ChatModel
from .tools import TOOL_SCHEMAS, ToolBox
from .trace import JsonlTrace


class RepairState(TypedDict, total=False):
    messages: list[Any]
    pending_calls: list[dict[str, Any]]
    steps: int
    tokens: dict[str, int]
    target_passed: bool
    status: str
    reason: str


def build_repair_graph(
    model: ChatModel,
    tools: ToolBox,
    settings: Settings,
    trace: JsonlTrace,
    checkpointer: Any,
):
    def decide(state: RepairState) -> RepairState:
        response = model.complete(state.get("messages", []), TOOL_SCHEMAS)
        steps = int(state.get("steps", 0)) + 1
        tokens = {
            key: int(state.get("tokens", {}).get(key, 0)) + response.usage.get(key, 0)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        pending_calls = [
            {
                "id": call.id,
                "name": call.name,
                "arguments": call.arguments,
                "parse_error": call.parse_error,
            }
            for call in response.tool_calls
        ]
        trace.event(
            "model_response",
            step=steps,
            content=response.content,
            tool_calls=[
                {"name": call["name"], "arguments": call["arguments"]}
                for call in pending_calls
            ],
            usage=response.usage,
        )
        update: RepairState = {
            "messages": [*state.get("messages", []), response.assistant_message],
            "pending_calls": pending_calls,
            "steps": steps,
            "tokens": tokens,
            "target_passed": False,
        }
        if not pending_calls:
            update["reason"] = "model_stopped_without_passing_tests"
        return update

    def route_after_decide(state: RepairState) -> str:
        pending = state.get("pending_calls", [])
        if not pending:
            return "finish"
        has_edit = any(
            call.get("name") == "edit_file" and not call.get("parse_error")
            for call in pending
        )
        if settings.approve_edits and has_edit:
            return "approve"
        return "act"

    def approve(state: RepairState) -> RepairState:
        edits = [
            {
                "path": call.get("arguments", {}).get("path"),
                "old_string": call.get("arguments", {}).get("old_string"),
                "new_string": call.get("arguments", {}).get("new_string"),
            }
            for call in state.get("pending_calls", [])
            if call.get("name") == "edit_file"
        ]
        payload = {
            "kind": "edit_approval",
            "step": state.get("steps", 0),
            "edits": edits,
        }
        approved = bool(interrupt(payload))
        trace.event("human_approval", step=state.get("steps", 0), approved=approved)
        if not approved:
            return {"reason": "edit_rejected_by_user"}
        return {}

    def route_after_approve(state: RepairState) -> str:
        return "finish" if state.get("reason") else "act"

    def act(state: RepairState) -> RepairState:
        messages = list(state.get("messages", []))
        target_passed = False
        for call in state.get("pending_calls", []):
            if call.get("parse_error"):
                result = {"ok": False, "error": call["parse_error"]}
            else:
                result = tools.execute(call.get("name", ""), call.get("arguments", {}))
            trace.event(
                "tool_result",
                step=state.get("steps", 0),
                tool=call.get("name"),
                result=result,
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
            if call.get("name") == "edit_file" and result.get("ok"):
                target_passed = False
            if call.get("name") == "run_tests" and result.get("ok"):
                target_passed = bool(result.get("passed"))
        update: RepairState = {
            "messages": messages,
            "pending_calls": [],
            "target_passed": target_passed,
        }
        if not target_passed and state.get("steps", 0) >= settings.max_steps:
            update["reason"] = "step_budget_exhausted"
        return update

    def route_after_act(state: RepairState) -> str:
        if state.get("target_passed"):
            return "verify"
        if state.get("reason"):
            return "finish"
        return "decide"

    def verify(state: RepairState) -> RepairState:
        if not settings.verify_all:
            return {"status": "success", "reason": "target_tests_passed"}
        verification = tools.run_tests(".")
        trace.event(
            "full_suite_verification",
            step=state.get("steps", 0),
            result=verification,
        )
        if verification.get("passed"):
            return {"status": "success", "reason": "all_tests_passed"}
        update: RepairState = {
            "messages": [
                *state.get("messages", []),
                {
                    "role": "user",
                    "content": "The target passed but the full test suite failed. "
                    "Inspect and repair the regression, then test again:\n"
                    + (verification.get("output") or verification.get("error", "")),
                },
            ],
            "target_passed": False,
        }
        if state.get("steps", 0) >= settings.max_steps:
            update["reason"] = "step_budget_exhausted"
        return update

    def route_after_verify(state: RepairState) -> str:
        if state.get("status") == "success" or state.get("reason"):
            return "finish"
        return "decide"

    def finish(state: RepairState) -> RepairState:
        status = state.get("status") or "failed"
        reason = state.get("reason") or "step_budget_exhausted"
        trace.event("graph_finished", status=status, reason=reason)
        return {"status": status, "reason": reason}

    builder = StateGraph(RepairState)
    builder.add_node("decide", decide)
    builder.add_node("approve", approve)
    builder.add_node("act", act)
    builder.add_node("verify", verify)
    builder.add_node("finish", finish)
    builder.add_edge(START, "decide")
    builder.add_conditional_edges(
        "decide",
        route_after_decide,
        {"approve": "approve", "act": "act", "finish": "finish"},
    )
    builder.add_conditional_edges(
        "approve", route_after_approve, {"act": "act", "finish": "finish"}
    )
    builder.add_conditional_edges(
        "act",
        route_after_act,
        {"decide": "decide", "verify": "verify", "finish": "finish"},
    )
    builder.add_conditional_edges(
        "verify", route_after_verify, {"decide": "decide", "finish": "finish"}
    )
    builder.add_edge("finish", END)
    return builder.compile(checkpointer=checkpointer)
