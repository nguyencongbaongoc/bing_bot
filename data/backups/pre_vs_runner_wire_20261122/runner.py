#!/usr/bin/env python3
"""
Unified Orchestrator - Phase 8.6 High-Speed Precision Autonomy.
Quản lý toàn bộ quy trình: Check-in/Activities -> Search Loop (Desktop+Mobile Cooldown) -> Logging & Alerting.
"""

import argparse
import datetime
import os
import random
import sys
import time
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None

sys.path.insert(0, str(Path(__file__).parent))
from core import (
    CooldownController, Notifier, StateStore, claim_msn_daily_gaming_tile, cooldown_sleep,
    detect_unusual_activity_warning, enable_streak_protection, ensure_streak_protection,
    extract_current_points, fetch_trending_queries, get_account_paths, get_daily_search_progress,
    get_natural_cooldown_seconds, get_unique_search_queries, get_variable_batch_size,
    handle_visual_search_streak, interact_with_copilot, log_session_result,
    migrate_legacy_default_account, perform_related_cluster_search, run_all_dashboard_activities,
    save_searched_keyword, set_oauth_file, solve_all_earn_quests, solve_keep_earning_activities,
    solve_mobile_app_daily_checkin, solve_mobile_app_promotions, solve_mobile_app_read_to_earn,
    solve_promotional_links, solve_punch_cards, solve_visual_search_streak, solve_wallpaper_trivia,
    tap_corner_rewards_coin, typing_delay, unload_model_from_vram,
)
from core.activities import (
    claim_all_available_rewards,
    claim_dashboard_pending_points,
    dismiss_popups,
    extract_bing_star_bonus,
    solve_mobile_checkin,
)
from core.browser_manager import (
    BLOCKED_AD_TRACKER_DOMAINS, BROWSER_ARGS, CIRCADIAN_END_HOUR, CIRCADIAN_END_MINUTE,
    CIRCADIAN_START_HOUR, CIRCADIAN_START_MINUTE, DEFAULT_TIMEOUT, DUPLICATE_PATCHRIGHT_FLAGS,
    FIRST_PARTY_ALLOWLIST_DOMAINS, SYNTHETIC_FLAGS, SYNTHETIC_FLAG_PREFIXES,
    _cleanup_profile_locks_and_processes, acquire_runner_lock as _bm_acquire_runner_lock,
    active_edge_browsing_cooldown as _bm_edge_cooldown, apply_stealth_scripts,
    attach_resource_blocker, build_launch_args, cleanup_zombie_browsers,
    create_desktop_context as _bm_create_desktop_context,
    create_mobile_context as _bm_create_mobile_context, ensure_context_page, get_sync_playwright,
    is_sibling_runner_active, is_user_active, is_within_circadian_window, perform_warmup_visit,
    purge_profile_caches, put_pc_to_sleep, release_runner_lock, should_abort_route, wait_for_internet,
)
from core.searcher import human_type_and_search
from core.state_store import EDGE_BROWSING_CAP_SECONDS
from core.tracker import (
    _log_debug, _log_error, _log_exception, _log_info, _log_warning,
    finish_session, init_session, logger, update_session,
)

# ==================== CONFIGURATION ====================
ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
ACCOUNT_ID = "default"
DAILY_STATE_FILE = DATA_DIR / "daily_state_default.json"
POINTS_LEDGER_FILE = DATA_DIR / "points_ledger_default.json"
LOCK_FILE = DATA_DIR / "locks" / "default.lock"
DESKTOP_PROFILE = ROOT_DIR / "profiles" / "default" / "desktop"
MOBILE_PROFILE = ROOT_DIR / "profiles" / "default" / "mobile"
REWARDS_URL = "https://rewards.bing.com"
BING_URL = "https://www.bing.com"

REWARDS_MILESTONES = [
    {"name": "Roblox Digital Card (100 Robux)", "points": 1500},
    {"name": "Thẻ nạp điện thoại Viettel/Mobi/Vina 50.000đ", "points": 3400},
    {"name": "Voucher Got It / Shopee / Tiki 100.000đ", "points": 6500},
    {"name": "Xbox Game Pass PC (1 Tháng)", "points": 7750},
    {"name": "Voucher Điện Máy XANH / TGDD 200.000đ", "points": 13000},
]

purge_browser_cache = purge_profile_caches


def configure_account(account_id: str = "default") -> StateStore:
    """Configure active account paths, migrate legacy default data if needed, and return StateStore."""
    global ACCOUNT_ID, DAILY_STATE_FILE, POINTS_LEDGER_FILE, LOCK_FILE, DESKTOP_PROFILE, MOBILE_PROFILE
    clean_id = (account_id or "default").strip() or "default"
    if clean_id == "default":
        migrate_legacy_default_account(DATA_DIR, ROOT_DIR / "profiles")
    paths = get_account_paths(clean_id, DATA_DIR, ROOT_DIR / "profiles")
    ACCOUNT_ID = clean_id
    DAILY_STATE_FILE = paths["daily_state_file"]
    POINTS_LEDGER_FILE = paths["points_ledger_file"]
    LOCK_FILE = paths["lock_file"]
    DESKTOP_PROFILE = paths["desktop_profile"]
    MOBILE_PROFILE = paths["mobile_profile"]
    set_oauth_file(paths["oauth_file"])
    return _get_state_store()


def _get_state_store() -> StateStore:
    return StateStore(
        account_id=ACCOUNT_ID,
        data_dir=DATA_DIR,
        profiles_dir=ROOT_DIR / "profiles",
        daily_state_file=DAILY_STATE_FILE,
        points_ledger_file=POINTS_LEDGER_FILE,
    )


# ==================== COMPATIBILITY WRAPPERS ====================
def acquire_runner_lock(lock_path: Path = None):
    return _bm_acquire_runner_lock(lock_path or LOCK_FILE, account_id=ACCOUNT_ID)


def execute_single_search(page, query: str, device_type: str = "desktop") -> bool:
    return human_type_and_search(page, query, is_mobile=(device_type.lower() == "mobile"))


def create_desktop_context(playwright, headless: bool, account_id: str = None, profile_dir: Path = None):
    return _bm_create_desktop_context(
        playwright, headless, account_id=account_id or ACCOUNT_ID, profile_dir=profile_dir or DESKTOP_PROFILE
    )


def create_mobile_context(playwright, headless: bool, account_id: str = None, profile_dir: Path = None):
    return _bm_create_mobile_context(
        playwright, headless, account_id=account_id or ACCOUNT_ID, profile_dir=profile_dir or MOBILE_PROFILE
    )


def ensure_desktop_page(d_context, playwright_instance, headless: bool):
    return ensure_context_page(
        d_context, playwright_instance, headless, DESKTOP_PROFILE, create_desktop_context,
        "Desktop", ACCOUNT_ID, cleanup_fn=_cleanup_profile_locks_and_processes,
    )


def ensure_mobile_page(m_context, playwright_instance, headless: bool):
    return ensure_context_page(
        m_context, playwright_instance, headless, MOBILE_PROFILE, create_mobile_context,
        "Mobile", ACCOUNT_ID, cleanup_fn=_cleanup_profile_locks_and_processes,
    )


def active_edge_browsing_cooldown(page, total_seconds: int = 900, initial_seconds: int = 0):
    return _bm_edge_cooldown(
        page,
        total_seconds=total_seconds,
        initial_seconds=initial_seconds,
        update_fn=lambda s, d: update_edge_browsing_state(s, done=d),
    )


def is_today_already_completed() -> bool:
    return _get_state_store().is_today_already_completed()


def get_edge_browsing_state(today_str: str = None) -> dict:
    return _get_state_store().get_edge_browsing_state(today_str=today_str)


def update_edge_browsing_state(seconds: int, done: bool = False, today_str: str = None) -> None:
    _get_state_store().update_edge_browsing_state(seconds, done=done, today_str=today_str)


def sync_edge_browsing_from_progress(progress: dict) -> None:
    _get_state_store().sync_edge_browsing_from_progress(progress)


def save_daily_state(
    completed_date: str = None,
    partial: bool = False,
    checkin_awarded: int = 0,
    searches_completed_today: bool = None,
    core_tasks_done: bool = None,
):
    _get_state_store().save_daily_state(
        completed_date=completed_date,
        partial=partial,
        checkin_awarded=checkin_awarded,
        searches_completed_today=searches_completed_today,
        core_tasks_done=core_tasks_done,
    )


mark_today_completed = save_daily_state


def update_points_ledger(total_points: int, earned_today: int, bing_star_bonus: str = None):
    _get_state_store().update_points_ledger(
        total_points=total_points,
        earned_today=earned_today,
        bing_star_bonus=bing_star_bonus,
        milestones=REWARDS_MILESTONES,
    )


def _close_quietly(ctx) -> None:
    if ctx is not None:
        try:
            ctx.close()
        except Exception:
            pass


def _safe_call(fn, label: str):
    try:
        return fn()
    except Exception as exc:
        _log_warning(f"Lỗi {label}: {exc}")
        return None


def _is_crash_or_closed_exception(exc: Exception) -> bool:
    err_name = type(exc).__name__.lower()
    err_msg = str(exc).lower()
    return (
        "page crashed" in err_msg
        or "targetclosed" in err_name
        or "targetclosederror" in err_msg
        or "target page, context or browser has been closed" in err_msg
        or "browser has been closed" in err_msg
    )


def _execute_query_with_recovery(
    ctx,
    page,
    ensure_fn,
    playwright_instance,
    headless: bool,
    query: str,
    device_type: str,
    label: str,
):
    """Thực thi 1 truy vấn tìm kiếm; tự động phục hồi context/page và thử lại 1 lần nếu gặp Page crashed / TargetClosedError."""
    for attempt in range(2):
        ctx, page = ensure_fn(ctx, playwright_instance, headless)
        try:
            ok = execute_single_search(page, query, device_type=device_type)
            if ok:
                return ctx, page, True
            if attempt == 0 and (page is None or page.is_closed()):
                _log_warning(f"[{label}] Tab bị đóng sau tìm kiếm lỗi, khôi phục ngay và thử lại '{query[:40]}'...")
                _close_quietly(ctx)
                ctx = page = None
                continue
            return ctx, page, False
        except Exception as exc:
            if _is_crash_or_closed_exception(exc) and attempt == 0:
                _log_warning(f"[{label}] Phát hiện {type(exc).__name__} ({exc}). Khôi phục trang ngay và thử lại '{query[:40]}'...")
                _close_quietly(ctx)
                ctx = page = None
                continue
            if _is_crash_or_closed_exception(exc):
                _log_warning(f"[{label}] Thử lại sau crash thất bại ({exc}).")
                _close_quietly(ctx)
                return None, None, False
            raise
    return ctx, page, False


def _check_auth_redirect(page, notifier: Notifier) -> bool:
    try:
        url = (getattr(page, "url", "") or "").lower()
        if "login.live.com" in url or "login.microsoftonline.com" in url:
            notifier.notify_auth_expired(f"Chuyển hướng tới {url[:80]}")
            return True
    except Exception:
        pass
    return False


def _merge_primary_four_fallback(progress: dict) -> bool:
    acts = progress.get("activities", {})
    primary_four = progress.get("primary_four_done", False) or (
        acts.get("Bing Search", {}).get("done", False)
        and acts.get("Daily Set", {}).get("done", False)
        and acts.get("Edge Browsing", {}).get("done", False)
        and acts.get("Visual Search", {}).get("done", False)
    )
    if not progress.get("all_activities_done") and progress.get("all_maxed") and primary_four and not acts.get("Mobile App", {}).get("done", False):
        progress["all_activities_done"] = True
        return True
    return bool(progress.get("all_activities_done", False))


# ==================== MAIN ORCHESTRATOR ====================
def run_full_pipeline(
    args=None,
    desktop_target: int = 30,
    mobile_target: int = 20,
    batch_size: int = 3,
    headless: bool = True,
) -> bool:
    if args is not None:
        desktop_target = getattr(args, "desktop_target", desktop_target)
        mobile_target = getattr(args, "mobile_target", mobile_target)
        batch_size = getattr(args, "batch_size", batch_size)
        if hasattr(args, "debug"):
            headless = not args.debug
        if getattr(args, "account", None):
            configure_account(args.account)

    if ACCOUNT_ID == "default" and not DESKTOP_PROFILE.exists():
        migrate_legacy_default_account(DATA_DIR, ROOT_DIR / "profiles")

    store = _get_state_store()
    notifier = Notifier(account_id=ACCOUNT_ID, state_store=store)
    cooldown_ctrl = CooldownController()

    is_debug = bool(args and getattr(args, "debug", False))
    has_custom_target = bool(args and (
        desktop_target != 30 or mobile_target != 20
        or any(a.startswith("--desktop-target") or a.startswith("--mobile-target") for a in sys.argv)
    ))
    if not is_debug and not has_custom_target and (is_today_already_completed() or store.should_fast_exit_today()):
        _log_info("[Daily Guard] Mục tiêu điểm ngày hôm nay đã hoàn tất. Bỏ qua lượt chạy dư thừa.")
        return True

    _log_info(f"Config [{ACCOUNT_ID}]: Desktop={desktop_target}, Mobile={mobile_target}, Batch={batch_size} | Headless: {headless}")
    if args and not is_debug:
        jitter_seconds = random.randint(30, 90)
        _log_info(f"[Stealth] Đợi ngẫu nhiên {jitter_seconds} giây (Startup Jitter)...")
        time.sleep(jitter_seconds)

    if not DESKTOP_PROFILE.exists():
        _log_error(f"Desktop profile không tồn tại: {DESKTOP_PROFILE}")
        return False
    if not MOBILE_PROFILE.exists():
        _log_error(f"Mobile profile không tồn tại: {MOBILE_PROFILE}")
        return False

    queries = get_unique_search_queries(target_count=desktop_target + mobile_target + 6, geo="VN")
    d_done = m_done = start_points = end_points = queries_idx = checkin_awarded = 0
    bing_star_bonus_str = "0 / 2,100"
    d_context = m_context = None
    all_tasks_completed = all_maxed = all_activities_done = safe_throttle_mode = False

    try:
        if not wait_for_internet(timeout=60):
            _log_error("[Network Guard] Không có kết nối Internet sau 60s, hủy bỏ phiên chạy.")
            failures = store.record_session_outcome(False)
            notifier.notify_consecutive_failures(failures)
            return False

        with get_sync_playwright()() as p:
            _log_info("[1/3] Khởi chạy Desktop Context để xử lý Activities & Streak...")
            try:
                d_context, d_page = ensure_desktop_page(d_context, p, headless)
                perform_warmup_visit(d_page)
                d_page.goto(REWARDS_URL, wait_until="domcontentloaded")
                d_page.wait_for_timeout(2000)
                _check_auth_redirect(d_page, notifier)
                dismiss_popups(d_page)
                start_points = extract_current_points(d_page)
                _log_info(f"Điểm số ban đầu: {start_points:,}")

                try:
                    if detect_unusual_activity_warning(d_page):
                        safe_throttle_mode = True
                        notifier.notify_account_restricted("Phát hiện cảnh báo giới hạn tốc độ tìm kiếm trên Dashboard.")
                except Exception:
                    pass

                init_session(start_points)
                initial_progress = get_daily_search_progress(d_page)
                sync_edge_browsing_from_progress(initial_progress)
                bing_star_bonus_str = initial_progress.get("bing_star_bonus", {}).get("text", bing_star_bonus_str)
                verified_pc_cur, verified_pc_max = initial_progress.get("pc_cur", 0), initial_progress.get("pc_max", 90)
                verified_mob_cur, verified_mob_max = initial_progress.get("mobile_cur", 0), initial_progress.get("mobile_max", 60)
                verified_found_pc, verified_found_mob = initial_progress.get("found_pc", False), initial_progress.get("found_mobile", False)
                all_maxed = initial_progress.get("all_maxed", False)
                all_activities_done = _merge_primary_four_fallback(initial_progress)

                is_explicit_test = bool(args and getattr(args, "desktop_target", None) == 0 and getattr(args, "mobile_target", None) == 0)
                is_test_target = bool(args and ((desktop_target not in (0, 30)) or (mobile_target not in (0, 20))))
                _log_info(f"[Scan Phase 1] Desktop {verified_pc_cur}/{verified_pc_max} | Mobile {verified_mob_cur}/{verified_mob_max}")

                if all_maxed and all_activities_done and not is_explicit_test and not is_test_target:
                    desktop_target = mobile_target = 0
                    all_tasks_completed = True
                    end_points = start_points
                    store.mark_searches_completed_today(True, core_tasks_done=True)
                else:
                    for fn, label in (
                        (lambda: ensure_streak_protection(d_page), "Streak Protection"),
                        (lambda: run_all_dashboard_activities(d_context, d_page), "Dashboard Activities"),
                        (lambda: solve_visual_search_streak(d_page, state_store=store), "Visual Search Streak"),
                    ):
                        _safe_call(fn, label)
                    if store.is_mobile_checkin_done():
                        _log_info("[App Check-in] [✓] Hôm nay đã hoàn tất Daily Check-in trong StateStore, bỏ qua payload 103.")
                    else:
                        checkin_awarded = _safe_call(
                            lambda: solve_mobile_app_daily_checkin(d_page, state_store=store),
                            "Mobile App Check-in",
                        ) or 0
                        if checkin_awarded:
                            store.mark_mobile_checkin_done(True)
                            time.sleep(random.uniform(2.0, 4.0))
                    for fn, label in (
                        (lambda: solve_mobile_app_promotions(d_page), "App Promotions"),
                        (lambda: solve_mobile_app_read_to_earn(d_page), "Read to Earn"),
                    ):
                        _safe_call(fn, label)
                    for fn, label in (
                        (solve_punch_cards, "Punch Cards"),
                        (solve_all_earn_quests, "/earn Quests"),
                        (lambda pg: solve_keep_earning_activities(pg, d_context), "Keep Earning"),
                        (claim_msn_daily_gaming_tile, "Gaming Tile"),
                        (solve_wallpaper_trivia, "Wallpaper Trivia"),
                        (lambda pg: interact_with_copilot(pg, query="Tin tức hôm nay"), "Copilot"),
                        (solve_promotional_links, "Promo Links"),
                        (claim_all_available_rewards, "Final Claim"),
                    ):
                        if d_page is None or d_page.is_closed():
                            d_context, d_page = ensure_desktop_page(d_context, p, headless)
                        _safe_call(lambda f=fn, pg=d_page: f(pg), label)
                    _safe_call(unload_model_from_vram, "VRAM Unload")

                    if d_page is None or d_page.is_closed():
                        d_context, d_page = ensure_desktop_page(d_context, p, headless)
                    post_progress = get_daily_search_progress(d_page)
                    sync_edge_browsing_from_progress(post_progress)

                    if post_progress.get("scrape_error") or (not post_progress.get("found_pc") and not post_progress.get("found_mobile") and (verified_found_pc or verified_found_mob)):
                        pc_cur, pc_max, mob_cur, mob_max = verified_pc_cur, verified_pc_max, verified_mob_cur, verified_mob_max
                    else:
                        pc_cur, pc_max = post_progress.get("pc_cur", 0), post_progress.get("pc_max", 90)
                        mob_cur, mob_max = post_progress.get("mobile_cur", 0), post_progress.get("mobile_max", 60)
                        all_maxed = post_progress.get("all_maxed", False)
                        all_activities_done = _merge_primary_four_fallback(post_progress)
                        if post_progress.get("found_pc"):
                            verified_pc_cur, verified_pc_max, verified_found_pc = pc_cur, pc_max, True
                        if post_progress.get("found_mobile"):
                            verified_mob_cur, verified_mob_max, verified_found_mob = mob_cur, mob_max, True

                    if all_maxed and not is_test_target:
                        desktop_target = mobile_target = 0
                        all_tasks_completed = all_activities_done
                        store.mark_searches_completed_today(True, core_tasks_done=all_activities_done)
                    elif not is_test_target:
                        desktop_target = (max(0, pc_max - pc_cur) + 2) // 3
                        mobile_target = (max(0, mob_max - mob_cur) + 2) // 3
                        _log_info(f"[Scan Phase 2] Desktop {pc_cur}/{pc_max} (~{desktop_target} câu) | Mobile {mob_cur}/{mob_max} (~{mobile_target} câu)")
                        if desktop_target == 0 and mobile_target == 0 and not is_explicit_test:
                            all_tasks_completed = all_activities_done
                            if all_maxed:
                                store.mark_searches_completed_today(True, core_tasks_done=all_activities_done)
            finally:
                _close_quietly(d_context)
                d_context = None

            if (all_tasks_completed and not is_test_target) or (desktop_target == 0 and mobile_target == 0):
                _log_info("[2/3] Bỏ qua chu trình tìm kiếm do toàn bộ điểm đã đạt tối đa.")
                if all_maxed and all_activities_done:
                    all_tasks_completed = True
            else:
                if not is_test_target and not is_explicit_test:
                    if desktop_target >= 30 and random.random() < 0.40:
                        desktop_target += random.randint(1, 2)
                    if mobile_target >= 20 and random.random() < 0.40:
                        mobile_target += random.randint(1, 2)

                _log_info("[2/3] Bắt đầu chu trình tìm kiếm chống Cooldown...")
                d_page = None
                m_page = None
                try:
                    cooldown_ctrl.reset_burst(start_points)
                    double_check_attempt = 0

                    while True:
                        while d_done < desktop_target or m_done < mobile_target:
                            pc_searched_round = 0
                            aborted_by_cooldown = False

                            if d_done < desktop_target:
                                batch_limit = min(
                                    get_variable_batch_size(batch_size, device_type="desktop"),
                                    desktop_target - d_done,
                                )
                                for _ in range(batch_limit):
                                    if queries_idx >= len(queries):
                                        queries.extend(get_unique_search_queries(target_count=10, geo="VN"))
                                    q = queries[queries_idx]
                                    queries_idx += 1
                                    _log_info(f"[PC Search {d_done + 1}/{desktop_target}] {q[:50]}...")
                                    d_context, d_page, search_ok = _execute_query_with_recovery(
                                        d_context,
                                        d_page,
                                        ensure_desktop_page,
                                        p,
                                        headless,
                                        q,
                                        device_type=("sidebar" if d_done < 3 else "desktop"),
                                        label="PC Search",
                                    )
                                    if search_ok and d_page is not None:
                                        d_done += 1
                                        pc_searched_round += 1
                                        save_searched_keyword(q)
                                        cur_pts = extract_current_points(d_page)
                                        update_session(d_done + m_done, cur_pts, f"Desktop search {d_done}/{desktop_target}")
                                        if not safe_throttle_mode and detect_unusual_activity_warning(d_page):
                                            safe_throttle_mode = True
                                            notifier.notify_account_restricted("Cảnh báo giới hạn tìm kiếm trên Bing Desktop.")
                                        if cooldown_ctrl.record_search(cur_pts, daily_cap_reached=all_maxed):
                                            aborted_by_cooldown = True
                                            break
                                        if d_done < desktop_target:
                                            time.sleep(random.uniform(35.0, 60.0) if safe_throttle_mode else random.uniform(3.0, 7.0))

                            if not aborted_by_cooldown and pc_searched_round > 0 and m_done < mobile_target:
                                time.sleep(random.randint(12, 30))

                            if not aborted_by_cooldown and m_done < mobile_target:
                                try:
                                    m_context, m_page = ensure_mobile_page(m_context, p, headless)
                                    batch_limit = min(
                                        get_variable_batch_size(batch_size, device_type="mobile"),
                                        mobile_target - m_done,
                                    )
                                    for _ in range(batch_limit):
                                        if queries_idx >= len(queries):
                                            queries.extend(get_unique_search_queries(target_count=10, geo="VN"))
                                        q = queries[queries_idx]
                                        queries_idx += 1
                                        _log_info(f"[Mobile Search {m_done + 1}/{mobile_target}] {q[:50]}...")
                                        m_context, m_page, search_ok = _execute_query_with_recovery(
                                            m_context,
                                            m_page,
                                            ensure_mobile_page,
                                            p,
                                            headless,
                                            q,
                                            device_type="mobile",
                                            label="Mobile Search",
                                        )
                                        if search_ok and m_page is not None:
                                            m_done += 1
                                            save_searched_keyword(q)
                                            cur_pts = extract_current_points(m_page)
                                            update_session(d_done + m_done, cur_pts, f"Mobile search {m_done}/{mobile_target}")
                                            if not safe_throttle_mode and detect_unusual_activity_warning(m_page):
                                                safe_throttle_mode = True
                                                notifier.notify_account_restricted("Cảnh báo giới hạn tìm kiếm trên Bing Mobile.")
                                            if cooldown_ctrl.record_search(cur_pts, daily_cap_reached=all_maxed):
                                                aborted_by_cooldown = True
                                                break
                                            if m_done < mobile_target:
                                                time.sleep(random.uniform(35.0, 60.0) if safe_throttle_mode else random.uniform(3.0, 7.0))
                                finally:
                                    _close_quietly(m_context)
                                    m_context = m_page = None

                            if d_done < desktop_target or m_done < mobile_target:
                                cooldown = cooldown_ctrl.next_cooldown_seconds()
                                _log_info(f"[Cooldown] Nghỉ {cooldown // 60}m {cooldown % 60}s (Desktop {d_done}/{desktop_target} | Mobile {m_done}/{mobile_target})")
                                update_session(d_done + m_done, None, f"Cooldown {cooldown // 60} minutes")
                                edge_state = get_edge_browsing_state()
                                if edge_state["done"] or edge_state["seconds"] >= EDGE_BROWSING_CAP_SECONDS:
                                    _close_quietly(d_context)
                                    d_context = d_page = None
                                    time.sleep(cooldown)
                                else:
                                    d_context, d_page = ensure_desktop_page(d_context, p, headless)
                                    target_dur = min(cooldown, EDGE_BROWSING_CAP_SECONDS - edge_state["seconds"])
                                    d_page, browsed = active_edge_browsing_cooldown(d_page, total_seconds=target_dur, initial_seconds=edge_state["seconds"])
                                    if cooldown - browsed > 0:
                                        _close_quietly(d_context)
                                        d_context = d_page = None
                                        time.sleep(cooldown - browsed)

                        if is_test_target or is_explicit_test:
                            all_tasks_completed = True
                            break

                        d_context, d_page = ensure_desktop_page(d_context, p, headless)
                        re_check = get_daily_search_progress(d_page) if d_page else {"found": False, "scrape_error": True}
                        sync_edge_browsing_from_progress(re_check)
                        if re_check.get("scrape_error") or (not re_check.get("found_pc") and not re_check.get("found_mobile")):
                            all_maxed = all_tasks_completed = False
                            break
                        re_pc, pc_max = re_check.get("pc_cur", 0), re_check.get("pc_max", 90)
                        re_mob, mob_max = re_check.get("mobile_cur", 0), re_check.get("mobile_max", 60)
                        all_activities_done = _merge_primary_four_fallback(re_check)
                        if re_check.get("found_pc") and re_pc >= pc_max and re_check.get("found_mobile") and re_mob >= mob_max:
                            all_maxed = True
                            all_tasks_completed = all_activities_done
                            store.mark_searches_completed_today(True, core_tasks_done=all_activities_done)
                            break
                        double_check_attempt += 1
                        if double_check_attempt > 3:
                            all_tasks_completed = all_maxed = False
                            break
                        desktop_target = d_done + ((max(0, pc_max - re_pc) + 2) // 3)
                        mobile_target = m_done + ((max(0, mob_max - re_mob) + 2) // 3)
                        if d_done < desktop_target and d_page is not None and not d_page.is_closed():
                            try:
                                d_page.goto("https://www.bing.com/", wait_until="domcontentloaded")
                            except Exception:
                                pass
                finally:
                    _close_quietly(d_context)
                    d_context = d_page = None

            _log_info("[3/3] Đang lấy điểm số tổng kết...")
            try:
                d_context, d_page = ensure_desktop_page(d_context, p, headless)
                try:
                    claim_dashboard_pending_points(d_page)
                except Exception as e:
                    _log_warning(f"[Final Claim] Lỗi kiểm tra điểm chờ nhận: {e}")
                d_page.goto(REWARDS_URL, wait_until="domcontentloaded")
                dismiss_popups(d_page)
                d_page.wait_for_timeout(2000)
                end_points = extract_current_points(d_page)
                if not end_points and start_points > 0:
                    end_points = start_points
                star_info = extract_bing_star_bonus(d_page)
                if star_info.get("found"):
                    bing_star_bonus_str = star_info.get("text", bing_star_bonus_str)
            except Exception as e:
                _log_warning(f"[3/3] Lỗi khi lấy điểm tổng kết: {e}")
                if not end_points and start_points > 0:
                    end_points = start_points
            finally:
                _close_quietly(d_context)
                d_context = None

            log_session_result(start_points, end_points, d_done + m_done)
            finish_session(end_points, status="completed" if (all_maxed and all_activities_done) else "partial")
            save_daily_state(
                completed_date=datetime.date.today().isoformat(),
                partial=not (all_maxed and all_activities_done),
                checkin_awarded=checkin_awarded,
                searches_completed_today=bool(all_maxed),
                core_tasks_done=bool(all_activities_done),
            )
            earned = max(0, end_points - start_points)
            update_points_ledger(total_points=end_points, earned_today=earned, bing_star_bonus=bing_star_bonus_str)
            notifier.check_and_notify_milestones(end_points)
            store.record_session_outcome(True)
            return all_tasks_completed

    except Exception as e:
        _log_error(f"Pipeline error: {e}")
        _log_exception("Full traceback:")
        finish_session(0, status="failed", error_msg=str(e))
        failures = store.record_session_outcome(False)
        notifier.notify_consecutive_failures(failures)
        return False
    finally:
        _close_quietly(d_context)
        _close_quietly(m_context)
        for cleanup_fn in (unload_model_from_vram, cleanup_zombie_browsers, purge_profile_caches):
            _safe_call(cleanup_fn, getattr(cleanup_fn, "__name__", "cleanup"))


def main():
    parser = argparse.ArgumentParser(description="MS Rewards Bot Phase 8.6 - High-Speed Precision Autonomy")
    parser.add_argument("--account", type=str, default="default", help="ID tài khoản độc lập (mặc định: default)")
    parser.add_argument("--debug", action="store_true", help="Chạy có giao diện trình duyệt (headless=False)")
    parser.add_argument("--desktop-target", type=int, default=30, help="Số lượt tìm kiếm Desktop mục tiêu")
    parser.add_argument("--mobile-target", type=int, default=20, help="Số lượt tìm kiếm Mobile mục tiêu")
    parser.add_argument("--batch-size", type=int, default=3, help="Kích thước batch mỗi lần trước cooldown")
    parser.add_argument("--daemon", "--loop", dest="daemon", action="store_true", help="Chạy nền liên tục")
    parser.add_argument("--interval", type=float, default=4, help="Khoảng nghỉ giữa các chu kỳ (giờ)")
    parser.add_argument("--auto-sleep", action="store_true", default=True, help="Tự động Sleep khi hoàn tất")
    parser.add_argument("--no-sleep", dest="auto_sleep", action="store_false", help="Tắt tự động Sleep")
    parser.add_argument("--retry-count", type=int, default=0, help="Số lần thử lại trong ngày")
    parser.add_argument("--force-hours", action="store_true", help="Bỏ qua Circadian Guard 07:00 - 22:30")
    args = parser.parse_args()

    configure_account(args.account)
    lock_handle = acquire_runner_lock()
    if lock_handle is None:
        sys.exit(0)

    try:
        if psutil:
            _safe_call(lambda: psutil.Process(os.getpid()).nice(psutil.BELOW_NORMAL_PRIORITY_CLASS), "nice")
        _safe_call(cleanup_zombie_browsers, "cleanup_zombie_browsers")

        if not args.force_hours and not is_within_circadian_window():
            logger.info("[Circadian Guard] Ngoài khung giờ sinh hoạt tự nhiên (07:00 - 22:30), tạm hoãn phiên chạy.")
            sys.exit(0)

        was_already_done_before_run = (
            not getattr(args, "debug", False)
            and args.desktop_target == 30
            and args.mobile_target == 20
            and (is_today_already_completed() or _get_state_store().should_fast_exit_today())
        )

        check_interval_seconds = int(args.interval * 3600)
        all_completed = False
        try:
            while True:
                logger.info("============================================================")
                logger.info("   Microsoft Rewards Bot - Phase 8.6 High-Speed Precision Autonomy")
                logger.info(f"   ACCOUNT: {ACCOUNT_ID} | {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                logger.info("============================================================")
                all_completed = run_full_pipeline(args)
                if not args.daemon:
                    break
                start_sleep = time.time()
                while time.time() - start_sleep < max(1, check_interval_seconds):
                    time.sleep(min(60, max(1, check_interval_seconds - (time.time() - start_sleep))))
        except KeyboardInterrupt:
            logger.info("\nNhận tín hiệu dừng (Ctrl+C). Đang tắt bot...")
            sys.exit(0)

        if (
            all_completed
            and getattr(args, "auto_sleep", False)
            and not getattr(args, "force_hours", False)
            and not was_already_done_before_run
        ):
            put_pc_to_sleep()
        sys.exit(0)
    finally:
        release_runner_lock(lock_handle)


if __name__ == "__main__":
    main()
