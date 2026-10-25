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
from .models import OpenAICompatibleModel


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repair-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    single = subparsers.add_parser("run", help="Repair one failing pytest target.")
    single.add_argument("--repo", type=Path, required=True)
    single.add_argument("--test", required=True, help="Pytest file, directory, or node id.")
    single.add_argument("--output", type=Path)
    _runtime_arguments(single)

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
    settings = replace(settings, **overrides)
    if settings.max_steps < 1 or settings.test_timeout < 1:
        raise ConfigError("max steps and test timeout must be positive")
    return settings


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

        settings = _settings(arguments)
        model = OpenAICompatibleModel(settings)
        if arguments.command == "run":
            summary = RepairAgent(
                model=model,
                settings=settings,
                repo=arguments.repo,
                test_target=arguments.test,
                run_dir=arguments.output,
            ).run()
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0 if summary["success"] else 1

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
