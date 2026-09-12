from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.position_monitor import send_notification_outbox


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send position-monitor notifications after portfolio persistence succeeds."
    )
    parser.add_argument("outbox", type=Path)
    args = parser.parse_args()

    if not args.outbox.exists():
        print("No persisted position event notification is pending.")
        return

    outcomes = send_notification_outbox(args.outbox)
    failed = [outcome for outcome in outcomes if outcome.status in {"failed", "not_configured"}]
    if failed:
        raise SystemExit("One or more post-persistence Telegram notifications failed.")
    args.outbox.unlink(missing_ok=True)
    print(f"Processed {len(outcomes)} post-persistence Telegram notification action(s).")


if __name__ == "__main__":
    main()
