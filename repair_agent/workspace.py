"""Git worktree isolation, patch capture, rollback, and cleanup."""

from __future__ import annotations

import subprocess
from pathlib import Path


class WorkspaceError(RuntimeError):
    pass


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if check and completed.returncode != 0:
        raise WorkspaceError((completed.stderr or completed.stdout).strip())
    return completed


class GitWorktree:
    def __init__(self, source_repo: Path, run_dir: Path) -> None:
        self.source_repo = source_repo.resolve()
        self.run_dir = run_dir.resolve()
        self.path = self.run_dir / "worktree"
        self.base_commit = ""
        self.created = False

    def create(self) -> Path:
        root = _git(self.source_repo, "rev-parse", "--show-toplevel").stdout.strip()
        self.source_repo = Path(root).resolve()
        dirty = _git(
            self.source_repo, "status", "--porcelain", "--untracked-files=no"
        ).stdout.strip()
        if dirty:
            raise WorkspaceError("Target repository has tracked uncommitted changes; commit or stash them first.")
        self.base_commit = _git(self.source_repo, "rev-parse", "HEAD").stdout.strip()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        _git(
            self.source_repo,
            "worktree",
            "add",
            "--detach",
            str(self.path),
            self.base_commit,
        )
        self.created = True
        return self.path

    def diff(self) -> str:
        if not self.created:
            return ""
        return _git(self.path, "diff", "--binary", "--full-index", self.base_commit).stdout

    def rollback(self) -> None:
        if not self.created:
            return
        _git(self.path, "reset", "--hard", self.base_commit)
        _git(self.path, "clean", "-fd")

    def cleanup(self) -> str | None:
        if not self.created:
            return None
        completed = _git(
            self.source_repo, "worktree", "remove", "--force", str(self.path), check=False
        )
        _git(self.source_repo, "worktree", "prune", check=False)
        self.created = False
        if completed.returncode != 0:
            return (completed.stderr or completed.stdout).strip()
        return None
