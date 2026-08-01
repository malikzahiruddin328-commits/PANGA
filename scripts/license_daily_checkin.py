"""Daily license check-in, run out-of-process via Windows Task Scheduler
(not a Claude scheduled task like panga-daily-job-search - this is
deterministic HTTP work with no reasoning involved, same rationale as
scripts/recover_access.py). Keeps local_state's last_successful_checkin_at
fresh even on days the app itself isn't opened, so a customer who only
opens Panga once a week doesn't see a stale "3 days offline" warning on
day one of actually launching it.

Exit code is 0 whether the license is verified or expired - a Task
Scheduler failure exit code should mean "this script itself broke", not
"the customer's trial ended". Whatever check_in() returns is already
persisted to local_state by client.py; there's nothing further to act on
here.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from licensing.client import check_in  # noqa: E402


def main() -> int:
    result = check_in()
    print(f"License check-in: {result['state']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
