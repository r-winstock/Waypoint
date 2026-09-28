"""CLI wrapper around app.google_timeline_import.run() - the actual import
logic lives there now (it ships inside the running container so the app's
own /api/import/google-timeline endpoint can drive it too; see that
module's docstring). This wrapper stays for the manual/precise-cutoff
workflow the API's auto-detected cutoff doesn't cover - e.g. backfilling a
specific historical gap.

Usage:
    python scripts/import_google_timeline.py "/path/to/Timeline (....json)"

--since YYYY-MM-DD restricts the import to segments starting on/after that
date - see app/google_timeline_import.py's docstring for why this matters.
For a precise sub-day cutoff, call app.google_timeline_import.run() directly
with an exact epoch since_ts instead of going through this date-only flag.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.google_timeline_import import run  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("json_path", type=Path, help="Path to a Google Timeline export JSON file")
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Only import segments starting on/after this date - see module docstring for why this matters",
    )
    args = parser.parse_args()

    since_ts = int(datetime.strptime(args.since, "%Y-%m-%d").timestamp()) if args.since else None
    run(args.json_path, since_ts)
