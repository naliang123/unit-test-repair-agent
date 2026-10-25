"""Workspace-confined tools exposed to the model."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable


class ToolError(ValueError):
    pass


def _function(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


TOOL_SCHEMAS = [
    _function(
        "read_file",
        "Read a UTF-8 file with line numbers. Use small ranges for large files.",
        {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "minimum": 1, "default": 1},
            "end_line": {"type": "integer", "minimum": 1, "default": 240},
        },
        ["path"],
    ),
    _function(
        "edit_file",
        "Replace one exact, unique string in a file. Read the file before editing.",
        {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
        },
        ["path", "old_string", "new_string"],
    ),
    _function(
        "search_code",
        "Search literal text in source files and return matching lines.",
        {
            "query": {"type": "string"},
            "path": {"type": "string", "default": "."},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
        },
        ["query"],
    ),
    _function(
        "run_tests",
        "Run pytest without a shell and return a truncated failure report.",
        {"target": {"type": "string", "description": "Test file, directory, or pytest node id."}},
        ["target"],
    ),
]


class ToolBox:
    def __init__(self, root: Path, test_timeout: int = 45, max_output_chars: int = 6000) -> None:
        self.root = root.resolve()
        self.test_timeout = test_timeout
        self.max_output_chars = max_output_chars
        self._handlers: dict[str, Callable[..., dict[str, Any]]] = {
            "read_file": self.read_file,
            "edit_file": self.edit_file,
            "search_code": self.search_code,
            "run_tests": self.run_tests,
        }

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handler = self._handlers.get(name)
        if handler is None:
            return {"ok": False, "error": f"Unknown tool: {name}"}
        try:
            return handler(**arguments)
        except TypeError as exc:
            return {"ok": False, "error": f"Invalid arguments: {exc}"}
        except (ToolError, OSError, UnicodeError) as exc:
            return {"ok": False, "error": str(exc)}

    def _path(self, user_path: str, must_exist: bool = True) -> Path:
        if not user_path or "\x00" in user_path:
            raise ToolError("A valid relative path is required.")
        candidate = (self.root / user_path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ToolError("Path escapes the isolated workspace.")
        if ".git" in {part.lower() for part in candidate.relative_to(self.root).parts}:
            raise ToolError("Access to .git is forbidden.")
        if must_exist and not candidate.exists():
            raise ToolError(f"Path does not exist: {user_path}")
        return candidate

    def read_file(self, path: str, start_line: int = 1, end_line: int = 240) -> dict[str, Any]:
        target = self._path(path)
        if not target.is_file() or target.stat().st_size > 500_000:
            raise ToolError("Expected a text file smaller than 500 KB.")
        if start_line < 1 or end_line < start_line or end_line - start_line > 299:
            raise ToolError("Use a line range of 1-300 lines, starting at line 1 or later.")
        lines = target.read_text(encoding="utf-8").splitlines()
        selected = lines[start_line - 1 : end_line]
        content = "\n".join(
            f"{number:>5}: {line}"
            for number, line in enumerate(selected, start=start_line)
        )
        return {
            "ok": True,
            "path": path,
            "total_lines": len(lines),
            "returned_lines": len(selected),
            "content": self._truncate(content),
        }

    def edit_file(self, path: str, old_string: str, new_string: str) -> dict[str, Any]:
        target = self._path(path)
        relative = target.relative_to(self.root)
        lowered_parts = {part.lower() for part in relative.parts}
        if {"test", "tests"}.intersection(lowered_parts) or target.name.lower().startswith("test_"):
            raise ToolError("Editing tests is forbidden; repair production code instead.")
        if not target.is_file() or not old_string or old_string == new_string:
            raise ToolError("Expected a file and two different, non-empty strings.")
        content = target.read_text(encoding="utf-8")
        occurrences = content.count(old_string)
        if occurrences != 1:
            raise ToolError(f"old_string must occur exactly once; found {occurrences}.")
        target.write_text(content.replace(old_string, new_string, 1), encoding="utf-8")
        return {"ok": True, "path": path, "replacements": 1}

    def search_code(self, query: str, path: str = ".", max_results: int = 20) -> dict[str, Any]:
        if not query:
            raise ToolError("query cannot be empty")
        base = self._path(path)
        matches: list[str] = []
        scanned = 0
        skipped = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache"}
        for file in sorted(base.rglob("*")):
            if scanned >= 2000 or len(matches) >= min(max_results, 50):
                break
            if not file.is_file() or skipped.intersection(file.parts) or file.stat().st_size > 300_000:
                continue
            scanned += 1
            try:
                lines = file.read_text(encoding="utf-8").splitlines()
            except UnicodeError:
                continue
            for number, line in enumerate(lines, start=1):
                if query in line:
                    relative = file.relative_to(self.root)
                    matches.append(f"{relative}:{number}: {line.strip()[:240]}")
                    if len(matches) >= min(max_results, 50):
                        break
        return {"ok": True, "matches": matches, "scanned_files": scanned}

    def run_tests(self, target: str) -> dict[str, Any]:
        file_part = target.split("::", 1)[0]
        if target.startswith("-") or not file_part:
            raise ToolError("A test path or node id is required; pytest flags are forbidden.")
        self._path(file_part)
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--tb=short",
            "--disable-warnings",
            "--color=no",
            "-p",
            "no:cacheprovider",
            target,
        ]
        env = os.environ.copy()
        env.update({"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=self.root,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.test_timeout,
                check=False,
            )
            output = (completed.stdout + "\n" + completed.stderr).strip()
            exit_code = completed.returncode
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            parts = []
            for stream in (exc.stdout, exc.stderr):
                if isinstance(stream, bytes):
                    parts.append(stream.decode("utf-8", errors="replace"))
                elif stream:
                    parts.append(stream)
            output = "\n".join(parts) + f"\nTimed out after {self.test_timeout} seconds."
            exit_code = 124
            timed_out = True
        return {
            "ok": True,
            "target": target,
            "passed": exit_code == 0,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "output": self._truncate(output.strip()),
        }

    def _truncate(self, text: str) -> str:
        if len(text) <= self.max_output_chars:
            return text
        omitted = len(text) - self.max_output_chars
        return f"... [{omitted} chars omitted] ...\n" + text[-self.max_output_chars :]
