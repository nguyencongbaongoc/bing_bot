"""
Atomic JSON I/O, crash-safe state persistence, legacy migration, and per-account StateStore.
Phase 8.6 - MS Rewards Bot.
"""

from __future__ import annotations

import copy
import datetime
import json
import math
import os
import shutil
from pathlib import Path
from typing import Any, Callable, Optional

from core.tracker import _log_info, _log_warning

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT_DIR / "data"
DEFAULT_PROFILES_DIR = ROOT_DIR / "profiles"
EDGE_BROWSING_CAP_SECONDS = 1860  # 31 phút (30 phút + 1 phút buffer an toàn)
MAX_VISUAL_SEARCH_ATTEMPTS_PER_DAY = 3


def _backup_path(path: Path) -> Path:
    return Path(f"{path}.bak")


def _tmp_path(path: Path) -> Path:
    return Path(f"{path}.tmp")


def _clone_default(default: Any) -> Any:
    if callable(default):
        return default()
    return copy.deepcopy(default)


def atomic_write_json(path: Path | str, payload: Any) -> Path:
    """
    Atomically write UTF-8 JSON to `path`:
      1. Write to `<path>.tmp`, flush and `os.fsync()`.
      2. If `<path>` already exists and contains valid non-empty JSON, copy it to `<path>.bak`.
      3. Atomically swap `<path>.tmp` -> `<path>` via `os.replace()`.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = _tmp_path(target)
    bak_file = _backup_path(target)

    # Preserve last known valid state in .bak before overwriting target
    if target.exists():
        try:
            if target.stat().st_size > 0:
                with open(target, "r", encoding="utf-8") as existing_f:
                    json.load(existing_f)
                shutil.copy2(target, bak_file)
        except Exception:
            # Never overwrite a good .bak with a corrupted primary file
            pass

    serialized = json.dumps(payload, indent=2, ensure_ascii=False)
    with open(tmp_file, "w", encoding="utf-8") as f:
        f.write(serialized)
        f.flush()
        os.fsync(f.fileno())

    os.replace(tmp_file, target)
    return target


def safe_read_json(path: Path | str, default: Any = None) -> Any:
    """
    Safely read UTF-8 JSON from `path`.
    If `path` is missing, empty, truncated, or raises `json.JSONDecodeError`,
    automatically falls back to `<path>.bak` and logs a recovery warning.
    """
    target = Path(path)
    bak_file = _backup_path(target)

    if target.exists():
        try:
            raw = target.read_text(encoding="utf-8")
            if not raw.strip():
                raise ValueError("empty JSON file")
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            _log_warning(
                f"[StateStore] Phát hiện file JSON lỗi/bị cắt ({target.name}: {exc}). "
                f"Đang thử khôi phục từ {bak_file.name}..."
            )
            if bak_file.exists():
                try:
                    bak_raw = bak_file.read_text(encoding="utf-8")
                    if not bak_raw.strip():
                        raise ValueError("empty backup JSON file")
                    recovered = json.loads(bak_raw)
                    _log_warning(f"[StateStore] Đã khôi phục thành công trạng thái từ {bak_file.name}.")
                    return recovered
                except Exception as bak_exc:
                    _log_warning(f"[StateStore] Khôi phục từ {bak_file.name} thất bại: {bak_exc}")
            return _clone_default(default)

    # Primary missing; check if backup exists
    if bak_file.exists():
        try:
            bak_raw = bak_file.read_text(encoding="utf-8")
            if bak_raw.strip():
                recovered = json.loads(bak_raw)
                _log_warning(f"[StateStore] File chính {target.name} thiếu, đã khôi phục từ {bak_file.name}.")
                return recovered
        except Exception as bak_exc:
            _log_warning(f"[StateStore] Không thể đọc {bak_file.name}: {bak_exc}")

    return _clone_default(default)


def get_account_paths(
    account_id: str = "default",
    data_dir: Path | str = DEFAULT_DATA_DIR,
    profiles_dir: Path | str = DEFAULT_PROFILES_DIR,
) -> dict[str, Path]:
    """Return isolated file and directory paths for a given `account_id`."""
    clean_id = (account_id or "default").strip() or "default"
    data_root = Path(data_dir)
    profiles_root = Path(profiles_dir)
    return {
        "account_id": clean_id,  # type: ignore[dict-item]
        "lock_file": data_root / "locks" / f"{clean_id}.lock",
        "daily_state_file": data_root / f"daily_state_{clean_id}.json",
        "points_ledger_file": data_root / f"points_ledger_{clean_id}.json",
        "oauth_file": data_root / (
            "mobile_oauth.json" if clean_id == "default" else f"mobile_oauth_{clean_id}.json"
        ),
        "desktop_profile": profiles_root / clean_id / "desktop",
        "mobile_profile": profiles_root / clean_id / "mobile",
    }


def migrate_legacy_default_account(
    data_dir: Path | str = DEFAULT_DATA_DIR,
    profiles_dir: Path | str = DEFAULT_PROFILES_DIR,
) -> bool:
    """
    Automatically migrate legacy single-account layout into `default` account layout:
      - `profiles/desktop` -> `profiles/default/desktop`
      - `profiles/mobile` -> `profiles/default/mobile`
      - `data/daily_state.json` -> `data/daily_state_default.json`
      - `data/points_ledger.json` -> `data/points_ledger_default.json`
    Runs only when `profiles/default` does not exist yet (or target state files are missing).
    Returns True if any migration occurred.
    """
    data_root = Path(data_dir)
    profiles_root = Path(profiles_dir)
    migrated = False

    default_profile_root = profiles_root / "default"
    legacy_desktop = profiles_root / "desktop"
    legacy_mobile = profiles_root / "mobile"

    if (legacy_desktop.exists() and not (default_profile_root / "desktop").exists()) or (
        legacy_mobile.exists() and not (default_profile_root / "mobile").exists()
    ):
        default_profile_root.mkdir(parents=True, exist_ok=True)
        if legacy_desktop.exists() and not (default_profile_root / "desktop").exists():
            _log_info(f"[Migration] Di chuyển legacy profile {legacy_desktop} -> {default_profile_root / 'desktop'}")
            shutil.copytree(legacy_desktop, default_profile_root / "desktop", dirs_exist_ok=True)
            migrated = True
        if legacy_mobile.exists() and not (default_profile_root / "mobile").exists():
            _log_info(f"[Migration] Di chuyển legacy profile {legacy_mobile} -> {default_profile_root / 'mobile'}")
            shutil.copytree(legacy_mobile, default_profile_root / "mobile", dirs_exist_ok=True)
            migrated = True

    legacy_daily = data_root / "daily_state.json"
    target_daily = data_root / "daily_state_default.json"
    if legacy_daily.exists() and not target_daily.exists():
        data_root.mkdir(parents=True, exist_ok=True)
        data = safe_read_json(legacy_daily, default={})
        if isinstance(data, dict) and data:
            atomic_write_json(target_daily, data)
            _log_info(f"[Migration] Di chuyển {legacy_daily.name} -> {target_daily.name}")
            migrated = True

    legacy_ledger = data_root / "points_ledger.json"
    target_ledger = data_root / "points_ledger_default.json"
    if legacy_ledger.exists() and not target_ledger.exists():
        data_root.mkdir(parents=True, exist_ok=True)
        ledger = safe_read_json(legacy_ledger, default={})
        if isinstance(ledger, dict) and ledger:
            atomic_write_json(target_ledger, ledger)
            _log_info(f"[Migration] Di chuyển {legacy_ledger.name} -> {target_ledger.name}")
            migrated = True

    return migrated


class StateStore:
    """Crash-safe per-account state & ledger persistence manager."""

    def __init__(
        self,
        account_id: str = "default",
        data_dir: Path | str = DEFAULT_DATA_DIR,
        profiles_dir: Path | str = DEFAULT_PROFILES_DIR,
        daily_state_file: Path | str | None = None,
        points_ledger_file: Path | str | None = None,
    ):
        self.account_id = (account_id or "default").strip() or "default"
        self.data_dir = Path(data_dir)
        self.profiles_dir = Path(profiles_dir)
        paths = get_account_paths(self.account_id, self.data_dir, self.profiles_dir)
        self.lock_file = paths["lock_file"]
        self.daily_state_file = Path(daily_state_file) if daily_state_file else paths["daily_state_file"]
        self.points_ledger_file = Path(points_ledger_file) if points_ledger_file else paths["points_ledger_file"]
        self.desktop_profile = paths["desktop_profile"]
        self.mobile_profile = paths["mobile_profile"]

    def load_daily_state(self) -> dict[str, Any]:
        data = safe_read_json(self.daily_state_file, default={})
        return data if isinstance(data, dict) else {}

    def save_daily_state_dict(self, state: dict[str, Any]) -> None:
        atomic_write_json(self.daily_state_file, state)

    def _load_today_state_dict(self, today_str: str | None = None) -> tuple[str, dict[str, Any]]:
        today = today_str or datetime.date.today().isoformat()
        data = self.load_daily_state()
        if data.get("date") == today or data.get("completed_date") == today:
            return today, dict(data)
        state: dict[str, Any] = {
            "completed_date": None,
            "completed_at": None,
            "status": "partial",
            "edge_browsing_seconds": 0,
            "edge_browsing_done": False,
            "searches_completed_today": False,
            "core_tasks_done": False,
            "visual_search_attempts_today": 0,
            "mobile_checkin_done": False,
        }
        if isinstance(data.get("alerts_sent"), dict):
            state["alerts_sent"] = dict(data["alerts_sent"])
        if "consecutive_failures" in data:
            state["consecutive_failures"] = int(data.get("consecutive_failures", 0) or 0)
        return today, state

    def is_today_already_completed(self, today_str: str | None = None) -> bool:
        data = self.load_daily_state()
        today = today_str or datetime.date.today().isoformat()
        return data.get("completed_date") == today and data.get("status") == "completed"

    def is_searches_completed_today(self, today_str: str | None = None) -> bool:
        today = today_str or datetime.date.today().isoformat()
        data = self.load_daily_state()
        if data.get("date") != today and data.get("completed_date") != today:
            return False
        return bool(data.get("searches_completed_today", False) or self.is_today_already_completed(today))

    def mark_searches_completed_today(
        self,
        completed: bool = True,
        core_tasks_done: Optional[bool] = None,
        today_str: str | None = None,
    ) -> None:
        today, state = self._load_today_state_dict(today_str)
        state["account_id"] = self.account_id
        state["date"] = today
        state["searches_completed_today"] = bool(completed)
        if core_tasks_done is not None:
            state["core_tasks_done"] = bool(core_tasks_done)
        state["updated_at"] = datetime.datetime.now().isoformat()
        self.save_daily_state_dict(state)

    def get_visual_search_attempts_today(self, today_str: str | None = None) -> int:
        today = today_str or datetime.date.today().isoformat()
        data = self.load_daily_state()
        if data.get("date") != today and data.get("completed_date") != today:
            return 0
        return int(data.get("visual_search_attempts_today", 0) or 0)

    def is_visual_search_exhausted_today(self, today_str: str | None = None) -> bool:
        return self.get_visual_search_attempts_today(today_str) >= MAX_VISUAL_SEARCH_ATTEMPTS_PER_DAY

    def increment_visual_search_attempts_today(
        self,
        attempts: int = 1,
        today_str: str | None = None,
    ) -> int:
        today, state = self._load_today_state_dict(today_str)
        cur = int(state.get("visual_search_attempts_today", 0) or 0)
        new_val = min(MAX_VISUAL_SEARCH_ATTEMPTS_PER_DAY, max(0, cur + int(attempts)))
        state["account_id"] = self.account_id
        state["date"] = today
        state["visual_search_attempts_today"] = new_val
        state["updated_at"] = datetime.datetime.now().isoformat()
        self.save_daily_state_dict(state)
        return new_val

    def is_mobile_checkin_done(self, today_str: str | None = None) -> bool:
        today = today_str or datetime.date.today().isoformat()
        data = self.load_daily_state()
        if data.get("date") != today and data.get("completed_date") != today:
            return False
        return bool(data.get("mobile_checkin_done", False))

    def mark_mobile_checkin_done(self, done: bool = True, today_str: str | None = None) -> None:
        today, state = self._load_today_state_dict(today_str)
        state["account_id"] = self.account_id
        state["date"] = today
        state["mobile_checkin_done"] = bool(done)
        state["updated_at"] = datetime.datetime.now().isoformat()
        self.save_daily_state_dict(state)

    def should_fast_exit_today(self, today_str: str | None = None) -> bool:
        today = today_str or datetime.date.today().isoformat()
        if self.is_today_already_completed(today):
            return True
        data = self.load_daily_state()
        if data.get("date") != today and data.get("completed_date") != today:
            return False
        if not bool(data.get("searches_completed_today", False)):
            return False
        core_done = bool(data.get("core_tasks_done", False) or data.get("all_activities_done", False))
        vs_attempts = int(data.get("visual_search_attempts_today", 0) or 0)
        return bool(core_done or vs_attempts >= MAX_VISUAL_SEARCH_ATTEMPTS_PER_DAY)

    def get_edge_browsing_state(self, today_str: str | None = None) -> dict[str, Any]:
        today = today_str or datetime.date.today().isoformat()
        data = self.load_daily_state()
        if data.get("date") != today and data.get("completed_date") != today:
            return {"date": today, "seconds": 0, "done": False}
        return {
            "date": today,
            "seconds": int(data.get("edge_browsing_seconds", 0) or 0),
            "done": bool(data.get("edge_browsing_done", False)),
        }

    def update_edge_browsing_state(
        self,
        seconds: int,
        done: bool = False,
        today_str: str | None = None,
    ) -> None:
        try:
            today, state = self._load_today_state_dict(today_str)
            now_iso = datetime.datetime.now().isoformat()

            prev_secs = int(state.get("edge_browsing_seconds", 0) or 0)
            new_secs = max(prev_secs, min(EDGE_BROWSING_CAP_SECONDS, int(seconds)))
            is_done = bool(
                done or state.get("edge_browsing_done", False) or new_secs >= EDGE_BROWSING_CAP_SECONDS
            )
            if is_done:
                new_secs = max(new_secs, EDGE_BROWSING_CAP_SECONDS)

            state.update({
                "account_id": self.account_id,
                "date": today,
                "edge_browsing_seconds": new_secs,
                "edge_browsing_done": is_done,
                "updated_at": now_iso,
            })
            self.save_daily_state_dict(state)
        except Exception as exc:
            _log_warning(f"[Edge Browsing] Lỗi khi lưu tiến độ vào {self.daily_state_file.name}: {exc}")

    def sync_edge_browsing_from_progress(self, progress: dict[str, Any]) -> None:
        if not isinstance(progress, dict):
            return
        edge_info = (progress.get("activities") or {}).get("Edge Browsing") or {}
        if edge_info.get("done", False):
            self.update_edge_browsing_state(EDGE_BROWSING_CAP_SECONDS, done=True)

    def save_daily_state(
        self,
        completed_date: str | None = None,
        partial: bool = False,
        checkin_awarded: int = 0,
        searches_completed_today: Optional[bool] = None,
        core_tasks_done: Optional[bool] = None,
    ) -> None:
        try:
            today_str, state = self._load_today_state_dict(completed_date)
            now_iso = datetime.datetime.now().isoformat()

            searches_done_val = (
                True if not partial
                else bool(state.get("searches_completed_today", False) if searches_completed_today is None else searches_completed_today)
            )
            core_done_val = (
                True if not partial
                else bool(state.get("core_tasks_done", False) if core_tasks_done is None else core_tasks_done)
            )

            state.update({
                "account_id": self.account_id,
                "date": today_str,
                "partial": partial,
                "checkin_awarded": checkin_awarded,
                "edge_browsing_seconds": int(state.get("edge_browsing_seconds", 0) or 0),
                "edge_browsing_done": bool(state.get("edge_browsing_done", False)),
                "searches_completed_today": searches_done_val,
                "core_tasks_done": core_done_val,
                "visual_search_attempts_today": int(state.get("visual_search_attempts_today", 0) or 0),
                "mobile_checkin_done": bool(state.get("mobile_checkin_done", False)),
                "updated_at": now_iso,
            })
            if not partial:
                state["completed_date"] = today_str
                state["completed_at"] = now_iso
                state["status"] = "completed"

            self.save_daily_state_dict(state)

            if partial:
                _log_info(
                    f"[Daily Lock] Ghi nhận tiến độ một phần ngày {today_str} "
                    f"(checkin +{checkin_awarded} pts)."
                )
            else:
                _log_info(
                    f"[Daily Lock] Đã ghi nhận hoàn tất 100% ngày hôm nay ({today_str}) vào {self.daily_state_file.name}."
                )
        except Exception as exc:
            _log_warning(f"[Daily Lock] Lỗi khi ghi {self.daily_state_file.name}: {exc}")

    # ---- Alert deduplication ----
    def has_alert_sent_today(self, event_key: str, today_str: str | None = None) -> bool:
        today = today_str or datetime.date.today().isoformat()
        data = self.load_daily_state()
        alerts = data.get("alerts_sent") or {}
        if not isinstance(alerts, dict):
            return False
        return alerts.get(event_key) == today

    def mark_alert_sent_today(self, event_key: str, today_str: str | None = None) -> None:
        today = today_str or datetime.date.today().isoformat()
        data = self.load_daily_state()
        alerts = dict(data.get("alerts_sent") or {})
        alerts[event_key] = today
        data["alerts_sent"] = alerts
        data["account_id"] = self.account_id
        data["updated_at"] = datetime.datetime.now().isoformat()
        self.save_daily_state_dict(data)

    # ---- Consecutive failure tracking ----
    def get_consecutive_failures(self) -> int:
        data = self.load_daily_state()
        return int(data.get("consecutive_failures", 0) or 0)

    def record_session_outcome(self, success: bool) -> int:
        data = self.load_daily_state()
        failures = 0 if success else int(data.get("consecutive_failures", 0) or 0) + 1
        data["consecutive_failures"] = failures
        data["account_id"] = self.account_id
        data["updated_at"] = datetime.datetime.now().isoformat()
        self.save_daily_state_dict(data)
        return failures

    # ---- Points Ledger ----
    def load_points_ledger(self) -> dict[str, Any]:
        now_iso = datetime.datetime.now().isoformat()
        data = safe_read_json(self.points_ledger_file, default={"last_updated": now_iso, "history": []})
        if not isinstance(data, dict):
            return {"last_updated": now_iso, "history": []}
        if "history" not in data or not isinstance(data["history"], list):
            data["history"] = []
        return data

    def save_points_ledger(self, ledger: dict[str, Any]) -> None:
        atomic_write_json(self.points_ledger_file, ledger)

    def update_points_ledger(
        self,
        total_points: int,
        earned_today: int,
        bing_star_bonus: Optional[str] = None,
        milestones: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        today_str = datetime.date.today().isoformat()
        now_iso = datetime.datetime.now().isoformat()
        ledger = self.load_points_ledger()
        if not bing_star_bonus:
            bing_star_bonus = ledger.get("bing_star_bonus") or "0 / 2,100"

        history = ledger.get("history", [])
        today_entry = next((item for item in history if item.get("date") == today_str), None)
        if today_entry:
            today_entry["total_points"] = total_points
            today_entry["earned_today"] = max(today_entry.get("earned_today", 0), earned_today)
            today_entry["bing_star_bonus"] = bing_star_bonus
            today_entry["updated_at"] = now_iso
        else:
            history.append({
                "date": today_str,
                "total_points": total_points,
                "earned_today": earned_today,
                "bing_star_bonus": bing_star_bonus,
                "updated_at": now_iso,
            })
        ledger.update({
            "account_id": self.account_id,
            "last_updated": now_iso,
            "total_points": total_points,
            "earned_today": earned_today,
            "bing_star_bonus": bing_star_bonus,
            "history": history,
        })
        self.save_points_ledger(ledger)
        _log_info(f"[Points Ledger] Đã cập nhật sổ cái điểm thưởng vào {self.points_ledger_file.name}.")

        ms_list = milestones or [
            {"name": "Roblox Digital Card (100 Robux)", "points": 1500},
            {"name": "Thẻ nạp điện thoại Viettel/Mobi/Vina 50.000đ", "points": 3400},
            {"name": "Voucher Got It / Shopee / Tiki 100.000đ", "points": 6500},
            {"name": "Xbox Game Pass PC (1 Tháng)", "points": 7750},
            {"name": "Voucher Điện Máy XANH / TGDD 200.000đ", "points": 13000},
        ]
        target_milestone = next((m for m in ms_list if m["points"] > total_points), ms_list[-1])
        m_name, m_pts = target_milestone["name"], target_milestone["points"]
        needed_pts = max(0, m_pts - total_points)
        progress_pct = min(100.0, (total_points / m_pts) * 100) if m_pts > 0 else 100.0
        rates = [h.get("earned_today", 0) for h in history if h.get("earned_today", 0) > 0]
        avg_daily_rate = max(50, sum(rates) // len(rates)) if len(history) > 1 and rates else 150
        est_days = max(1, math.ceil(needed_pts / avg_daily_rate)) if needed_pts > 0 else 0
        filled_len = int(20 * (progress_pct / 100.0))
        bar_str = "█" * filled_len + "░" * (20 - filled_len)

        _log_info("=" * 72)
        _log_info(f"  BẢNG TỔNG KẾT ĐIỂM SỐ ({self.account_id}) - {today_str}")
        _log_info(f"  * Hôm nay: +{earned_today:,} pts | Tổng: {total_points:,} pts | Bing Star: {bing_star_bonus}")
        _log_info(f"  * Mốc kế: {m_name} ({m_pts:,} pts) [{bar_str}] {progress_pct:.1f}%")
        if needed_pts > 0:
            _log_info(f"  * Còn thiếu: {needed_pts:,} pts (~{est_days} ngày @ {avg_daily_rate} pts/ngày)")
        else:
            _log_info("  * Trạng thái: [✓] ĐÃ ĐẠT ĐỦ ĐIỂM ĐỔI QUÀ!")
        _log_info("=" * 72)
