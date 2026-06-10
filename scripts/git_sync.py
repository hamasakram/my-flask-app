"""Commit and push project changes to the git remote."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(command: list[str], cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=str(cwd),
        check=True,
        text=True,
        capture_output=True,
    )


def git_commit_push(message: str, push: bool = True) -> str:
    """
    Stage all changes, create a commit, and optionally push to origin.

    Returns the new commit hash on success.
    """
    status = _run(["git", "status", "--porcelain"])
    if not status.stdout.strip():
        return "No changes to commit."

    _run(["git", "add", "-A"])
    commit = _run(["git", "commit", "-m", message])
    commit_line = next(
        (line for line in commit.stdout.splitlines() if line.startswith("[")),
        "",
    )

    if push:
        _run(["git", "push", "origin", "HEAD"])

    return commit_line or "Committed."


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/git_sync.py \"commit message\"")
        return 1

    message = sys.argv[1]
    try:
        result = git_commit_push(message)
        print(result)
        return 0
    except subprocess.CalledProcessError as exc:
        print(exc.stderr or exc.stdout or str(exc), file=sys.stderr)
        return exc.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main())
