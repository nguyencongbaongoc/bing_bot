"""
SQLite WAL persistence layer for MS Rewards Bot (Phase 9.0 Polyglot).
Manages daily_progress, query_history, and session_logs in data/rewards.db.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT_DIR / "data"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "rewards.db"
MAX_VISUAL_SEARCH_ATTEMPTS_PER_DAY = 3


def normalize_offer_title(title: str) -> str:
    """Normalize offer/card title by stripping point badges and collapsing whitespace."""
    text = str(title or "")
    text = re.sub(r"\(\+\d+\s*pts?\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\+\d+\s*pts?\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def atomic_write_json(path: Path | str, payload: Any) -> Path:
    """Atomically write UTF-8 JSON via temp file + fsync + os.replace."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = Path(f"{target}.tmp")
    if target.exists():
        try:
            if target.stat().st_size > 0:
                json.loads(target.read_text(encoding="utf-8"))
                shutil.copy2(target, Path(f"{target}.bak"))
        except Exception:
            pass
    serialized = json.dumps(payload, indent=2, ensure_ascii=False)
    with open(tmp_file, "w", encoding="utf-8") as f:
        f.write(serialized)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_file, target)
    return target


def safe_read_json(path: Path | str, default: Any = None) -> Any:
    """Safely read JSON file with fallback to .bak if corrupted."""
    target = Path(path)
    for candidate in (target, Path(f"{target}.bak")):
        if candidate.exists():
            try:
                raw = candidate.read_text(encoding="utf-8")
                if raw.strip():
                    return json.loads(raw)
            except Exception:
                continue
    return default() if callable(default) else default


class StateDB:
    """Thread- and process-safe SQLite WAL state manager."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS daily_progress (
                    date_key TEXT NOT NULL,
                    account TEXT NOT NULL,
                    daily_activities_done INTEGER NOT NULL DEFAULT 0,
                    checkin_done INTEGER NOT NULL DEFAULT 0,
                    read_to_earn_done INTEGER NOT NULL DEFAULT 0,
                    visual_search_done INTEGER NOT NULL DEFAULT 0,
                    visual_search_attempts INTEGER NOT NULL DEFAULT 0,
                    edge_browsing_done INTEGER NOT NULL DEFAULT 0,
                    pc_cur INTEGER NOT NULL DEFAULT 0,
                    pc_max INTEGER NOT NULL DEFAULT 90,
                    mobile_cur INTEGER NOT NULL DEFAULT 0,
                    mobile_max INTEGER NOT NULL DEFAULT 60,
                    last_points INTEGER NOT NULL DEFAULT 0,
                    bing_star_points INTEGER NOT NULL DEFAULT 0,
                    attempted_offers TEXT NOT NULL DEFAULT '[]',
                    attempted_quest_tasks TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (date_key, account)
                );

                CREATE TABLE IF NOT EXISTS query_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account TEXT NOT NULL,
                    query_text TEXT NOT NULL,
                    normalized_tokens TEXT NOT NULL,
                    topic TEXT NOT NULL DEFAULT 'general',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_query_history_account_id
                    ON query_history (account, id DESC);

                CREATE TABLE IF NOT EXISTS session_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account TEXT NOT NULL,
                    date_key TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    status TEXT NOT NULL,
                    points_before INTEGER NOT NULL DEFAULT 0,
                    points_after INTEGER NOT NULL DEFAULT 0,
                    pc_completed INTEGER NOT NULL DEFAULT 0,
                    mobile_completed INTEGER NOT NULL DEFAULT 0,
                    bing_star_points INTEGER NOT NULL DEFAULT 0,
                    details TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                """
            )
            conn.commit()

    def get_journal_mode(self) -> str:
        with self.connect() as conn:
            row = conn.execute("PRAGMA journal_mode;").fetchone()
            return str(row[0]).lower() if row else ""

    def ensure_daily_row(self, date_key: str, account: str = "default") -> dict[str, Any]:
        now_iso = datetime.datetime.now().isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO daily_progress (date_key, account, updated_at)
                VALUES (?, ?, ?)
                """,
                (date_key, account, now_iso),
            )
            conn.commit()
        return self.get_daily_progress(date_key, account)

    def get_daily_progress(self, date_key: str, account: str = "default") -> dict[str, Any]:
        now_iso = datetime.datetime.now().isoformat()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM daily_progress WHERE date_key = ? AND account = ?",
                (date_key, account),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO daily_progress (date_key, account, updated_at) VALUES (?, ?, ?)",
                    (date_key, account, now_iso),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM daily_progress WHERE date_key = ? AND account = ?",
                    (date_key, account),
                ).fetchone()
        data = dict(row)
        data["daily_activities_done"] = bool(data.get("daily_activities_done"))
        data["checkin_done"] = bool(data.get("checkin_done"))
        data["read_to_earn_done"] = bool(data.get("read_to_earn_done"))
        data["visual_search_done"] = bool(data.get("visual_search_done"))
        data["edge_browsing_done"] = bool(data.get("edge_browsing_done"))
        for list_col in ("attempted_offers", "attempted_quest_tasks"):
            try:
                parsed = json.loads(data.get(list_col) or "[]")
                data[list_col] = parsed if isinstance(parsed, list) else []
            except Exception:
                data[list_col] = []
        return data

    def get_last_known_points(self, account: str = "default") -> int:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT last_points FROM daily_progress
                WHERE account = ? AND last_points > 0
                ORDER BY date_key DESC LIMIT 1
                """,
                (account,),
            ).fetchone()
            return int(row["last_points"]) if row else 0

    def update_daily_fields(self, date_key: str, account: str = "default", **fields: Any) -> dict[str, Any]:
        """Update specific whitelisted fields in daily_progress."""
        allowed_bool = {
            "daily_activities_done",
            "checkin_done",
            "read_to_earn_done",
            "visual_search_done",
            "edge_browsing_done",
        }
        allowed_int = {
            "visual_search_attempts",
            "pc_max",
            "mobile_max",
            "last_points",
        }
        self.ensure_daily_row(date_key, account)
        assignments: list[str] = []
        params: list[Any] = []

        for key, val in fields.items():
            if key in allowed_bool:
                assignments.append(f"{key} = ?")
                params.append(1 if val else 0)
            elif key in allowed_int:
                assignments.append(f"{key} = ?")
                params.append(max(0, int(val or 0)))
            elif key in ("pc_cur", "mobile_cur", "bing_star_points"):
                # Monotonic enforcement within the same date_key; ignore negative values (< 0)
                if val is not None:
                    parsed = int(val)
                    if parsed >= 0:
                        assignments.append(f"{key} = MAX({key}, ?)")
                        params.append(parsed)
            elif key in ("attempted_offers", "attempted_quest_tasks"):
                assignments.append(f"{key} = ?")
                params.append(json.dumps(list(val or []), ensure_ascii=False))

        if assignments:
            assignments.append("updated_at = ?")
            params.append(datetime.datetime.now().isoformat())
            params.extend([date_key, account])
            sql = f"UPDATE daily_progress SET {', '.join(assignments)} WHERE date_key = ? AND account = ?"
            with self.connect() as conn:
                conn.execute(sql, params)
                conn.commit()

        return self.get_daily_progress(date_key, account)

    def update_bing_star_monotonic(self, date_key: str, account: str, new_points: int) -> int:
        """Persist bing_star_points monotonically as MAX(old, new_points)."""
        self.ensure_daily_row(date_key, account)
        val = max(0, int(new_points or 0))
        now_iso = datetime.datetime.now().isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE daily_progress
                SET bing_star_points = MAX(bing_star_points, ?),
                    updated_at = ?
                WHERE date_key = ? AND account = ?
                """,
                (val, now_iso, date_key, account),
            )
            conn.commit()
            row = conn.execute(
                "SELECT bing_star_points FROM daily_progress WHERE date_key = ? AND account = ?",
                (date_key, account),
            ).fetchone()
            return int(row["bing_star_points"]) if row else val

    def increment_visual_search_attempts(
        self,
        date_key: str,
        account: str = "default",
        delta: int = 1,
    ) -> int:
        """Increment visual_search_attempts capped at MAX_VISUAL_SEARCH_ATTEMPTS_PER_DAY."""
        self.ensure_daily_row(date_key, account)
        inc = max(0, int(delta))
        now_iso = datetime.datetime.now().isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE daily_progress
                SET visual_search_attempts = MIN(?, visual_search_attempts + ?),
                    updated_at = ?
                WHERE date_key = ? AND account = ?
                """,
                (MAX_VISUAL_SEARCH_ATTEMPTS_PER_DAY, inc, now_iso, date_key, account),
            )
            conn.commit()
            row = conn.execute(
                "SELECT visual_search_attempts FROM daily_progress WHERE date_key = ? AND account = ?",
                (date_key, account),
            ).fetchone()
            return int(row["visual_search_attempts"]) if row else 0

    def append_attempted_items(
        self,
        date_key: str,
        account: str,
        offers: Optional[list[str]] = None,
        quest_tasks: Optional[list[str]] = None,
    ) -> None:
        prog = self.get_daily_progress(date_key, account)
        cur_offers = [normalize_offer_title(x) for x in prog["attempted_offers"] if normalize_offer_title(x)]
        for o in offers or []:
            norm = normalize_offer_title(o)
            if norm and norm not in cur_offers:
                cur_offers.append(norm)

        cur_quests = [str(x).strip() for x in prog["attempted_quest_tasks"] if str(x).strip()]
        for qt in quest_tasks or []:
            clean = str(qt).strip()
            if clean and clean not in cur_quests:
                cur_quests.append(clean)

        self.update_daily_fields(
            date_key,
            account,
            attempted_offers=cur_offers,
            attempted_quest_tasks=cur_quests,
        )

    def add_query_history(
        self,
        account: str,
        query_text: str,
        normalized_tokens: list[str],
        topic: str = "general",
    ) -> None:
        clean = str(query_text or "").strip()
        if not clean:
            return
        now_iso = datetime.datetime.now().isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO query_history (account, query_text, normalized_tokens, topic, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (account, clean, json.dumps(normalized_tokens, ensure_ascii=False), topic, now_iso),
            )
            conn.commit()

    def get_recent_queries(self, account: str = "default", limit: int = 15) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT query_text FROM query_history
                WHERE account = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (account, max(1, int(limit))),
            ).fetchall()
        return [str(r["query_text"]) for r in rows]

    def log_session(
        self,
        account: str,
        date_key: str,
        phase: str,
        status: str,
        points_before: int = 0,
        points_after: int = 0,
        pc_completed: int = 0,
        mobile_completed: int = 0,
        bing_star_points: int = 0,
        details: str = "",
    ) -> None:
        now_iso = datetime.datetime.now().isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO session_logs (
                    account, date_key, phase, status,
                    points_before, points_after,
                    pc_completed, mobile_completed,
                    bing_star_points, details, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account,
                    date_key,
                    phase,
                    status,
                    int(points_before or 0),
                    int(points_after or 0),
                    int(pc_completed or 0),
                    int(mobile_completed or 0),
                    int(bing_star_points or 0),
                    str(details or ""),
                    now_iso,
                ),
            )
            conn.commit()

    def seed_from_legacy_json_if_empty(
        self,
        date_key: str,
        account: str = "default",
        data_dir: Path | str = DEFAULT_DATA_DIR,
    ) -> None:
        """
        One-time read-only migration from legacy Phase 8.6 JSON files into SQLite WAL
        without deleting or altering legacy profile/token files.
        """
        data_root = Path(data_dir)
        with self.connect() as conn:
            existing_row = conn.execute(
                "SELECT date_key FROM daily_progress WHERE date_key = ? AND account = ?",
                (date_key, account),
            ).fetchone()
            qh_count = conn.execute(
                "SELECT COUNT(*) FROM query_history WHERE account = ?",
                (account,),
            ).fetchone()[0]

        if existing_row is None:
            daily_file = data_root / f"daily_state_{account}.json"
            ledger_file = data_root / f"points_ledger_{account}.json"
            daily_json = safe_read_json(daily_file, default={}) or {}
            ledger_json = safe_read_json(ledger_file, default={}) or {}

            last_pts = 0
            try:
                last_pts = int(ledger_json.get("total_points", 0) or 0)
            except (TypeError, ValueError):
                last_pts = 0

            row_date = daily_json.get("date") or daily_json.get("completed_date")
            self.ensure_daily_row(date_key, account)
            updates: dict[str, Any] = {"last_points": last_pts}
            if row_date == date_key:
                updates.update(
                    {
                        "daily_activities_done": bool(daily_json.get("daily_activities_done") or daily_json.get("daily_activities_completed")),
                        "checkin_done": bool(daily_json.get("mobile_checkin_done")),
                        "read_to_earn_done": bool(daily_json.get("read_to_earn_done")),
                        "visual_search_attempts": int(daily_json.get("visual_search_attempts_today", 0) or 0),
                        "edge_browsing_done": bool(daily_json.get("edge_browsing_done")),
                        "bing_star_points": int(daily_json.get("bing_star_points", 0) or 0),
                        "attempted_offers": list(daily_json.get("attempted_offers") or []),
                        "attempted_quest_tasks": list(daily_json.get("attempted_quest_tasks") or []),
                    }
                )
            self.update_daily_fields(date_key, account, **updates)

        if qh_count == 0:
            hist_file = data_root / "searched_history.json"
            hist = safe_read_json(hist_file, default=[])
            if isinstance(hist, list):
                for kw in hist[-30:]:
                    clean_kw = str(kw or "").strip()
                    if clean_kw:
                        tokens = re.findall(r"\w+", clean_kw.lower())
                        self.add_query_history(account, clean_kw, tokens, topic="legacy")
