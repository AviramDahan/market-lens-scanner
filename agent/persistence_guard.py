"""Read-only conflict check before overlaying generated state on a newer commit."""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

STATE_PATHS = ("agent_tracker", "agent_results")


def assert_state_unchanged(base: str, remote: str, *, cwd: Path | None = None) -> None:
    # Compare committed trees, not locally generated files awaiting persistence.
    commits = []
    for ref in (base, remote):
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
            cwd=cwd, capture_output=True, text=True, timeout=30,
        )
        if result.returncode:
            raise RuntimeError("PERSISTENCE_CHECK_FAILED: cannot resolve comparison commit")
        commits.append(result.stdout.strip())
    result = subprocess.run(
        ["git", "diff", "--quiet", "--no-ext-diff", *commits, "--", *STATE_PATHS],
        cwd=cwd, capture_output=True, timeout=60,
    )
    if result.returncode == 1:
        raise RuntimeError(
            "PERSISTENCE_CONFLICT: remote portfolio/results changed during this run; "
            "refusing stale state overwrite. Preserve recovery artifact and reconcile before retry."
        )
    if result.returncode:
        raise RuntimeError("PERSISTENCE_CHECK_FAILED: cannot compare committed state")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base")
    parser.add_argument("remote")
    args = parser.parse_args()
    try:
        assert_state_unchanged(args.base, args.remote)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        print(str(exc))
        return 1
    print("Persistence check passed: remote committed state matches run baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
