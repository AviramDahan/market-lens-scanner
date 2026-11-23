"""Memory diagnostics must remain outside the web memory cgroup and portfolio."""
import ast
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from deploy.hetzner import runtime_diagnostics as diagnostics


def identity():
    return {"pid": 123, "running": True, "memory_limit": 1024,
            "restarts": 0, "started_at": "2026-11-21T20:00:00Z"}


def sample(**updates):
    return {"memory_bytes": 512, "limit_bytes": 1024, "usage_ratio": 0.5,
            "oom_events": 0, "oom_kills": 0, **updates}


@pytest.mark.parametrize("values,status", [({}, "ok"), ({"usage_ratio": 0.85}, "attention"),
                                           ({"oom_events": 1}, "attention")])
def test_read_only_memory_result(values, status):
    before = identity()
    result = diagnostics.diagnose(inspect=lambda: before, sample=lambda _: sample(**values))
    assert result["status"] == status
    assert result["execution_location"] == "host"
    assert result["read_only"] is True
    assert before == identity()


def test_restart_during_diagnosis_is_not_reported_healthy():
    rows = iter([identity(), {**identity(), "pid": 456}])
    result = diagnostics.diagnose(inspect=lambda: next(rows), sample=lambda _: sample())
    assert result["status"] == "NOT_VERIFIED"


def test_mismatched_memory_limit_is_not_verified():
    result = diagnostics.diagnose(inspect=identity, sample=lambda _: sample(limit_bytes=2048))
    assert result["status"] == "NOT_VERIFIED"


def test_memory_accounting_from_fixture(tmp_path):
    proc = tmp_path / "proc"
    cgroups = tmp_path / "cgroup"
    (proc / "123").mkdir(parents=True)
    group = cgroups / "system.slice/docker-test.scope"
    group.mkdir(parents=True)
    (proc / "123/cgroup").write_text("0::/system.slice/docker-test.scope\n")
    (group / "memory.current").write_text("512\n")
    (group / "memory.max").write_text("1024\n")
    (group / "memory.events").write_text("low 0\nhigh 0\nmax 5\noom 1\noom_kill 1\n")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = diagnostics.memory_sample(123, proc=proc, cgroups=cgroups)
    assert result == sample(oom_events=1, oom_kills=1)
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_only_read_only_docker_api_is_used_and_environment_is_not_exposed(monkeypatch):
    calls = []
    raw = {"State": {"Pid": 123, "Running": True, "StartedAt": identity()["started_at"]},
           "HostConfig": {"Memory": 1024}, "RestartCount": 0,
           "Config": {"Env": ["SECRET=must-not-be-exposed"]}}

    class Connection:
        def __init__(self, host, timeout):
            assert timeout == 10

        def request(self, method, path):
            calls.append((method, path))

        def getresponse(self):
            return SimpleNamespace(status=200, read=lambda limit: json.dumps(raw).encode()[:limit])

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(diagnostics, "DockerConnection", Connection)
    assert diagnostics.inspect_web() == identity()
    assert calls == [("GET", f"/containers/{diagnostics.CONTAINER}/json"), "closed"]


def test_script_has_no_application_or_database_imports():
    tree = ast.parse(Path(diagnostics.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
    assert not any(name.startswith(("app", "agent", "sqlite3", "pandas", "matplotlib")) for name in modules)


def test_host_limits_never_raise_an_existing_hard_limit(monkeypatch):
    calls = []
    resource = SimpleNamespace(RLIMIT_AS=1, RLIMIT_CPU=2, RLIMIT_NOFILE=3, RLIM_INFINITY=-1,
                               getrlimit=lambda _: (32, 32),
                               setrlimit=lambda name, values: calls.append((name, values)))
    monkeypatch.setitem(sys.modules, "resource", resource)
    diagnostics.limit_diagnostic_resources()
    assert calls == [(1, (32, 32)), (2, (15, 15)), (3, (32, 32))]


def test_error_details_are_redacted(monkeypatch, capsys):
    monkeypatch.setattr(diagnostics, "limit_diagnostic_resources", lambda: None)

    def fail():
        raise ValueError("secret-password-value")

    monkeypatch.setattr(diagnostics, "diagnose", fail)
    assert diagnostics.main() == 1
    text = capsys.readouterr().out
    assert "secret-password-value" not in text
    assert json.loads(text)["status"] == "NOT_VERIFIED"
