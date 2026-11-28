"""
CLI Entrypoint for Tier-2 Python Brain (Phase 9.0 Polyglot).
Usage:
  python core/main.py --phase api-and-plan --account default [--no-exit-on-cooldown] [--headed]
  python core/main.py --phase ingest-result --account default [--no-exit-on-cooldown]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from core.planner import build_batch_plan, ingest_worker_result
from core.state_db import DEFAULT_DATA_DIR


def main() -> int:
    parser = argparse.ArgumentParser(description="MS Rewards Polyglot - Tier 2 Python Brain")
    parser.add_argument(
        "--phase",
        required=True,
        choices=("api-and-plan", "ingest-result"),
        help="Pipeline phase to run",
    )
    parser.add_argument("--account", default="default", help="Account identifier")
    parser.add_argument(
        "--no-exit-on-cooldown",
        dest="exit_on_cooldown",
        action="store_false",
        default=True,
        help="Disable Stateless Burst cooldown exit (exit code 20)",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        default=False,
        help="Request headed browser execution in batch_plan.json",
    )
    args = parser.parse_args()

    if args.phase == "api-and-plan":
        asyncio.run(
            build_batch_plan(
                account=args.account,
                exit_on_cooldown=args.exit_on_cooldown,
                headless=not args.headed,
                root_dir=ROOT_DIR,
                data_dir=DEFAULT_DATA_DIR,
            )
        )
        return 0

    return ingest_worker_result(
        account=args.account,
        exit_on_cooldown=args.exit_on_cooldown,
        data_dir=DEFAULT_DATA_DIR,
    )


if __name__ == "__main__":
    sys.exit(main())
