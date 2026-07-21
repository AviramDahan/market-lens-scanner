from __future__ import annotations

import base64
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
_LAST_SNAPSHOT_SYNC_AT = 0.0
_LAST_SNAPSHOT_SYNC_RESULT: dict[str, Any] = {"enabled": False, "reason": "not run yet"}
_LAST_SNAPSHOT_ASSET_SYNC_AT = 0.0
_LAST_SNAPSHOT_ASSET_SYNC_RESULT: dict[str, Any] = {"enabled": False, "reason": "not run yet"}

SNAPSHOT_ASSET_SUFFIXES = {".json", ".jsonl", ".md", ".png", ".jpg", ".jpeg"}


def sync_dashboard_snapshot_if_enabled(project_root: Path) -> dict[str, Any]:
    global _LAST_SNAPSHOT_SYNC_AT, _LAST_SNAPSHOT_SYNC_RESULT

    enabled, reason = snapshot_sync_enabled()
    if not enabled:
        _LAST_SNAPSHOT_SYNC_RESULT = {"enabled": False, "reason": reason}
        return _LAST_SNAPSHOT_SYNC_RESULT

    now = time.time()
    ttl = int_env("MARKET_LENS_DASHBOARD_SNAPSHOT_SYNC_TTL_SECONDS", 45)
    if now - _LAST_SNAPSHOT_SYNC_AT < ttl:
        return {**_LAST_SNAPSHOT_SYNC_RESULT, "cached": True}

    repo = os.getenv("GITHUB_ACTIONS_REPOSITORY", "AviramDahan/market-lens-scanner")
    ref = os.getenv("GITHUB_ACTIONS_REF", "main")
    target = "agent_results/dashboard_snapshot.json"
    result: dict[str, Any] = {
        "enabled": True,
        "repo": repo,
        "ref": ref,
        "target": target,
        "downloaded": False,
        "synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        meta = github_file_metadata(repo, ref, target)
        sha = str(meta.get("sha") or "")
        local_path = project_root / target
        download_github_blob_to_path(repo, sha, local_path)
        result["downloaded"] = True
        result["sha"] = sha
    except Exception as exc:
        result["enabled"] = False
        result["reason"] = f"snapshot sync failed: {exc}"

    _LAST_SNAPSHOT_SYNC_AT = time.time()
    _LAST_SNAPSHOT_SYNC_RESULT = result
    return result


def snapshot_sync_enabled() -> tuple[bool, str]:
    configured = os.getenv("MARKET_LENS_DASHBOARD_SNAPSHOT_SYNC_ENABLED")
    if configured is not None and not truthy(configured):
        return False, "disabled by MARKET_LENS_DASHBOARD_SNAPSHOT_SYNC_ENABLED"
    if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
        return False, "disabled inside GitHub Actions"
    if not github_token():
        return False, "missing GitHub token"
    return True, "enabled"


def sync_dashboard_snapshot_assets_if_enabled(project_root: Path, dashboard: dict[str, Any]) -> dict[str, Any]:
    """Download media and report files referenced by the current dashboard snapshot.

    The live Render service can refresh `dashboard_snapshot.json` without a deploy.
    That snapshot may point at newly committed charts/screenshots, so the server
    needs to pull those referenced files too before the browser tries to render them.
    """
    global _LAST_SNAPSHOT_ASSET_SYNC_AT, _LAST_SNAPSHOT_ASSET_SYNC_RESULT

    enabled, reason = snapshot_sync_enabled()
    if not enabled:
        _LAST_SNAPSHOT_ASSET_SYNC_RESULT = {"enabled": False, "reason": reason}
        return _LAST_SNAPSHOT_ASSET_SYNC_RESULT

    referenced_paths = dashboard_asset_paths(dashboard)
    if not referenced_paths:
        _LAST_SNAPSHOT_ASSET_SYNC_RESULT = {
            "enabled": True,
            "downloaded": 0,
            "skipped": 0,
            "missing_before_sync": 0,
            "reason": "snapshot references no agent result assets",
        }
        return _LAST_SNAPSHOT_ASSET_SYNC_RESULT

    missing_paths = [path for path in referenced_paths if not (project_root / path).exists()]
    ttl = int_env("MARKET_LENS_DASHBOARD_ASSET_SYNC_TTL_SECONDS", 45)
    now = time.time()
    if not missing_paths and now - _LAST_SNAPSHOT_ASSET_SYNC_AT < ttl:
        return {**_LAST_SNAPSHOT_ASSET_SYNC_RESULT, "cached": True}

    repo = os.getenv("GITHUB_ACTIONS_REPOSITORY", "AviramDahan/market-lens-scanner")
    ref = os.getenv("GITHUB_ACTIONS_REF", "main")
    state = load_state(project_root)
    limit = int_env("MARKET_LENS_DASHBOARD_ASSET_SYNC_LIMIT", 40)
    time_budget = int_env("MARKET_LENS_DASHBOARD_ASSET_SYNC_TIME_BUDGET_SECONDS", 15)
    started_at = time.monotonic()
    downloaded: list[str] = []
    skipped = 0
    warnings: list[str] = []
    limit_reached = False

    for target in referenced_paths[:limit]:
        if time_budget > 0 and time.monotonic() - started_at >= time_budget:
            limit_reached = True
            break
        try:
            local_path = project_root / target
            if local_path.exists():
                skipped += 1
                continue
            meta = github_file_metadata(repo, ref, target)
            sha = str(meta.get("sha") or "")
            download_url = raw_github_url(repo, ref, target, sha)
            download_to_path(download_url, local_path)
            if sha:
                state[target] = sha
            downloaded.append(target)
        except Exception as exc:  # pragma: no cover - depends on live GitHub/network state
            warnings.append(f"{target}: {exc}")

    if len(referenced_paths) > limit:
        limit_reached = True

    save_state(project_root, state)
    result = {
        "enabled": True,
        "repo": repo,
        "ref": ref,
        "referenced": len(referenced_paths),
        "missing_before_sync": len(missing_paths),
        "downloaded": len(downloaded),
        "skipped": skipped,
        "download_limit": limit,
        "download_limit_reached": limit_reached,
        "time_budget_seconds": time_budget,
        "warnings": warnings[:10],
        "downloaded_paths": downloaded[:20],
        "synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _LAST_SNAPSHOT_ASSET_SYNC_AT = time.time()
    _LAST_SNAPSHOT_ASSET_SYNC_RESULT = result
    return result


def dashboard_asset_paths(value: Any) -> list[str]:
    seen: set[str] = set()
    paths: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for child in item.values():
                visit(child)
            return
        if isinstance(item, list):
            for child in item:
                visit(child)
            return
        if not isinstance(item, str):
            return
        path = agent_result_path_from_url(item)
        if path and path not in seen:
            seen.add(path)
            paths.append(path)

    visit(value)
    return paths


def agent_result_path_from_url(value: str) -> str:
    text = value.strip()
    if not text.startswith("/agent-results/"):
        return ""
    path = "agent_results/" + text.split("/agent-results/", 1)[1].split("?", 1)[0].split("#", 1)[0]
    suffix = Path(path).suffix.lower()
    if suffix not in SNAPSHOT_ASSET_SUFFIXES:
        return ""
    normalized = Path(path)
    if normalized.is_absolute() or ".." in normalized.parts:
        return ""
    return path.replace("\\", "/")


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
    if configured is None:
        if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
            return False, "disabled inside GitHub Actions"
        return False, "disabled by default; set MARKET_LENS_RESULTS_SYNC_ENABLED=true to enable"
    if not truthy(configured):
        return False, "disabled by MARKET_LENS_RESULTS_SYNC_ENABLED"

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
    started_at = time.monotonic()
    download_limit = int_env("MARKET_LENS_RESULTS_SYNC_MAX_DOWNLOADS_PER_REQUEST", 10)
    time_budget = int_env("MARKET_LENS_RESULTS_SYNC_TIME_BUDGET_SECONDS", 18)

    targets: list[dict[str, Any]] = []
    add_file_target(targets, repo, ref, "agent_tracker/" + TRACKER_NAME, warnings)
    targets.extend(
        limited_directory_file_metadata(
            repo,
            ref,
            "agent_results/summaries",
            int_env("MARKET_LENS_RESULTS_SYNC_SUMMARY_LIMIT", 8),
            {".json", ".md"},
        )
    )
    targets.extend(
        limited_directory_file_metadata(
            repo,
            ref,
            "agent_results/position_monitor",
            int_env("MARKET_LENS_RESULTS_SYNC_MONITOR_LIMIT", 5),
            {".md"},
        )
    )
    targets.extend(
        limited_directory_file_metadata(
            repo,
            ref,
            "agent_results/screenshots",
            int_env("MARKET_LENS_RESULTS_SYNC_SCREENSHOT_LIMIT", 2),
            {".png", ".jpg", ".jpeg"},
        )
    )
    targets.extend(
        limited_directory_file_metadata(
            repo,
            ref,
            "agent_results/charts",
            int_env("MARKET_LENS_RESULTS_SYNC_CHART_LIMIT", 3),
            {".png", ".jpg", ".jpeg"},
        )
    )
    targets.extend(
        limited_directory_file_metadata(
            repo,
            ref,
            "agent_results/decisions",
            int_env("MARKET_LENS_RESULTS_SYNC_DECISION_LIMIT", 5),
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
        if time_budget > 0 and time.monotonic() - started_at >= time_budget:
            download_limit_reached = True
            break
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
        "time_budget_seconds": time_budget,
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


def github_blob(repo: str, sha: str) -> dict[str, Any]:
    if not sha:
        raise RuntimeError("missing blob sha")
    url = f"https://api.github.com/repos/{repo}/git/blobs/{urllib.parse.quote(sha, safe='')}"
    request = urllib.request.Request(url, headers=github_headers())
    with urllib.request.urlopen(request, timeout=int_env("MARKET_LENS_RESULTS_SYNC_TIMEOUT_SECONDS", 12)) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict) or data.get("sha") != sha:
        raise RuntimeError("GitHub blob response did not match requested sha")
    return data


def download_github_blob_to_path(repo: str, sha: str, path: Path) -> None:
    data = github_blob(repo, sha)
    content = str(data.get("content") or "")
    encoding = str(data.get("encoding") or "")
    if encoding != "base64" or not content:
        raise RuntimeError("GitHub blob content is not base64 encoded")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_bytes(base64.b64decode("".join(content.split())))
    temp_path.replace(path)


def raw_github_url(repo: str, ref: str, path: str, cache_bust: str = "") -> str:
    encoded_path = "/".join(urllib.parse.quote(part) for part in path.split("/"))
    encoded_ref = urllib.parse.quote(ref, safe="")
    url = f"https://raw.githubusercontent.com/{repo}/{encoded_ref}/{encoded_path}"
    bust = cache_bust or str(int(time.time()))
    return f"{url}?market_lens_sync={urllib.parse.quote(bust)}"


def github_json(repo: str, ref: str, path: str) -> Any:
    encoded_path = "/".join(urllib.parse.quote(part) for part in path.split("/"))
    url = f"https://api.github.com/repos/{repo}/contents/{encoded_path}?ref={urllib.parse.quote(ref)}"
    request = urllib.request.Request(url, headers=github_headers())
    with urllib.request.urlopen(request, timeout=int_env("MARKET_LENS_RESULTS_SYNC_TIMEOUT_SECONDS", 12)) as response:
        return json.loads(response.read().decode("utf-8"))


def download_to_path(url: str, path: Path) -> None:
    request = urllib.request.Request(url, headers=github_headers())
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with urllib.request.urlopen(request, timeout=int_env("MARKET_LENS_RESULTS_SYNC_TIMEOUT_SECONDS", 12)) as response:
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
