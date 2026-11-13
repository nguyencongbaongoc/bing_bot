# Phase 7.1 Audit Report: State, File I/O & Persistence Coupling

---

## 1. Complete File Path, JSON Schema & Read/Write Function Inventory

| File | Line Range | JSON File | Schema | Function | Operation |
|------|-----------|-----------|--------|----------|-----------|
| `runner.py` | 1166-1179 | `data/daily_state.json` | `{completed_date, completed_at, status, date, partial, checkin_awarded, mobile_ui_triggered, updated_at}` | `is_today_already_completed()` | **Read** - checks if `completed_date == today` |
| `runner.py` | 1182-1225 | `data/daily_state.json` | Same as above | `save_daily_state()` | **Write** - overwrites entire file with new state |
| `runner.py` | 1231-1323 | `data/points_ledger.json` | `{last_updated, history:[{date,total_points,earned_today,updated_at,bing_star_bonus?}], total_points, earned_today, bing_star_bonus}` | `update_points_ledger()` | **Read/Write** - reads full file, appends to history, writes back |
| `core/tracker.py` | 137-172 | `logs/history.json` | `[{timestamp,points_before,points_after,earned,searches_completed,status,error_msg,last_update?,end_timestamp?,current_task?}]` | `init_session()` | **Read/Write** - reads array, appends new record, writes back |
| `core/tracker.py` | 175-211 | `logs/history.json` | Same as above | `update_session()` | **Read/Write** - reads array, updates last record, writes back |
| `core/tracker.py` | 214-251 | `logs/history.json` | Same as above | `finish_session()` | **Read/Write** - reads array, updates last record with final values, writes back |
| `core/tracker.py` | 255-293 | `logs/history.json` | Same as above | `log_session_result()` | **Read/Write** - legacy function, reads array, appends completed record, writes back |
| `core/tracker.py` | 296-331 | `logs/history.json` | Same as above | `get_history_summary()` | **Read** - reads array, filters by date, computes aggregates |
| `core/mobile_app_api.py` | 64-98 | `data/mobile_oauth.json` | `{access_token, refresh_token, updated_at}` | `get_mobile_access_token()` | **Read** - loads cache, attempts refresh |
| `core/mobile_app_api.py` | 170-177 | `data/mobile_oauth.json` | Same as above | `get_mobile_access_token()` | **Write** - saves new access_token + refresh_token |
| `core/mobile_app_api.py` | 309-333 | `data/debug_dapi_me.json` | Raw DAPI `/me?options=613` response | `_get_checkin_promotion()` | **Write** - debug dump when `logger.isEnabledFor(DEBUG)` |
| `core/mobile_app_api.py` | 522-542 | `data/debug_dapi_me.json` | Same as above | `solve_mobile_app_promotions()` | **Write** - debug dump when `logger.isEnabledFor(DEBUG)` |
| `core/trends.py` | 77-92 | `data/searched_history.json` (or legacy `used_keywords.json`) | `["keyword1", "keyword2", ...]` or `{keywords: [...]}` | `load_searched_history()` | **Read** - loads array of strings |
| `core/trends.py` | 95-117 | `data/searched_history.json` | Same as above | `save_searched_keyword()` | **Read/Write** - loads array, dedupes, appends, truncates to 200, writes back |

---

## 2. Single-Global-Account Assumptions (Critical Findings)

| JSON File | Current Assumption | Evidence |
|-----------|-------------------|----------|
| `daily_state.json` | **One `completed_date` for ALL accounts** | Line 1-10: No `account_id` field; `is_today_already_completed()` compares global date only |
| `points_ledger.json` | **One `history` array for ALL accounts** | Lines 3-84: Flat array of daily entries; `total_points`/`earned_today` are global scalars |
| `mobile_oauth.json` | **ONE refresh_token/access_token pair** | Lines 2-4: Single token object; no account partitioning |
| `searched_history.json` | **Single global keyword deduplication list** | Lines 1-202: Flat array of 200 strings; no account namespace |
| `logs/history.json` | **One session log for ALL accounts** | `tracker.py` lines 137-331: No `account_id` in any record |

**Code Reference - `runner.py:92`**: `ACCOUNT_ID = "acc_primary"` is a **hardcoded constant** used everywhere, never parameterized.

---

## 3. Target SQLite WAL Schema (DDL)

```sql
-- Enable WAL mode for concurrent readers + single writer
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;

-- ============================================================
-- CORE TABLES
-- ============================================================

CREATE TABLE accounts (
    account_id       TEXT PRIMARY KEY,           -- e.g., 'acc_primary', 'acc_secondary'
    display_name     TEXT,                       -- Human-readable name
    profile_dir      TEXT,                       -- Path to browser profile
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    is_active        INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX idx_accounts_active ON accounts(is_active);

-- ============================================================
-- DAILY PROGRESS (replaces daily_state.json)
-- ============================================================

CREATE TABLE daily_progress (
    account_id       TEXT NOT NULL,
    progress_date    TEXT NOT NULL,              -- ISO date: 'YYYY-MM-DD'
    status           TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'completed' | 'partial'
    completed_at     TEXT,                       -- ISO datetime when marked complete
    checkin_awarded  INTEGER DEFAULT 0,          -- Points from mobile check-in
    mobile_ui_triggered INTEGER DEFAULT 0,      -- Whether mobile UI flow was triggered
    partial          INTEGER DEFAULT 0,         -- 1 if partial completion
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (account_id, progress_date),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
);

CREATE INDEX idx_daily_progress_date ON daily_progress(progress_date);
CREATE INDEX idx_daily_progress_status ON daily_progress(status);

-- ============================================================
-- POINTS LEDGER (replaces points_ledger.json)
-- ============================================================

CREATE TABLE points_ledger (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id       TEXT NOT NULL,
    entry_date       TEXT NOT NULL,              -- ISO date: 'YYYY-MM-DD'
    total_points     INTEGER NOT NULL,           -- Cumulative total at end of day
    earned_today     INTEGER NOT NULL DEFAULT 0, -- Points earned this day
    bing_star_bonus  TEXT,                       -- e.g., '3 / 2,100'
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX idx_points_ledger_unique ON points_ledger(account_id, entry_date);
CREATE INDEX idx_points_ledger_date ON points_ledger(entry_date);

-- ============================================================
-- OAUTH TOKENS (replaces mobile_oauth.json)
-- ============================================================

CREATE TABLE oauth_tokens (
    account_id       TEXT PRIMARY KEY,
    access_token     TEXT NOT NULL,
    refresh_token    TEXT NOT NULL,
    token_type       TEXT DEFAULT 'Bearer',
    expires_at       TEXT,                        -- ISO datetime when access token expires
    scope            TEXT,                       -- OAuth scope string
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
);

-- ============================================================
-- SEARCH HISTORY (replaces searched_history.json)
-- ============================================================

CREATE TABLE search_history (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id       TEXT NOT NULL,
    keyword          TEXT NOT NULL,
    searched_at      TEXT NOT NULL DEFAULT (datetime('now')),
    source           TEXT DEFAULT 'trends',      -- 'trends' | 'synthesized' | 'manual'
    FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
);

CREATE INDEX idx_search_history_account ON search_history(account_id);
CREATE INDEX idx_search_history_keyword ON search_history(keyword);
CREATE INDEX idx_search_history_date ON search_history(searched_at);

-- Optional: enforce per-account dedup within sliding window (e.g., last 200)
-- Handled at application layer for flexibility

-- ============================================================
-- RUN LOGS (replaces logs/history.json)
-- ============================================================

CREATE TABLE run_logs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id       TEXT NOT NULL,
    session_id       TEXT NOT NULL,              -- UUID for each run
    started_at       TEXT NOT NULL,              -- ISO datetime
    ended_at         TEXT,                       -- ISO datetime (NULL if running)
    points_before    INTEGER NOT NULL,
    points_after     INTEGER,                    -- NULL until finished
    earned           INTEGER,                    -- Computed at finish
    searches_completed INTEGER DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'running',  -- 'running' | 'completed' | 'failed' | 'interrupted'
    error_msg        TEXT,
    current_task     TEXT,                       -- Last task description
    last_update      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
);

CREATE INDEX idx_run_logs_account ON run_logs(account_id);
CREATE INDEX idx_run_logs_session ON run_logs(session_id);
CREATE INDEX idx_run_logs_started ON run_logs(started_at);
CREATE INDEX idx_run_logs_status ON run_logs(status);

-- ============================================================
-- DEBUG DAPI CACHE (replaces debug_dapi_me.json)
-- ============================================================

CREATE TABLE dapi_debug_cache (
    account_id       TEXT NOT NULL,
    endpoint         TEXT NOT NULL,              -- e.g., '/me?options=613'
    response_json    TEXT NOT NULL,              -- Full JSON response
    fetched_at       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (account_id, endpoint)
);
```

---

## 4. Auto-Migration Logic (Pseudocode)

```python
def migrate_json_to_sqlite(db_path: Path, data_dir: Path, logs_dir: Path):
    """One-time migration from flat JSON files to SQLite WAL database."""
    import sqlite3, json, uuid
    from datetime import datetime
    
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    
    # --- Create tables (DDL from above) ---
    conn.executescript(SCHEMA_DDL)
    
    # --- Ensure primary account exists ---
    primary_id = "acc_primary"
    conn.execute(
        "INSERT OR IGNORE INTO accounts (account_id, display_name, profile_dir) VALUES (?, ?, ?)",
        (primary_id, "Primary Account", str(data_dir.parent / "profiles" / "desktop"))
    )
    
    # ============================================================
    # 1. MIGRATE daily_state.json -> daily_progress
    # ============================================================
    daily_state_file = data_dir / "daily_state.json"
    if daily_state_file.exists():
        with open(daily_state_file) as f:
            ds = json.load(f)
        
        conn.execute("""
            INSERT OR REPLACE INTO daily_progress 
            (account_id, progress_date, status, completed_at, checkin_awarded, 
             mobile_ui_triggered, partial, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            primary_id,
            ds.get("date") or ds.get("completed_date"),
            ds.get("status", "completed"),
            ds.get("completed_at"),
            ds.get("checkin_awarded", 0),
            1 if ds.get("mobile_ui_triggered") else 0,
            1 if ds.get("partial") else 0,
            ds.get("updated_at") or datetime.now().isoformat()
        ))
    
    # ============================================================
    # 2. MIGRATE points_ledger.json -> points_ledger
    # ============================================================
    ledger_file = data_dir / "points_ledger.json"
    if ledger_file.exists():
        with open(ledger_file) as f:
            pl = json.load(f)
        
        for entry in pl.get("history", []):
            conn.execute("""
                INSERT OR REPLACE INTO points_ledger
                (account_id, entry_date, total_points, earned_today, bing_star_bonus, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                primary_id,
                entry.get("date"),
                entry.get("total_points", 0),
                entry.get("earned_today", 0),
                entry.get("bing_star_bonus"),
                entry.get("updated_at") or datetime.now().isoformat()
            ))
    
    # ============================================================
    # 3. MIGRATE mobile_oauth.json -> oauth_tokens
    # ============================================================
    oauth_file = data_dir / "mobile_oauth.json"
    if oauth_file.exists():
        with open(oauth_file) as f:
            oa = json.load(f)
        
        conn.execute("""
            INSERT OR REPLACE INTO oauth_tokens
            (account_id, access_token, refresh_token, updated_at)
            VALUES (?, ?, ?, ?)
        """, (
            primary_id,
            oa.get("access_token", ""),
            oa.get("refresh_token", ""),
            oa.get("updated_at") or datetime.now().isoformat()
        ))
    
    # ============================================================
    # 4. MIGRATE searched_history.json -> search_history
    # ============================================================
    history_file = data_dir / "searched_history.json"
    legacy_file = data_dir / "used_keywords.json"
    target_file = history_file if history_file.exists() else legacy_file
    
    if target_file.exists():
        with open(target_file) as f:
            keywords = json.load(f)
            if isinstance(keywords, dict) and "keywords" in keywords:
                keywords = keywords["keywords"]
        
        # Insert with synthetic timestamps (oldest first)
        base_time = datetime.now()
        for i, kw in enumerate(keywords):
            searched_at = (base_time - timedelta(days=len(keywords)-i)).isoformat()
            conn.execute("""
                INSERT INTO search_history (account_id, keyword, searched_at, source)
                VALUES (?, ?, ?, 'migrated')
            """, (primary_id, kw.strip(), searched_at))
    
    # ============================================================
    # 5. MIGRATE logs/history.json -> run_logs
    # ============================================================
    history_log_file = logs_dir / "history.json"
    if history_log_file.exists():
        with open(history_log_file) as f:
            sessions = json.load(f)
        
        for sess in sessions:
            if not isinstance(sess, dict):
                continue
            
            session_id = str(uuid.uuid4())
            status = sess.get("status", "completed")
            ended_at = sess.get("end_timestamp") if status != "running" else None
            
            conn.execute("""
                INSERT INTO run_logs
                (account_id, session_id, started_at, ended_at, points_before, points_after,
                 earned, searches_completed, status, error_msg, current_task, last_update)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                primary_id,
                session_id,
                sess.get("timestamp"),
                ended_at,
                sess.get("points_before", 0),
                sess.get("points_after"),
                sess.get("earned"),
                sess.get("searches_completed", 0),
                status,
                sess.get("error_msg"),
                sess.get("current_task"),
                sess.get("last_update") or sess.get("end_timestamp") or sess.get("timestamp")
            ))
    
    conn.commit()
    conn.close()
    
    # --- Backup original JSON files ---
    backup_dir = data_dir / "backup_pre_sqlite"
    backup_dir.mkdir(exist_ok=True)
    for f in [daily_state_file, ledger_file, oauth_file, history_file, legacy_file, history_log_file]:
        if f.exists():
            shutil.move(str(f), str(backup_dir / f.name))
```

---

## 5. Concurrent Access Safety Analysis

### Current State: **UNSAFE** - Blocking JSON Reads/Writes

| Operation | File | Risk | Scenario |
|-----------|------|------|----------|
| `save_daily_state()` | `daily_state.json` | **Race condition** | Web UI reads `completed_date` while worker overwrites file |
| `update_points_ledger()` | `points_ledger.json` | **Data loss** | Worker appends to history while Web UI reads mid-write (partial JSON) |
| `init/update/finish_session()` | `history.json` | **Corruption** | Concurrent read + write = JSON parse error or lost updates |
| `get_mobile_access_token()` | `mobile_oauth.json` | **Token clobber** | Two workers refresh simultaneously; last write wins |
| `save_searched_keyword()` | `searched_history.json` | **Dedup failure** | Two workers load same history, both append, both write - one keyword lost |

### Critical Sections Requiring Transaction Wrapping (SQLite)

```python
# ============================================================
# CRITICAL SECTION 1: Daily Completion Check + Mark
# runner.py:1166-1225 (is_today_already_completed + save_daily_state)
# ============================================================
def check_and_mark_daily_complete(account_id: str) -> bool:
    """ATOMIC: Check if today done; if not, mark complete."""
    with db_transaction() as conn:
        cur = conn.execute("""
            SELECT status FROM daily_progress 
            WHERE account_id = ? AND progress_date = date('now')
        """, (account_id,))
        row = cur.fetchone()
        if row and row[0] == 'completed':
            return True  # Already done
        
        # Mark complete atomically
        conn.execute("""
            INSERT OR REPLACE INTO daily_progress
            (account_id, progress_date, status, completed_at, updated_at)
            VALUES (?, date('now'), 'completed', datetime('now'), datetime('now'))
        """, (account_id,))
        return False


# ============================================================
# CRITICAL SECTION 2: Points Ledger Update
# runner.py:1231-1323 (update_points_ledger)
# ============================================================
def append_points_ledger(account_id: str, total_points: int, earned_today: int, 
                          bing_star_bonus: str = None):
    """ATOMIC: Upsert today's ledger entry."""
    with db_transaction() as conn:
        conn.execute("""
            INSERT INTO points_ledger (account_id, entry_date, total_points, earned_today, bing_star_bonus, updated_at)
            VALUES (?, date('now'), ?, ?, ?, datetime('now'))
            ON CONFLICT(account_id, entry_date) DO UPDATE SET
                total_points = excluded.total_points,
                earned_today = excluded.earned_today,
                bing_star_bonus = excluded.bing_star_bonus,
                updated_at = excluded.updated_at
        """, (account_id, total_points, earned_today, bing_star_bonus))


# ============================================================
# CRITICAL SECTION 3: OAuth Token Refresh
# mobile_app_api.py:64-177 (get_mobile_access_token)
# ============================================================
def refresh_oauth_token(account_id: str, new_access: str, new_refresh: str):
    """ATOMIC: Update token pair."""
    with db_transaction() as conn:
        conn.execute("""
            INSERT INTO oauth_tokens (account_id, access_token, refresh_token, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(account_id) DO UPDATE SET
                access_token = excluded.access_token,
                refresh_token = excluded.refresh_token,
                updated_at = excluded.updated_at
        """, (account_id, new_access, new_refresh))


# ============================================================
# CRITICAL SECTION 4: Search History Dedup + Append
# trends.py:95-117 (save_searched_keyword)
# ============================================================
def add_search_keyword(account_id: str, keyword: str, max_history: int = 200):
    """ATOMIC: Dedup + append + truncate per account."""
    with db_transaction() as conn:
        # Check existence (case-insensitive)
        cur = conn.execute("""
            SELECT 1 FROM search_history 
            WHERE account_id = ? AND LOWER(keyword) = LOWER(?)
        """, (account_id, keyword))
        if cur.fetchone():
            return  # Already exists
        
        # Insert new
        conn.execute("""
            INSERT INTO search_history (account_id, keyword, searched_at, source)
            VALUES (?, ?, datetime('now'), 'trends')
        """, (account_id, keyword))
        
        # Truncate to max_history per account (delete oldest)
        conn.execute("""
            DELETE FROM search_history 
            WHERE account_id = ? AND id NOT IN (
                SELECT id FROM search_history 
                WHERE account_id = ? 
                ORDER BY searched_at DESC 
                LIMIT ?
            )
        """, (account_id, account_id, max_history))


# ============================================================
# CRITICAL SECTION 5: Session Logging (init/update/finish)
# tracker.py:137-251
# ============================================================
def init_run_session(account_id: str, start_points: int) -> str:
    """ATOMIC: Create new run_logs row, return session_id."""
    session_id = str(uuid.uuid4())
    with db_transaction() as conn:
        conn.execute("""
            INSERT INTO run_logs 
            (account_id, session_id, started_at, points_before, status, last_update)
            VALUES (?, ?, datetime('now'), ?, 'running', datetime('now'))
        """, (account_id, session_id, start_points))
    return session_id

def update_run_session(account_id: str, session_id: str, searches_done: int, 
                       current_points: int = None, current_task: str = ""):
    """ATOMIC: Update running session."""
    with db_transaction() as conn:
        updates = ["searches_completed = ?", "last_update = datetime('now')"]
        params = [searches_done]
        if current_points is not None:
            updates.append("points_after = ?")
            updates.append("earned = MAX(0, ? - points_before)")
            params.extend([current_points, current_points])
        if current_task:
            updates.append("current_task = ?")
            params.append(current_task)
        params.extend([account_id, session_id])
        
        conn.execute(f"""
            UPDATE run_logs SET {', '.join(updates)}
            WHERE account_id = ? AND session_id = ? AND status = 'running'
        """, params)

def finish_run_session(account_id: str, session_id: str, end_points: int, 
                       status: str = "completed", error_msg: str = None):
    """ATOMIC: Finalize session."""
    with db_transaction() as conn:
        conn.execute("""
            UPDATE run_logs SET
                points_after = ?,
                earned = MAX(0, ? - points_before),
                status = ?,
                ended_at = datetime('now'),
                error_msg = ?,
                last_update = datetime('now')
            WHERE account_id = ? AND session_id = ?
        """, (end_points, end_points, status, error_msg, account_id, session_id))
```

### SQLite WAL Concurrency Guarantees

| Pattern | JSON (Current) | SQLite WAL (Target) |
|---------|---------------|---------------------|
| **Reader + Writer** | Blocked / Corrupt | ✅ **Concurrent** - Readers don't block writer |
| **Multiple Readers** | ✅ OK | ✅ OK |
| **Multiple Writers** | ❌ Last-write-wins | ✅ **Serialized** - `busy_timeout=5000` queues |
| **Transaction Atomicity** | ❌ None | ✅ **Full ACID** |
| **Partial Write Recovery** | ❌ Corrupt file | ✅ **WAL rollback** |

---

## 6. Required Refactor Summary

| Component | Current | Target | Effort |
|-----------|---------|--------|--------|
| `runner.py:1166-1323` | JSON file I/O | SQLite `daily_progress` + `points_ledger` | Medium |
| `tracker.py:137-331` | JSON file I/O | SQLite `run_logs` | Medium |
| `mobile_app_api.py:64-177` | JSON file I/O | SQLite `oauth_tokens` | Low |
| `trends.py:77-117` | JSON file I/O | SQLite `search_history` | Low |
| `mobile_app_api.py:309-333` | Debug JSON write | SQLite `dapi_debug_cache` | Low |
| **New** | — | `accounts` table + migration script | Medium |

**Total estimated refactor: ~8-12 hours** for complete migration with zero-downtime backward compatibility.