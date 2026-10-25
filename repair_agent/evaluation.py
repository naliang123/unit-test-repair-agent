"""Sequential benchmark execution and aggregate reporting."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from .agent import RepairAgent
from .benchmark import Mutation, materialize_repo
from .config import Settings
from .models import ChatModel
from .trace import write_json


def evaluate_tasks(
    tasks: list[Mutation], model: ChatModel, settings: Settings, output_dir: Path
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for index, task in enumerate(tasks, start=1):
        print(f"[{index}/{len(tasks)}] {task.id}: {task.title}", flush=True)
        with tempfile.TemporaryDirectory(prefix=f"repair-{task.id.lower()}-") as temporary:
            repo = materialize_repo(Path(temporary) / "repo", task)
            agent = RepairAgent(
                model=model,
                settings=settings,
                repo=repo,
                test_target=task.test_target,
                run_dir=output_dir / "tasks" / task.id,
            )
            summary = agent.run()
        records.append({"task_id": task.id, "title": task.title, **summary})
        print(f"  -> {summary['status']} in {summary['steps']} steps", flush=True)

    total = len(records)
    successes = sum(bool(record["success"]) for record in records)
    tokens = {
        key: sum(record["tokens"][key] for record in records)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }
    costs = [
        record["estimated_cost_usd"]
        for record in records
        if record["estimated_cost_usd"] is not None
    ]
    report = {
        "task_count": total,
        "success_count": successes,
        "success_rate": round(successes / total, 4) if total else 0,
        "average_steps": round(sum(record["steps"] for record in records) / total, 2)
        if total
        else 0,
        "tokens": tokens,
        "estimated_cost_usd": round(sum(costs), 6) if costs else None,
        "model": settings.model,
        "max_steps": settings.max_steps,
        "records": records,
    }
    write_json(output_dir / "evaluation.json", report)
    (output_dir / "evaluation.md").write_text(_markdown(report), encoding="utf-8")
    return report


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Repair benchmark report",
        "",
        f"- Model: `{report['model']}`",
        f"- Tasks: {report['task_count']}",
        f"- Success: {report['success_count']} ({report['success_rate']:.1%})",
        f"- Average model steps: {report['average_steps']}",
        f"- Total tokens: {report['tokens']['total_tokens']}",
        f"- Estimated cost (USD): {report['estimated_cost_usd']}",
        "",
        "| Task | Result | Steps | Tokens | Reason |",
        "|---|---|---:|---:|---|",
    ]
    for record in report["records"]:
        lines.append(
            f"| {record['task_id']} | {record['status']} | {record['steps']} "
            f"| {record['tokens']['total_tokens']} | {record['reason']} |"
        )
    lines += [
        "",
        "Each task directory contains its JSONL trace, baseline observation, attempted patch, and final patch.",
        "No score in this file is hard-coded; all rows come from actual agent runs.",
        "",
    ]
    return "\n".join(lines)
