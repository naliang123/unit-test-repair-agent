"""Deterministic mutation fixtures and benchmark validation."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .tools import ToolBox

BENCHMARK_DIR = Path(__file__).resolve().parents[1] / "benchmarks"
BASE_PROJECT = BENCHMARK_DIR / "base_project"
MUTATIONS_FILE = BENCHMARK_DIR / "mutations.json"


@dataclass(frozen=True, slots=True)
class Mutation:
    id: str
    title: str
    file: str
    old: str
    new: str
    test_target: str


def load_tasks(selected: list[str] | None = None) -> list[Mutation]:
    records = json.loads(MUTATIONS_FILE.read_text(encoding="utf-8"))
    tasks = [Mutation(**record) for record in records]
    if selected:
        requested = set(selected)
        tasks = [task for task in tasks if task.id in requested]
        missing = requested - {task.id for task in tasks}
        if missing:
            raise ValueError(f"Unknown mutation ids: {', '.join(sorted(missing))}")
    return tasks


def _git(repo: Path, *args: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())


def materialize_repo(destination: Path, mutation: Mutation | None = None) -> Path:
    shutil.copytree(BASE_PROJECT, destination)
    if mutation:
        target = destination / mutation.file
        content = target.read_text(encoding="utf-8")
        if content.count(mutation.old) != 1:
            raise RuntimeError(f"Mutation {mutation.id} no longer matches exactly once.")
        target.write_text(content.replace(mutation.old, mutation.new, 1), encoding="utf-8")
    _git(destination, "init", "-q")
    _git(destination, "config", "user.name", "Repair Benchmark")
    _git(destination, "config", "user.email", "benchmark@localhost")
    _git(destination, "add", ".")
    _git(destination, "commit", "-q", "-m", f"Baseline {mutation.id if mutation else 'clean'}")
    return destination


def validate_benchmark(selected: list[str] | None = None) -> list[dict[str, object]]:
    tasks = load_tasks(selected)
    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="repair-benchmark-") as temporary:
        root = Path(temporary)
        clean_repo = materialize_repo(root / "clean")
        clean_result = ToolBox(clean_repo, test_timeout=30).run_tests(".")
        results.append(
            {
                "id": "CLEAN",
                "expected": "pass",
                "valid": clean_result.get("passed", False),
                "output": clean_result.get("output", ""),
            }
        )
        for task in tasks:
            repo = materialize_repo(root / task.id, task)
            result = ToolBox(repo, test_timeout=30).run_tests(".")
            results.append(
                {
                    "id": task.id,
                    "title": task.title,
                    "expected": "fail",
                    "valid": not result.get("passed", False),
                    "output": result.get("output", ""),
                }
            )
    return results
