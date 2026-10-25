"""Command-line entry points for single repairs and benchmark runs."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from .agent import RepairAgent
from .benchmark import load_tasks, validate_benchmark
from .config import ConfigError, Settings
from .evaluation import evaluate_tasks
from .models import LangChainModel


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repair-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    single = subparsers.add_parser("run", help="Repair one failing pytest target.")
    single.add_argument("--repo", type=Path, required=True)
    single.add_argument("--test", required=True, help="Pytest file, directory, or node id.")
    single.add_argument("--output", type=Path)
    single.add_argument(
        "--approve-edits",
        action="store_true",
        help="Pause before each file edit and wait for interactive approval.",
    )
    _runtime_arguments(single)

    resume = subparsers.add_parser(
        "resume", help="Resume an interrupted or approval-paused run from SQLite checkpoints."
    )
    resume.add_argument("--run-dir", type=Path, required=True)

    evaluate = subparsers.add_parser("evaluate", help="Run the built-in mutation benchmark.")
    evaluate.add_argument("--task", action="append", help="Mutation id; repeatable.")
    evaluate.add_argument("--limit", type=int)
    evaluate.add_argument("--output", type=Path)
    _runtime_arguments(evaluate)

    validate = subparsers.add_parser(
        "validate-benchmark", help="Verify clean fixtures pass and mutants fail; no API key needed."
    )
    validate.add_argument("--task", action="append", help="Mutation id; repeatable.")
    return parser


def _runtime_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", help="Override MODEL_ID.")
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--test-timeout", type=int)
    parser.add_argument(
        "--verify-target-only",
        action="store_true",
        help="Skip the full-suite regression check (not recommended).",
    )


def _settings(arguments: argparse.Namespace) -> Settings:
    settings = Settings.from_env(require_key=True)
    overrides = {}
    if arguments.model:
        overrides["model"] = arguments.model
    if arguments.max_steps is not None:
        overrides["max_steps"] = arguments.max_steps
    if arguments.test_timeout is not None:
        overrides["test_timeout"] = arguments.test_timeout
    if arguments.verify_target_only:
        overrides["verify_all"] = False
    if getattr(arguments, "approve_edits", False):
        overrides["approve_edits"] = True
    settings = replace(settings, **overrides)
    if settings.max_steps < 1 or settings.test_timeout < 1:
        raise ConfigError("max steps and test timeout must be positive")
    return settings


def _settings_from_context(run_dir: Path) -> tuple[Settings, dict]:
    context_path = run_dir / "run_context.json"
    if not context_path.exists():
        raise ConfigError(f"Missing run context: {context_path}")
    context = json.loads(context_path.read_text(encoding="utf-8"))
    settings = replace(Settings.from_env(require_key=True), **context.get("settings", {}))
    return settings, context


def _drive(agent: RepairAgent, resume: bool = False) -> dict:
    if resume:
        paused_summary = agent.run_dir / "summary.json"
        if (
            paused_summary.exists()
            and json.loads(paused_summary.read_text(encoding="utf-8")).get("reason")
            == "approval_required"
        ):
            summary = json.loads(paused_summary.read_text(encoding="utf-8"))
        else:
            summary = agent.resume()
    else:
        summary = agent.run()
    while summary.get("status") == "paused" and summary.get("reason") == "approval_required":
        pending = summary.get("pending_approval") or {}
        edits = pending.get("edits", [])
        print("\nThe agent wants to edit production files:")
        for index, edit in enumerate(edits, start=1):
            print(f"{index}. {edit.get('path')}")
            print(f"   old: {str(edit.get('old_string'))[:500]}")
            print(f"   new: {str(edit.get('new_string'))[:500]}")
        answer = input("Approve this edit? [y]es / [n]o / [q]uit: ").strip().lower()
        if answer in {"q", "quit"}:
            break
        summary = agent.resume(decision=answer in {"y", "yes"})
    return summary


def _exit_code(summary: dict) -> int:
    if summary.get("success"):
        return 0
    if summary.get("paused"):
        return 2
    return 1


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.command == "validate-benchmark":
            results = validate_benchmark(arguments.task)
            for result in results:
                state = "OK" if result["valid"] else "INVALID"
                print(f"{state:7} {result['id']:5} expected={result['expected']}")
            invalid = [result for result in results if not result["valid"]]
            if invalid:
                print(json.dumps(invalid, ensure_ascii=False, indent=2))
                return 1
            print(f"Validated {len(results) - 1} mutations plus the clean baseline.")
            return 0

        if arguments.command == "resume":
            settings, context = _settings_from_context(arguments.run_dir)
            model = LangChainModel(settings)
            agent = RepairAgent(
                model=model,
                settings=settings,
                repo=Path(context["repo"]),
                test_target=context["test_target"],
                run_dir=arguments.run_dir,
            )
            summary = _drive(agent, resume=True)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return _exit_code(summary)

        settings = _settings(arguments)
        model = LangChainModel(settings)
        if arguments.command == "run":
            agent = RepairAgent(
                model=model,
                settings=settings,
                repo=arguments.repo,
                test_target=arguments.test,
                run_dir=arguments.output,
            )
            summary = _drive(agent)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return _exit_code(summary)

        settings = replace(settings, approve_edits=False)
        tasks = load_tasks(arguments.task)
        if arguments.limit is not None:
            if arguments.limit < 1:
                raise ConfigError("--limit must be positive")
            tasks = tasks[: arguments.limit]
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = arguments.output or Path("runs") / f"evaluation_{stamp}"
        report = evaluate_tasks(tasks, model, settings, output)
        print(json.dumps({key: value for key, value in report.items() if key != "records"}, indent=2))
        print(f"Report: {output / 'evaluation.md'}")
        return 0
    except (ConfigError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}")
        return 2
