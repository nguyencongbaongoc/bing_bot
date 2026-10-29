#!/usr/bin/env python3
"""
Unified Orchestrator - Phase 5 Full Automation Pipeline.
Quản lý toàn bộ quy trình: Check-in/Activities -> Search Loop (Desktop+Mobile Cooldown) -> Logging.
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
)
from core.searcher import human_type_and_search
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

# ==================== CONFIGURATION ====================
ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
DAILY_STATE_FILE = DATA_DIR / "daily_state.json"
DESKTOP_PROFILE = ROOT_DIR / "profiles" / "desktop"
MOBILE_PROFILE = ROOT_DIR / "profiles" / "mobile"
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


def get_sync_playwright():
    """Import Playwright only when the browser pipeline is started."""
    try:
        return importlib.import_module("playwright.sync_api").sync_playwright
    except ModuleNotFoundError as exc:
        if exc.name == "playwright" or (exc.name and exc.name.startswith("playwright.")):
            raise RuntimeError(
                "Playwright is required. Install it with 'pip install playwright'."
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
    """
    logger.info(f"[Edge Browsing] Bắt đầu phiên duyệt web ngầm tích lũy thời gian ({total_seconds // 60} phút)...")
    
    # Điều hướng tới trang tin tức nhẹ
    try:
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
            
    logger.info("[Edge Browsing] Hoàn thành đợt tích lũy thời gian Cooldown.")


def apply_stealth_scripts(context, is_mobile: bool = False):
    """Tiêm script xóa dấu vết tự động hóa của Playwright/CDP và đồng bộ User-Agent Client Hints, hardwareConcurrency, deviceMemory."""
    platform_val = "Linux armv8l" if is_mobile else "Win32"
    ua_platform = "Android" if is_mobile else "Windows"
    is_mob_js = "true" if is_mobile else "false"
    model_val = "Pixel 7" if is_mobile else ""
    platform_version = "14.0.0" if is_mobile else "15.0.0"
    arch_val = "arm" if is_mobile else "x86"
    hw_concurrency = 8 if is_mobile else 12
    device_memory = 8

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


def create_desktop_context(playwright, headless: bool):
    """Tạo Desktop persistent context với chế độ ngụy trang (Stealth)."""
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
        user_data_dir=str(DESKTOP_PROFILE),
        channel="msedge",
        headless=headless,
        args=args,
        no_viewport=not headless,
        locale="vi-VN",
        timezone_id="Asia/Ho_Chi_Minh",
        extra_http_headers=extra_headers,
    )
    apply_stealth_scripts(context, is_mobile=False)
    return context


def create_mobile_context(playwright, headless: bool):
    """Tạo Mobile emulation persistent context giả lập Android (Pixel 7)."""
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
        user_data_dir=str(MOBILE_PROFILE),
        channel="msedge",
        headless=headless,
        args=args,
        **mobile_device,
        locale="vi-VN",
        timezone_id="Asia/Ho_Chi_Minh",
        extra_http_headers=extra_headers,
    )
    apply_stealth_scripts(context, is_mobile=True)
    return context


# ==================== MAIN ORCHESTRATOR ====================
def run_full_pipeline(
    args=None,
    desktop_target: int = 30,
    mobile_target: int = 20,
    batch_size: int = 3,
    headless: bool = True,
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

    # Kiểm tra cờ chạy tự động vs tùy biến
    is_debug = bool(args and getattr(args, "debug", False))
    has_custom_target = bool(args and (
        (getattr(args, 'desktop_target', 30) != 30) or 
        (getattr(args, 'mobile_target', 20) != 20) or
        any(arg.startswith("--desktop-target") or arg.startswith("--mobile-target") for arg in sys.argv)
    ))

    # Zero-Browser Daily Lock: Nếu chạy tự động không debug/custom target và hôm nay đã hoàn thành 100%
    if not is_debug and not has_custom_target and is_today_already_completed():
        _log_info("[Daily Lock] Hôm nay đã hoàn thành 100% mục tiêu điểm. Thoát sớm để bảo vệ SSD & tài khoản!")
        return True

    try:
        cleanup_zombie_browsers()
    except Exception:
        pass

    _log_info("=" * 60)
    _log_info("  Microsoft Rewards Bot - Phase 5 Full Automation")
    _log_info("=" * 60)
    _log_info(f"Config: Desktop={desktop_target}, Mobile={mobile_target}, Batch={batch_size}")
    _log_info(f"Headless: {headless}")
    _log_info("")

    # Trễ khởi động ngẫu nhiên (Startup Jitter) khi chạy tự động ngầm để làm lệch mốc giờ máy móc
    if args and not getattr(args, "debug", False):
        jitter_seconds = random.randint(30, 90)
        _log_info(f"[Stealth] Đợi ngẫu nhiên {jitter_seconds} giây để làm lệch mốc giờ cố định (Startup Jitter)...")
        time.sleep(jitter_seconds)

    # Kiểm tra profile tồn tại
    if not DESKTOP_PROFILE.exists():
        _log_error(f"Desktop profile không tồn tại: {DESKTOP_PROFILE}")
        _log_info("   Chạy: python setup_login.py --desktop")
        return False
    
    if not MOBILE_PROFILE.exists():
        _log_error(f"Mobile profile không tồn tại: {MOBILE_PROFILE}")
        _log_info("   Chạy: python setup_login.py --mobile")
        return False

    # Chuẩn bị danh sách từ khóa độc nhất (lọc qua data/searched_history.json & đa dạng đời sống)
    total_needed = desktop_target + mobile_target + 6
    _log_info(f"Đang chuẩn bị {total_needed} từ khóa độc nhất (lọc trùng qua lịch sử 200 câu gần nhất)...")
    queries = get_unique_search_queries(target_count=total_needed, geo="VN")
    random.shuffle(queries)  # Xáo trộn để không theo thứ tự cố định
    
    _log_info(f"Đã chuẩn bị {len(queries)} từ khóa độc nhất không trùng lặp.")
    _log_info("")

    # Biến đếm
    d_done = 0
    m_done = 0
    start_points = 0
    end_points = 0
    queries_idx = 0

    d_context = None
    m_context = None
    all_tasks_completed = False

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
                d_context = create_desktop_context(p, headless)
                d_page = d_context.pages[0] if d_context.pages else d_context.new_page()
                d_page.set_default_timeout(DEFAULT_TIMEOUT)

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

                # KIỂM TRA TIẾN ĐỘ THỰC TẾ TRÊN DASHBOARD / POINTS BREAKDOWN NGAY TỪ ĐẦU
                initial_progress = get_daily_search_progress(d_page)
                is_explicit_test = bool(args and getattr(args, "desktop_target", None) == 0 and getattr(args, "mobile_target", None) == 0)
                is_test_target = bool(args and ((getattr(args, 'desktop_target', 30) not in (0, 30)) or (getattr(args, 'mobile_target', 20) not in (0, 20))))
                if initial_progress.get("all_maxed") and initial_progress.get("all_activities_done", False) and not is_explicit_test and not is_test_target:
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
                        solve_mobile_app_daily_checkin(d_page)
                        sleep_time = random.uniform(3.0, 6.0)
                        _log_debug(f"[App Check-in] Nghỉ {sleep_time:.1f}s trước khi chuyển sang Read to Earn...")
                        time.sleep(sleep_time)
                    except Exception as e:
                        _log_warning(f"Lỗi khi xử lý Mobile App Daily Check-in: {e}")

                    # Quét App Promotions (GET /dapi/me) để thu thập điểm ẩn
                    try:
                        solve_mobile_app_promotions(d_page)
                    except Exception as e:
                        _log_warning(f"Lỗi khi quét Mobile App Promotions: {e}")

                    # Thực hiện nhiệm vụ Read to Earn qua Mobile App API (+30 điểm/ngày)
                    try:
                        solve_mobile_app_read_to_earn(d_page)
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

                    # Kiểm tra lại tiến độ sau khi hoàn thành Activities
                    post_progress = get_daily_search_progress(d_page)
                    if post_progress.get("all_maxed"):
                        if is_test_target:
                            _log_info(f"[Test Mode] Điểm hôm nay đã đạt tối đa, nhưng tiếp tục chạy lượt tìm kiếm thử nghiệm: Desktop={desktop_target}, Mobile={mobile_target}")
                        else:
                            _log_info("[✓] Điểm tìm kiếm PC và Mobile hôm nay đã đạt tối đa! Không cần tìm kiếm thêm.")
                            desktop_target = 0
                            mobile_target = 0
                            all_tasks_completed = True
                    else:
                        desktop_needed = max(0, (post_progress["pc_max"] - post_progress["pc_cur"]) // 3)
                        mobile_needed = max(0, (post_progress["mobile_max"] - post_progress["mobile_cur"]) // 3)
                        orig_d = desktop_target
                        orig_m = mobile_target
                        if not is_test_target:
                            desktop_target = min(orig_d, desktop_needed)
                            mobile_target = min(orig_m, mobile_needed)
                        _log_info(f"[Target Updated] Số lượt cần tìm thực tế: Desktop={desktop_target} (còn thiếu {post_progress['pc_max'] - post_progress['pc_cur']} pts) | Mobile={mobile_target} (còn thiếu {post_progress['mobile_max'] - post_progress['mobile_cur']} pts)")
                        if desktop_target == 0 and mobile_target == 0 and not is_explicit_test and not is_test_target:
                            _log_info("[✓] Điểm tìm kiếm PC và Mobile hôm nay đã đạt tối đa! Không cần tìm kiếm thêm.")
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
            # BƯỚC 1.5: MOBILE CONTEXT - CHECK-IN
            # ========================================================
            if not all_tasks_completed or is_explicit_test:
                _log_info("Khởi chạy Mobile Context để xử lý Mobile App Check-in...")
                try:
                    m_context = create_mobile_context(p, headless)
                    m_page = m_context.pages[0] if m_context.pages else m_context.new_page()
                    m_page.set_default_timeout(DEFAULT_TIMEOUT)

                    try:
                        solve_mobile_checkin(m_page)
                        claim_all_available_rewards(m_page)
                        update_session(0, None, "Mobile check-in completed")
                    except Exception as e:
                        _log_warning(f"Bỏ qua điểm danh Mobile: {e}")

                except Exception as e:
                    _log_error(f"Lỗi khi mở Mobile Context cho check-in: {e}")
                finally:
                    if m_context:
                        try:
                            m_context.close()
                            _log_info("Đã đóng Mobile context sau Mobile Check-in.")
                        except Exception:
                            _log_info("Mobile context đã tự đóng trước đó.")
                        m_context = None

            # ========================================================
            # BƯỚC 2: VÒNG LẶP XEN KẼ TÌM KIẾM (COOLDOWN PIPELINE)
            # ========================================================
            if (all_tasks_completed and not is_test_target) or (desktop_target == 0 and mobile_target == 0):
                _log_info("[2/3] Bỏ qua hoàn toàn chu trình tìm kiếm chống Cooldown do toàn bộ điểm đã đạt tối đa.")
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
                    d_context = create_desktop_context(p, headless)
                    d_page = d_context.pages[0] if d_context.pages else d_context.new_page()
                    d_page.set_default_timeout(DEFAULT_TIMEOUT)

                    while d_done < desktop_target or m_done < mobile_target:
                        batch_completed = 0
                        pc_searched_round = 0
                        mob_searched_round = 0

                        # Đảm bảo d_page vẫn hoạt động
                        if d_page is None or d_page.is_closed():
                            d_page = d_context.new_page()
                            d_page.set_default_timeout(DEFAULT_TIMEOUT)

                        # ---- 2.1 DESKTOP BATCH ----
                        if d_done < desktop_target:
                            # Biến thiên kích thước batch tự nhiên 2-4 câu thay vì cố định 3 câu
                            current_d_batch = random.randint(2, 4) if (args is None or getattr(args, "batch_size", 3) == 3) else batch_size
                            batch_limit = min(current_d_batch, desktop_target - d_done)
                            for i in range(batch_limit):
                                if queries_idx >= len(queries):
                                    more_q = get_unique_search_queries(target_count=10, geo="VN")
                                    queries.extend(more_q)
                                q = queries[queries_idx]
                                queries_idx += 1
                                
                                _log_info(f"[PC Search {d_done + 1}/{desktop_target}] {q[:50]}...")
                                
                                # 3 lượt đầu tiên dùng Sidebar Search (form=PRSB01), sau đó dùng Desktop thông thường
                                search_type = "sidebar" if d_done < 3 else "desktop"
                                if execute_single_search(d_page, q, device_type=search_type):
                                    d_done += 1
                                    batch_completed += 1
                                    pc_searched_round += 1
                                    save_searched_keyword(q)
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

                                    # Cluster Search (~30% xác suất nếu còn lượt tìm kiếm kế tiếp trong batch)
                                    if d_done < desktop_target and random.random() < 0.30:
                                        try:
                                            cluster_kw = perform_related_cluster_search(d_page, is_mobile=False)
                                            if cluster_kw:
                                                d_done += 1
                                                batch_completed += 1
                                                pc_searched_round += 1
                                                save_searched_keyword(cluster_kw)
                                                _log_info(f"[Cluster Search {d_done}/{desktop_target}] Tiếp nối chuỗi tìm kiếm liên quan: '{cluster_kw}'")
                                                current_points = extract_current_points(d_page)
                                                update_session(d_done + m_done, current_points, f"Desktop cluster search {d_done}/{desktop_target}")
                                        except Exception as e:
                                            _log_debug(f"Lỗi Cluster Search PC: {e}")

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
                                m_context = create_mobile_context(p, headless)
                                m_page = m_context.pages[0] if m_context.pages else m_context.new_page()
                                m_page.set_default_timeout(DEFAULT_TIMEOUT)

                                # Biến thiên kích thước batch tự nhiên 2-4 câu
                                current_m_batch = random.randint(2, 4) if (args is None or getattr(args, "batch_size", 3) == 3) else batch_size
                                batch_limit = min(current_m_batch, mobile_target - m_done)
                                for i in range(batch_limit):
                                    if queries_idx >= len(queries):
                                        more_q = get_unique_search_queries(target_count=10, geo="VN")
                                        queries.extend(more_q)
                                    q = queries[queries_idx]
                                    queries_idx += 1
                                    
                                    _log_info(f"[Mobile Search {m_done + 1}/{mobile_target}] {q[:50]}...")
                                    if execute_single_search(m_page, q, device_type="mobile"):
                                        m_done += 1
                                        batch_completed += 1
                                        mob_searched_round += 1
                                        save_searched_keyword(q)
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

                                        # Cluster Search (~30% xác suất nếu còn lượt tìm kiếm kế tiếp trong batch)
                                        if m_done < mobile_target and random.random() < 0.30:
                                            try:
                                                cluster_kw = perform_related_cluster_search(m_page, is_mobile=True)
                                                if cluster_kw:
                                                    m_done += 1
                                                    batch_completed += 1
                                                    mob_searched_round += 1
                                                    save_searched_keyword(cluster_kw)
                                                    _log_info(f"[Mobile Cluster Search {m_done}/{mobile_target}] Tiếp nối chuỗi tìm kiếm liên quan: '{cluster_kw}'")
                                                    current_points = extract_current_points(m_page)
                                                    update_session(d_done + m_done, current_points, f"Mobile cluster search {m_done}/{mobile_target}")
                                            except Exception as e:
                                                _log_debug(f"Lỗi Cluster Search Mobile: {e}")

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
                            if d_context is None or d_page is None or d_page.is_closed():
                                try:
                                    d_context = create_desktop_context(p, headless)
                                    d_page = d_context.pages[0] if d_context.pages else d_context.new_page()
                                    d_page.set_default_timeout(DEFAULT_TIMEOUT)
                                except Exception:
                                    pass

                            active_edge_browsing_cooldown(d_page, total_seconds=cooldown)
                            _log_info("")
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
                    d_context = create_desktop_context(p, headless)
                    d_page = d_context.pages[0] if d_context.pages else d_context.new_page()
                    d_page.set_default_timeout(DEFAULT_TIMEOUT)
                    d_page.goto(REWARDS_URL, wait_until="domcontentloaded")
                    dismiss_popups(d_page)
                    
                    # Chờ 2 giây để API Bing tải điểm số mới nhất
                    d_page.wait_for_timeout(2000)
                    
                    end_points = extract_current_points(d_page)
                    _log_info(f"Điểm số kết thúc: {end_points:,}")
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
            
            all_tasks_completed = all_tasks_completed or (d_done >= desktop_target and m_done >= mobile_target)
            finish_session(end_points, status="completed" if all_tasks_completed else "partial")

            if all_tasks_completed and not is_explicit_test and not is_test_target:
                mark_today_completed()

            # Sổ cái thống kê điểm số & Dự báo đổi quà
            earned = max(0, end_points - start_points)
            try:
                update_points_ledger(total_points=end_points, earned_today=earned)
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


def mark_today_completed():
    """
    Ghi nhận ngày hôm nay đã hoàn thành 100% mục tiêu vào data/daily_state.json.
    """
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        today_str = datetime.date.today().isoformat()
        state = {
            "completed_date": today_str,
            "completed_at": datetime.datetime.now().isoformat(),
            "status": "completed",
        }
        with open(DAILY_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        _log_info(f"[Daily Lock] Đã ghi nhận hoàn tất 100% ngày hôm nay ({today_str}) vào data/daily_state.json.")
    except Exception as e:
        _log_warning(f"[Daily Lock] Lỗi khi ghi daily_state.json: {e}")


def update_points_ledger(total_points: int, earned_today: int):
    """
    Lưu thống kê điểm số vào data/points_ledger.json và in ra bảng tổng kết chuyên nghiệp:
    Điểm kiếm được hôm nay, Tổng điểm hiện có, và ước tính tiến độ đổi mốc quà tiếp theo.
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

    history = ledger.get("history", [])
    today_entry = next((item for item in history if item.get("date") == today_str), None)
    if today_entry:
        today_entry["total_points"] = total_points
        today_entry["earned_today"] = max(today_entry.get("earned_today", 0), earned_today)
        today_entry["updated_at"] = now_iso
    else:
        history.append({
            "date": today_str,
            "total_points": total_points,
            "earned_today": earned_today,
            "updated_at": now_iso,
        })
    ledger["last_updated"] = now_iso
    ledger["total_points"] = total_points
    ledger["earned_today"] = earned_today
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
            all_completed = run_full_pipeline(args)

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

    if all_completed and getattr(args, "auto_sleep", False):
        if is_user_active(threshold_seconds=120):
            logger.info("[Auto-Sleep] Phát hiện người dùng đang sử dụng máy tính. Hủy lệnh Sleep để không làm gián đoạn công việc!")
        else:
            logger.info("[Auto-Sleep] Máy tính đang ở trạng thái không người dùng. Chuẩn bị đưa máy vào chế độ Sleep...")
            put_pc_to_sleep()

    sys.exit(0 if all_completed else 1)


if __name__ == "__main__":
    main()
