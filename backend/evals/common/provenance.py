"""What a saved eval run records about the code that produced it."""

import subprocess


def git_version() -> str:
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "."], capture_output=True, text=True).stdout.strip()
    return commit + ("+dirty" if dirty else "")
