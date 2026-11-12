"""
Multi-Account Orchestrator - Phase 7.3
Manages sequential FIFO queue execution for multiple accounts.
"""

import random
import time
from datetime import datetime, timedelta
from typing import List, Optional
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from core.database import (
    list_accounts, get_account, update_account_status,
    get_daily_progress, upsert_daily_progress,
    insert_run_log
)
from runner import run_full_pipeline


def get_next_run_time(preferred_hour: Optional[int] = None) -> datetime:
    """Calculate next run time based on circadian preference with 15-40 min jitter."""
    if preferred_hour is None:
        preferred_hour = 6
    
    now = datetime.now()
    target = now.replace(hour=preferred_hour, minute=0, second=0, microsecond=0)
    
    if now >= target:
        target += timedelta(days=1)
    
    # Add jitter 15-40 minutes
    jitter = random.randint(15, 40)
    return target + timedelta(minutes=jitter)


def run_multi_account_queue(suppress_auto_sleep: bool = True):
    """
    Run sequential FIFO queue for all enabled accounts.
    Accounts are ordered by priority (desc) then last_run_at (asc).
    """
    accounts = list_accounts(enabled_only=True)
    
    # Sort by priority desc, then by last_run_at asc (nulls first)
    accounts.sort(key=lambda a: (-a.get("priority", 0), a.get("last_run_at") or ""))
    
    for acc in accounts:
        account_id = acc["account_id"]
        
        update_account_status(account_id, "running")
        insert_run_log(account_id, "INFO", "orchestrator", f"Starting pipeline for {account_id}")
        
        try:
            run_full_pipeline(
                account_id=account_id,
                suppress_auto_sleep=suppress_auto_sleep
            )
            update_account_status(account_id, "completed")
            insert_run_log(account_id, "SUCCESS", "orchestrator", f"Completed pipeline for {account_id}")
        except Exception as e:
            update_account_status(account_id, "failed")
            insert_run_log(account_id, "ERROR", "orchestrator", f"Pipeline failed for {account_id}: {e}")
            raise
        
        # Circadian jitter between accounts (15-40 minutes)
        if acc != accounts[-1]:  # Not the last account
            jitter = random.randint(900, 2400)  # seconds
            insert_run_log("system", "INFO", "orchestrator", f"Waiting {jitter}s before next account")
            time.sleep(jitter)
    
    insert_run_log("system", "SUCCESS", "orchestrator", "Multi-account queue completed")


if __name__ == "__main__":
    run_multi_account_queue()