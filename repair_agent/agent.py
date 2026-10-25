"""Bounded observe-decide-act loop for repository repair."""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .models import ChatModel
from .prompts import SYSTEM_PROMPT, initial_user_prompt
from .tools import TOOL_SCHEMAS, ToolBox
from .trace import JsonlTrace, write_json
from .workspace import GitWorktree


class RepairAgent:
    def __init__(
        self,
        model: ChatModel,
        settings: Settings,
        repo: Path,
        test_target: str,
        run_dir: Path | None = None,
    ) -> None:
        self.model = model
        self.settings = settings
        self.repo = Path(repo)
        self.test_target = test_target
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = run_dir or Path("runs") / f"{stamp}_{uuid.uuid4().hex[:8]}"
        self.trace = JsonlTrace(self.run_dir / "trace.jsonl")

    def run(self) -> dict[str, Any]:
        started = time.monotonic()
        workspace = GitWorktree(self.repo, self.run_dir)
        status, reason = "failed", "not_started"
        steps = 0
        rollback_performed = False
        baseline: dict[str, Any] = {}
        attempt_patch = ""
        final_patch = ""
        tokens = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        try:
            worktree = workspace.create()
            tools = ToolBox(
                worktree,
                test_timeout=self.settings.test_timeout,
                max_output_chars=self.settings.max_tool_output_chars,
            )
            self.trace.event(
                "run_started",
                repo=str(workspace.source_repo),
                base_commit=workspace.base_commit,
                test_target=self.test_target,
                max_steps=self.settings.max_steps,
            )
            baseline = tools.run_tests(self.test_target)
            self.trace.event("baseline_test", result=baseline)
            if not baseline.get("ok"):
                raise RuntimeError(f"Could not execute baseline tests: {baseline.get('error')}")
            if baseline.get("passed"):
                status, reason = "invalid", "baseline_already_passing"
            else:
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": initial_user_prompt(
                            self.test_target, baseline.get("output", "")
                        ),
                    },
                ]
                for step in range(1, self.settings.max_steps + 1):
                    response = self.model.complete(messages, TOOL_SCHEMAS)
                    steps = step
                    for key in tokens:
                        tokens[key] += response.usage.get(key, 0)
                    self.trace.event(
                        "model_response",
                        step=step,
                        content=response.content,
                        tool_calls=[
                            {"name": call.name, "arguments": call.arguments}
                            for call in response.tool_calls
                        ],
                        usage=response.usage,
                    )
                    messages.append(response.assistant_message)
                    if not response.tool_calls:
                        reason = "model_stopped_without_passing_tests"
                        break

                    target_passed = False
                    for call in response.tool_calls:
                        if call.parse_error:
                            result = {"ok": False, "error": call.parse_error}
                        else:
                            result = tools.execute(call.name, call.arguments)
                        self.trace.event(
                            "tool_result", step=step, tool=call.name, result=result
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.id,
                                "content": json.dumps(result, ensure_ascii=False),
                            }
                        )
                        if call.name == "edit_file" and result.get("ok"):
                            target_passed = False
                        if call.name == "run_tests" and result.get("ok"):
                            target_passed = bool(result.get("passed"))

                    if target_passed:
                        if not self.settings.verify_all:
                            status, reason = "success", "target_tests_passed"
                            break
                        verification = tools.run_tests(".")
                        self.trace.event(
                            "full_suite_verification", step=step, result=verification
                        )
                        if verification.get("passed"):
                            status, reason = "success", "all_tests_passed"
                            break
                        messages.append(
                            {
                                "role": "user",
                                "content": "The target passed but the full test suite failed. "
                                "Inspect and repair the regression, then test again:\n"
                                + verification.get("output", ""),
                            }
                        )
                else:
                    reason = "step_budget_exhausted"

            attempt_patch = workspace.diff()
            if status == "success":
                final_patch = attempt_patch
            else:
                workspace.rollback()
                rollback_performed = True
        except Exception as exc:
            status, reason = "failed", f"{type(exc).__name__}: {exc}"
            self.trace.event("run_error", error=reason)
            try:
                attempt_patch = workspace.diff()
                if workspace.created:
                    workspace.rollback()
                    rollback_performed = True
            except Exception as rollback_error:
                self.trace.event("rollback_error", error=str(rollback_error))
        finally:
            cleanup_warning = workspace.cleanup()

        (self.run_dir / "attempt.patch").write_text(attempt_patch, encoding="utf-8")
        (self.run_dir / "final.patch").write_text(final_patch, encoding="utf-8")
        write_json(self.run_dir / "baseline.json", baseline)
        estimated_cost = None
        if self.settings.input_price_per_million or self.settings.output_price_per_million:
            estimated_cost = round(
                tokens["prompt_tokens"] * self.settings.input_price_per_million / 1_000_000
                + tokens["completion_tokens"]
                * self.settings.output_price_per_million
                / 1_000_000,
                6,
            )
        summary = {
            "run_id": self.run_dir.name,
            "status": status,
            "success": status == "success",
            "reason": reason,
            "test_target": self.test_target,
            "steps": steps,
            "max_steps": self.settings.max_steps,
            "tokens": tokens,
            "estimated_cost_usd": estimated_cost,
            "wall_time_seconds": round(time.monotonic() - started, 3),
            "rollback_performed": rollback_performed,
            "worktree_cleanup_warning": cleanup_warning,
            "changed_patch_bytes": len(final_patch.encode("utf-8")),
            "artifacts": {
                "trace": "trace.jsonl",
                "baseline": "baseline.json",
                "attempt_patch": "attempt.patch",
                "final_patch": "final.patch",
            },
        }
        write_json(self.run_dir / "summary.json", summary)
        self.trace.event(
            "run_finished",
            status=status,
            reason=reason,
            steps=steps,
            rollback_performed=rollback_performed,
        )
        return summary
