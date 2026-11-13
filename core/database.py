"""
core/database.py - SQLite WAL-mode database layer for MS Rewards Bot
Replaces all single-account JSON files with multi-account concurrent-safe storage.
"""

import contextlib
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from core.logger import logger

DB_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_DB_PATH = DB_DIR / "rewards_hub.db"
LEGACY_DIR = DB_DIR / "legacy_backup"

PRAGMAS = (
    "PRAGMA journal_mode = WAL;",
    "PRAGMA synchronous = NORMAL;",
    "PRAGMA busy_timeout = 5000;",
    "PRAGMA foreign_keys = ON;",
    "PRAGMA temp_store = MEMORY;",
)


@contextlib.contextmanager
def get_db(db_path: Optional[Path] = None):
    """
    Context manager for SQLite connections with WAL mode and automatic commit/rollback.
    Yields a connection with sqlite3.Row factory.
    """
    path = db_path or DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        for pragma in PRAGMAS:
            conn.execute(pragma)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Optional[Path] = None) -> None:
    """
    Initialize database schema (idempotent) and run legacy JSON migration if needed.
    """
    with get_db(db_path) as conn:
        _create_tables(conn)
        _maybe_run_migration(conn)


def _create_tables(conn: sqlite3.Connection) -> None:
    """Create all 7 tables and indexes."""
    conn.executescript("""
    -- 1. ACCOUNTS
    CREATE TABLE IF NOT EXISTS accounts (
        account_id      TEXT PRIMARY KEY,
        display_name    TEXT,
        profile_dir     TEXT,
        enabled         INTEGER DEFAULT 1,
        proxy_url       TEXT,
        timezone        TEXT DEFAULT 'Asia/Ho_Chi_Minh',
        preferred_hour  INTEGER,
        target_reward   TEXT,
        target_points   INTEGER DEFAULT 0,
        status          TEXT DEFAULT 'idle',
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- 2. DAILY_PROGRESS
    CREATE TABLE IF NOT EXISTS daily_progress (
        account_id      TEXT NOT NULL,
        date_str        TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'pending',
        pc_searches_done INTEGER DEFAULT 0,
        pc_searches_target INTEGER DEFAULT 90,
        mobile_searches_done INTEGER DEFAULT 0,
        mobile_searches_target INTEGER DEFAULT 60,
        activities_done INTEGER DEFAULT 0,
        checkin_awarded INTEGER DEFAULT 0,
        bing_star_bonus TEXT DEFAULT '0 / 2,100',
        started_at      TEXT,
        completed_at    TEXT,
        error_msg       TEXT,
        PRIMARY KEY (account_id, date_str),
        FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_daily_progress_date ON daily_progress(date_str);
    CREATE INDEX IF NOT EXISTS idx_daily_progress_status ON daily_progress(status);

    -- 3. POINTS_LEDGER
    CREATE TABLE IF NOT EXISTS points_ledger (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id      TEXT NOT NULL,
        date_str        TEXT NOT NULL,
        start_points    INTEGER,
        end_points      INTEGER,
        earned_points   INTEGER,
        bing_star_pts   INTEGER DEFAULT 0,
        desktop_searches INTEGER DEFAULT 0,
        mobile_searches  INTEGER DEFAULT 0,
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_points_ledger_account_date 
        ON points_ledger(account_id, date_str);
    CREATE INDEX IF NOT EXISTS idx_points_ledger_date ON points_ledger(date_str);

    -- 4. OAUTH_TOKENS
    CREATE TABLE IF NOT EXISTS oauth_tokens (
        account_id      TEXT PRIMARY KEY,
        access_token    TEXT NOT NULL,
        refresh_token   TEXT NOT NULL,
        expires_at      TEXT,
        client_id       TEXT,
        updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
    );

    -- 5. SEARCH_HISTORY
    CREATE TABLE IF NOT EXISTS search_history (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id      TEXT NOT NULL,
        keyword         TEXT NOT NULL,
        searched_at     TEXT NOT NULL DEFAULT (datetime('now')),
        platform        TEXT NOT NULL,
        FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_search_history_account_keyword 
        ON search_history(account_id, keyword);
    CREATE INDEX IF NOT EXISTS idx_search_history_account_date 
        ON search_history(account_id, searched_at);

    -- 6. RUN_LOGS
    CREATE TABLE IF NOT EXISTS run_logs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id      TEXT NOT NULL,
        timestamp       TEXT NOT NULL,
        level           TEXT NOT NULL,
        module          TEXT,
        message         TEXT NOT NULL,
        FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_run_logs_account_time 
        ON run_logs(account_id, timestamp);
    CREATE INDEX IF NOT EXISTS idx_run_logs_level ON run_logs(level);

    -- 7. DAPI_DEBUG_CACHE
    CREATE TABLE IF NOT EXISTS dapi_debug_cache (
        account_id      TEXT PRIMARY KEY,
        channel         TEXT NOT NULL,
        raw_json_str    TEXT NOT NULL,
        captured_at     TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
    );
    """)


def _maybe_run_migration(conn: sqlite3.Connection) -> None:
    """
    Run legacy JSON migration if acc_primary not yet migrated and JSON files exist.
    """
    cursor = conn.execute("SELECT COUNT(*) FROM accounts WHERE account_id = 'acc_primary'")
    if cursor.fetchone()[0] > 0:
        return
    
    data_dir = DEFAULT_DB_PATH.parent
    json_files_exist = any([
        (data_dir / "points_ledger.json").exists(),
        (data_dir / "daily_state.json").exists(),
        (data_dir / "mobile_oauth.json").exists(),
        (data_dir / "mobile_tokens.json").exists(),
    ])
    
    if not json_files_exist:
        return
    
    logger.info("[Migration] Starting legacy JSON migration to SQLite...")
    _migrate_legacy_json(conn, data_dir)
    logger.info("[Migration] Legacy JSON migration completed.")


def _migrate_legacy_json(conn: sqlite3.Connection, data_dir: Path) -> None:
    """Migrate legacy JSON files into acc_primary and backup originals."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = data_dir / "legacy_backup" / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    _ensure_primary_account(conn)
    
    _migrate_points_ledger(conn, data_dir, backup_dir)
    _migrate_daily_state(conn, data_dir, backup_dir)
    _migrate_oauth_tokens(conn, data_dir, backup_dir)
    _migrate_search_history(conn, data_dir, backup_dir)
    
    conn.commit()


def _ensure_primary_account(conn: sqlite3.Connection) -> None:
    """Ensure acc_primary exists in accounts table."""
    conn.execute("""
        INSERT OR IGNORE INTO accounts (account_id, display_name, profile_dir, enabled)
        VALUES ('acc_primary', 'Primary (Migrated)', 'profiles/desktop', 1)
    """)


def _migrate_points_ledger(conn: sqlite3.Connection, data_dir: Path, backup_dir: Path) -> None:
    """Migrate data/points_ledger.json -> points_ledger table."""
    src = data_dir / "points_ledger.json"
    if not src.exists():
        return
    
    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    for entry in data.get("history", []):
        conn.execute("""
            INSERT OR IGNORE INTO points_ledger 
            (account_id, date_str, start_points, end_points, earned_points, bing_star_pts, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            "acc_primary",
            entry.get("date"),
            entry.get("total_points", 0) - entry.get("earned_today", 0),
            entry.get("total_points", 0),
            entry.get("earned_today", 0),
            0,
            entry.get("updated_at")
        ))
    
    shutil.move(str(src), str(backup_dir / "points_ledger.json"))


def _migrate_daily_state(conn: sqlite3.Connection, data_dir: Path, backup_dir: Path) -> None:
    """Migrate data/daily_state.json -> daily_progress table."""
    src = data_dir / "daily_state.json"
    if not src.exists():
        return
    
    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    conn.execute("""
        INSERT OR REPLACE INTO daily_progress 
        (account_id, date_str, status, checkin_awarded, completed_at, error_msg)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        "acc_primary",
        data.get("date"),
        data.get("status"),
        data.get("checkin_awarded", 0),
        data.get("completed_at"),
        None
    ))
    
    shutil.move(str(src), str(backup_dir / "daily_state.json"))


def _migrate_oauth_tokens(conn: sqlite3.Connection, data_dir: Path, backup_dir: Path) -> None:
    """Migrate data/mobile_oauth.json and/or mobile_tokens.json -> oauth_tokens table."""
    for src_name in ("mobile_oauth.json", "mobile_tokens.json"):
        src = data_dir / src_name
        if not src.exists():
            continue
        
        with open(src, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        access_token = data.get("access_token") or data.get("accessToken")
        refresh_token = data.get("refresh_token") or data.get("refreshToken")
        expires_at = data.get("expires_at") or data.get("expiresAt")
        updated_at = data.get("updated_at")
        
        if access_token and refresh_token:
            conn.execute("""
                INSERT OR REPLACE INTO oauth_tokens 
                (account_id, access_token, refresh_token, expires_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
            """, ("acc_primary", access_token, refresh_token, expires_at, updated_at))
        
        shutil.move(str(src), str(backup_dir / src_name))


def _migrate_search_history(conn: sqlite3.Connection, data_dir: Path, backup_dir: Path) -> None:
    """Migrate search keyword history JSON -> search_history table."""
    for src_name in ("searched_history.json", "search_history.json", "keywords.json"):
        src = data_dir / src_name
        if not src.exists():
            continue
        
        with open(src, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        if isinstance(data, list):
            for keyword in data:
                if keyword and isinstance(keyword, str):
                    conn.execute("""
                        INSERT OR IGNORE INTO search_history (account_id, keyword, platform)
                        VALUES (?, ?, 'desktop')
                    """, ("acc_primary", keyword))
        elif isinstance(data, dict) and "history" in data:
            for keyword in data["history"]:
                if keyword and isinstance(keyword, str):
                    conn.execute("""
                        INSERT OR IGNORE INTO search_history (account_id, keyword, platform)
                        VALUES (?, ?, 'desktop')
                    """, ("acc_primary", keyword))
        
        shutil.move(str(src), str(backup_dir / src_name))


# =============================================================================
# ACCOUNTS REPOSITORY
# =============================================================================

def upsert_account(
    account_id: str,
    display_name: Optional[str] = None,
    profile_dir: Optional[str] = None,
    enabled: int = 1,
    proxy_url: Optional[str] = None,
    timezone: str = "Asia/Ho_Chi_Minh",
    preferred_hour: Optional[int] = None,
    target_reward: Optional[str] = None,
    target_points: int = 0,
    db_path: Optional[Path] = None
) -> None:
    with get_db(db_path) as conn:
        conn.execute("""
            INSERT INTO accounts (account_id, display_name, profile_dir, enabled, proxy_url, 
                                 timezone, preferred_hour, target_reward, target_points, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(account_id) DO UPDATE SET
                display_name = COALESCE(excluded.display_name, accounts.display_name),
                profile_dir = COALESCE(excluded.profile_dir, accounts.profile_dir),
                enabled = excluded.enabled,
                proxy_url = COALESCE(excluded.proxy_url, accounts.proxy_url),
                timezone = COALESCE(excluded.timezone, accounts.timezone),
                preferred_hour = COALESCE(excluded.preferred_hour, accounts.preferred_hour),
                target_reward = COALESCE(excluded.target_reward, accounts.target_reward),
                target_points = excluded.target_points,
                updated_at = datetime('now')
        """, (account_id, display_name, profile_dir, enabled, proxy_url, 
              timezone, preferred_hour, target_reward, target_points))


def get_account(account_id: str, db_path: Optional[Path] = None) -> Optional[dict]:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM accounts WHERE account_id = ?", (account_id,)
        ).fetchone()
        return dict(row) if row else None


def list_accounts(enabled_only: bool = False, db_path: Optional[Path] = None) -> list[dict]:
    with get_db(db_path) as conn:
        query = "SELECT * FROM accounts"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY account_id"
        rows = conn.execute(query).fetchall()
        return [dict(r) for r in rows]


def update_account_status(account_id: str, status: str, db_path: Optional[Path] = None) -> None:
    with get_db(db_path) as conn:
        conn.execute(
            "UPDATE accounts SET status = ?, updated_at = datetime('now') WHERE account_id = ?",
            (status, account_id)
        )


# =============================================================================
# DAILY PROGRESS REPOSITORY
# =============================================================================

def get_daily_progress(account_id: str, date_str: str, db_path: Optional[Path] = None) -> Optional[dict]:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM daily_progress WHERE account_id = ? AND date_str = ?",
            (account_id, date_str)
        ).fetchone()
        return dict(row) if row else None


def upsert_daily_progress(
    account_id: str,
    date_str: str,
    status: Optional[str] = None,
    pc_searches_done: Optional[int] = None,
    pc_searches_target: Optional[int] = None,
    mobile_searches_done: Optional[int] = None,
    mobile_searches_target: Optional[int] = None,
    activities_done: Optional[int] = None,
    checkin_awarded: Optional[int] = None,
    bing_star_bonus: Optional[str] = None,
    started_at: Optional[str] = None,
    completed_at: Optional[str] = None,
    error_msg: Optional[str] = None,
    db_path: Optional[Path] = None
) -> None:
    with get_db(db_path) as conn:
        existing = conn.execute(
            "SELECT * FROM daily_progress WHERE account_id = ? AND date_str = ?",
            (account_id, date_str)
        ).fetchone()
        
        if existing:
            updates = []
            params = []
            if status is not None:
                updates.append("status = ?")
                params.append(status)
            if pc_searches_done is not None:
                updates.append("pc_searches_done = ?")
                params.append(pc_searches_done)
            if pc_searches_target is not None:
                updates.append("pc_searches_target = ?")
                params.append(pc_searches_target)
            if mobile_searches_done is not None:
                updates.append("mobile_searches_done = ?")
                params.append(mobile_searches_done)
            if mobile_searches_target is not None:
                updates.append("mobile_searches_target = ?")
                params.append(mobile_searches_target)
            if activities_done is not None:
                updates.append("activities_done = ?")
                params.append(activities_done)
            if checkin_awarded is not None:
                updates.append("checkin_awarded = ?")
                params.append(checkin_awarded)
            if bing_star_bonus is not None:
                updates.append("bing_star_bonus = ?")
                params.append(bing_star_bonus)
            if started_at is not None:
                updates.append("started_at = ?")
                params.append(started_at)
            if completed_at is not None:
                updates.append("completed_at = ?")
                params.append(completed_at)
            if error_msg is not None:
                updates.append("error_msg = ?")
                params.append(error_msg)
            
            if updates:
                params.extend([account_id, date_str])
                conn.execute(
                    f"UPDATE daily_progress SET {', '.join(updates)} WHERE account_id = ? AND date_str = ?",
                    params
                )
        else:
            conn.execute("""
                INSERT INTO daily_progress 
                (account_id, date_str, status, pc_searches_done, pc_searches_target,
                 mobile_searches_done, mobile_searches_target, activities_done,
                 checkin_awarded, bing_star_bonus, started_at, completed_at, error_msg)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                account_id, date_str,
                status or "pending",
                pc_searches_done or 0,
                pc_searches_target or 90,
                mobile_searches_done or 0,
                mobile_searches_target or 60,
                activities_done or 0,
                checkin_awarded or 0,
                bing_star_bonus or "0 / 2,100",
                started_at,
                completed_at,
                error_msg
            ))


def is_day_completed(account_id: str, date_str: str, db_path: Optional[Path] = None) -> bool:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT status FROM daily_progress WHERE account_id = ? AND date_str = ?",
            (account_id, date_str)
        ).fetchone()
        return row and row["status"] == "completed"


# =============================================================================
# POINTS LEDGER REPOSITORY
# =============================================================================

def record_points_snapshot(
    account_id: str,
    date_str: str,
    start_points: int,
    end_points: int,
    earned_points: int,
    bing_star_pts: int = 0,
    desktop_searches: int = 0,
    mobile_searches: int = 0,
    db_path: Optional[Path] = None
) -> None:
    with get_db(db_path) as conn:
        conn.execute("""
            INSERT INTO points_ledger 
            (account_id, date_str, start_points, end_points, earned_points, 
             bing_star_pts, desktop_searches, mobile_searches, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(account_id, date_str) DO UPDATE SET
                end_points = excluded.end_points,
                earned_points = excluded.earned_points,
                bing_star_pts = excluded.bing_star_pts,
                desktop_searches = excluded.desktop_searches,
                mobile_searches = excluded.mobile_searches
        """, (account_id, date_str, start_points, end_points, earned_points,
              bing_star_pts, desktop_searches, mobile_searches))


def get_latest_ledger(account_id: str, db_path: Optional[Path] = None) -> Optional[dict]:
    with get_db(db_path) as conn:
        row = conn.execute("""
            SELECT * FROM points_ledger 
            WHERE account_id = ? 
            ORDER BY date_str DESC 
            LIMIT 1
        """, (account_id,)).fetchone()
        return dict(row) if row else None


def get_ledger_history(account_id: str, limit: int = 30, db_path: Optional[Path] = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute("""
            SELECT * FROM points_ledger 
            WHERE account_id = ? 
            ORDER BY date_str DESC 
            LIMIT ?
        """, (account_id, limit)).fetchall()
        return [dict(r) for r in rows]


# =============================================================================
# OAUTH TOKENS REPOSITORY
# =============================================================================

def get_oauth_tokens(account_id: str, db_path: Optional[Path] = None) -> Optional[dict]:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM oauth_tokens WHERE account_id = ?", (account_id,)
        ).fetchone()
        return dict(row) if row else None


def save_oauth_tokens(
    account_id: str,
    access_token: str,
    refresh_token: str,
    expires_at: Optional[str] = None,
    client_id: Optional[str] = None,
    db_path: Optional[Path] = None
) -> None:
    with get_db(db_path) as conn:
        conn.execute("""
            INSERT INTO oauth_tokens (account_id, access_token, refresh_token, expires_at, client_id, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(account_id) DO UPDATE SET
                access_token = excluded.access_token,
                refresh_token = excluded.refresh_token,
                expires_at = excluded.expires_at,
                client_id = excluded.client_id,
                updated_at = datetime('now')
        """, (account_id, access_token, refresh_token, expires_at, client_id))


# =============================================================================
# SEARCH HISTORY REPOSITORY
# =============================================================================

def get_recent_search_queries(account_id: str, limit: int = 200, db_path: Optional[Path] = None) -> list[str]:
    with get_db(db_path) as conn:
        rows = conn.execute("""
            SELECT keyword FROM search_history 
            WHERE account_id = ? 
            ORDER BY searched_at DESC 
            LIMIT ?
        """, (account_id, limit)).fetchall()
        return [r["keyword"] for r in rows]


def add_search_queries(
    account_id: str,
    queries: list[str],
    platform: str = "desktop",
    db_path: Optional[Path] = None
) -> None:
    if not queries:
        return
    with get_db(db_path) as conn:
        for q in queries:
            if q and isinstance(q, str):
                conn.execute("""
                    INSERT OR IGNORE INTO search_history (account_id, keyword, platform)
                    VALUES (?, ?, ?)
                """, (account_id, q, platform))


# =============================================================================
# RUN LOGS REPOSITORY
# =============================================================================

def insert_run_log(
    account_id: str,
    level: str,
    module: Optional[str] = None,
    message: str = "",
    db_path: Optional[Path] = None
) -> None:
    with get_db(db_path) as conn:
        conn.execute("""
            INSERT INTO run_logs (account_id, timestamp, level, module, message)
            VALUES (?, datetime('now'), ?, ?, ?)
        """, (account_id, level, module, message))


# =============================================================================
# DAPI DEBUG CACHE
# =============================================================================

def save_dapi_debug_cache(
    account_id: str,
    channel: str,
    raw_json_str: str,
    db_path: Optional[Path] = None
) -> None:
    with get_db(db_path) as conn:
        conn.execute("""
            INSERT INTO dapi_debug_cache (account_id, channel, raw_json_str, captured_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(account_id) DO UPDATE SET
                channel = excluded.channel,
                raw_json_str = excluded.raw_json_str,
                captured_at = datetime('now')
        """, (account_id, channel, raw_json_str))


# =============================================================================
# UTILITIES
# =============================================================================

def get_db_path() -> Path:
    """Return the default database path."""
    return DEFAULT_DB_PATH


def verify_wal_mode(db_path: Optional[Path] = None) -> bool:
    """Verify database is in WAL mode."""
    with get_db(db_path) as conn:
        row = conn.execute("PRAGMA journal_mode").fetchone()
        return row and row[0].lower() == "wal"


def list_tables(db_path: Optional[Path] = None) -> list[str]:
    """List all tables in the database."""
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        return [r[0] for r in rows]


if __name__ == "__main__":
    init_db()
    print("Database initialized.")
    print(f"DB path: {DEFAULT_DB_PATH}")
    print(f"Tables: {list_tables()}")
    print(f"WAL mode: {verify_wal_mode()}")