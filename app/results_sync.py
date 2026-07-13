from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

TRACKER_NAME = "market_lens_agent_portfolio_budget_100k.xlsx"
STATE_FILE = ".agent_results_sync_state.json"

_LOCK = threading.Lock()
_LAST_SYNC_AT = 0.0
_LAST_SYNC_RESULT: dict[str, Any] = {"enabled": False, "reason": "not run yet"}


def sync_agent_results_if_enabled(project_root: Path) -> dict[str, Any]:
    """Refresh generated Agent results from GitHub without triggering Render deploys."""
    global _LAST_SYNC_AT, _LAST_SYNC_RESULT

    enabled, reason = sync_enabled()
    if not enabled:
        _LAST_SYNC_RESULT = {"enabled": False, "reason": reason}
        return _LAST_SYNC_RESULT

    now = time.time()
    ttl = int_env("MARKET_LENS_RESULTS_SYNC_TTL_SECONDS", 60)
    if now - _LAST_SYNC_AT < ttl:
        return {**_LAST_SYNC_RESULT, "cached": True}

    if not _LOCK.acquire(blocking=False):
        return {**_LAST_SYNC_RESULT, "cached": True, "reason": "sync already in progress"}

    try:
        if now - _LAST_SYNC_AT < ttl:
            return {**_LAST_SYNC_RESULT, "cached": True}
        result = sync_agent_results(project_root)
        _LAST_SYNC_AT = time.time()
        _LAST_SYNC_RESULT = result
        return result
    finally:
        _LOCK.release()


def sync_enabled() -> tuple[bool, str]:
    configured = os.getenv("MARKET_LENS_RESULTS_SYNC_ENABLED")
    if configured is not None:
        if not truthy(configured):
            return False, "disabled by MARKET_LENS_RESULTS_SYNC_ENABLED"
    elif os.getenv("GITHUB_ACTIONS", "").lower() == "true":
        return False, "disabled inside GitHub Actions"

    if not github_token():
        return False, "missing GitHub token"
    return True, "enabled"


def sync_agent_results(project_root: Path) -> dict[str, Any]:
    repo = os.getenv("GITHUB_ACTIONS_REPOSITORY", "AviramDahan/market-lens-scanner")
    ref = os.getenv("GITHUB_ACTIONS_REF", "main")
    state = load_state(project_root)
    downloaded: list[str] = []
    skipped = 0
    warnings: list[str] = []
    download_limit = int_env("MARKET_LENS_RESULTS_SYNC_MAX_DOWNLOADS_PER_REQUEST", 45)

    targets: list[dict[str, Any]] = []
    add_file_target(targets, repo, ref, "agent_tracker/" + TRACKER_NAME, warnings)
    targets.extend(
        limited_directory_file_metadata(
            repo,
            ref,
            "agent_results/summaries",
            int_env("MARKET_LENS_RESULTS_SYNC_SUMMARY_LIMIT", 25),
            {".json", ".md"},
        )
    )
    targets.extend(
        limited_directory_file_metadata(
            repo,
            ref,
            "agent_results/position_monitor",
            int_env("MARKET_LENS_RESULTS_SYNC_MONITOR_LIMIT", 20),
            {".md"},
        )
    )
    targets.extend(
        limited_directory_file_metadata(
            repo,
            ref,
            "agent_results/screenshots",
            int_env("MARKET_LENS_RESULTS_SYNC_SCREENSHOT_LIMIT", 5),
            {".png", ".jpg", ".jpeg"},
        )
    )
    targets.extend(
        limited_directory_file_metadata(
            repo,
            ref,
            "agent_results/charts",
            int_env("MARKET_LENS_RESULTS_SYNC_CHART_LIMIT", 10),
            {".png", ".jpg", ".jpeg"},
        )
    )
    targets.extend(
        limited_directory_file_metadata(
            repo,
            ref,
            "agent_results/decisions",
            int_env("MARKET_LENS_RESULTS_SYNC_DECISION_LIMIT", 20),
            {".jsonl"},
        ),
    )

    seen: set[str] = set()
    download_limit_reached = False
    for meta in targets:
        target = str(meta.get("path") or "")
        if target in seen:
            continue
        seen.add(target)
        if download_limit > 0 and len(downloaded) >= download_limit:
            download_limit_reached = True
            break
        try:
            local_path = project_root / target
            sha = str(meta.get("sha") or "")
            if local_path.exists() and sha and state.get(target) == sha:
                skipped += 1
                continue
            download_url = str(meta.get("download_url") or "")
            if not download_url:
                warnings.append(f"{target}: missing download_url")
                continue
            download_to_path(download_url, local_path)
            if sha:
                state[target] = sha
            downloaded.append(target)
        except Exception as exc:  # pragma: no cover - exercised through integration/runtime
            warnings.append(f"{target}: {exc}")

    save_state(project_root, state)
    return {
        "enabled": True,
        "repo": repo,
        "ref": ref,
        "downloaded": len(downloaded),
        "skipped": skipped,
        "download_limit": download_limit,
        "download_limit_reached": download_limit_reached,
        "warnings": warnings[:10],
        "downloaded_paths": downloaded[:20],
        "synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def add_file_target(targets: list[dict[str, Any]], repo: str, ref: str, path: str, warnings: list[str]) -> None:
    try:
        meta = github_file_metadata(repo, ref, path)
        meta["path"] = path
        targets.append(meta)
    except Exception as exc:
        warnings.append(f"{path}: {exc}")


def limited_directory_file_metadata(
    repo: str,
    ref: str,
    directory: str,
    limit: int,
    suffixes: set[str],
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    try:
        items = github_directory(repo, ref, directory)
    except Exception:
        return []
    files = [
        item
        for item in items
        if item.get("type") == "file" and Path(str(item.get("name") or "")).suffix.lower() in suffixes
    ]
    return sorted(files, key=lambda item: str(item.get("path") or ""), reverse=True)[:limit]


def github_directory(repo: str, ref: str, directory: str) -> list[dict[str, Any]]:
    data = github_json(repo, ref, directory)
    if isinstance(data, list):
        return data
    return []


def github_file_metadata(repo: str, ref: str, path: str) -> dict[str, Any]:
    data = github_json(repo, ref, path)
    if not isinstance(data, dict) or data.get("type") != "file":
        raise RuntimeError("GitHub path is not a file")
    return data


def github_json(repo: str, ref: str, path: str) -> Any:
    encoded_path = "/".join(urllib.parse.quote(part) for part in path.split("/"))
    url = f"https://api.github.com/repos/{repo}/contents/{encoded_path}?ref={urllib.parse.quote(ref)}"
    request = urllib.request.Request(url, headers=github_headers())
    with urllib.request.urlopen(request, timeout=int_env("MARKET_LENS_RESULTS_SYNC_TIMEOUT_SECONDS", 25)) as response:
        return json.loads(response.read().decode("utf-8"))


def download_to_path(url: str, path: Path) -> None:
    request = urllib.request.Request(url, headers=github_headers())
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with urllib.request.urlopen(request, timeout=int_env("MARKET_LENS_RESULTS_SYNC_TIMEOUT_SECONDS", 25)) as response:
        temp_path.write_bytes(response.read())
    temp_path.replace(path)


def github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "market-lens-results-sync",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def github_token() -> str:
    return os.getenv("GITHUB_ACTIONS_TRIGGER_TOKEN") or os.getenv("MARKET_LENS_GITHUB_ACTIONS_TOKEN") or ""


def load_state(project_root: Path) -> dict[str, str]:
    path = project_root / STATE_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(key): str(value) for key, value in data.items()}
    except Exception:
        return {}
    return {}


def save_state(project_root: Path, state: dict[str, str]) -> None:
    path = project_root / STATE_FILE
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
