"""Minute tick for the existing New York paper-trading schedule."""
from __future__ import annotations

import subprocess
import sys
import os
import argparse
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo


NY = ZoneInfo("America/New_York")
WEEKDAY_SCANS = frozenset(
    "00:30 01:30 02:30 03:30 04:30 05:30 06:30 07:30 08:30 "
    "09:10 09:35 09:45 10:00 10:30 11:00 11:30 12:00 12:30 "
    "13:00 13:30 14:00 14:30 15:00 15:30 15:55 16:15 16:20 "
    "17:30 18:30 19:30 20:15 21:30 22:30 23:30".split()
)
SATURDAY_SCANS = frozenset({"11:00"})
SUNDAY_SCANS = frozenset({"18:30", "22:00"})
RUNNER = Path(__file__).with_name("run_runtime.py")


def due_jobs(instant: datetime) -> list[str]:
    local = instant.astimezone(NY)
    hhmm = local.strftime("%H:%M")
    jobs = []
    if local.weekday() < 5 and time(9, 35) <= local.time().replace(second=0, microsecond=0) <= time(16, 5):
        jobs.append("monitor")
    if (local.weekday() < 5 and hhmm in WEEKDAY_SCANS
            or local.weekday() == 5 and hhmm in SATURDAY_SCANS
            or local.weekday() == 6 and hhmm in SUNDAY_SCANS):
        jobs.append("scanner")
    return jobs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("monitor", "scanner"))
    args = parser.parse_args()
    if os.getenv("MARKET_LENS_HETZNER_WRITER_ENABLED") != "true":
        return
    jobs = due_jobs(datetime.now().astimezone())
    for job in jobs:
        if args.kind and job != args.kind:
            continue
        subprocess.run([sys.executable, str(RUNNER), job], check=True)


if __name__ == "__main__":
    main()
