"""Isolate a long scanner run from the live paper portfolio."""
from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path


# Run-addressed evidence is never edited in place. A read-only container mount
# lets the isolated worker read it without copying gigabytes on every scan.
READ_ONLY_HISTORY = frozenset({
    "charts", "screenshots", "decisions", "runtime", "diagnostics", "policies", "audits",
})
CONTAINER_HISTORY = Path("/app/agent_results_history")
WRITABLE_DIRECTORIES = frozenset({
    "charts", "screenshots", "decisions", "runtime", "diagnostics", "policies",
    "summaries", "regime", "experiments",
})


@dataclass
class ScannerStage:
    root: Path
    tracker: Path
    results: Path
    runtime: Path
    original_tracker_hash: str
    baseline: dict[Path, tuple[int, int]]
    history_paths: frozenset[Path]
    history_source: Path
    history_signatures: dict[Path, tuple[int, int, int]]


def signature(path: Path) -> tuple[int, int]:
    stat = path.lstat()
    return stat.st_size, stat.st_mtime_ns


def prepare_stage(repo: Path, state: Path, tracker_hash: str,
                  *, history_root: Path | None = None) -> ScannerStage:
    root = state / "scanner-staging" / uuid.uuid4().hex
    root.mkdir(parents=True, mode=0o700)
    tracker = root / "agent_tracker"
    results = root / "agent_results"
    runtime = root / "runtime"
    try:
        shutil.copytree(repo / "agent_tracker", tracker, copy_function=shutil.copy2)
        source_results = repo / "agent_results"
        linked_root = history_root or CONTAINER_HISTORY
        results.mkdir()
        for source in source_results.rglob("*"):
            relative = source.relative_to(source_results)
            target = results / relative
            if source.is_symlink():
                raise RuntimeError("Scanner history must not contain symlinks")
            if source.is_dir():
                target.mkdir(exist_ok=True)
            elif source.is_file():
                if (relative.parts[0] in READ_ONLY_HISTORY
                        and not (relative.parts[0] == "charts" and not source.name.startswith("market_lens_agent_"))):
                    target.symlink_to(linked_root / relative)
                else:
                    shutil.copy2(source, target)
        # The root worker must not create these as root-owned directories;
        # otherwise trader cannot remove their contents after a successful run.
        for name in WRITABLE_DIRECTORIES:
            (results / name).mkdir(exist_ok=True)
        runtime.mkdir()
        (runtime / "outbox").mkdir()
    except Exception:
        shutil.rmtree(root)
        raise
    baseline = {
        path.relative_to(results): signature(path)
        for path in results.rglob("*") if path.is_file() or path.is_symlink()
    }
    history_paths = frozenset(path.relative_to(results) for path in results.rglob("*") if path.is_symlink())
    history_signatures = {}
    for relative in history_paths:
        info = (repo / "agent_results" / relative).stat()
        history_signatures[relative] = info.st_size, info.st_mtime_ns, info.st_ino
    return ScannerStage(root, tracker, results, runtime, tracker_hash, baseline, history_paths,
                        repo / "agent_results", history_signatures)


def changed_results(stage: ScannerStage) -> list[Path]:
    for relative in stage.history_paths:
        path = stage.results / relative
        if not path.is_symlink() or signature(path) != stage.baseline[relative]:
            raise RuntimeError(f"Read-only historical evidence changed: {relative}")
        try:
            source = stage.history_source / relative
            info = source.stat()
            current = info.st_size, info.st_mtime_ns, info.st_ino
        except OSError:
            current = None
        if source.is_symlink() or current != stage.history_signatures[relative]:
            raise RuntimeError(f"Historical source changed during scan: {relative}")
    changed = [
        path for path in stage.results.rglob("*")
        if not path.is_symlink() and path.is_file()
        and stage.baseline.get(path.relative_to(stage.results)) != signature(path)
    ]
    return sorted(changed, key=lambda path: (
        path.relative_to(stage.results).parts[0] == "runtime", str(path)
    ))


def compact_failed_stage(stage: ScannerStage) -> None:
    """Keep new diagnostic evidence, not duplicate copies of old result files."""
    if stage.root.parent.name != "scanner-staging":
        raise RuntimeError("Invalid scanner stage path")
    for relative, original in stage.baseline.items():
        path = stage.results / relative
        if (path.is_file() or path.is_symlink()) and signature(path) == original:
            path.unlink()


def promote_stage(stage: ScannerStage, repo: Path, state: Path) -> None:
    regular_results = []
    runtime_results = []
    for source in changed_results(stage):
        destination = repo / "agent_results" / source.relative_to(stage.results)
        (runtime_results if source.relative_to(stage.results).parts[0] == "runtime"
         else regular_results).append((source, destination))
    staged_tracker = stage.tracker / "market_lens_agent_portfolio_budget_100k.xlsx"
    if not staged_tracker.is_file():
        raise RuntimeError("Staged scanner did not produce a portfolio workbook")
    destinations = regular_results + [(staged_tracker, repo / "agent_tracker" / staged_tracker.name)]
    outbox = stage.runtime / "outbox/scanner.json"
    if outbox.exists():
        destinations.append((outbox, state / "outbox/scanner.json"))
    destinations.extend(runtime_results)

    rollback = stage.root / "rollback"
    applied: list[tuple[Path, Path | None]] = []
    try:
        for source, target in destinations:
            target.parent.mkdir(parents=True, exist_ok=True)
            previous = None
            if target.exists():
                previous = rollback / str(len(applied))
                previous.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, previous)
            temporary = stage.root / f"promote-{uuid.uuid4().hex}"
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
            applied.append((target, previous))
    except Exception:
        for target, previous in reversed(applied):
            if previous is None:
                target.unlink(missing_ok=True)
            else:
                os.replace(previous, target)
        raise
