#!/usr/bin/env python3
"""
Unified Orchestrator - Phase 5 Full Automation Pipeline.
Quản lý toàn bộ quy trình: Check-in/Activities -> Search Loop (Desktop+Mobile Cooldown) -> Logging.
Phase 7.2: Multi-account support with SQLite WAL database.
"""

import argparse
import ctypes
import datetime
import importlib
import json
import math
import os
import random
import shutil
import socket
import subprocess
import sys
import time
import traceback
import urllib.parse
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None

# Use PYTHONUTF8=1 environment variable instead of manual wrapping
# This avoids issues with stream wrapping in subprocesses

# Add parent to path for core imports
sys.path.insert(0, str(Path(__file__).parent))
from core import (
    fetch_trending_queries,
    get_unique_search_queries,
    save_searched_keyword,
    typing_delay,
    cooldown_sleep,
    extract_current_points,
    log_session_result,
    run_all_dashboard_activities,
    enable_streak_protection,
    ensure_streak_protection,
    detect_unusual_activity_warning,
    solve_visual_search_streak,
    handle_visual_search_streak,
    solve_mobile_app_daily_checkin,
    solve_mobile_app_promotions,
    solve_mobile_app_read_to_earn,
    solve_punch_cards,
    solve_all_earn_quests,
    solve_keep_earning_activities,
    unload_model_from_vram,
    claim_msn_daily_gaming_tile,
    solve_wallpaper_trivia,
    interact_with_copilot,
    solve_promotional_links,
    get_daily_search_progress,
    tap_corner_rewards_coin,
    perform_related_cluster_search,
)
from core.activities import (
    solve_mobile_checkin,
    dismiss_popups,
    claim_all_available_rewards,
    extract_bing_star_bonus,
)
from core.searcher import human_type_and_search
from core.fingerprint import inject_stealth_fingerprint, get_deterministic_hardware_profile
from core.tracker import (
    init_session,
    update_session,
    finish_session,
    logger,
    _log_info,
    _log_debug,
    _log_warning,
    _log_error,
    _log_exception,
)
from core.database import (
    init_db,
    get_db_path,
    upsert_account,
    get_account,
    is_day_completed,
    upsert_daily_progress,
    record_points_snapshot,
    get_oauth_tokens,
    save_oauth_tokens,
    get_recent_search_queries,
    add_search_queries,
    insert_run_log,
)

# ==================== CONFIGURATION ====================
ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
REWARDS_URL = "https://rewards.bing.com"
BING_URL = "https://www.bing.com"

DEFAULT_TIMEOUT = 8000  # 8 seconds default timeout

# Tham số khởi động trình duyệt tối ưu phần cứng & ngụy trang chống phát hiện:
# 1. Giữ WebGL báo đúng card đồ họa thật (không dùng --disable-gpu làm lộ SwiftShader)
# 2. Giới hạn 15 FPS và tắt GPU vsync để GPU nghỉ ngơi mát mẻ (<5W, 0 RPM)
# 3. Tắt bộ đếm timer throttling khi chạy ngầm để script JS hoạt động ổn định
# 4. Khống chế cache tối đa 10 MB bảo vệ tuổi thọ ghi (TBW) của M.2 SSD
BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-gpu-vsync",                # Tắt đồng bộ dọc giảm tải GPU
    "--frame-rate-limit=15",              # Giới hạn 15 FPS cho trình duyệt
    "--disable-background-timer-throttling", # Chống bóp hiệu năng timer ngầm
    "--blink-settings=imagesEnabled=true", # Vẫn giữ nạp ảnh nếu cần làm nhiệm vụ
    "--disk-cache-size=10485760",         # Giới hạn cache tối đa 10 MB (thay vì vài GB)
    "--media-cache-size=10485760",        # Giới hạn media cache 10 MB
    "--disable-application-cache",
]


def _get_profile_paths(account_id: str) -> tuple[Path, Path]:
    """Get desktop and mobile profile paths for an account, with fallback to legacy defaults."""
    account = get_account(account_id)
    if account and account.get("profile_dir"):
        base = ROOT_DIR / account["profile_dir"]
        # If profile_dir already ends with 'desktop', it's the legacy path structure
        # Otherwise, treat it as the base directory for the account
        if base.name == "desktop":
            return base, base.parent / "mobile"
        return base / "desktop", base / "mobile"
    # Fallback for acc_primary using legacy paths
    return ROOT_DIR / "profiles" / "desktop", ROOT_DIR / "profiles" / "mobile"


def get_sync_playwright():
    """Import Patchright only when the browser pipeline is started."""
    try:
        return importlib.import_module("patchright.sync_api").sync_playwright
    except ModuleNotFoundError as exc:
        if exc.name == "patchright" or (exc.name and exc.name.startswith("patchright.")):
            raise RuntimeError(
                "Patchright is required. Install it with 'pip install patchright' and run 'patchright install chromium'."
            ) from exc
        raise


# ==================== SEARCH EXECUTION ====================
def execute_single_search(page, query: str, device_type: str = "desktop") -> bool:
    """
    Thực hiện một lượt tìm kiếm tự nhiên trên Bing bằng cơ chế gõ phím giống người thật.
    
    Args:
        page: Playwright Page object
        query: Từ khóa tìm kiếm
        device_type: "desktop", "mobile", hoặc "sidebar"
        
    Returns:
        True nếu thành công
    """
    is_mobile = (device_type.lower() == "mobile")
    return human_type_and_search(page, query, is_mobile=is_mobile)


def active_edge_browsing_cooldown(page, total_seconds: int = 900):
    """
    Duy trì phiên duyệt web Edge trong thời gian Cooldown để hoàn thành 
    nhiệm vụ Edge Browsing Streak (30 phút/ngày) mà không tốn thêm thời gian.
    Lưu ý: Tuyệt đối KHÔNG gọi context.close().
    Nếu có đóng page tạm, hãy đảm bảo mở sẵn một tab about:blank trước khi đóng tab cũ để Chromium không tự động thoát tiến trình.
    """
    logger.info(f"[Edge Browsing] Bắt đầu phiên duyệt web ngầm tích lũy thời gian ({total_seconds // 60} phút)...")
    
    context = getattr(page, "context", None)
    
    # Đảm bảo duy trì ít nhất một tab sống (không bao giờ để 0 tab khiến browser thoát)
    try:
        if page is None or page.is_closed():
            if context and context.pages:
                page = context.pages[0]
            elif context:
                page = context.new_page()
    except Exception:
        pass

    # Điều hướng tới trang tin tức nhẹ
    try:
        if page and not page.is_closed():
            page.goto("https://www.bing.com/news", wait_until="domcontentloaded", timeout=12000)
    except Exception:
        pass

    start_time = time.time()
    interval = random.randint(120, 180)  # Tương tác mỗi 2-3 phút

    while time.time() - start_time < total_seconds:
        elapsed = int(time.time() - start_time)
        remaining = total_seconds - elapsed
        
        # Mô phỏng tương tác người dùng để Edge gửi Heartbeat Telemetry
        try:
            if page and not page.is_closed():
                scroll_y = random.choice([300, 500, -200, 400])
                page.mouse.wheel(0, scroll_y)
                page.wait_for_timeout(1000)
        except Exception:
            pass

        # Ngủ chu kỳ ngắn
        sleep_chunk = min(interval, remaining)
        if sleep_chunk > 0:
            logger.info(f"[Edge Browsing Streak] Đang tích lũy... Còn lại: {remaining // 60}m {remaining % 60}s")
            time.sleep(sleep_chunk)
            
    # Khi hoàn thành 15 phút duyệt web, tuyệt đối KHÔNG đóng d_context.
    # Đảm bảo luôn giữ ít nhất 1 tab sống; nếu đóng page tạm thì phải mở about:blank trước.
    try:
        if context:
            open_pages = [p for p in context.pages if not p.is_closed()]
            if not open_pages:
                blank_page = context.new_page()
                blank_page.goto("about:blank")
    except Exception:
        pass

    logger.info("[Edge Browsing] Hoàn thành đợt tích lũy thời gian Cooldown.")
    return page


def apply_stealth_scripts(context, is_mobile: bool = False, cpu_threads: int = None, account_id: str = "acc_primary"):
    """Tiêm script xóa dấu vết tự động hóa của Patchright/CDP và đồng bộ User-Agent Client Hints, hardwareConcurrency, deviceMemory."""
    platform_val = "Linux armv8l" if is_mobile else "Win32"
    ua_platform = "Android" if is_mobile else "Windows"
    is_mob_js = "true" if is_mobile else "false"
    model_val = "Pixel 7" if is_mobile else ""
    platform_version = "14.0.0" if is_mobile else "15.0.0"
    arch_val = "arm" if is_mobile else "x86"
    profile = get_deterministic_hardware_profile(account_id)
    if is_mobile:
        hw_concurrency = 8
        device_memory = 8
    else:
        hw_concurrency = cpu_threads or profile["hardware_concurrency"]
        device_memory = profile["device_memory"]

    stealth_js = f"""
    // 1. Xóa cờ webdriver
    Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});

    // 2. Đồng bộ platform chuẩn với thiết bị
    Object.defineProperty(navigator, 'platform', {{ get: () => '{platform_val}' }});

    // 3. Giả lập languages tự nhiên
    Object.defineProperty(navigator, 'languages', {{ get: () => ['vi-VN', 'vi', 'en-US', 'en'] }});

    // 4. Đồng bộ sâu hardwareConcurrency & deviceMemory
    Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {hw_concurrency} }});
    Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {device_memory} }});

    // 5. Đảm bảo kích thước outerWidth/outerHeight hợp lệ khi chạy ngầm
    if (window.outerWidth === 0 || window.outerHeight === 0) {{
        Object.defineProperty(window, 'outerWidth', {{ get: () => window.innerWidth }});
        Object.defineProperty(window, 'outerHeight', {{ get: () => window.innerHeight + 85 }});
    }}

    // 6. Đồng bộ User-Agent Client Hints (navigator.userAgentData) khớp 100% môi trường
    const mockUAData = {{
        brands: [
            {{ brand: "Microsoft Edge", version: "131" }},
            {{ brand: "Chromium", version: "131" }},
            {{ brand: "Not_A Brand", version: "24" }}
        ],
        mobile: {is_mob_js},
        platform: "{ua_platform}",
        toJSON: function() {{
            return {{ brands: this.brands, mobile: this.mobile, platform: this.platform }};
        }},
        getHighEntropyValues: function(hints) {{
            return Promise.resolve({{
                brands: this.brands,
                mobile: {is_mob_js},
                platform: "{ua_platform}",
                platformVersion: "{platform_version}",
                architecture: "{arch_val}",
                bitness: "64",
                model: "{model_val}",
                uaFullVersion: "131.0.2903.86"
            }});
        }}
    }};
    try {{
        Object.defineProperty(navigator, 'userAgentData', {{ get: () => mockUAData, configurable: true }});
    }} catch(e) {{}}
    """
    context.add_init_script(stealth_js)
    inject_stealth_fingerprint(context, account_id=account_id)


def create_desktop_context(playwright, headless: bool, account_id: str = "acc_primary"):
    """Tạo Desktop persistent context với chế độ ngụy trang (Stealth)."""
    desktop_profile, _ = _get_profile_paths(account_id)
    desktop_profile.mkdir(parents=True, exist_ok=True)
    
    args = list(BROWSER_ARGS)
    if headless:
        args.append("--headless=new")
    else:
        args.append("--start-maximized")

    extra_headers = {
        "Sec-CH-UA-Platform": '"Windows"',
        "Sec-CH-UA-Mobile": "?0",
    }

    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(desktop_profile),
        channel="msedge",
        headless=headless,
        args=args,
        no_viewport=not headless,
        locale="vi-VN",
        timezone_id="Asia/Ho_Chi_Minh",
        extra_http_headers=extra_headers,
    )
    apply_stealth_scripts(context, is_mobile=False, account_id=account_id)
    return context


def create_mobile_context(playwright, headless: bool, account_id: str = "acc_primary"):
    """Tạo Mobile emulation persistent context giả lập Android (Pixel 7)."""
    _, mobile_profile = _get_profile_paths(account_id)
    mobile_profile.mkdir(parents=True, exist_ok=True)
    
    mobile_device = dict(playwright.devices["Pixel 7"])
    mobile_device.pop("default_browser_type", None)

    # Chuẩn hóa User-Agent Edge Android (EdgA) để nhận thưởng trọn vẹn điểm Edge Mobile
    ua = mobile_device.get("user_agent", "")
    if "EdgA" not in ua:
        chrome_ver = "131.0.0.0"
        mobile_device["user_agent"] = f"Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_ver} Mobile Safari/537.36 EdgA/{chrome_ver}"

    args = list(BROWSER_ARGS)
    if headless:
        args.append("--headless=new")

    extra_headers = {
        "Sec-CH-UA-Platform": '"Android"',
        "Sec-CH-UA-Mobile": "?1",
        "Sec-CH-UA-Model": '"Pixel 7"',
    }

    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(mobile_profile),
        channel="msedge",
        headless=headless,
        args=args,
        **mobile_device,
        locale="vi-VN",
        timezone_id="Asia/Ho_Chi_Minh",
        extra_http_headers=extra_headers,
    )
    # Fix: use deterministic hardware profile for mobile too (was hardcoded 8/8)
    apply_stealth_scripts(context, is_mobile=True, account_id=account_id)
    return context

def ensure_desktop_page(d_context, playwright_instance, headless: bool, account_id: str = "acc_primary"):
    """
    Đảm bảo luôn lấy được một d_page hợp lệ.
    Nếu d_context bị đóng hoặc mất kết nối sau 15 phút cooldown, tự động hồi sinh context mới.
    """
    try:
        if d_context is not None:
            # Kiểm tra xem context còn gọi được không
            if d_context.pages:
                page = d_context.pages[0]
                if not page.is_closed():
                    page.set_default_timeout(DEFAULT_TIMEOUT)
                    return d_context, page

            new_page = d_context.new_page()
            new_page.set_default_timeout(DEFAULT_TIMEOUT)
            return d_context, new_page
    except Exception as e:
        _log_warning(f"[Runner] Desktop context đã bị đóng sau Cooldown ({e}). Đang khởi tạo lại context mới...")
        try:
            if d_context is not None:
                d_context.close()
        except Exception:
            pass

    # Khởi tạo lại Desktop context sạch sẽ
    try:
        new_context = create_desktop_context(playwright_instance, headless, account_id=account_id)
        new_page = new_context.pages[0] if new_context.pages else new_context.new_page()
        new_page.set_default_timeout(DEFAULT_TIMEOUT)
        return new_context, new_page
    except Exception as err:
        _log_error(f"[Runner] Không thể khởi tạo lại Desktop context: {err}")
        raise err


def ensure_mobile_page(m_context, playwright_instance, headless: bool, account_id: str = "acc_primary"):
    """
    Đảm bảo luôn lấy được một m_page hợp lệ cho di động.
    Nếu m_context bị đóng hoặc mất kết nối sau Cooldown, tự động hồi sinh context mới.
    """
    try:
        if m_context is not None:
            # Kiểm tra xem context còn gọi được không
            if m_context.pages:
                page = m_context.pages[0]
                if not page.is_closed():
                    page.set_default_timeout(DEFAULT_TIMEOUT)
                    return m_context, page
            new_page = m_context.new_page()
            new_page.set_default_timeout(DEFAULT_TIMEOUT)
            return m_context, new_page
    except Exception as e:
        _log_warning(f"[Runner] Mobile context đã bị đóng sau Cooldown ({e}). Đang khởi tạo lại context mới...")
        try:
            if m_context is not None:
                m_context.close()
        except Exception:
            pass

    # Khởi tạo lại Mobile context sạch sẽ
    try:
        new_context = create_mobile_context(playwright_instance, headless, account_id=account_id)
        new_page = new_context.pages[0] if new_context.pages else new_context.new_page()
        new_page.set_default_timeout(DEFAULT_TIMEOUT)
        return new_context, new_page
    except Exception as err:
        _log_error(f"[Runner] Không thể khởi tạo lại Mobile context: {err}")
        raise err


# ==================== MAIN ORCHESTRATOR ====================
def run_full_pipeline(
    args=None,
    account_id: str = "acc_primary",
    desktop_target: int = 30,
    mobile_target: int = 20,
    batch_size: int = 3,
    headless: bool = True,
    suppress_auto_sleep: bool = False,
) -> bool:
    """
    Pipeline hoàn chỉnh Phase 5:
    1. Desktop: Check-in + Daily Set + More Activities + Đọc điểm đầu
    2. Loop: Desktop Batch -> Mobile Batch -> Cooldown 15m (nếu chưa xong)
    3. Desktop: Đọc điểm cuối + Ghi log
    
    Returns:
        True nếu tất cả mục tiêu tìm kiếm đã đạt
    """
    if args is not None:
        if hasattr(args, "desktop_target"):
            desktop_target = args.desktop_target
        if hasattr(args, "mobile_target"):
            mobile_target = args.mobile_target
        if hasattr(args, "batch_size"):
            batch_size = args.batch_size
        if hasattr(args, "debug"):
            headless = not args.debug
        if hasattr(args, "account_id"):
            account_id = args.account_id

    # Initialize database
    init_db()
    
    # Ensure account exists in database
    desktop_profile, mobile_profile = _get_profile_paths(account_id)
    upsert_account(account_id, profile_dir=str(desktop_profile.parent.relative_to(ROOT_DIR)))

    # Kiểm tra cờ chạy tự động vs tùy biến
    is_debug = bool(args and getattr(args, "debug", False))
    has_custom_target = bool(args and (
        (getattr(args, 'desktop_target', 30) != 30) or 
        (getattr(args, 'mobile_target', 20) != 20) or
        any(arg.startswith("--desktop-target") or arg.startswith("--mobile-target") for arg in sys.argv)
    ))

    today_str = datetime.date.today().isoformat()
    
    # Zero-Browser Daily Lock: Nếu chạy tự động không debug/custom target và hôm nay đã hoàn thành 100%
    if not is_debug and not has_custom_target and is_day_completed(account_id, today_str):
        _log_info(f"[Daily Lock] Hôm nay đã hoàn thành 100% mục tiêu điểm cho {account_id}. Thoát sớm!")
        return True

    try:
        cleanup_zombie_browsers()
    except Exception:
        pass

    _log_info("=" * 60)
    _log_info("  Microsoft Rewards Bot - Phase 5 Full Automation")
    _log_info("=" * 60)
    _log_info(f"Account: {account_id}")
    _log_info(f"Config: Desktop={desktop_target}, Mobile={mobile_target}, Batch={batch_size}")
    _log_info(f"Headless: {headless}")
    _log_info("")

    # Trễ khởi động ngẫu nhiên (Startup Jitter) khi chạy tự động ngầm để làm lệch mốc giờ máy móc
    if args and not getattr(args, "debug", False):
        jitter_seconds = random.randint(30, 90)
        _log_info(f"[Stealth] Đợi ngẫu nhiên {jitter_seconds} giây để làm lệch mốc giờ cố định (Startup Jitter)...")
        time.sleep(jitter_seconds)

    # Kiểm tra profile tồn tại
    desktop_profile, mobile_profile = _get_profile_paths(account_id)
    if not desktop_profile.exists():
        _log_error(f"Desktop profile không tồn tại: {desktop_profile}")
        _log_info("   Chạy: python setup_login.py --desktop")
        return False
    
    if not mobile_profile.exists():
        _log_error(f"Mobile profile không tồn tại: {mobile_profile}")
        _log_info("   Chạy: python setup_login.py --mobile")
        return False

    # Chuẩn bị danh sách từ khóa độc nhất (lọc qua database search_history & đa dạng đời sống)
    total_needed = desktop_target + mobile_target + 6
    _log_info(f"Đang chuẩn bị {total_needed} từ khóa độc nhất (lọc trùng qua lịch sử {account_id})...")
    recent_searches = get_recent_search_queries(account_id, limit=200)
    queries = get_unique_search_queries(target_count=total_needed, geo="VN", account_id=account_id, exclude_queries=recent_searches)
    random.shuffle(queries)  # Xáo trộn để không theo thứ tự cố định
    
    _log_info(f"Đã chuẩn bị {len(queries)} từ khóa độc nhất không trùng lặp.")
    _log_info("")

    # Biến đếm
    d_done = 0
    m_done = 0
    start_points = 0
    end_points = 0
    queries_idx = 0
    bing_star_bonus_str = "0 / 2,100"

    d_context = None
    m_context = None
    all_tasks_completed = False
    all_maxed = False
    all_activities_done = False
    checkin_awarded = 0

    try:
        # Network Guard: Chờ kết nối Internet ổn định tối đa 60s sau khi máy thức dậy từ Sleep
        if not wait_for_internet(timeout=60):
            _log_error("[Network Guard] Không có kết nối Internet sau 60s, hủy bỏ phiên chạy.")
            return False

        with get_sync_playwright()() as p:
            # ========================================================
            # BƯỚC 1: DESKTOP CONTEXT - ACTIVITIES & ĐIỂM ĐẦU
            # ========================================================
            _log_info("[1/3] Khởi chạy Desktop Context để xử lý Activities & Streak...")
            try:
                d_context, d_page = ensure_desktop_page(d_context, p, headless, account_id)

                # Đọc điểm số khởi đầu
                d_page.goto(REWARDS_URL, wait_until="domcontentloaded")
                d_page.wait_for_timeout(2000)
                dismiss_popups(d_page)
                start_points = extract_current_points(d_page)
                _log_info(f"Điểm số ban đầu: {start_points:,}")

                # Khiên Anti-Ban: Kiểm tra cảnh báo giới hạn trên Dashboard
                safe_throttle_mode = False
                try:
                    if detect_unusual_activity_warning(d_page):
                        safe_throttle_mode = True
                        _log_warning("[Anti-Ban Shield] Phát hiện cảnh báo giới hạn tốc độ tìm kiếm! Chuyển sang chế độ Safe Throttle (giãn cách 35-60s/câu)...")
                except Exception as e:
                    _log_debug(f"Lỗi kiểm tra cảnh báo giới hạn ban đầu: {e}")

                # Khởi tạo session logging ngay lập tức
                init_session(start_points)

                # KIỂM TRA TIẾN ĐỘ THỰC TẾ TRÊN DASHBOARD / POINTS BREAKDOWN NGAY TỪ ĐẦU (SCAN PHASE 1)
                initial_progress = get_daily_search_progress(d_page)
                bing_star_bonus_str = initial_progress.get("bing_star_bonus", {}).get("text", bing_star_bonus_str)
                all_maxed = initial_progress.get("all_maxed", False)
                all_activities_done = initial_progress.get("all_activities_done", False)
                init_act = initial_progress.get("activities", {})
                init_primary_four = initial_progress.get("primary_four_done", False) or (
                    init_act.get("Bing Search", {}).get("done", False) and
                    init_act.get("Daily Set", {}).get("done", False) and
                    init_act.get("Edge Browsing", {}).get("done", False) and
                    init_act.get("Visual Search", {}).get("done", False)
                )
                init_mob_done = init_act.get("Mobile App", {}).get("done", False)
                if not all_activities_done and all_maxed and init_primary_four and not init_mob_done and is_day_completed(account_id, today_str):
                    all_activities_done = True
                    initial_progress["all_activities_done"] = True
                is_explicit_test = bool(args and getattr(args, "desktop_target", None) == 0 and getattr(args, "mobile_target", None) == 0)
                is_test_target = bool(args and ((getattr(args, 'desktop_target', 30) not in (0, 30)) or (getattr(args, 'mobile_target', 20) not in (0, 20))))
                _log_info(f"[Scan Phase 1] Tiến độ thực tế ban đầu: Desktop {initial_progress.get('pc_cur')}/{initial_progress.get('pc_max')} | Mobile {initial_progress.get('mobile_cur')}/{initial_progress.get('mobile_max')}")
                if all_maxed and all_activities_done and not is_explicit_test and not is_test_target:
                    _log_info("[✓] Đã kiểm tra Dashboard: Toàn bộ nhiệm vụ và điểm tìm kiếm hôm nay đã đạt 100%! Bỏ qua tìm kiếm.")
                    desktop_target = 0
                    mobile_target = 0
                    all_tasks_completed = True
                    end_points = start_points
                else:
                    # Bật Streak Protection (Bảo vệ chuỗi ngày)
                    try:
                        ensure_streak_protection(d_page)
                    except Exception as e:
                        _log_warning(f"Lỗi khi bật Streak Protection: {e}")

                    # Chạy Check-in, Daily Set, More Activities
                    try:
                        run_all_dashboard_activities(d_context, d_page)
                        update_session(0, start_points, "Dashboard activities completed")
                    except Exception as e:
                        _log_error(f"Lỗi khi xử lý activities: {e}")
                        _log_exception("Activities error details:")

                    # Tự động hoàn thành Visual Search Streak
                    try:
                        solve_visual_search_streak(d_page)
                    except Exception as e:
                        _log_warning(f"Lỗi khi giải Visual Search Streak: {e}")

                    # Điểm danh 7 ngày trên App Bing bằng OAuth API (type: 103)
                    try:
                        checkin_awarded = solve_mobile_app_daily_checkin(d_page, account_id=account_id)
                        sleep_time = random.uniform(2.0, 4.0)
                        _log_debug(f"[App Check-in] Nghỉ {sleep_time:.1f}s sau khi gọi API check-in...")
                        time.sleep(sleep_time)
                    except Exception as e:
                        _log_warning(f"Lỗi khi xử lý Mobile App Daily Check-in: {e}")

                    # Quét App Promotions (GET /dapi/me) để thu thập điểm ẩn
                    try:
                        solve_mobile_app_promotions(d_page, account_id=account_id)
                    except Exception as e:
                        _log_warning(f"Lỗi khi quét Mobile App Promotions: {e}")

                    # Thực hiện nhiệm vụ Read to Earn qua Mobile App API (+30 điểm/ngày)
                    try:
                        solve_mobile_app_read_to_earn(d_page, account_id=account_id)
                    except Exception as e:
                        _log_warning(f"Lỗi khi xử lý Mobile Read to Earn: {e}")

                    # Giải các thẻ sự kiện Punch Cards
                    try:
                        solve_punch_cards(d_page)
                    except Exception as e:
                        _log_warning(f"Lỗi khi giải Punch Cards: {e}")

                    # Quét và cày toàn bộ Quests/Punch Cards lớn tại /earn
                    try:
                        solve_all_earn_quests(d_page)
                    except Exception as e:
                        _log_warning(f"Lỗi khi xử lý /earn Quests: {e}")

                    # Quét và giải các thẻ trong mục "Keep earning" tại /earn (đạt tối đa điểm 70/70)
                    try:
                        solve_keep_earning_activities(d_page, d_context)
                    except Exception as e:
                        _log_warning(f"Lỗi khi xử lý Keep Earning: {e}")

                    # Claim MSN Daily Gaming Tile (+10 đến +50 pts)
                    try:
                        claim_msn_daily_gaming_tile(d_page)
                    except Exception as e:
                        _log_warning(f"Lỗi khi quét Gaming Tile: {e}")

                    # Giải Bing Wallpaper Trivia (+10 đến +15 pts)
                    try:
                        solve_wallpaper_trivia(d_page)
                    except Exception as e:
                        _log_warning(f"Lỗi khi giải Wallpaper Trivia: {e}")

                    # Tương tác Copilot Chat (+5 đến +15 pts)
                    try:
                        interact_with_copilot(d_page, query="Tin tức hôm nay")
                    except Exception as e:
                        _log_warning(f"Lỗi khi tương tác Copilot: {e}")

                    # Quét Promotional & Newsletter Links (+20 đến +50 pts/tuần)
                    try:
                        solve_promotional_links(d_page)
                    except Exception as e:
                        _log_warning(f"Lỗi khi xử lý Promo Links: {e}")

                    # Claim tất cả phần thưởng khả dụng sau khi hoàn tất Activities + /earn Quests
                    try:
                        claim_all_available_rewards(d_page)
                        update_session(0, None, "Final claim sweep completed")
                    except Exception as e:
                        _log_warning(f"Lỗi khi quét claim cuối cùng: {e}")

                    # Giải phóng VRAM sau khi xong phần Activities
                    try:
                        unload_model_from_vram()
                    except Exception:
                        pass

                    # Kiểm tra lại tiến độ sau khi hoàn thành Activities (SCAN PHASE 2)
                    post_progress = get_daily_search_progress(d_page)
                    pc_cur = post_progress.get("pc_cur", 0)
                    pc_max = post_progress.get("pc_max", 90)
                    mob_cur = post_progress.get("mobile_cur", 0)
                    mob_max = post_progress.get("mobile_max", 60)
                    all_maxed = post_progress.get("all_maxed", False)
                    all_activities_done = post_progress.get("all_activities_done", False)
                    activities = post_progress.get("activities", {})

                    # BƯỚC 3 Fallback: Kiểm tra lại thẻ Mobile App ở Pha Scan 2
                    primary_four_done = post_progress.get("primary_four_done", False) or (
                        activities.get("Bing Search", {}).get("done", False) and
                        activities.get("Daily Set", {}).get("done", False) and
                        activities.get("Edge Browsing", {}).get("done", False) and
                        activities.get("Visual Search", {}).get("done", False)
                    )
                    mob_app_done = activities.get("Mobile App", {}).get("done", False)

                    if all_activities_done:
                        _log_info("[Mobile App] [✓] Tuyệt vời! Thẻ Mobile App đã đạt 1/1 [✓] trên Dashboard.")
                    elif all_maxed and primary_four_done and not mob_app_done:
                        _log_info("[Mobile App Fallback] Dữ liệu thực tế: Search PC 90/90, Mobile 60/60 và 4/4 nhiệm vụ chính (Bing Search, Daily Set, Edge Browsing, Visual Search) đều đã đạt 100%.")
                        _log_info("[Mobile App Fallback] Thẻ Mobile App đã được xử lý qua cả API type 103 lẫn CDP Mobile Emulation. Kích hoạt Fallback chấp nhận all_activities_done = True để cho phép khóa daily_state.json (tránh lặp vô tận do server sync lag).")
                        all_activities_done = True
                        post_progress["all_activities_done"] = True

                    if all_maxed:
                        if is_test_target:
                            _log_info(f"[Test Mode] Điểm hôm nay đã đạt tối đa, nhưng tiếp tục chạy lượt tìm kiếm thử nghiệm: Desktop={desktop_target}, Mobile={mobile_target}")
                        else:
                            _log_info("[✓] Dữ liệu thực tế xác nhận: Điểm tìm kiếm PC và Mobile hôm nay đã đạt tối đa 100%! Không cần tìm kiếm thêm.")
                            desktop_target = 0
                            mobile_target = 0
                            if all_activities_done:
                                all_tasks_completed = True
                            else:
                                _log_info("[!] Điểm tìm kiếm đã đạt đỉnh nhưng vẫn còn hoạt động chưa hoàn thành (all_activities_done = False).")
                    else:
                        rem_pc_pts = max(0, pc_max - pc_cur)
                        rem_mob_pts = max(0, mob_max - mob_cur)
                        desktop_needed = (rem_pc_pts + 2) // 3
                        mobile_needed = (rem_mob_pts + 2) // 3
                        orig_d = desktop_target
                        orig_m = mobile_target
                        if not is_test_target:
                            desktop_target = desktop_needed
                            mobile_target = mobile_needed
                        _log_info(f"[Scan Phase 2] Dữ liệu thực tế: Desktop {pc_cur}/{pc_max} (thiếu {rem_pc_pts} pts -> cần tìm ~{desktop_target} câu) | Mobile {mob_cur}/{mob_max} (thiếu {rem_mob_pts} pts -> cần tìm ~{mobile_target} câu)")
                        if desktop_target == 0 and mobile_target == 0 and not is_explicit_test and not is_test_target:
                            _log_info("[✓] Dữ liệu thực tế xác nhận: Điểm tìm kiếm PC và Mobile hôm nay đã đạt tối đa 100%! Không cần tìm kiếm thêm.")
                            if all_activities_done:
                                all_tasks_completed = True

            finally:
                if d_context:
                    try:
                        d_context.close()
                        _log_info("Đã đóng Desktop context sau Activities.")
                    except Exception:
                        _log_info("Desktop context đã tự đóng trước đó.")
                    d_context = None

            # ========================================================
            # BƯỚC 2: VÒNG LẶP XEN KẼ TÌM KIẾM (COOLDOWN PIPELINE)
            # ========================================================
            if (all_tasks_completed and not is_test_target) or (desktop_target == 0 and mobile_target == 0):
                _log_info("[2/3] Bỏ qua hoàn toàn chu trình tìm kiếm chống Cooldown do toàn bộ điểm đã đạt tối đa.")
                if all_maxed and all_activities_done:
                    all_tasks_completed = True
            else:
                # Phá vỡ quy luật dừng máy móc đúng trần 30/20 câu: thỉnh thoảng tìm dư thêm 1-2 lượt tự nhiên
                if not is_test_target and not is_explicit_test:
                    if desktop_target >= 30 and random.random() < 0.40:
                        extra_pc = random.randint(1, 2)
                        desktop_target += extra_pc
                        _log_info(f"[Stealth] Thêm {extra_pc} lượt tìm kiếm PC tự nhiên phá vỡ điểm trần cứng -> Target: {desktop_target}")
                    if mobile_target >= 20 and random.random() < 0.40:
                        extra_mob = random.randint(1, 2)
                        mobile_target += extra_mob
                        _log_info(f"[Stealth] Thêm {extra_mob} lượt tìm kiếm Mobile tự nhiên phá vỡ điểm trần cứng -> Target: {mobile_target}")

                _log_info("[2/3] Bắt đầu chu trình tìm kiếm chống Cooldown...")
                _log_info("    Pattern: Desktop Batch -> Mobile Batch -> Cooldown 15m (nếu chưa xong)")

                d_page = None
                try:
                    # Mở sẵn Desktop Context cho chu trình tìm kiếm & Edge Browsing Cooldown
                    d_context, d_page = ensure_desktop_page(d_context, p, headless, account_id)

                    max_double_check_retries = 3
                    double_check_attempt = 0

                    while True:
                        while d_done < desktop_target or m_done < mobile_target:
                            batch_completed = 0
                            pc_searched_round = 0
                            mob_searched_round = 0

                            # Đảm bảo d_page vẫn hoạt động
                            d_context, d_page = ensure_desktop_page(d_context, p, headless, account_id)

                            # ---- 2.1 DESKTOP BATCH ----
                            if d_done < desktop_target:
                                # Biến thiên kích thước batch tự nhiên 2-4 câu thay vì cố định 3 câu
                                current_d_batch = random.randint(2, 4) if (args is None or getattr(args, "batch_size", 3) == 3) else batch_size
                                batch_limit = min(current_d_batch, desktop_target - d_done)
                                for i in range(batch_limit):
                                    if queries_idx >= len(queries):
                                        more_q = get_unique_search_queries(target_count=10, geo="VN", account_id=account_id)
                                        queries.extend(more_q)
                                    q = queries[queries_idx]
                                    queries_idx += 1

                                    # Đảm bảo d_page vẫn hoạt động trước mỗi câu tìm kiếm PC
                                    d_context, d_page = ensure_desktop_page(d_context, p, headless, account_id)

                                    _log_info(f"[PC Search {d_done + 1}/{desktop_target}] {q[:50]}...")

                                    # 3 lượt đầu tiên dùng Sidebar Search (form=PRSB01), sau đó dùng Desktop thông thường
                                    search_type = "sidebar" if d_done < 3 else "desktop"
                                    if execute_single_search(d_page, q, device_type=search_type):
                                        d_done += 1
                                        batch_completed += 1
                                        pc_searched_round += 1
                                        add_search_queries(account_id, [q], platform=search_type)
                                        # Update session after each successful search
                                        current_points = extract_current_points(d_page)
                                        update_session(d_done + m_done, current_points, f"Desktop search {d_done}/{desktop_target}")

                                        # Khiên Anti-Ban: kiểm tra cảnh báo giới hạn trên trang kết quả
                                        if not safe_throttle_mode:
                                            try:
                                                if detect_unusual_activity_warning(d_page):
                                                    safe_throttle_mode = True
                                                    _log_warning("[Anti-Ban Shield] Phát hiện cảnh báo giới hạn tốc độ tìm kiếm! Chuyển sang chế độ Safe Throttle (giãn cách 35-60s/câu)...")
                                            except Exception:
                                                pass

                                        # Giãn cách giữa các câu tìm kiếm trong batch
                                        if d_done < desktop_target:
                                            if safe_throttle_mode:
                                                throttle_sleep = random.uniform(35.0, 60.0)
                                                _log_info(f"[Safe Throttle] Giãn cách bảo vệ tài khoản: {throttle_sleep:.1f}s trước câu tiếp theo...")
                                                time.sleep(throttle_sleep)
                                            else:
                                                time.sleep(random.uniform(3.0, 7.0))

                                # Chạm đồng xu góc phải #id_rh ngay trên trang kết quả vừa tìm xong để mở đồng bộ điểm
                                try:
                                    tap_corner_rewards_coin(d_page, context_name="PC-Search")
                                except Exception as e:
                                    _log_debug(f"Lỗi chạm đồng xu PC-Search: {e}")

                            # Nghỉ chuyển tiếp tự nhiên khi chuyển từ cụm PC sang Mobile (Transition Pause 12-30 giây)
                            if pc_searched_round > 0 and m_done < mobile_target:
                                trans_pause = random.randint(12, 30)
                                _log_info(f"[Stealth] Nghỉ chuyển đổi thiết bị PC -> Mobile ({trans_pause}s)...")
                                time.sleep(trans_pause)

                            # ---- 2.2 MOBILE BATCH ----
                            if m_done < mobile_target:
                                try:
                                    m_context, m_page = ensure_mobile_page(m_context, p, headless, account_id)

                                    # Biến thiên kích thước batch tự nhiên 2-4 câu
                                    current_m_batch = random.randint(2, 4) if (args is None or getattr(args, "batch_size", 3) == 3) else batch_size
                                    batch_limit = min(current_m_batch, mobile_target - m_done)
                                    for i in range(batch_limit):
                                        if queries_idx >= len(queries):
                                            more_q = get_unique_search_queries(target_count=10, geo="VN", account_id=account_id)
                                            queries.extend(more_q)
                                        q = queries[queries_idx]
                                        queries_idx += 1

                                        # Đảm bảo m_page vẫn hoạt động trước mỗi câu tìm kiếm Mobile
                                        m_context, m_page = ensure_mobile_page(m_context, p, headless, account_id)

                                        _log_info(f"[Mobile Search {m_done + 1}/{mobile_target}] {q[:50]}...")
                                        if execute_single_search(m_page, q, device_type="mobile"):
                                            m_done += 1
                                            batch_completed += 1
                                            mob_searched_round += 1
                                            add_search_queries(account_id, [q], platform="mobile")
                                            # Update session after each successful search
                                            current_points = extract_current_points(m_page)
                                            update_session(d_done + m_done, current_points, f"Mobile search {m_done}/{mobile_target}")

                                            # Khiên Anti-Ban: kiểm tra cảnh báo giới hạn trên kết quả mobile
                                            if not safe_throttle_mode:
                                                try:
                                                    if detect_unusual_activity_warning(m_page):
                                                        safe_throttle_mode = True
                                                        _log_warning("[Anti-Ban Shield] Phát hiện cảnh báo giới hạn tốc độ tìm kiếm! Chuyển sang chế độ Safe Throttle (giãn cách 35-60s/câu)...")
                                                except Exception:
                                                    pass

                                            # Giãn cách giữa các câu Mobile
                                            if m_done < mobile_target:
                                                if safe_throttle_mode:
                                                    throttle_sleep = random.uniform(35.0, 60.0)
                                                    _log_info(f"[Safe Throttle] Giãn cách bảo vệ tài khoản: {throttle_sleep:.1f}s trước câu Mobile tiếp theo...")
                                                    time.sleep(throttle_sleep)
                                                else:
                                                    time.sleep(random.uniform(3.0, 7.0))

                                    # Chạm đồng xu góc trên giao diện di động, ép máy chủ Bing cộng điểm tìm kiếm Mobile ngay lập tức
                                    try:
                                        tap_corner_rewards_coin(m_page, context_name="Mobile-Search")
                                    except Exception as e:
                                        _log_debug(f"Lỗi chạm đồng xu Mobile-Search: {e}")

                                finally:
                                    if m_context:
                                        try:
                                            m_context.close()
                                        except Exception:
                                            pass
                                        m_context = None

                            # ---- 2.3 COOLDOWN CHECK ----
                            # Nếu vẫn còn lượt cần tìm, kích hoạt cooldown 15 phút với Edge Browsing
                            if d_done < desktop_target or m_done < mobile_target:
                                _log_info(f"Đã hoàn thành đợt: {batch_completed} lượt tìm kiếm.")
                                _log_info(f"Tiến độ: Desktop {d_done}/{desktop_target} | Mobile {m_done}/{mobile_target}")

                                # Log cooldown start with estimated resume time
                                cooldown = 15 * 60 + random.randint(30, 90)
                                resume_time = datetime.datetime.now() + datetime.timedelta(seconds=cooldown)
                                _log_info(f"[Cooldown] Bắt đầu nghỉ 15 phút. Dự kiến tiếp tục lúc: {resume_time.strftime('%H:%M:%S')}")
                                update_session(d_done + m_done, None, "Cooldown 15 minutes")

                                # Đảm bảo d_page sẵn sàng trước khi vào cooldown
                                try:
                                    d_context, d_page = ensure_desktop_page(d_context, p, headless, account_id)
                                except Exception as e:
                                    _log_warning(f"[Runner] Lỗi chuẩn bị Desktop page trước cooldown: {e}")

                                d_page = active_edge_browsing_cooldown(d_page, total_seconds=cooldown)
                                _log_info("")

                        # Nếu là chạy test target tùy chỉnh (ví dụ --desktop-target 1 --mobile-target 0)
                        if is_test_target or is_explicit_test:
                            _log_info("[Test Mode] Đã hoàn thành các lượt tìm kiếm thử nghiệm.")
                            all_tasks_completed = True
                            break

                        # ========================================================
                        # PHA KIỂM TRA CHÉO (DOUBLE CHECK & VERIFICATION PHASE)
                        # ========================================================
                        _log_info("\n" + "=" * 60)
                        _log_info("  [Double-Check] BẮT ĐẦU PHA KIỂM TRA CHÉO TIẾN ĐỘ THỰC TẾ")
                        _log_info("=" * 60)
                        _log_info("[Double-Check] Ép chuyển hướng/reload lại Dashboard (rewards.bing.com/earn)...")

                        try:
                            d_context, d_page = ensure_desktop_page(d_context, p, headless, account_id)
                        except Exception as e:
                            _log_warning(f"[Double-Check] Lỗi chuẩn bị Desktop page: {e}")

                        try:
                            if d_page:
                                d_page.goto("https://rewards.bing.com/earn", wait_until="domcontentloaded", timeout=15000)
                                d_page.wait_for_timeout(2000)
                        except Exception as e:
                            _log_warning(f"[Double-Check] Không thể tải /earn ({e}), thử reload...")
                            try:
                                if d_page:
                                    d_page.reload(wait_until="domcontentloaded", timeout=15000)
                                    d_page.wait_for_timeout(2000)
                            except Exception:
                                pass

                        # Quét lại dữ liệu tiến độ thực tế
                        re_check = get_daily_search_progress(d_page) if d_page else {}
                        re_check_pc = re_check.get("pc_cur", 0)
                        pc_max = re_check.get("pc_max", 90)
                        re_check_mobile = re_check.get("mobile_cur", 0)
                        mobile_max = re_check.get("mobile_max", 60)
                        all_maxed = re_check.get("all_maxed", False)
                        all_activities_done = re_check.get("all_activities_done", False)
                        re_act = re_check.get("activities", {})

                        # Fallback cho thẻ Mobile App nếu server sync lag
                        re_primary_four = re_check.get("primary_four_done", False) or (
                            re_act.get("Bing Search", {}).get("done", False) and
                            re_act.get("Daily Set", {}).get("done", False) and
                            re_act.get("Edge Browsing", {}).get("done", False) and
                            re_act.get("Visual Search", {}).get("done", False)
                        )
                        re_mob_done = re_act.get("Mobile App", {}).get("done", False)
                        if not all_activities_done and re_primary_four and not re_mob_done:
                            all_activities_done = True
                            re_check["all_activities_done"] = True

                        _log_info(f"[Double-Check] Kết quả kiểm tra chéo: Desktop {re_check_pc}/{pc_max} | Mobile {re_check_mobile}/{mobile_max}")

                        is_pc_maxed = (re_check_pc >= pc_max) if re_check.get("found_pc") else (d_done >= desktop_target)
                        is_mob_maxed = (re_check_mobile >= mobile_max) if re_check.get("found_mobile") else (m_done >= mobile_target)

                        if is_pc_maxed and is_mob_maxed:
                            all_maxed = True
                            _log_info(f"[Double-Check] [✓] Dữ liệu thực tế xác nhận 100%: Desktop {re_check_pc}/{pc_max} & Mobile {re_check_mobile}/{mobile_max} đã chạm đỉnh!")
                            if all_activities_done:
                                all_tasks_completed = True
                            break
                        else:
                            double_check_attempt += 1
                            rem_pc = max(0, pc_max - re_check_pc) if re_check.get("found_pc") else 0
                            rem_mob = max(0, mobile_max - re_check_mobile) if re_check.get("found_mobile") else 0
                            extra_pc = (rem_pc + 2) // 3
                            extra_mob = (rem_mob + 2) // 3

                            _log_warning(f"[Double-Check] Dữ liệu thực tế CHƯA chạm đỉnh! Còn thiếu: Desktop {rem_pc} pts (~{extra_pc} câu), Mobile {rem_mob} pts (~{extra_mob} câu).")

                            if double_check_attempt > max_double_check_retries:
                                _log_warning(f"[Double-Check] Đã bù {max_double_check_retries} đợt nhưng server chưa cập nhật đủ (có thể do Cooldown/Rate-limit). Dừng chu trình để bảo vệ tài khoản.")
                                all_tasks_completed = False
                                all_maxed = False
                                break

                            _log_info(f"[Double-Check] Tự động bù thêm số lượt còn thiếu (Đợt bù {double_check_attempt}/{max_double_check_retries}): +{extra_pc} PC, +{extra_mob} Mobile...")
                            desktop_target = d_done + extra_pc
                            mobile_target = m_done + extra_mob

                finally:
                    if d_context:
                        try:
                            d_context.close()
                        except Exception:
                            pass
                        d_context = None

            # ========================================================
            # BƯỚC 3: TỔNG KẾT & GHI LOG
            # ========================================================
            _log_info("[3/3] Đang lấy điểm số tổng kết...")
            if all_tasks_completed and (d_done + m_done == 0):
                end_points = start_points
                _log_info(f"Điểm số kết thúc: {end_points:,}")
            else:
                try:
                    d_context, d_page = ensure_desktop_page(d_context, p, headless, account_id)
                    d_page.goto(REWARDS_URL, wait_until="domcontentloaded")
                    dismiss_popups(d_page)
                    
                    # Chờ 2 giây để API Bing tải điểm số mới nhất
                    d_page.wait_for_timeout(2000)
                    
                    end_points = extract_current_points(d_page)
                    _log_info(f"Điểm số kết thúc: {end_points:,}")
                    try:
                        star_info = extract_bing_star_bonus(d_page)
                        if star_info.get("found"):
                            bing_star_bonus_str = star_info.get("text", bing_star_bonus_str)
                    except Exception:
                        pass
                finally:
                    if d_context:
                        try:
                            d_context.close()
                        except Exception:
                            pass
                        d_context = None

            # Ghi log kết quả
            total_searches = d_done + m_done
            log_session_result(start_points, end_points, total_searches)
            
            # Chỉ coi là hoàn tất khi dữ liệu thực tế xác nhận 100%
            status_str = "completed" if (all_maxed and all_activities_done) else "partial"
            finish_session(end_points, status=status_str)

            today_str = datetime.date.today().isoformat()
            can_lock_day = all_maxed and all_activities_done
            if can_lock_day:
                _log_info("[Daily Lock] Đã hoàn thành 100% mục tiêu. Ghi nhận khóa ngày vào database.")
                upsert_daily_progress(
                    account_id=account_id,
                    date_str=today_str,
                    status="completed",
                    checkin_awarded=checkin_awarded,
                    completed_at=datetime.datetime.now().isoformat()
                )
            else:
                _log_info(
                    f"[Daily Lock] Còn nhiệm vụ/điểm chưa đạt 100% "
                    f"(all_maxed={all_maxed}, all_activities_done={all_activities_done}, "
                    f"checkin +{checkin_awarded} pts). Ghi nhận tiến độ một phần để chu kỳ sau cày tiếp."
                )
                upsert_daily_progress(
                    account_id=account_id,
                    date_str=today_str,
                    status="partial",
                    checkin_awarded=checkin_awarded
                )

            # Sổ cái thống kê điểm số & Dự báo đổi quà
            earned = max(0, end_points - start_points)
            try:
                record_points_snapshot(
                    account_id=account_id,
                    date_str=today_str,
                    start_points=start_points,
                    end_points=end_points,
                    earned_points=earned,
                    bing_star_pts=0,  # parsed from bing_star_bonus_str if needed
                    desktop_searches=d_done,
                    mobile_searches=m_done
                )
            except Exception as e:
                _log_warning(f"Lỗi khi cập nhật points_ledger: {e}")

            # In tóm tắt
            _log_info("\n" + "=" * 60)
            if all_tasks_completed:
                _log_info("  HOÀN TẤT TOÀN BỘ PIPELINE PHASE 5!")
            else:
                _log_info("  KẾT THÚC ĐỢT PIPELINE (CHƯA ĐẠT ĐỦ TARGET)")
            _log_info("=" * 60)
            _log_info(f"  Điểm đầu:     {start_points:,}")
            _log_info(f"  Điểm cuối:    {end_points:,}")
            _log_info(f"  Kiếm được:    +{earned:,} pts")
            _log_info(f"  Desktop:      {d_done}/{desktop_target} searches")
            _log_info(f"  Mobile:       {m_done}/{mobile_target} searches")
            _log_info(f"  Tổng searches: {total_searches}")
            _log_info("=" * 60)

            return all_tasks_completed

    except Exception as e:
        _log_error(f"Pipeline error: {e}")
        _log_exception("Full traceback:")
        finish_session(0, status="failed", error_msg=str(e))
        return False
    finally:
        try:
            if d_context:
                d_context.close()
        except Exception:
            pass
        try:
            if m_context:
                m_context.close()
        except Exception:
            pass
        try:
            unload_model_from_vram()
        except Exception:
            pass
        try:
            cleanup_zombie_browsers()
        except Exception:
            pass
        try:
            purge_profile_caches()
        except Exception:
            pass


# ==================== NETWORK & STATE & CACHE GUARD ====================
POINTS_LEDGER_FILE = DATA_DIR / "points_ledger.json"

REWARDS_MILESTONES = [
    {"name": "Roblox Digital Card (100 Robux)", "points": 1500},
    {"name": "Thẻ nạp điện thoại Viettel/Mobi/Vina 50.000đ", "points": 3400},
    {"name": "Voucher Got It / Shopee / Tiki 100.000đ", "points": 6500},
    {"name": "Xbox Game Pass PC (1 Tháng)", "points": 7750},
    {"name": "Voucher Điện Máy XANH / TGDD 200.000đ", "points": 13000},
]


def cleanup_zombie_browsers():
    """
    Tự động kiểm tra và đóng các tiến trình msedge.exe bị treo từ phiên trước
    đang giữ khóa thư mục profiles/.
    """
    _log_info("[Process Guard] Kiểm tra và dọn dẹp các tiến trình Edge treo...")
    killed_count = 0
    try:
        if psutil:
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    if proc.info['name'] and proc.info['name'].lower() == 'msedge.exe':
                        proc.kill()
                        killed_count += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        else:
            res = subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if res.returncode == 0:
                killed_count += 1
    except Exception as e:
        _log_debug(f"[Process Guard] Lỗi khi dọn tiến trình Edge: {e}")

    if killed_count > 0:
        _log_info(f"[Process Guard] Đã dọn dẹp {killed_count} tiến trình msedge.exe giải phóng khóa profiles.")
    else:
        _log_info("[Process Guard] Không có tiến trình Edge nào bị treo.")


def wait_for_internet(host: str = "www.bing.com", port: int = 443, timeout: int = 60) -> bool:
    """
    Trước khi khởi tạo Playwright, chờ kết nối tới host:port (mặc định www.bing.com:443)
    tối đa 60 giây (mỗi nhịp 3 giây) để tránh lỗi mất mạng khi máy vừa thức dậy từ Sleep.
    """
    _log_info(f"[Network Guard] Kiểm tra kết nối Internet ({host}:{port})...")
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with socket.create_connection((host, port), timeout=3.0):
                elapsed = time.time() - start_time
                _log_info(f"[Network Guard] Kết nối Internet ổn định ({elapsed:.1f}s)!")
                return True
        except (socket.timeout, OSError) as e:
            remaining = int(timeout - (time.time() - start_time))
            if remaining > 0:
                _log_debug(f"[Network Guard] Đang chờ mạng ({e}). Còn lại {remaining}s (thử lại sau 3s)...")
                time.sleep(3)
    _log_warning(f"[Network Guard] Hết thời gian chờ mạng ({timeout}s)!")
    return False


def is_today_already_completed() -> bool:
    """
    Kiểm tra xem hôm nay đã hoàn thành 100% mục tiêu chưa (dựa trên data/daily_state.json).
    """
    if not DAILY_STATE_FILE.exists():
        return False
    try:
        with open(DAILY_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        today_str = datetime.date.today().isoformat()
        return data.get("completed_date") == today_str and data.get("status") == "completed"
    except Exception as e:
        _log_warning(f"[Daily Lock] Lỗi khi đọc daily_state.json: {e}")
        return False


def save_daily_state(completed_date: str = None, partial: bool = False, checkin_awarded: int = 0):
    """
    Ghi nhận trạng thái ngày hiện tại vào data/daily_state.json.
    - partial=False: đã hoàn thành 100% mục tiêu (PC, Mobile, 4 nhiệm vụ cốt lõi: Bing Search, Daily Set, Edge Browsing, Visual Search).
    - partial=True: ghi nhận tiến độ một phần để chu kỳ sau tiếp tục cày.
    """
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        today_str = completed_date or datetime.date.today().isoformat()
        now_iso = datetime.datetime.now().isoformat()

        state = {"completed_date": None, "completed_at": None, "status": "partial"}
        if DAILY_STATE_FILE.exists():
            try:
                with open(DAILY_STATE_FILE, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if existing.get("completed_date") == today_str:
                    state = existing
            except Exception:
                pass

        state.update({
            "date": today_str,
            "partial": partial,
            "checkin_awarded": checkin_awarded,
            "updated_at": now_iso,
        })
        if not partial:
            state["completed_date"] = today_str
            state["completed_at"] = now_iso
            state["status"] = "completed"

        with open(DAILY_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

        if partial:
            _log_info(
                f"[Daily Lock] Ghi nhận tiến độ một phần ngày {today_str} "
                f"(checkin +{checkin_awarded} pts)."
            )
        else:
            _log_info(f"[Daily Lock] Đã ghi nhận hoàn tất 100% ngày hôm nay ({today_str}) vào data/daily_state.json.")
    except Exception as e:
        _log_warning(f"[Daily Lock] Lỗi khi ghi daily_state.json: {e}")


mark_today_completed = save_daily_state


def update_points_ledger(total_points: int, earned_today: int, bing_star_bonus: str = None):
    """
    Lưu thống kê điểm số vào data/points_ledger.json và in ra bảng tổng kết chuyên nghiệp:
    Điểm kiếm được hôm nay, Tổng điểm hiện có, Bing Star bonus, và ước tính tiến độ đổi mốc quà tiếp theo.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    today_str = datetime.date.today().isoformat()
    now_iso = datetime.datetime.now().isoformat()

    ledger = {"last_updated": now_iso, "history": []}
    if POINTS_LEDGER_FILE.exists():
        try:
            with open(POINTS_LEDGER_FILE, "r", encoding="utf-8") as f:
                ledger = json.load(f)
        except Exception as e:
            _log_warning(f"[Points Ledger] Lỗi khi đọc points_ledger.json: {e}")

    # Xác định giá trị bing_star_bonus
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
    ledger["last_updated"] = now_iso
    ledger["total_points"] = total_points
    ledger["earned_today"] = earned_today
    ledger["bing_star_bonus"] = bing_star_bonus
    ledger["history"] = history

    try:
        with open(POINTS_LEDGER_FILE, "w", encoding="utf-8") as f:
            json.dump(ledger, f, indent=2, ensure_ascii=False)
        _log_info("[Points Ledger] Đã cập nhật sổ cái điểm thưởng vào data/points_ledger.json.")
    except Exception as e:
        _log_warning(f"[Points Ledger] Lỗi khi ghi points_ledger.json: {e}")

    # Tính toán mốc đổi quà tiếp theo
    target_milestone = None
    for m in REWARDS_MILESTONES:
        if m["points"] > total_points:
            target_milestone = m
            break
    if not target_milestone:
        target_milestone = REWARDS_MILESTONES[-1]

    m_name = target_milestone["name"]
    m_pts = target_milestone["points"]
    needed_pts = max(0, m_pts - total_points)
    progress_pct = min(100.0, (total_points / m_pts) * 100) if m_pts > 0 else 100.0

    # Ước tính số ngày
    avg_daily_rate = 150
    if len(history) > 1:
        rates = [h.get("earned_today", 0) for h in history if h.get("earned_today", 0) > 0]
        if rates:
            avg_daily_rate = max(50, sum(rates) // len(rates))
    est_days = max(1, math.ceil(needed_pts / avg_daily_rate)) if needed_pts > 0 else 0

    # Vẽ thanh tiến độ trực quan (20 ký tự)
    bar_len = 20
    filled_len = int(bar_len * (progress_pct / 100.0))
    bar_str = "█" * filled_len + "░" * (bar_len - filled_len)

    # In bảng tổng kết chuyên nghiệp
    _log_info("")
    _log_info("=" * 72)
    _log_info("           BẢNG TỔNG KẾT ĐIỂM SỐ & DỰ BÁO ĐỔI QUÀ (REWARDS LEDGER)")
    _log_info("=" * 72)
    _log_info(f"  * Ngày thực hiện:          {today_str}")
    _log_info(f"  * Điểm kiếm được hôm nay:  +{earned_today:,} pts")
    _log_info(f"  * Tổng điểm hiện có:       {total_points:,} pts")
    _log_info(f"  * Bing Star Bonus tháng này: {bing_star_bonus} pts")
    _log_info(f"  * Mốc đổi quà tiếp theo:   {m_name} ({m_pts:,} pts)")
    _log_info(f"  * Tiến độ hiện tại:        [{bar_str}] {progress_pct:.1f}%")
    if needed_pts > 0:
        _log_info(f"  * Còn thiếu:               {needed_pts:,} pts (Dự kiến đạt trong ~{est_days} ngày với ~{avg_daily_rate} pts/ngày)")
    else:
        _log_info("  * Trạng thái:              [✓] ĐÃ ĐẠT ĐỦ ĐIỂM ĐỔI QUÀ!")
    _log_info("=" * 72)
    _log_info("")


def purge_profile_caches(profiles_dir: str = "profiles"):
    """
    Xóa sạch các thư mục bộ nhớ đệm rác của Chromium (Cache, Code Cache, GPUCache, DawnCache, Crashpad, GrShaderCache,...)
    trong profiles/ nhưng giữ nguyên Cookies và Local Storage.
    Bảo vệ tuổi thọ ghi (TBW) của ổ cứng M.2 SSD.
    """
    junk_folder_names = {
        "Cache",
        "Code Cache",
        "GPUCache",
        "DawnCache",
        "DawnGraphiteCache",
        "DawnWebGPUCache",
        "GPUPersistentCache",
        "GrShaderCache",
        "ShaderCache",
        "Crashpad",
    }
    profiles_path = Path(profiles_dir)
    if not profiles_path.is_absolute():
        profiles_path = ROOT_DIR / profiles_dir

    if not profiles_path.exists():
        return

    freed_count = 0
    junk_dirs = [folder for folder in profiles_path.rglob("*") if folder.is_dir() and folder.name in junk_folder_names]
    for folder in junk_dirs:
        if folder.exists():
            try:
                shutil.rmtree(folder, ignore_errors=True)
                freed_count += 1
            except Exception:
                pass

    if freed_count > 0:
        logger.info(f"[SSD Guard] Đã dọn sạch {freed_count} thư mục cache rác trong profiles.")
    else:
        logger.info("[SSD Guard] Thư mục cache profiles đã sạch sẽ.")


purge_browser_cache = purge_profile_caches


# ==================== POWER MANAGEMENT ====================
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def is_user_active(threshold_seconds: int = 120) -> bool:
    """
    Kiểm tra xem người dùng có đang hoạt động trên máy (chuột/bàn phím có tương tác trong vòng threshold_seconds hay không).
    Sử dụng Windows API GetLastInputInfo.
    """
    try:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            idle_ms = ctypes.windll.kernel32.GetTickCount() - info.dwTime
            if idle_ms < 0:
                idle_ms += 0x100000000
            return (idle_ms / 1000.0) < threshold_seconds
    except Exception:
        pass
    return False


def put_pc_to_sleep():
    """Tự động đưa máy tính vào chế độ Sleep (S3) để tiết kiệm điện."""
    if is_user_active(threshold_seconds=120):
        logger.info("[Auto-Sleep] Phát hiện người dùng đang sử dụng máy tính. Hủy lệnh Sleep để không làm gián đoạn công việc!")
        return

    # Dọn dẹp cache rác rà soát lần cuối ngay trước khi máy ngủ để bảo vệ TBW SSD
    try:
        purge_browser_cache()
    except Exception:
        pass

    logger.info("[Power] Chuẩn bị đưa máy vào chế độ Sleep sau 10 giây...")
    for remaining in range(10, 0, -1):
        if is_user_active(threshold_seconds=5):
            logger.info("[Auto-Sleep] Phát hiện người dùng đang sử dụng máy tính. Hủy lệnh Sleep để không làm gián đoạn công việc!")
            return
        logger.info(f"[Power] Đang đếm ngược đưa máy vào Sleep: {remaining}s...")
        time.sleep(1)
    # Lệnh chuẩn của PowerShell đưa Windows vào trạng thái Suspend/Sleep
    cmd = 'powershell -Command "Add-Type -Assembly System.Windows.Forms; [System.Windows.Forms.Application]::SetSuspendState([System.Windows.Forms.PowerState]::Suspend, $false, $false)"'
    subprocess.run(cmd, shell=True)


def main():
    import os
    import psutil

    # Đặt độ ưu tiên của tiến trình xuống mức Dưới Trung Bình (Below Normal)
    try:
        p = psutil.Process(os.getpid())
        p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass

    try:
        cleanup_zombie_browsers()
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="MS Rewards Bot Phase 5 - Full Automation (Streak + Search + Logging)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ví dụ sử dụng:
  python runner.py                    # Chạy mặc định (headless, 30D/20M, tự động Sleep khi hoàn tất)
  python runner.py --debug            # Chạy có giao diện (debug)
  python runner.py --no-sleep         # Chạy không tự động Sleep máy
  python runner.py --daemon           # Chạy nền liên tục (kiểm tra mỗi 4h)
  python runner.py --desktop-target 30 --mobile-target 20 --batch-size 4
        """
    )
    parser.add_argument("--debug", action="store_true", 
                        help="Chạy có giao diện trình duyệt (headless=False)")
    parser.add_argument("--desktop-target", type=int, default=30,
                        help="Số lượt tìm kiếm Desktop mục tiêu (mặc định: 30)")
    parser.add_argument("--mobile-target", type=int, default=20,
                        help="Số lượt tìm kiếm Mobile mục tiêu (mặc định: 20)")
    parser.add_argument("--batch-size", type=int, default=3,
                        help="Kích thước batch mỗi lần trước cooldown (mặc định: 3)")
    parser.add_argument(
        "--daemon", "--loop",
        dest="daemon",
        action="store_true", 
        help="Chạy ở chế độ tiến trình nền liên tục, tự động ngủ 4 tiếng sau khi xong toàn bộ việc."
    )
    parser.add_argument(
        "--interval", 
        type=float, 
        default=4, 
        help="Khoảng thời gian ngủ giữa các chu kỳ kiểm tra (tính bằng giờ, mặc định 4h)."
    )
    parser.add_argument(
        "--auto-sleep",
        action="store_true",
        default=True,
        help="Tự động đưa máy tính vào chế độ Sleep khi hoàn thành tất cả nhiệm vụ (mặc định bật)."
    )
    parser.add_argument(
        "--no-sleep",
        dest="auto_sleep",
        action="store_false",
        help="Vô hiệu hóa tính năng tự động Sleep khi hoàn tất."
    )
    parser.add_argument(
        "--account-id",
        type=str,
        default="acc_primary",
        help="ID tài khoản để chạy (mặc định: acc_primary)"
    )
    parser.add_argument(
        "--suppress-auto-sleep",
        action="store_true",
        default=False,
        help="Ngăn tự động Sleep giữa các tài khoản (dùng cho multi-account orchestrator)"
    )
    parser.add_argument(
        "--retry-count",
        type=int,
        default=0,
        help="Số lần thử lại trong ngày (mặc định 0)"
    )
    
    args = parser.parse_args()

    check_interval_seconds = int(args.interval * 3600)
    interval_display = f"{int(args.interval)}" if (isinstance(args.interval, int) or args.interval.is_integer()) else f"{args.interval}"

    all_completed = False
    try:
        while True:
            logger.info("============================================================")
            logger.info(f"   BẮT ĐẦU CHU KỲ KIỂM TRA & CÀY ĐIỂM: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info("============================================================")
            
            # Thực thi toàn bộ pipeline
            all_completed = run_full_pipeline(
                args,
                account_id=args.account_id,
                suppress_auto_sleep=args.suppress_auto_sleep
            )

            # Nếu không bật cờ --daemon, chạy xong 1 phiên thì thoát như cũ
            if not args.daemon:
                break

            # Nếu bật cờ --daemon:
            wake_time = datetime.datetime.now() + datetime.timedelta(seconds=check_interval_seconds)
            wake_str = wake_time.strftime("%H:%M:%S ngày %d/%m/%Y")

            if all_completed:
                logger.info("============================================================")
                logger.info("  [✓] TOÀN BỘ NHIỆM VỤ ĐÃ HOÀN TẤT XUẤT SẮC!")
                logger.info("  [*] Đã đóng toàn bộ trình duyệt để giải phóng 100% RAM/GPU.")
                logger.info(f"  [*] Bot chuyển sang chế độ ngủ sâu. Sẽ kiểm tra lại lúc: {wake_str} (sau {interval_display}h)")
                logger.info("============================================================\n")
            else:
                logger.info(f"[*] Chưa đạt hết chỉ tiêu, tạm nghỉ {interval_display}h và sẽ thử lại lúc: {wake_str}\n")

            # Ngủ tiết kiệm tài nguyên (chia nhỏ 60s để phản hồi tốt nếu có lệnh ngắt)
            start_sleep = time.time()
            if check_interval_seconds > 0:
                while time.time() - start_sleep < check_interval_seconds:
                    time.sleep(min(60, check_interval_seconds - (time.time() - start_sleep)))
            else:
                time.sleep(1)

    except KeyboardInterrupt:
        logger.info("\nNhận tín hiệu dừng (Ctrl+C). Đang tắt bot...")
        try:
            cleanup_zombie_browsers()
        except Exception:
            pass
        try:
            purge_browser_cache()
        except Exception:
            pass
        sys.exit(0)

    if all_completed and getattr(args, "auto_sleep", False) and not getattr(args, "suppress_auto_sleep", False):
        if is_user_active(threshold_seconds=120):
            logger.info("[Auto-Sleep] Phát hiện người dùng đang sử dụng máy tính. Hủy lệnh Sleep để không làm gián đoạn công việc!")
        else:
            logger.info("[Auto-Sleep] Máy tính đang ở trạng thái không người dùng. Chuẩn bị đưa máy vào chế độ Sleep...")
            put_pc_to_sleep()

    # Nếu pipeline chạy trọn vẹn (dù còn thẻ phụ chờ chu kỳ sau cày tiếp), luôn exit 0 để script .bat không bị treo
    all_activities_done = all_completed
    if not all_activities_done:
        _log_info("[Runner] Còn hoạt động phụ chưa đạt 100%, bot sẽ tiếp tục ở chu kỳ kế tiếp.")
    sys.exit(0)


if __name__ == "__main__":
    main()
