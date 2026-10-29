"""
Module theo dõi điểm số Microsoft Rewards và ghi log lịch sử.
- Cào số điểm hiện tại từ giao diện Dashboard.
- Lưu lịch sử biến động điểm vào logs/history.json.
- Real-time file & console logging via standard logging module.
"""

import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from playwright.sync_api import Page

# Use PYTHONUTF8=1 environment variable instead of manual wrapping
# This avoids issues with stream wrapping in subprocesses

# ==================== LOGGING SETUP ====================
ROOT_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

BOT_LOG_FILE = LOGS_DIR / "bot.log"
HISTORY_JSON = LOGS_DIR / "history.json"

# Configure logging - both file and console with immediate flush
_logger = logging.getLogger("ms_rewards_bot")
_logger.setLevel(logging.DEBUG)

# File handler
_file_handler = logging.FileHandler(BOT_LOG_FILE, encoding="utf-8")
_file_handler.setLevel(logging.DEBUG)
_file_formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
_file_handler.setFormatter(_file_formatter)

# Console handler with flush
_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setLevel(logging.INFO)
_console_formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
_console_handler.setFormatter(_console_formatter)

# Avoid duplicate handlers if module reloads
if not _logger.handlers:
    _logger.addHandler(_file_handler)
    _logger.addHandler(_console_handler)

logger = _logger


def _log_info(msg: str):
    _logger.info(msg)


def _log_debug(msg: str):
    _logger.debug(msg)


def _log_warning(msg: str):
    _logger.warning(msg)


def _log_error(msg: str):
    _logger.error(msg)


def _log_exception(msg: str):
    _logger.exception(msg)


# ==================== EXISTING FUNCTIONS ====================
def extract_current_points(page: Page) -> int:
    """Trích xuất chính xác số dư điểm Rewards hiện tại trên header của Bing/Rewards."""
    try:
        page.wait_for_selector(
            "#id_rc, #rh_meter, [id*='reward_points'], span[class*='pointsValue'], header, #b_header",
            timeout=3000
        )
    except Exception:
        pass

    selectors = [
        "#id_rc",                    # Số điểm trên thanh header Bing Search
        "#rh_meter",
        "[id*='reward_points']",
        "span[class*='pointsValue']",
        "header div[class*='points']",
        "button[aria-label*='points' i]",
        "header button:has(img[src*='Medals'])",
        "header button:has(img[alt='Member'])",
        "#flyout-points"
    ]
    for sel in selectors:
        try:
            elem = page.locator(sel).first
            if elem.is_visible(timeout=1000):
                text = elem.inner_text().strip()
                if not text:
                    text = elem.get_attribute("aria-label") or elem.get_attribute("title") or ""
                # Loại bỏ dấu phẩy/chấm phân tách hàng nghìn (ví dụ: 1,250 -> 1250)
                clean_text = text.replace(",", "").replace(".", "")
                nums = [int(s) for s in clean_text.split() if s.isdigit()]
                if not nums:
                    nums = [int(s) for s in re.findall(r'\d+', clean_text)]
                if nums:
                    return nums[0]
        except Exception:
            continue

    # Fallback: Quét bằng Regex tìm số đứng ngay trước/sau biểu tượng điểm
    try:
        raw_text = page.locator("header, #b_header, [role='banner']").first.inner_text()
        matches = re.findall(r'\b\d{2,6}\b', raw_text.replace(",", "").replace(".", ""))
        if not matches:
            matches = re.findall(r'\d{2,6}', raw_text.replace(",", "").replace(".", ""))
        if matches:
            return int(matches[0])
    except Exception:
        pass

    # Fallback 2: Thẻ điểm khả dụng trên dashboard Rewards (tránh lấy nhầm hạn ngạch 300)
    try:
        status_items = page.locator("mee-rewards-user-status-item").all()
        for item in status_items:
            item_text = item.inner_text()
            if "available" in item_text.lower() or "khả dụng" in item_text.lower():
                matches = re.findall(r'\b\d{2,6}\b', item_text.replace(",", "").replace(".", ""))
                if matches:
                    return int(matches[0])
    except Exception:
        pass

    return 0


# ==================== NEW INCREMENTAL LOGGING FUNCTIONS ====================
def init_session(start_points: int) -> None:
    """
    Khởi tạo phiên chạy mới - ghi ngay bản ghi với status="running".
    
    Args:
        start_points: Điểm số lúc bắt đầu phiên
    """
    HISTORY_JSON.parent.mkdir(parents=True, exist_ok=True)
    
    data = []
    if HISTORY_JSON.exists():
        try:
            with open(HISTORY_JSON, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if not isinstance(data, list):
                    data = []
        except Exception:
            data = []
    
    record = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "points_before": start_points,
        "points_after": None,
        "earned": None,
        "searches_completed": 0,
        "status": "running",
        "error_msg": None
    }
    data.append(record)
    
    try:
        with open(HISTORY_JSON, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        _log_info(f"Session started. Start points: {start_points}")
    except Exception as e:
        _log_error(f"Lỗi khi ghi init_session: {e}")


def update_session(searches_done: int, current_points: int = None, current_task: str = "") -> None:
    """
    Cập nhật tiến độ sau mỗi batch hoặc sau mỗi nhiệm vụ.
    
    Args:
        searches_done: Tổng số lượt tìm kiếm đã hoàn thành
        current_points: Điểm số hiện tại (optional)
        current_task: Tên tác vụ đang thực hiện (optional)
    """
    if not HISTORY_JSON.exists():
        return
        
    try:
        with open(HISTORY_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if not isinstance(data, list) or not data:
                return
    except Exception:
        return
    
    # Cập nhật bản ghi cuối cùng (đang running)
    last_record = data[-1]
    if last_record.get("status") == "running":
        last_record["searches_completed"] = searches_done
        if current_points is not None:
            last_record["points_after"] = current_points
            last_record["earned"] = max(0, current_points - last_record["points_before"])
        if current_task:
            last_record["current_task"] = current_task
        last_record["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        try:
            with open(HISTORY_JSON, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            _log_debug(f"Progress: {searches_done} searches, points: {current_points}, task: {current_task}")
        except Exception as e:
            _log_error(f"Lỗi khi ghi update_session: {e}")


def finish_session(end_points: int, status: str = "completed", error_msg: str = None) -> None:
    """
    Chốt phiên chạy, tính earned = end_points - start_points.
    
    Args:
        end_points: Điểm số kết thúc
        status: "completed" | "failed" | "interrupted"
        error_msg: Thông báo lỗi nếu status != "completed"
    """
    if not HISTORY_JSON.exists():
        return
        
    try:
        with open(HISTORY_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if not isinstance(data, list) or not data:
                return
    except Exception:
        return
    
    last_record = data[-1]
    if last_record.get("status") == "running":
        start_points = last_record["points_before"]
        earned = max(0, end_points - start_points)
        
        last_record["points_after"] = end_points
        last_record["earned"] = earned
        last_record["status"] = status
        last_record["end_timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if error_msg:
            last_record["error_msg"] = error_msg
        
        try:
            with open(HISTORY_JSON, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            _log_info(f"Session finished. Status: {status}, Earned: +{earned} pts")
        except Exception as e:
            _log_error(f"Lỗi khi ghi finish_session: {e}")


# ==================== LEGACY FUNCTION (KEPT FOR COMPATIBILITY) ====================
def log_session_result(start_pts: int, end_pts: int, searches_done: int) -> None:
    """
    Lưu kết quả chạy vào file JSON (legacy - kept for backward compatibility).
    
    Args:
        start_pts: Điểm số trước khi chạy
        end_pts: Điểm số sau khi chạy
        searches_done: Tổng số lượt tìm kiếm đã hoàn thành
    """
    HISTORY_JSON.parent.mkdir(parents=True, exist_ok=True)
    
    data = []
    if HISTORY_JSON.exists():
        try:
            with open(HISTORY_JSON, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if not isinstance(data, list):
                    data = []
        except Exception:
            data = []

    earned = max(0, end_pts - start_pts)
    
    record = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "points_before": start_pts,
        "points_after": end_pts,
        "earned": earned,
        "searches_completed": searches_done,
        "status": "completed"
    }
    data.append(record)

    try:
        with open(HISTORY_JSON, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        _log_info(f"Legacy log: Start={start_pts}, End={end_pts}, Earned=+{earned}, Searches={searches_done}")
    except Exception as e:
        _log_error(f"Lỗi khi ghi log_session_result: {e}")


def get_history_summary(days: int = 30) -> dict:
    """
    Lấy tóm tắt lịch sử điểm trong N ngày gần nhất.
    
    Args:
        days: Số ngày quay lại
        
    Returns:
        Dict chứa tổng điểm kiếm được, trung bình/ngày, số phiên chạy
    """
    if not HISTORY_JSON.exists():
        return {"total_earned": 0, "avg_per_day": 0, "sessions": 0}
    
    try:
        with open(HISTORY_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        from datetime import timedelta
        cutoff = datetime.now() - timedelta(days=days)
        
        recent = [
            r for r in data 
            if datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S") >= cutoff
        ]
        
        total = sum(r.get("earned", 0) for r in recent)
        sessions = len(recent)
        avg = total / sessions if sessions > 0 else 0
        
        return {
            "total_earned": total,
            "avg_per_day": round(avg, 1),
            "sessions": sessions
        }
    except Exception:
        return {"total_earned": 0, "avg_per_day": 0, "sessions": 0}


if __name__ == "__main__":
    # Test nhanh
    print("Tracker module loaded.")
    print(f"LOG_DIR: {LOGS_DIR}")
    print(f"BOT_LOG_FILE: {BOT_LOG_FILE}")
    print(f"HISTORY_JSON: {HISTORY_JSON}")
    summary = get_history_summary()
    print(f"30-day summary: {summary}")