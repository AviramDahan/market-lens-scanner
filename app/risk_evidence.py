"""Read-only interpretation of recorded risk evidence, including legacy records."""
from typing import Any


def correlation_passed(record: dict[str, Any]) -> bool | None:
    if record.get("correlation_warning") is True:
        return False
    if record.get("correlation_status") in {"UNKNOWN", "PARTIAL", "LEGACY_UNRECORDED"}:
        return None
    if record.get("correlation_status") in {"PASS", "NOT_REQUIRED"}:
        return True
    warning = record.get("correlation_warning")
    return None if warning is None else not bool(warning)
