"""Repository repair lifecycle orchestrated by a persistent LangGraph graph."""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .config import Settings
from .graph import build_repair_graph
from .mcp import MCPToolRegistry, parse_mcp_servers
from .models import ChatModel
from .prompts import SYSTEM_PROMPT, initial_user_prompt
from .tools import ToolBox
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
        started = time.time()
        workspace = GitWorktree(self.repo, self.run_dir)
        baseline: dict[str, Any] = {}
        try:
            worktree = workspace.create()
            tools = self._tools(worktree)
            self.trace.event(
                "run_started",
                repo=str(workspace.source_repo),
                base_commit=workspace.base_commit,
                test_target=self.test_target,
                max_steps=self.settings.max_steps,
                approve_edits=self.settings.approve_edits,
            )
            baseline = tools.run_tests(self.test_target)
            self.trace.event("baseline_test", result=baseline)
            if not baseline.get("ok"):
                raise RuntimeError(
                    f"Could not execute baseline tests: {baseline.get('error')}"
                )
            self._write_context(workspace, started, active=True)
            if baseline.get("passed"):
                return self._finish(
                    workspace,
                    baseline,
                    started,
                    status="invalid",
                    reason="baseline_already_passing",
                    steps=0,
                    tokens=self._empty_tokens(),
                )
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": initial_user_prompt(
                        self.test_target, baseline.get("output", "")
                    ),
                },
            ]
            return self._execute_graph(
                workspace=workspace,
                baseline=baseline,
                started=started,
                graph_input={"messages": messages},
            )
        except Exception as exc:
            return self._fail(workspace, baseline, started, exc)

    def resume(self, decision: bool | None = None) -> dict[str, Any]:
        context = self._read_context()
        summary_path = self.run_dir / "summary.json"
        if not context.get("active") and summary_path.exists():
            return json.loads(summary_path.read_text(encoding="utf-8"))
        workspace = GitWorktree(Path(context["repo"]), self.run_dir)
        workspace.base_commit = context["base_commit"]
        if not workspace.path.exists():
            raise RuntimeError("The saved worktree is missing; this run cannot be resumed.")
        workspace.created = True
        baseline_path = self.run_dir / "baseline.json"
        baseline = (
            json.loads(baseline_path.read_text(encoding="utf-8"))
            if baseline_path.exists()
            else {}
        )
        self.trace.event(
            "run_resumed",
            decision=decision,
            checkpoint="checkpoints.sqlite",
        )
        command = Command(resume=decision) if decision is not None else None
        return self._execute_graph(
            workspace=workspace,
            baseline=baseline,
            started=float(context.get("started_unix", time.time())),
            graph_input=None,
            resume_command=command,
        )

    def _execute_graph(
        self,
        workspace: GitWorktree,
        baseline: dict[str, Any],
        started: float,
        graph_input: dict[str, Any] | None,
        resume_command: Command | None = None,
    ) -> dict[str, Any]:
        tools = self._tools(workspace.path)
        checkpoint_path = self.run_dir / "checkpoints.sqlite"
        mcp_registry = self._mcp_registry()
        try:
            if mcp_registry is not None:
                try:
                    mcp_registry.connect()
                    self.trace.event(
                        "mcp_connected", tools=sorted(mcp_registry.tool_names())
                    )
                except Exception as exc:  # noqa: BLE001
                    self.trace.event(
                        "mcp_connect_error", error=f"{type(exc).__name__}: {exc}"
                    )
            try:
                with SqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
                    graph = build_repair_graph(
                        model=self.model,
                        tools=tools,
                        settings=self.settings,
                        trace=self.trace,
                        checkpointer=checkpointer,
                        mcp_registry=mcp_registry,
                    )
                    config = {
                        "configurable": {"thread_id": "repair"},
                        "recursion_limit": max(50, self.settings.max_steps * 6 + 20),
                    }
                    if resume_command is not None:
                        invocation: Any = resume_command
                    elif graph_input is not None:
                        invocation = graph_input
                    else:
                        invocation = None
                    result = graph.invoke(invocation, config=config)
                if "__interrupt__" in result:
                    pending = result["__interrupt__"][0].value
                    self.trace.event(
                        "run_paused", reason="approval_required", pending=pending
                    )
                    return self._finish(
                        workspace,
                        baseline,
                        started,
                        status="paused",
                        reason="approval_required",
                        steps=int(result.get("steps", 0)),
                        tokens=result.get("tokens", self._empty_tokens()),
                        paused=True,
                        pending_approval=pending,
                    )
                return self._finish(
                    workspace,
                    baseline,
                    started,
                    status=result.get("status", "failed"),
                    reason=result.get("reason", "graph_ended_without_reason"),
                    steps=int(result.get("steps", 0)),
                    tokens=result.get("tokens", self._empty_tokens()),
                )
            except KeyboardInterrupt:
                self.trace.event("run_paused", reason="interrupted_by_user")
                return self._finish(
                    workspace,
                    baseline,
                    started,
                    status="paused",
                    reason="interrupted_by_user",
                    steps=0,
                    tokens=self._empty_tokens(),
                    paused=True,
                )
            except Exception as exc:
                return self._fail(workspace, baseline, started, exc)
        finally:
            if mcp_registry is not None:
                mcp_registry.close()

    def _finish(
        self,
        workspace: GitWorktree,
        baseline: dict[str, Any],
        started: float,
        status: str,
        reason: str,
        steps: int,
        tokens: dict[str, int],
        paused: bool = False,
        pending_approval: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        attempt_patch = workspace.diff() if workspace.created else ""
        final_patch = ""
        rollback_performed = False
        cleanup_warning = None
        if not paused:
            if status == "success":
                final_patch = attempt_patch
            else:
                workspace.rollback()
                rollback_performed = True
            cleanup_warning = workspace.cleanup()
        self._write_context(workspace, started, active=paused)

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
            "paused": paused,
            "resumable": paused,
            "reason": reason,
            "pending_approval": pending_approval,
            "test_target": self.test_target,
            "steps": steps,
            "max_steps": self.settings.max_steps,
            "tokens": tokens,
            "estimated_cost_usd": estimated_cost,
            "wall_time_seconds": round(time.time() - started, 3),
            "rollback_performed": rollback_performed,
            "worktree_cleanup_warning": cleanup_warning,
            "changed_patch_bytes": len(final_patch.encode("utf-8")),
            "artifacts": {
                "trace": "trace.jsonl",
                "baseline": "baseline.json",
                "attempt_patch": "attempt.patch",
                "final_patch": "final.patch",
                "checkpoint": "checkpoints.sqlite",
                "context": "run_context.json",
            },
        }
        write_json(self.run_dir / "summary.json", summary)
        if paused:
            self.trace.event("pause_finished", status=status, reason=reason, steps=steps)
        else:
            self.trace.event(
                "run_finished",
                status=status,
                reason=reason,
                steps=steps,
                rollback_performed=rollback_performed,
            )
        return summary

    def _fail(
        self,
        workspace: GitWorktree,
        baseline: dict[str, Any],
        started: float,
        exc: Exception,
    ) -> dict[str, Any]:
        reason = f"{type(exc).__name__}: {exc}"
        self.trace.event("run_error", error=reason)
        return self._finish(
            workspace,
            baseline,
            started,
            status="failed",
            reason=reason,
            steps=0,
            tokens=self._empty_tokens(),
        )

    def _tools(self, worktree: Path) -> ToolBox:
        return ToolBox(
            worktree,
            test_timeout=self.settings.test_timeout,
            max_output_chars=self.settings.max_tool_output_chars,
        )

    def _mcp_registry(self) -> MCPToolRegistry | None:
        specs = parse_mcp_servers(self.settings.mcp_servers_json)
        return MCPToolRegistry(specs) if specs else None

    def _write_context(
        self, workspace: GitWorktree, started: float, active: bool
    ) -> None:
        if not workspace.base_commit:
            return
        write_json(
            self.run_dir / "run_context.json",
            {
                "version": 1,
                "repo": str(workspace.source_repo),
                "test_target": self.test_target,
                "base_commit": workspace.base_commit,
                "started_unix": started,
                "active": active,
                "settings": {
                    "model": self.settings.model,
                    "base_url": self.settings.base_url,
                    "max_steps": self.settings.max_steps,
                    "test_timeout": self.settings.test_timeout,
                    "max_tool_output_chars": self.settings.max_tool_output_chars,
                    "verify_all": self.settings.verify_all,
                    "approve_edits": self.settings.approve_edits,
                    "mcp_servers_json": self.settings.mcp_servers_json,
                    "input_price_per_million": self.settings.input_price_per_million,
                    "output_price_per_million": self.settings.output_price_per_million,
                },
            },
        )

    def _read_context(self) -> dict[str, Any]:
        path = self.run_dir / "run_context.json"
        if not path.exists():
            raise RuntimeError(f"Missing resumable context: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _empty_tokens() -> dict[str, int]:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
