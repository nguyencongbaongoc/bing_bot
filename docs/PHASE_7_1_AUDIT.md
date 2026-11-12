# PHASE 7.1 AUDIT REPORT — Multi-Account Orchestration & Visual Web Dashboard

**Generated:** 2026-11-12  
**Graphify Version:** 0.9.79  
**Repository Commit:** e570f34e  
**Scope:** Read-only architectural audit of single-account bottlenecks, shared global state, browser lifecycle conflicts, and anti-detection boundaries.

---

## TABLE OF CONTENTS

1. [Graphify Architecture Summary](#1-graphify-architecture-summary)
2. [Single-Account Coupling Matrix](#2-single-account-coupling-matrix)
3. [Target SQLite WAL Schema (DDL)](#3-target-sqlite-wal-schema-ddl)
4. [Multi-Account Safety & Anti-Correlation Rules](#4-multi-account-safety--anti-correlation-rules)
5. [Phased Implementation Roadmap (7.2 → 7.4)](#5-phased-implementation-roadmap-72--74)
6. [Subagent Audit Findings](#6-subagent-audit-findings)
   - [6.1 Entrypoint, Browser Lifecycle & OS Power/Process Guard](#61-entrypoint-browser-lifecycle--os-powerprocess-guard)
   - [6.2 State, File I/O & Persistence Coupling](#62-state-file-io--persistence-coupling)
   - [6.3 Anti-Detection, Fingerprint & Network Isolation](#63-anti-detection-fingerprint--network-isolation)
   - [6.4 Mobile DAPI & OAuth Token Harvester](#64-mobile-dapi--oauth-token-harvester)

---

## 1. GRAPHIFY ARCHITECTURE SUMMARY

### 1.1 Graph Statistics
- **Nodes:** 296
- **Edges:** 720
- **Communities:** 41 (8 major shown)
- **Extraction:** 99% EXTRACTED, 1% INFERRED
- **Token Cost:** ~$0.033 (Gemini)

### 1.2 God Nodes (Highest Betweenness Centrality)
| Rank | Function | Edges | Module | Role |
|------|----------|-------|--------|------|
| 1 | `run_full_pipeline()` | 48 | `runner.py` | **Main orchestrator** — bridges all communities |
| 2 | `_log_info()` | 28 | `core/tracker.py` | Logging hub |
| 3 | `_log_warning()` | 23 | `core/tracker.py` | Logging hub |
| 4 | `dismiss_popups()` | 16 | `core/activities.py` | UI interaction |
| 5 | `solve_mobile_app_promotions()` | 15 | `core/mobile_app_api.py` | Mobile API |
| 6 | `_log_debug()` | 15 | `core/tracker.py` | Logging hub |
| 7 | `_log_error()` | 14 | `core/tracker.py` | Logging hub |
| 8 | `solve_keep_earning_activities()` | 13 | `core/activities.py` | Activities |
| 9 | `get_mobile_access_token()` | 13 | `core/mobile_app_api.py` | OAuth token manager |
| 10 | `solve_mobile_app_read_to_earn()` | 13 | `core/mobile_app_api.py` | Mobile API |

### 1.3 Critical Cross-Module Dependency Edges (Extracted)
```
run_full_pipeline()  ──calls──> solve_mobile_app_daily_checkin()    [runner.py → mobile_app_api.py]
run_full_pipeline()  ──calls──> solve_mobile_app_promotions()       [runner.py → mobile_app_api.py]
run_full_pipeline()  ──calls──> solve_mobile_app_read_to_earn()     [runner.py → mobile_app_api.py]
run_full_pipeline()  ──calls──> extract_current_points()            [runner.py → tracker.py]
run_full_pipeline()  ──calls──> finish_session()                    [runner.py → tracker.py]
```

### 1.4 Community Hubs (Navigation)
| Community | Name | Cohesion | Key Nodes |
|-----------|------|----------|-----------|
| 0 | Microsoft Rewards Activity Automation | 0.07 | 27 nodes (activities, check-ins, quests) |
| 1 | Mobile API and Logging | 0.09 | 26 nodes (OAuth, DAPI, tracker) |
| 2 | Anti-Detection Fingerprinting | 0.06 | 15 nodes (stealth, context, hardware profile) |
| 3 | Human Behavior Simulation | 0.10 | 11 nodes (delays, typing, scroll) |
| 4 | Login and Inspection Tools | 0.14 | 8 nodes (setup_login, inspection) |
| 5 | Human-Like Search Automation | 0.19 | 9 nodes (search, CTR, autocomplete) |
| 6 | AI Quiz Solver | 0.18 | 5 nodes (Ollama, quiz, cooldown) |

### 1.5 Knowledge Gaps & Surprising Connections
- **34 isolated nodes** — mostly screenshot/image assets from `Screenshots/` directory (not code)
- **33 thin communities** (<3 nodes) omitted
- **Import Cycles:** None detected
- **Key Insight:** `run_full_pipeline()` is the **single cross-community bridge** connecting Activity Automation, Mobile API, Fingerprinting, Human Simulation, Search, and AI Quiz Solver. This makes it the critical refactor point for multi-account orchestration.

---

## 2. SINGLE-ACCOUNT COUPLING MATRIX

| File | Line Range | Current Single-Account Assumption | Required Phase 7 Refactor |
|------|------------|-----------------------------------|---------------------------|
| **runner.py** | 87-88 | `DESKTOP_PROFILE = ROOT_DIR / "profiles" / "desktop"`<br>`MOBILE_PROFILE = ROOT_DIR / "profiles" / "mobile"` | Replace with `get_profile_path(account_id, "desktop")` / `get_profile_path(account_id, "mobile")` |
| **runner.py** | 92 | `ACCOUNT_ID = "acc_primary"` — **global constant** | Remove global; thread `account_id` through entire call chain |
| **runner.py** | 289 | `user_data_dir=str(DESKTOP_PROFILE)` in `create_desktop_context()` | Pass `account_id` → `user_data_dir=str(get_desktop_profile_path(account_id))` |
| **runner.py** | 324 | `user_data_dir=str(MOBILE_PROFILE)` in `create_mobile_context()` | Pass `account_id` → `user_data_dir=str(get_mobile_profile_path(account_id))` |
| **runner.py** | 363, 398 | Context recreation uses `account_id=ACCOUNT_ID` (global) | Pass per-account ID from queue |
| **runner.py** | 466-474 | Profile existence checks use hardcoded paths | Check per-account profile directories |
| **runner.py** | 1115-1141 | `cleanup_zombie_browsers()` kills **ALL** `msedge.exe` by name | Filter by `--user-data-dir` containing current account's profile |
| **runner.py** | 1371-1390 | `is_user_active()` uses `ctypes.windll.user32.GetLastInputInfo` (Windows-only) | Add platform guard; macOS: `ioreg -c IOHIDSystem` |
| **runner.py** | 1393-1414 | `put_pc_to_sleep()` calls PowerShell `SetSuspendState` | Add platform guard; macOS: `pmset sleepnow` |
| **runner.py** | 1536-1541 | Auto-sleep triggers after **single account** pipeline | Suppress per-account sleep; trigger ONLY after full FIFO queue |
| **setup_login.py** | 19-21 | `DESKTOP_PROFILE = PROFILES_DIR / "desktop"`<br>`MOBILE_PROFILE = PROFILES_DIR / "mobile"` | Add `--account-id` CLI; create `profiles/{account_id}/{desktop,mobile}` |
| **setup_login.py** | 146-148, 216-218 | `user_data_dir=str(DESKTOP_PROFILE/MOBILE_PROFILE)` | Use per-account profile path |
| **core/fingerprint.py** | 32 | `inject_stealth_fingerprint(context, account_id="primary_account")` | Default must be dynamic; all callers pass real `account_id` |
| **core/mobile_app_api.py** | 53 | `OAUTH_FILE = DATA_DIR / "mobile_oauth.json"` (hardcoded) | Replace with SQLite `mobile_oauth_tokens` table keyed by `account_id` |
| **core/mobile_app_api.py** | 57-194 | `get_mobile_access_token(page)` — no `account_id` param | Add `account_id: str` parameter; load/store per account |
| **core/mobile_app_api.py** | 397-486, 492-592, 595-709 | All mobile API functions call `get_mobile_access_token()` without account context | Propagate `account_id` through all 3 functions |
| **core/trends.py** | — | `load_searched_history()` / `save_searched_keyword()` use global `data/searched_history.json` | Per-account search history: `data/searched_history_{account_id}.json` |
| **core/tracker.py** | 137-293 | All session functions use global `logs/history.json` | SQLite `run_logs` table with `account_id` FK |
| **runner.py** | 1182-1225 | `save_daily_state()` writes global `data/daily_state.json` | SQLite `daily_progress` table per account |
| **runner.py** | 1231-1323 | `update_points_ledger()` writes global `data/points_ledger.json` | SQLite `points_ledger` table per account |

---

## 3. TARGET SQLITE WAL SCHEMA (DDL)

### 3.1 Database Configuration
```sql
-- File: data/rewards_hub.db
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;
PRAGMA temp_store = MEMORY;
```

### 3.2 Core Tables

```sql
-- 1. ACCOUNTS — Master account registry
CREATE TABLE accounts (
    account_id      TEXT PRIMARY KEY,           -- e.g., "acc_primary", "acc_02"
    email           TEXT NOT NULL,
    display_name    TEXT,
    proxy_url       TEXT,                       -- http:// or socks5:// or NULL
    proxy_type      TEXT,                       -- "http", "socks5", "none"
    timezone_id     TEXT DEFAULT 'Asia/Ho_Chi_Minh',
    locale          TEXT DEFAULT 'vi-VN',
    device_profile  TEXT DEFAULT 'Pixel 7',     -- mobile emulation preset
    desktop_profile_path TEXT,                 -- profiles/desktop/{account_id}
    mobile_profile_path  TEXT,                 -- profiles/mobile/{account_id}
    is_active       INTEGER DEFAULT 1,          -- 1=active, 0=disabled
    priority        INTEGER DEFAULT 0,          -- queue priority (higher = first)
    preferred_start_hour INTEGER,              -- circadian window start (0-23)
    preferred_end_hour   INTEGER,              -- circadian window end (0-23)
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    last_run_at     TEXT,                       -- ISO 8601
    last_run_status TEXT                        -- 'completed' | 'partial' | 'failed'
);

CREATE INDEX idx_accounts_active ON accounts(is_active) WHERE is_active = 1;
CREATE INDEX idx_accounts_priority ON accounts(priority DESC);

-- 2. DAILY_PROGRESS — Per-account daily completion state
CREATE TABLE daily_progress (
    account_id      TEXT NOT NULL,
    date            TEXT NOT NULL,              -- YYYY-MM-DD
    status          TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'running' | 'completed' | 'partial' | 'failed'
    pc_cur          INTEGER DEFAULT 0,          -- PC searches done
    pc_max          INTEGER DEFAULT 90,
    mobile_cur      INTEGER DEFAULT 0,          -- Mobile searches done
    mobile_max      INTEGER DEFAULT 60,
    activities_done INTEGER DEFAULT 0,          -- 0/1 flag
    checkin_awarded INTEGER DEFAULT 0,          -- points from check-in
    bing_star_bonus TEXT DEFAULT '0 / 2,100',
    started_at      TEXT,                       -- ISO 8601
    completed_at    TEXT,                       -- ISO 8601
    error_msg       TEXT,
    PRIMARY KEY (account_id, date),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
);

CREATE INDEX idx_daily_progress_date ON daily_progress(date);
CREATE INDEX idx_daily_progress_status ON daily_progress(status);

-- 3. POINTS_LEDGER — Per-account points history & redemption forecasting
CREATE TABLE points_ledger (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      TEXT NOT NULL,
    date            TEXT NOT NULL,              -- YYYY-MM-DD
    total_points    INTEGER NOT NULL,
    earned_today    INTEGER NOT NULL,
    bing_star_bonus TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX idx_points_ledger_account_date ON points_ledger(account_id, date);
CREATE INDEX idx_points_ledger_date ON points_ledger(date);

-- 4. OAUTH_TOKENS — Mobile app OAuth tokens (replaces mobile_oauth.json)
CREATE TABLE oauth_tokens (
    account_id      TEXT PRIMARY KEY,
    access_token    TEXT NOT NULL,
    refresh_token   TEXT NOT NULL,
    expires_at      TEXT,                       -- ISO 8601 (calculated from expires_in)
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
);

-- 5. SEARCH_HISTORY — Per-account keyword deduplication
CREATE TABLE search_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      TEXT NOT NULL,
    keyword         TEXT NOT NULL,
    searched_at     TEXT NOT NULL DEFAULT (datetime('now')),
    device_type     TEXT NOT NULL,              -- 'desktop' | 'mobile' | 'sidebar'
    FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX idx_search_history_account_keyword ON search_history(account_id, keyword);
CREATE INDEX idx_search_history_account_date ON search_history(account_id, searched_at);

-- 6. RUN_LOGS — Per-account session execution logs (replaces logs/history.json)
CREATE TABLE run_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      TEXT NOT NULL,
    timestamp       TEXT NOT NULL,              -- ISO 8601
    points_before   INTEGER,
    points_after    INTEGER,
    earned          INTEGER,
    searches_completed INTEGER DEFAULT 0,
    status          TEXT NOT NULL,              -- 'running' | 'completed' | 'partial' | 'failed' | 'interrupted'
    current_task    TEXT,
    error_msg       TEXT,
    duration_seconds INTEGER,
    FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
);

CREATE INDEX idx_run_logs_account_timestamp ON run_logs(account_id, timestamp);
CREATE INDEX idx_run_logs_status ON run_logs(status);

-- 7. DAPI_DEBUG_CACHE — Debug DAPI responses (replaces debug_dapi_me.json)
CREATE TABLE dapi_debug_cache (
    account_id      TEXT PRIMARY KEY,
    endpoint        TEXT NOT NULL,
    response_json   TEXT NOT NULL,
    captured_at     TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
);
```

### 3.3 Legacy JSON Auto-Migration Logic

```python
# Pseudocode for auto-migration in core/database.py
def migrate_legacy_jsons():
    """
    Migrate all existing JSON files into SQLite under acc_primary.
    Run once on first startup if rewards_hub.db doesn't exist or is empty.
    """
    db = get_db()
    primary_id = "acc_primary"
    
    # 1. Ensure acc_primary exists in accounts
    db.execute("""
        INSERT OR IGNORE INTO accounts (account_id, email, display_name, is_active)
        VALUES (?, ?, ?, 1)
    """, (primary_id, "legacy@migrated", "Primary (Migrated)"))
    
    # 2. Migrate daily_state.json → daily_progress
    if DAILY_STATE_FILE.exists():
        data = json.load(open(DAILY_STATE_FILE))
        db.execute("""
            INSERT OR REPLACE INTO daily_progress 
            (account_id, date, status, checkin_awarded, completed_at)
            VALUES (?, ?, ?, ?, ?)
        """, (primary_id, data.get("date"), data.get("status"), 
              data.get("checkin_awarded", 0), data.get("completed_at")))
    
    # 3. Migrate points_ledger.json → points_ledger
    if POINTS_LEDGER_FILE.exists():
        ledger = json.load(open(POINTS_LEDGER_FILE))
        for entry in ledger.get("history", []):
            db.execute("""
                INSERT OR REPLACE INTO points_ledger 
                (account_id, date, total_points, earned_today, bing_star_bonus, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (primary_id, entry["date"], entry["total_points"], 
                  entry.get("earned_today", 0), entry.get("bing_star_bonus"), 
                  entry.get("updated_at")))
    
    # 4. Migrate mobile_oauth.json → oauth_tokens
    if MOBILE_OAUTH_FILE.exists():
        oauth = json.load(open(MOBILE_OAUTH_FILE))
        db.execute("""
            INSERT OR REPLACE INTO oauth_tokens 
            (account_id, access_token, refresh_token, updated_at)
            VALUES (?, ?, ?, ?)
        """, (primary_id, oauth.get("access_token"), oauth.get("refresh_token"), 
              oauth.get("updated_at")))
    
    # 5. Migrate searched_history.json → search_history
    if SEARCHED_HISTORY_FILE.exists():
        keywords = json.load(open(SEARCHED_HISTORY_FILE))
        for kw in keywords:
            db.execute("""
                INSERT OR IGNORE INTO search_history (account_id, keyword, device_type)
                VALUES (?, ?, 'desktop')
            """, (primary_id, kw))
    
    # 6. Migrate logs/history.json → run_logs
    if HISTORY_JSON.exists():
        history = json.load(open(HISTORY_JSON))
        for entry in history:
            db.execute("""
                INSERT INTO run_logs 
                (account_id, timestamp, points_before, points_after, earned, 
                 searches_completed, status, error_msg)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (primary_id, entry["timestamp"], entry.get("points_before"),
                  entry.get("points_after"), entry.get("earned"),
                  entry.get("searches_completed"), entry.get("status"),
                  entry.get("error_msg")))
    
    db.commit()
    _log_info("[Migration] Legacy JSONs migrated to SQLite under acc_primary")
```

---

## 4. MULTI-ACCOUNT SAFETY & ANTI-CORRELATION RULES

### 4.1 Strict Sequential FIFO Queue
- **Never parallel on same IP** — accounts processed sequentially
- Queue order: by `accounts.priority DESC`, then `last_run_at ASC` (least recently run first)
- Each account gets isolated browser profile, proxy, fingerprint, search history

### 4.2 Circadian Rhythm Scheduling
```python
# Per-account preferred time window + 15–40 min jitter
preferred_start_hour = account.preferred_start_hour or 6   # 6 AM default
preferred_end_hour   = account.preferred_end_hour   or 22  # 10 PM default

# Calculate next run window
now = datetime.now()
if now.hour < preferred_start_hour:
    target = now.replace(hour=preferred_start_hour, minute=0, second=0)
elif now.hour > preferred_end_hour:
    target = (now + timedelta(days=1)).replace(hour=preferred_start_hour, minute=0, second=0)
else:
    target = now

# Add jitter 15-40 minutes
jitter = random.randint(15, 40)
run_at = target + timedelta(minutes=jitter)
```

### 4.3 Redeem Safety Guard (UI Alert)
- **Flag/warn in Web UI** if >2 accounts share the same residential egress IP
- Query: `SELECT proxy_url, COUNT(*) FROM accounts WHERE is_active=1 GROUP BY proxy_url HAVING COUNT(*) > 2`
- Show warning banner: "⚠️ 3 accounts using same residential IP — high correlation risk"

### 4.4 Browser Isolation Guarantees
| Layer | Isolation Mechanism |
|-------|---------------------|
| **Profile** | `profiles/desktop/{account_id}`, `profiles/mobile/{account_id}` — separate cookie jars, localStorage |
| **Fingerprint** | Deterministic per-account: Canvas noise, AudioContext noise, hardwareConcurrency, deviceMemory, WebGL vendor/renderer |
| **Network** | Per-account proxy (HTTP/SOCKS5); WebRTC IP leak protection (`--force-webrtc-ip-handling-policy=disable_non_proxied_udp`); timezone/locale aligned to proxy egress IP |
| **Search History** | `search_history` table keyed by `account_id` — no cross-account keyword dedup |
| **Process Guard** | `cleanup_zombie_browsers(account_id)` filters by `--user-data-dir` containing account's profile path |
| **OAuth Tokens** | `oauth_tokens` table keyed by `account_id` — separate refresh/access tokens |

---

## 5. PHASED IMPLEMENTATION ROADMAP (7.2 → 7.4)

### PHASE 7.2 — Core Database & Migration (`core/database.py`)
**Timeline:** Week 1-2  
**Files to Create/Modify:**
| File | Action |
|------|--------|
| `core/database.py` | **NEW** — SQLite WAL connection pool, schema init, CRUD for all 7 tables, migration logic |
| `core/models.py` | **NEW** — Dataclasses/Pydantic models for Account, DailyProgress, PointsLedger, OAuthToken, SearchHistory, RunLog |
| `core/config.py` | **NEW** — Central config: paths, DB pragmas, default timeouts, feature flags |
| `runner.py` | **MODIFY** — Import `database`, replace all JSON reads/writes with DB calls |
| `core/tracker.py` | **MODIFY** — Replace `logs/history.json` with `run_logs` table |
| `core/mobile_app_api.py` | **MODIFY** — Replace `mobile_oauth.json` with `oauth_tokens` table; add `account_id` param to all functions |
| `core/trends.py` | **MODIFY** — Replace `searched_history.json` with `search_history` table per account |
| `setup_login.py` | **MODIFY** — Add `--account-id` CLI; create per-account profile dirs |
| `data/rewards_hub.db` | **GENERATED** — SQLite WAL database (auto-created) |

**Acceptance Criteria:**
- [ ] All 5 legacy JSONs auto-migrate to `acc_primary` on first run without data loss
- [ ] `oauth_tokens` table stores/retrieves tokens per `account_id`
- [ ] `search_history` deduplication works per account
- [ ] Concurrent read (Web UI) + write (worker) never collides (WAL mode verified)
- [ ] `PRAGMA busy_timeout=5000` prevents lock contention

---

### PHASE 7.3 — Orchestrator, Onboarding & Multi-Account CLI (`core/orchestrator.py`, `core/onboarding.py`, `runner.py`)
**Timeline:** Week 2-3  
**Files to Create/Modify:**
| File | Action |
|------|--------|
| `core/orchestrator.py` | **NEW** — `MultiAccountOrchestrator` class: FIFO queue, circadian scheduler, per-account `run_full_pipeline(account_id)`, final auto-sleep |
| `core/onboarding.py` | **NEW** — `launch_mobile_oauth_onboarding(account_id)`: visible browser → intercept OAuth redirect → exchange code → store in SQLite |
| `core/profile_manager.py` | **NEW** — Profile path resolution, creation, cleanup per account |
| `runner.py` | **MAJOR REFACTOR** — Remove global `ACCOUNT_ID`; thread `account_id` through all calls; add `--account-id` / `--all-accounts` CLI args |
| `runner.py` | **MODIFY** — `create_desktop_context(account_id)`, `create_mobile_context(account_id)` accept proxy, timezone, locale params |
| `runner.py` | **MODIFY** — `cleanup_zombie_browsers(account_id)` filters by profile path |
| `runner.py` | **MODIFY** — Auto-sleep logic MOVED to orchestrator (runs only after queue completes) |
| `core/fingerprint.py` | **MODIFY** — Fix mobile to use deterministic `hardware_concurrency`/`device_memory` from profile |
| `core/searcher.py` | **MODIFY** — Add smart cooldown detection: check point delta after first 3 sidebar searches |
| `setup_login.py` | **MODIFY** — Import stealth scripts from `runner` + `fingerprint` (deduplicate) |

**New CLI Interface:**
```bash
# Run single account
python runner.py --account-id acc_02 --desktop-target 30 --mobile-target 20

# Run all active accounts in FIFO queue
python runner.py --all-accounts

# Onboard new account (interactive OAuth)
python setup_login.py --onboard acc_03 --device "Pixel 7"
```

**Acceptance Criteria:**
- [ ] `--all-accounts` processes accounts sequentially with circadian scheduling + jitter
- [ ] Each account uses isolated profile, proxy, fingerprint, search history
- [ ] Process guard only kills browsers for the current account
- [ ] Auto-sleep triggers ONLY after entire queue completes
- [ ] 1-click onboarding: visible browser → manual login → OAuth code intercepted → tokens in SQLite
- [ ] Smart cooldown detection logs early warning after first 3 searches

---

### PHASE 7.4 — Visual Web Dashboard (`web/server.py`, `web/static/index.html`)
**Timeline:** Week 3-4  
**Files to Create:**
| File | Action |
|------|--------|
| `web/server.py` | **NEW** — FastAPI + WebSocket server: `/api/accounts`, `/api/accounts/{id}/progress`, `/api/accounts/{id}/logs`, `/ws/logs` (live stream) |
| `web/static/index.html` | **NEW** — Single-file Tailwind CSS + Chart.js/SVG dashboard |
| `web/requirements.txt` | **NEW** — `fastapi`, `uvicorn`, `websockets`, `pydantic` |

**Dashboard Features:**
1. **Account Cards** — Grid of all accounts with status badges (Pending/Running/Completed/Failed)
2. **Live Progress Bars** — Per-account: PC searches (0-90), Mobile searches (0-60), Activities (✓/✗), Check-in (+pts)
3. **Bing Star Bonus Tracker** — Visual progress bar: `current / 2,100` with monthly forecast
4. **Points Ledger Chart** — Chart.js line chart: daily earned points, cumulative total, redemption milestones
5. **1-Click Onboarding** — "Add Account" button → opens modal → enters email → launches visible browser → intercepts OAuth → saves
6. **Safety Alerts** — Banner: "⚠️ 3 accounts share same residential IP" with "View Details" link
7. **Live Log Streamer** — WebSocket tail of `run_logs` for running account (color-coded: INFO/WARNING/ERROR)
8. **Circadian Schedule View** — Timeline showing each account's preferred window + next run ETA

**Tech Stack:**
- **Backend:** FastAPI (async), SQLAlchemy async (or raw `aiosqlite`), WebSocket (`websockets` or `fastapi.WebSocket`)
- **Frontend:** Single HTML file, Tailwind CSS (CDN), Chart.js (CDN), vanilla JS (no build step)
- **Deployment:** `uvicorn web.server:app --host 0.0.0.0 --port 8080`

**Acceptance Criteria:**
- [ ] Dashboard loads in <500ms, shows all accounts with real-time data
- [ ] WebSocket streams logs live during pipeline execution
- [ ] 1-click onboarding completes OAuth flow without CLI
- [ ] Safety guard detects and displays IP correlation warnings
- [ ] Chart.js renders points history and Bing Star bonus correctly
- [ ] Responsive: works on mobile browser (portrait/landscape)

---

## 6. SUBAGENT AUDIT FINDINGS

### 6.1 Entrypoint, Browser Lifecycle & OS Power/Process Guard

**Audited Files:** `runner.py` (1551 lines), `run.bat`, `setup_login.py`, `core/fingerprint.py`

#### Critical Findings:

| Issue | Severity | Location | Evidence |
|-------|----------|----------|----------|
| **Process Guard kills ALL Edge processes** | **CRITICAL** | `runner.py:1122-1133` | `for proc in psutil.process_iter(): if proc.name == 'msedge.exe': proc.kill()` — no profile filtering |
| **Global `ACCOUNT_ID = "acc_primary"`** | **CRITICAL** | `runner.py:92` | Used at lines 289, 324, 363, 398, 466-474 — all contexts share same profile |
| **Single profile paths hardcoded** | **CRITICAL** | `runner.py:87-88` | `DESKTOP_PROFILE = .../profiles/desktop`, `MOBILE_PROFILE = .../profiles/mobile` |
| **Auto-sleep triggers per-account** | **HIGH** | `runner.py:1536-1541` | `put_pc_to_sleep()` called after single `run_full_pipeline()` completes |
| **Windows-only APIs without guards** | **HIGH** | `runner.py:1371-1414` | `ctypes.windll.user32.GetLastInputInfo`, PowerShell `SetSuspendState` — crashes on macOS |
| **Mobile context ignores deterministic hardware profile** | **MEDIUM** | `runner.py:216-217` | Hardcoded `hw_concurrency=8, device_memory=8` instead of using `profile` |

#### Required Refactors:
1. Replace global profiles with `profiles/{account_id}/{desktop,mobile}`
2. `cleanup_zombie_browsers(account_id)` → filter by `cmdline()` containing profile path
3. Remove global `ACCOUNT_ID`; thread `account_id` through all context creators
4. Add `if sys.platform == "win32":` guards with macOS fallbacks (`ioreg`, `pmset`)
5. Move auto-sleep to queue-level orchestrator (Phase 7.3)

---

### 6.2 State, File I/O & Persistence Coupling

**Audited Files:** `runner.py`, `core/tracker.py`, `core/mobile_app_api.py`, `core/trends.py`, all `data/*.json`

#### JSON Files & Schemas:

| File | Schema | Read/Write Functions | Single-Account Assumption |
|------|--------|---------------------|---------------------------|
| `data/daily_state.json` | `{completed_date, completed_at, status, date, partial, checkin_awarded, updated_at}` | `is_today_already_completed()` (r:1166), `save_daily_state()` (r:1182) | One global completion date |
| `data/points_ledger.json` | `{last_updated, history:[{date, total_points, earned_today, bing_star_bonus, updated_at}], total_points, earned_today, bing_star_bonus}` | `update_points_ledger()` (r:1231) | Single history array for all accounts |
| `data/mobile_oauth.json` | `{access_token, refresh_token, updated_at}` | `get_mobile_access_token()` (ma:64,170) | One token pair globally |
| `data/debug_dapi_me.json` | DAPI response dump | `_get_checkin_promotion()` (ma:309) | Single debug file |
| `data/searched_history.json` | `["keyword1", "keyword2", ...]` (max 200) | `load_searched_history()` (tr:991), `save_searched_keyword()` (tr:1183) | Global dedup list |
| `logs/history.json` | `[{timestamp, points_before, points_after, earned, searches_completed, status, error_msg}]` | `init_session()` (tr:137), `update_session()` (tr:175), `finish_session()` (tr:214), `log_session_result()` (tr:255) | Single session log |

#### Concurrent Access Risks (Current JSON):
| File | Risk | Scenario |
|------|------|----------|
| `daily_state.json` | Race condition | Worker writes completion while Web UI reads |
| `points_ledger.json` | Lost updates | Two accounts append to history simultaneously |
| `mobile_oauth.json` | Token clobber | Account A refreshes while Account B reads |
| `searched_history.json` | Corruption | Concurrent append + truncate |
| `history.json` | Interleaved writes | Multiple sessions write logs |

#### SQLite WAL Solution:
- **Readers never block writers, writers never block readers**
- **5-second busy timeout** prevents indefinite blocking
- **Transactions** for atomic multi-table updates (see Section 3.3)

---

### 6.3 Anti-Detection, Fingerprint & Network Isolation

**Audited Files:** `core/fingerprint.py`, `runner.py` (stealth scripts), `core/searcher.py`, `core/activities.py`, `setup_login.py`

#### Fingerprint Propagation Status:
| Function | `account_id` Parameter | Actually Used? |
|----------|------------------------|----------------|
| `get_deterministic_hardware_profile(account_id)` | ✅ Required | ✅ Yes — SHA256 seed |
| `inject_stealth_fingerprint(context, account_id)` | ✅ Required | ✅ Yes — passes to profile |
| `apply_stealth_scripts(context, ..., account_id)` | ✅ Required | ✅ Yes — reads profile |
| `create_desktop_context(playwright, headless, account_id)` | ✅ Optional (default) | ⚠️ **Default = "acc_primary"** |
| `create_mobile_context(playwright, headless, account_id)` | ✅ Optional (default) | ⚠️ **Default = "acc_primary"** |

#### Deterministic Profile Per Account (Verified):
```python
# fingerprint.py:15-29 — CORRECT
def get_deterministic_hardware_profile(account_id):
    rng = random.Random(generate_seed_hash(account_id))  # SHA256(account_id)[:8]
    return {
        "hardware_concurrency": rng.choice([8, 12, 16]),
        "device_memory": rng.choice([8, 16]),
        "canvas_noise": (rng.uniform(-0.0001, 0.0001), ...),
        "audio_noise": rng.uniform(0.00001, 0.00005),
    }
```

#### Missing Network Isolation (Phase 7 Requirements):
| Feature | Current | Required |
|---------|---------|----------|
| Per-account proxy | ❌ None | `proxy=http://user:pass@ip:port` or `socks5://` |
| WebRTC IP leak protection | ❌ None | `--force-webrtc-ip-handling-policy=disable_non_proxied_udp` |
| Timezone/locale aligned to proxy | ❌ Hardcoded `Asia/Ho_Chi_Minh`, `vi-VN` | Dynamic per proxy GeoIP |
| Per-account search history | ❌ Global `searched_history.json` | `search_history` table keyed by `account_id` |

#### Smart Cooldown Detection Gap:
- **Current:** Reactive — `detect_unusual_activity_warning()` checks page text for "unusual search activity"
- **Needed:** Proactive — After first 3 sidebar searches (line 775), compare `extract_current_points()` delta vs expected +9 pts. If delta < 6 → early soft-ban signal.

#### Code Deduplication Needed:
| File | Function | Issue |
|------|----------|-------|
| `runner.py:206-272` | `apply_stealth_scripts()` | Full implementation |
| `setup_login.py:35-89` | `apply_stealth_scripts()` | **Stale copy** — missing hardwareConcurrency/deviceMemory, missing `inject_stealth_fingerprint()` call |

---

### 6.4 Mobile DAPI & OAuth Token Harvester

**Audited Files:** `core/mobile_app_api.py` (710 lines), `setup_login.py`, `runner.py`

#### OAuth Token Lifecycle:
```
1. LOAD from mobile_oauth.json (or SQLite oauth_tokens)
   └─→ refresh_token exists? → POST /oauth20_token.srf (grant_type=refresh_token)
       └─→ 200 OK → save new access_token + refresh_token → RETURN
   └─→ refresh failed / no file → BROWSER FALLBACK

2. BROWSER FALLBACK (get_mobile_access_token lines 100-193)
   └─→ Open AUTH_URL in new page
   └─→ Poll URL for "code=" (max 25s)
   └─→ Auto-click "Yes"/"Accept" buttons
   └─→ Exchange code → POST /oauth20_token.srf (grant_type=authorization_code)
   └─→ Save tokens to file → RETURN access_token
```

#### Type 103 Daily Check-in Payload:
```python
# mobile_app_api.py:370-382
payload = {
    "id": str(uuid.uuid4()),
    "type": 103,                          # Check-in activity type
    "amount": 1,
    "country": "vn",
    "risk_context": {"app": "BingSapphire", "platform": "Android"},
    "attributes": {"offerid": "<promotion_offer_id>"}  # From DAPI
}
# POST to: https://prod.rewardsplatform.microsoft.com/dapi/me/activities
```

#### Type 101 Read-to-Earn Loop:
```python
# mobile_app_api.py:620-628 (per iteration)
payload = {
    "amount": 1,
    "country": "vn",
    "id": str(uuid.uuid4()),
    "type": 101,
    "attributes": {"offerid": "ENUS_readarticle3_30points"}
}
# Loop 10x, track balance delta, stop after 2 consecutive no-increase
# Sleep 12-22s between articles
```

#### 1-Click Onboarding Design (NEW):
```
launch_mobile_oauth_onboarding(account_id):
    1. Launch visible Patchright browser with mobile emulation
    2. Navigate to AUTH_URL + state=base64(account_id)
    3. page.on("framenavigated") → intercept oauth20_desktop.srf?code=...
    4. Extract code + verify state.account_id matches
    5. Exchange code for tokens → store in SQLite oauth_tokens table
    6. Close browser cleanly
```

#### Required Refactors:
| Priority | Change |
|----------|--------|
| **P0** | Replace `mobile_oauth.json` with SQLite `oauth_tokens` table (PK=account_id) |
| **P0** | Add `account_id: str` to `get_mobile_access_token()` |
| **P0** | Propagate `account_id` through `solve_mobile_app_daily_checkin`, `solve_mobile_app_promotions`, `solve_mobile_app_read_to_earn` |
| **P1** | Implement `launch_mobile_oauth_onboarding(account_id)` with redirect interception |
| **P1** | Add `state` parameter to AUTH_URL for account_id round-trip |

---

## 7. ACCEPTANCE CRITERIA FOR PHASE 7.1 COMPLETION

- [x] `graphify-out/GRAPH_REPORT.md` and `graphify-out/graph.json` exist and referenced accurately
- [x] All 4 Subagent audits complete with exact line numbers from current codebase
- [x] `docs/PHASE_7_1_AUDIT.md` written cleanly and thoroughly
- [x] Zero unintended edits to existing runtime `.py` files
- [x] Executive summary presented for confirmation to execute **Phase 7.2**

---

## 8. EXECUTIVE SUMMARY & APPROVAL REQUEST

### Key Architectural Findings

1. **`run_full_pipeline()` is the single God Node** (48 edges) bridging all 6 functional communities — it must be refactored into a per-account callable invoked by a queue orchestrator.

2. **Process Guard is a critical multi-account blocker** — `cleanup_zombie_browsers()` kills ALL `msedge.exe` processes indiscriminately. Running account B's cleanup will terminate account A's active session.

3. **All state is globally shared** — 5 JSON files + 1 log file assume single account (`acc_primary`). No `account_id` partitioning exists anywhere.

4. **Anti-detection fingerprinting works deterministically per account** — but only because `account_id` is threaded through the call chain. The **mobile context ignores the deterministic profile** (hardcoded 8/8), and **no network isolation** (proxy, WebRTC, timezone) exists.

5. **Mobile OAuth is functional but single-account** — the token flow (refresh → browser fallback) works correctly but stores to a single global file. The 1-click onboarding design is well-understood and implementable.

### Phase 7.2 Readiness: **APPROVED TO PROCEED**

The audit provides:
- ✅ Complete coupling matrix with line-accurate refactor targets
- ✅ SQLite WAL schema (7 tables) with migration logic
- ✅ Multi-account safety rules (FIFO queue, circadian scheduling, IP correlation guard)
- ✅ 3-phase implementation roadmap with acceptance criteria
- ✅ Zero runtime code modifications — pure read-only analysis

**Requesting approval to begin Phase 7.2: Core Database & Migration (`core/database.py`)**

---