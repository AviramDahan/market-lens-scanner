"""Read-only host memory diagnostics; never starts a process inside the web container."""
from __future__ import annotations

import json
import http.client
from pathlib import Path
import socket


CONTAINER = "market-lens-runtime-web-1"
WARNING_RATIO = 0.85


class DockerConnection(http.client.HTTPConnection):
    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect("/var/run/docker.sock")


def inspect_web() -> dict:
    # Only GET is used. Do not spawn Docker/Go under Python's address-space cap.
    connection = DockerConnection("localhost", timeout=10)
    try:
        connection.request("GET", f"/containers/{CONTAINER}/json")
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("Web container identity is unavailable")
        raw = response.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise ValueError("Container identity response exceeded its bound")
        data = json.loads(raw)
    finally:
        connection.close()
    # Never expose the raw response: Docker metadata can include environment secrets.
    record = {"pid": data["State"]["Pid"], "running": data["State"]["Running"],
              "started_at": data["State"]["StartedAt"], "restarts": data["RestartCount"],
              "memory_limit": data["HostConfig"]["Memory"]}
    if (record.get("running") is not True or type(record.get("pid")) is not int
            or record["pid"] <= 0):
        raise ValueError("Web container is not running")
    return record


def small_text(path: Path) -> str:
    with path.open(encoding="utf-8") as stream:
        text = stream.read(8193)
    if len(text) > 8192:
        raise ValueError("Diagnostic input exceeded its bound")
    return text


def memory_sample(pid: int, *, proc: Path = Path("/proc"),
                  cgroups: Path = Path("/sys/fs/cgroup")) -> dict:
    # Resolve cgroup v2 from the live PID, not a guessed Docker filesystem path.
    entries = small_text(proc / str(pid) / "cgroup").splitlines()
    unified = [row[3:] for row in entries if row.startswith("0::/")]
    if len(unified) != 1:
        raise ValueError("Unified memory accounting is unavailable")
    root = cgroups.resolve()
    group = (root / unified[0].lstrip("/")).resolve()
    if not group.is_relative_to(root):
        raise ValueError("Cgroup escaped the accounting root")
    current = int(small_text(group / "memory.current").strip())
    maximum = small_text(group / "memory.max").strip()
    limit = None if maximum == "max" else int(maximum)
    events = dict(row.split() for row in small_text(group / "memory.events").splitlines())
    oom = int(events.get("oom", "0"))
    kills = int(events.get("oom_kill", "0"))
    if current < 0 or (limit is not None and limit <= 0) or min(oom, kills) < 0:
        raise ValueError("Invalid memory accounting")
    return {"memory_bytes": current, "limit_bytes": limit,
            "usage_ratio": round(current / limit, 4) if limit else None,
            "oom_events": oom, "oom_kills": kills}


def diagnose(*, inspect=inspect_web, sample=memory_sample) -> dict:
    before = inspect()
    memory = sample(before["pid"])
    after = inspect()
    if any(before.get(key) != after.get(key) for key in ("pid", "started_at", "restarts")):
        return {"status": "NOT_VERIFIED", "reason": "Container changed during diagnosis"}
    ratio = memory["usage_ratio"]
    if memory["limit_bytes"] != before["memory_limit"]:
        return {"status": "NOT_VERIFIED", "reason": "Memory limit identity mismatch"}
    attention = ratio is None or ratio >= WARNING_RATIO or memory["oom_events"] > 0
    return {"status": "attention" if attention else "ok", "container": CONTAINER,
            "started_at": before["started_at"], "restart_count": before["restarts"],
            **memory, "execution_location": "host", "read_only": True}


def limit_diagnostic_resources() -> None:
    import resource

    # These limits affect only this host-side process, never the web service.
    for name, budget in ((resource.RLIMIT_AS, 192 * 1024**2),
                         (resource.RLIMIT_CPU, 15), (resource.RLIMIT_NOFILE, 64)):
        _, hard = resource.getrlimit(name)
        bound = budget if hard == resource.RLIM_INFINITY else min(budget, hard)
        resource.setrlimit(name, (bound, bound))


def main() -> int:
    try:
        limit_diagnostic_resources()
        result = diagnose()
    except Exception as exc:
        # Exception text and raw Docker output may contain private host metadata.
        result = {"status": "NOT_VERIFIED", "reason": type(exc).__name__}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
