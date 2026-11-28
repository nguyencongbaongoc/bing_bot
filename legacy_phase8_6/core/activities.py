"""
Module tự động hóa hoàn thành Daily Set, Check-in, Polls, Quizzes trên Microsoft Rewards.
Xử lý các loại nhiệm vụ: Daily Check-in, Daily Set (giữ Streak), More Activities (Poll, Quiz, Link).
"""

import base64
import hashlib
import random
import re
import time
import urllib.parse
import zlib
import struct
from pathlib import Path
from typing import Optional
try:
    from patchright.sync_api import Page, BrowserContext, TargetClosedError
except ImportError:
    try:
        from playwright.sync_api import Page, BrowserContext, TargetClosedError
    except ImportError:
        class TargetClosedError(Exception):
            pass
from core.humanizer import human_delay
from core.state_store import normalize_offer_title
from core.tracker import _log_info, _log_debug, _log_warning, _log_error, _log_exception, logger
from core.ai_solver import ask_local_ai, solve_quiz_with_ai, unload_model_from_vram
from core.searcher import (
    human_type_and_search,
    execute_single_search,
    perform_related_cluster_search,
)

# Use PYTHONUTF8=1 environment variable instead of manual wrapping
# This avoids issues with stream wrapping in subprocesses


REWARDS_URL = "https://rewards.bing.com/"

DEFAULT_TIMEOUT = 8000  # 8 seconds default timeout
_keep_earning_executed_in_session: bool = False
_last_keep_earning_done: Optional[bool] = None


def dismiss_popups(page: Page) -> None:
    """Đóng các popup, banner cookie và hộp thoại cản trở giao diện."""
    popup_selectors = [
        '#bnp_btn_reject',
        '#bnp_btn_accept',
        'button:has-text("Reject")',
        'button:has-text("Accept")',
        'button:has-text("Maybe later")',
        'button:has-text("Để sau")',
        'button:has-text("Not now")',
        'button:has-text("No thanks")',
        'button:has-text("Skip")',
        'button[aria-label="Close"]',
        'button[title="Close"]'
    ]
    for sel in popup_selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=400):
                btn.click(force=True, timeout=1000)
                page.wait_for_timeout(300)
        except Exception:
            pass
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass


def claim_all_available_rewards(page: Page) -> int:
    """
    Tự động tìm và bấm nhận tất cả đồng xu / phần thưởng đang chờ Claim.
    Xử lý chuẩn xác cả nút ngoài Dashboard lẫn popup modal z-40 ('Claim points').
    Đặc biệt luôn cuộn container dialog xuống đáy (scrollTop = scrollHeight)
    để lộ và nhận trọn vẹn nút Claim của Bing Star bonus vào đầu tháng.
    """
    claimed_count = 0
    try:
        # Bước 1: Nếu đang có popup modal nổi (z-40 / role="dialog"), cuộn xuống đáy trước khi quét nút Claim
        try:
            page.evaluate("""() => {
                const dialogs = document.querySelectorAll('[role="dialog"], .overflow-auto, .scrollbar-panel, div.fixed.inset-0');
                dialogs.forEach(d => { d.scrollTop = d.scrollHeight; });
            }""")
            page.wait_for_timeout(500)
        except Exception:
            pass

        modal_selectors = [
            'div.fixed.inset-0 button:has-text("Claim points")',
            '[role="dialog"] button:has-text("Claim points")',
            'button:has-text("Claim points")',
            'span:text-is("Claim points")',
            '[role="dialog"] button:has-text("Claim")',
            'button:has-text("Claim")',
        ]
        for sel in modal_selectors:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=1000):
                loc.click(force=True, timeout=3000)
                claimed_count += 1
                logger.info(f"[Coin] Đã bấm nhận xu trong popup với selector: {sel}")
                page.wait_for_timeout(1500)

        # Bước 2: Tìm các nút "Ready to claim" hoặc thẻ "Bing Star bonus" trên mặt trang Dashboard
        ready_buttons = page.locator(
            'button:has-text("Ready to claim"), a:has-text("Ready to claim"), '
            '[aria-label*="Ready to claim" i], [aria-label*="Star bonus" i] button, '
            'div:has-text("Bing Star bonus") button:has-text("Claim"), '
            'div:has-text("Star bonus") button:has-text("Claim")'
        )
        count = min(ready_buttons.count(), 10)
        for i in range(count):
            try:
                btn = ready_buttons.nth(i)
                if btn.is_visible(timeout=1000):
                    btn.scroll_into_view_if_needed(timeout=2000)
                    btn.click(timeout=3000)
                    logger.info("[Coin] Đã chạm thẻ 'Ready to claim' / 'Bing Star bonus', đang kiểm tra popup nhận xu...")
                    page.wait_for_timeout(1500)
                    claimed_count += 1

                    # Cuộn xuống đáy popup để lộ nút Claim của Bing Star bonus
                    try:
                        page.evaluate("""() => {
                            const dialogs = document.querySelectorAll('[role="dialog"], .overflow-auto, .scrollbar-panel, div.fixed.inset-0');
                            dialogs.forEach(d => { d.scrollTop = d.scrollHeight; });
                        }""")
                        page.wait_for_timeout(500)
                    except Exception:
                        pass

                    # Ngay sau khi bấm thẻ, nếu popup "Claim points" hiện ra thì bấm xác nhận ngay
                    confirm_btn = page.locator(
                        'button:has-text("Claim points"), [role="dialog"] button:has-text("Claim"), '
                        '[role="dialog"] button:has-text("Claim points"), button:has-text("Claim"), '
                        'div.fixed.inset-0 button:has-text("Claim")'
                    ).first
                    if confirm_btn.count() > 0 and confirm_btn.is_visible(timeout=2000):
                        confirm_btn.click(force=True, timeout=3000)
                        logger.info("[Coin] -> Đã xác nhận 'Claim points' trong popup thành công!")
                        page.wait_for_timeout(1500)

                    # Nếu popup vẫn còn mở, nhấn Escape để đóng sạch
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)
            except Exception:
                pass

    except Exception as e:
        logger.debug(f"[Coin] Lỗi nhẹ khi quét claim rewards: {e}")

    return claimed_count


def tap_corner_rewards_coin(page: Page, context_name: str = "PC") -> bool:
    """
    No-op stub (Phase 8.6): Biểu tượng 'đồng xu góc màn hình' không tồn tại trên PC Bing/Visual Search.
    Giữ lại chữ ký hàm để tương thích ngược các module/test import cũ.
    """
    return False


def _is_locator_visible(loc, timeout: int = 1000) -> bool:
    """Kiểm tra locator có hiển thị hay không một cách an toàn (tương thích cả Playwright và Mock)."""
    try:
        if hasattr(loc, "count"):
            cnt = loc.count()
            if isinstance(cnt, int) and cnt == 0:
                return False
        if hasattr(loc, "is_visible"):
            res = loc.is_visible(timeout=timeout)
            if res is False or str(res).lower() == "false":
                return False
            return True
        return False
    except Exception:
        return False


def _is_button_enabled(btn) -> bool:
    """Kiểm tra nút bấm có đang ở trạng thái active / enabled hay không."""
    try:
        if hasattr(btn, "is_enabled"):
            res = btn.is_enabled()
            if res is False or str(res).lower() == "false":
                return False
        if hasattr(btn, "get_attribute"):
            aria_dis = btn.get_attribute("aria-disabled")
            if aria_dis and str(aria_dis).lower() in ("true", "1"):
                return False
            dis = btn.get_attribute("disabled")
            if dis is not None and dis is not False and str(dis).lower() not in ("none", "false", ""):
                return False
        return True
    except Exception:
        return False


def claim_dashboard_pending_points(page: Page) -> bool:
    """
    Tự động mở https://rewards.bing.com/dashboard và kiểm tra thẻ 'Ready to claim'.
    Nếu có điểm chờ nhận, mở ngăn kéo slide-over 'Claim points' và bấm nút 'Claim points'.
    Trả về True nếu bấm nhận thành công, False nếu không có điểm chờ nhận (0 pts / disabled).
    """
    _log_info("[Final Claim] Đang mở https://rewards.bing.com/dashboard để kiểm tra điểm chờ nhận...")
    try:
        # Bước 1: Điều hướng tới /dashboard và đợi các thẻ summary hydrate
        page.goto("https://rewards.bing.com/dashboard", wait_until="domcontentloaded")
        dismiss_popups(page)
        page.wait_for_timeout(2500)
    except Exception as e:
        _log_warning(f"[Final Claim] Không thể mở /dashboard: {e}")

    try:
        # Bước 2: Kiểm tra ngăn kéo bên phải 'Claim points' đã mở sẵn hay chưa
        drawer_button_selectors = [
            'button:has-text("Claim points")',
            'button:has-text("Nhận điểm")',
            '[role="dialog"] button:has-text("Claim points")',
            '[role="dialog"] button:has-text("Claim")',
            '[role="button"]:has-text("Claim points")',
        ]
        drawer_already_open = False
        for sel in drawer_button_selectors:
            try:
                btn_candidate = page.locator(sel).first
                if _is_locator_visible(btn_candidate, timeout=300):
                    drawer_already_open = True
                    break
            except Exception:
                pass

        if not drawer_already_open:
            # Tìm và click thẻ / liên kết "Ready to claim" cạnh "Available points"
            card_selectors = [
                'a:has-text("Ready to claim")',
                'button:has-text("Ready to claim")',
                '[role="button"]:has-text("Ready to claim")',
                'a:has-text("Sẵn sàng nhận")',
                'button:has-text("Sẵn sàng nhận")',
                '[role="button"]:has-text("Sẵn sàng nhận")',
                'div:has-text("Ready to claim") button',
                'div:has-text("Ready to claim") a',
                'div:has-text("Ready to claim") [role="button"]',
                'div:has-text("Sẵn sàng nhận") button',
                'div:has-text("Sẵn sàng nhận") a',
                'div:has-text("Sẵn sàng nhận") [role="button"]',
                '[aria-label*="Ready to claim" i]',
                '[aria-label*="Sẵn sàng nhận" i]',
                'button:has-text("Claim")',
                'a:has-text("Claim")',
                '[role="button"]:has-text("Claim")',
                'text="Ready to claim"',
                ':has-text("Ready to claim")',
                ':has-text("Sẵn sàng nhận")',
            ]
            clicked_card = False
            for sel in card_selectors:
                try:
                    card_loc = page.locator(sel).first
                    if _is_locator_visible(card_loc, timeout=800):
                        try:
                            card_loc.scroll_into_view_if_needed(timeout=1000)
                        except Exception:
                            pass
                        card_loc.click(timeout=3000)
                        clicked_card = True
                        break
                except Exception:
                    continue

            if clicked_card:
                page.wait_for_timeout(1800)  # Đợi 1.5–2.0s để slide-over drawer mở ra

        # Bước 3: Kiểm tra trạng thái và bấm nút 'Claim points'
        # Kiểm tra xem có thông báo chặn "No points to claim right now" hay không
        no_points_blocked = False
        no_points_selectors = [
            'text="No points to claim right now"',
            ':has-text("No points to claim right now")',
            'text="Không có điểm nào để nhận"',
            ':has-text("Không có điểm nào để nhận")',
            'text="0 points to claim"',
            ':has-text("0 points to claim")',
        ]
        for np_sel in no_points_selectors:
            try:
                np_loc = page.locator(np_sel).first
                if _is_locator_visible(np_loc, timeout=300):
                    no_points_blocked = True
                    break
            except Exception:
                pass

        primary_claim_selectors = [
            'button:has-text("Claim points")',
            'button:has-text("Claim")',
            'button:has-text("Nhận điểm")',
            '[role="button"]:has-text("Claim points")',
            '[role="dialog"] button:has-text("Claim points")',
            '[role="dialog"] button:has-text("Claim")',
            '[role="dialog"] button:has-text("Nhận điểm")',
        ]

        target_btn = None
        for sel in primary_claim_selectors:
            try:
                btn_loc = page.locator(sel).first
                if _is_locator_visible(btn_loc, timeout=1000):
                    target_btn = btn_loc
                    break
            except Exception:
                continue

        if target_btn is not None:
            enabled = _is_button_enabled(target_btn)
            if enabled and not no_points_blocked:
                target_btn.click(timeout=3000)
                page.wait_for_timeout(2800)  # Đợi 2.5–3.0s để Available points cập nhật
                _log_info("[Final Claim] [✓] Đã bấm nút 'Claim points' trong ngăn kéo Dashboard!")
                return True
            else:
                _log_info("[Final Claim] Không có điểm chờ nhận trong ngăn kéo 'Claim points' (0 pts).")
                return False
        else:
            _log_info("[Final Claim] Không có điểm chờ nhận trong ngăn kéo 'Claim points' (0 pts).")
            return False

    except Exception as e:
        _log_warning(f"[Final Claim] Lỗi trong quá trình kiểm tra điểm chờ nhận: {e}")
        return False


def solve_checkin(page: Page) -> bool:
    """
    Tự động bấm nhận điểm danh hàng ngày (Daily Check-in).
    
    Args:
        page: Playwright Page object
        
    Returns:
        True nếu đã click thành công, False nếu không tìm thấy hoặc đã nhận
    """
    try:
        # Các selector cho nút điểm danh
        checkin_selectors = [
            'mee-rewards-daily-checkin-module button:has-text("Claim")',
            'mee-rewards-daily-checkin-module button:has-text("Điểm danh")',
            'mee-rewards-daily-checkin-module button:has-text("Nhận")',
            'button:has-text("Claim daily points")',
            'button:has-text("Nhận điểm hằng ngày")',
            '[data-bi-id="daily-checkin-claim"]',
            'mee-rewards-daily-checkin-module .claim-button',
        ]
        
        for selector in checkin_selectors:
            try:
                btn = page.locator(selector).first
                if btn.is_visible(timeout=2000):
                    try:
                        btn.click()
                    except Exception:
                        try:
                            btn.click(force=True, timeout=2000)
                        except Exception:
                            btn.evaluate("el => el.click()")
                    page.wait_for_timeout(2000)
                    _log_info("Đã nhận điểm danh Daily Check-in.")
                    return True
            except Exception:
                continue
                
    except Exception as e:
        _log_error(f"Lỗi check-in: {e}")
    
    return False


def solve_interactive_tab(page: Page, timeout_sec: int = 5, card_title: str = ""):
    """
    Xử lý tab con của Daily Set / Dashboard Activities:
    - Nếu có câu hỏi trắc nghiệm/Quiz (.btOption, #QuestionPane, input[type="radio"], .rqOption):
      Click ngẫu nhiên 1 đáp án để hoàn thành câu đố, đợi 3-5 giây rồi đóng tab.
    - Nếu là Bing Search Offer (có ô tìm kiếm và chưa phải SERP hoàn tất hoặc có prompt tìm kiếm):
      Nhập truy vấn từ card_title (hoặc fallback), Enter, cuộn 2 lần và dwell 4.5-6.5s.
    - Nếu là trang UrlOffer thông thường: Cuộn nhẹ trang, duy trì 4-6 giây rồi đóng tab.
    """
    from core.tracker import logger
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass
    page.wait_for_timeout(1000)

    # 1. Kiểm tra nút bắt đầu Quiz nếu có
    try:
        start_btn = page.locator('#rqStartQuiz, input[value*="Start" i], button:has-text("Start playing"), button:has-text("Start quiz"), a:has-text("Start"), input[value*="Play" i]').first
        if start_btn.is_visible(timeout=1000):
            logger.info("   [*] Phát hiện nút bắt đầu Quiz, đang click...")
            start_btn.click(force=True, timeout=2000)
            page.wait_for_timeout(1500)
    except Exception:
        pass

    # 2. Kiểm tra xem có phần tử câu hỏi trắc nghiệm / Quiz hay không
    quiz_selectors = [
        ".btOption",
        "#QuestionPane",
        "input[type='radio']",
        ".rqOption",
        ".wk_question",
        "#QuestionText",
        ".b_focusTextExtra",
        "[class*='quizQuestion']",
        ".bt_corOps",
        "#rqQuestion",
    ]
    is_real_quiz = False
    for sel in quiz_selectors:
        try:
            if page.locator(sel).first.is_visible(timeout=800):
                is_real_quiz = True
                break
        except Exception:
            continue

    if is_real_quiz:
        logger.info("   [*] Phát hiện câu hỏi trắc nghiệm/Quiz...")
        # Tìm các options trắc nghiệm (.btOption, #QuestionPane, input[type="radio"], .rqOption, ...)
        opt_selectors = [
            ".btOption",
            "#QuestionPane input",
            "#QuestionPane label",
            "input[type='radio']",
            ".rqOption",
            ".wk_ans_choice",
            "[class*='quizQuestion'] [role='button']",
            "div[role='radio']",
            "input[type='button']",
        ]
        options = []
        for opt_sel in opt_selectors:
            try:
                for el in page.locator(opt_sel).all():
                    if el.is_visible(timeout=300):
                        options.append(el)
                if options:
                    break
            except Exception:
                continue

        if options:
            chosen = random.choice(options)
            try:
                logger.info(f"   [*] Đang click ngẫu nhiên 1 trong {len(options)} đáp án Quiz...")
                chosen.click(force=True, timeout=2000)
            except Exception as e:
                logger.debug(f"   [!] Lỗi khi click đáp án: {e}")
        else:
            # Fallback sang solve_quiz_with_ai nếu có
            try:
                from core.ai_solver import solve_quiz_with_ai
                solve_quiz_with_ai(page)
            except Exception:
                pass

        wait_sec = random.uniform(3.0, 5.0)
        logger.info(f"   [*] Đợi {wait_sec:.1f}s để hoàn tất câu đố...")
        time.sleep(wait_sec)
    else:
        # Kiểm tra xem tab có phải Bing Search Offer hay UrlOffer thông thường
        cur_url = (getattr(page, "url", "") or "").lower()
        body_lower = ""
        try:
            body_lower = (page.evaluate("() => document.body ? document.body.innerText.toLowerCase() : ''") or "")
        except Exception:
            pass
        has_search_prompt = any(p in body_lower for p in ("search on bing", "tìm kiếm trên bing"))

        search_input = None
        for q_sel in ("#sb_form_q", "textarea[name='q']", "input[name='q']"):
            try:
                cand = page.locator(q_sel).first
                if cand.count() > 0 and cand.is_visible(timeout=800):
                    search_input = cand
                    break
            except Exception:
                continue

        is_bing_offer = ("bing.com" in cur_url) or (search_input is not None) or has_search_prompt
        parsed_tab = urllib.parse.urlsplit(cur_url)
        qs_tab = urllib.parse.parse_qs(parsed_tab.query)
        has_q_param = bool((qs_tab.get("q") or [""])[0].strip())
        has_b_results = False
        try:
            res_loc = page.locator("#b_results .b_algo, #b_results").first
            if res_loc.count() > 0 and res_loc.is_visible(timeout=500):
                has_b_results = True
        except Exception:
            pass
        is_completed_serp = ("/search" in parsed_tab.path) and has_q_param and has_b_results

        if is_bing_offer and search_input is not None and (not is_completed_serp or has_search_prompt):
            clean_query = normalize_offer_title(card_title) if card_title else ""
            if (
                not clean_query
                or len(clean_query) < 3
                or clean_query in {"task", "offer", "search on bing", "tìm kiếm trên bing"}
                or clean_query.startswith("card ")
            ):
                clean_query = "trending news today"
            logger.info(f"   [*] Phát hiện Bing Search Offer, đang thực hiện tìm kiếm: '{clean_query}'...")
            try:
                search_input.click(force=True, timeout=2000)
                search_input.fill(clean_query)
                page.keyboard.press("Enter")
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=8000)
                except Exception:
                    pass
                for _ in range(2):
                    page.wait_for_timeout(random.randint(600, 1000))
                    try:
                        page.mouse.wheel(0, random.randint(250, 450))
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"   [!] Lỗi khi nhập tìm kiếm trên Bing Search Offer: {e}")
            wait_sec = random.uniform(3.0, 4.5)
            logger.info(f"   [*] Duy trì trang kết quả {wait_sec:.1f}s để nhận điểm Search Offer...")
            time.sleep(wait_sec)
        else:
            # Nếu là trang tìm kiếm Bing thông thường (UrlOffer)
            logger.info("   [*] Thẻ dạng UrlOffer/Search thông thường, cuộn nhẹ trang...")
            try:
                scroll_amt = random.randint(250, 450)
                page.mouse.wheel(0, scroll_amt)
            except Exception:
                pass
            wait_sec = random.uniform(3.0, 4.5)
            logger.info(f"   [*] Duy trì trang {wait_sec:.1f}s để nhận điểm...")
            time.sleep(wait_sec)


def ensure_streak_protection(page: Page) -> bool:
    """
    Trên trang Dashboard rewards.bing.com, kiểm tra công tắc Streak Protection
    (button[role="switch"] cạnh chữ "Streak protection").
    Nếu đang tắt (aria-checked="false"), tự động click bật lên.
    """
    _log_info("[Streak Protection] Đang kiểm tra công tắc Streak Protection...")
    try:
        selectors = [
            'button[role="switch"][aria-label*="streak protection" i]',
            'button[role="switch"][aria-label*="bảo vệ chuỗi" i]',
            'button[role="switch"]:right-of(:text("Streak protection"))',
            'button[role="switch"]:near(:text("Streak protection"))',
            'button[role="switch"]:near(:text("Bảo vệ chuỗi"))',
            'div:has-text("Streak protection") button[role="switch"]',
            'div:has-text("Bảo vệ chuỗi") button[role="switch"]',
            'button[role="switch"]',
            'button[aria-label*="streak protection" i]',
            'button[aria-label*="bảo vệ chuỗi" i]',
            '[data-bi-name*="streakProtection" i]',
            '#streak-protection-toggle',
        ]

        for sel in selectors:
            try:
                btns = page.locator(sel)
                cnt = btns.count()
                for i in range(cnt):
                    btn = btns.nth(i)
                    if not btn.is_visible(timeout=1000):
                        continue
                    if sel == 'button[role="switch"]':
                        parent_cnt = btn.locator('xpath=./ancestor::*[contains(., "Streak") or contains(., "streak") or contains(., "chuỗi")]').count()
                        if parent_cnt == 0:
                            continue

                    is_checked = btn.get_attribute("aria-checked")
                    inner = (btn.inner_text() or "").lower()
                    if is_checked == "false" or "off" in inner:
                        _log_info("[Streak Protection] Phát hiện công tắc đang tắt (aria-checked='false'). Đang bật...")
                        btn.click(force=True)
                        page.wait_for_timeout(1000)
                        confirm_btn = page.locator('button:has-text("Turn on"), button:has-text("Bật"), button:has-text("Confirm")').first
                        if confirm_btn.is_visible(timeout=2000):
                            confirm_btn.click(force=True)
                            page.wait_for_timeout(1000)
                        _log_info("[Streak Protection] [✓] Đã kích hoạt bảo vệ chuỗi ngày thành công!")
                        return True
                    elif is_checked == "true" or "on" in inner:
                        _log_info("[Streak Protection] [✓] Streak Protection đã được bật sẵn (aria-checked='true').")
                        return True
            except Exception:
                continue

        _log_info("[Streak Protection] Đã bật sẵn hoặc không có công tắc trên giao diện hiện tại.")
        return False
    except Exception as e:
        _log_warning(f"[Streak Protection] Không thể thao tác: {e}")
        return False


enable_streak_protection = ensure_streak_protection


def detect_unusual_activity_warning(page: Page) -> bool:
    """
    Kiểm tra xem trên Dashboard, Bing Search, hoặc popup có chứa thông báo cảnh báo
    "unusual search activity" / "limit your ability to earn points" hay không.
    Trả về True nếu phát hiện cảnh báo.
    """
    warning_phrases = [
        "unusual search activity",
        "limit your ability to earn points",
        "unusual search",
        "suspicious activity",
        "hoạt động tìm kiếm bất thường",
        "giới hạn khả năng kiếm điểm",
        "hạn chế khả năng kiếm điểm",
    ]
    try:
        page_text = page.evaluate("() => document.body ? document.body.innerText.toLowerCase() : ''")
        for phrase in warning_phrases:
            if phrase in page_text:
                _log_warning(f"[Anti-Ban Shield] Phát hiện cảnh báo trên trang: '{phrase}'")
                return True

        for alert_sel in ['[role="alert"]', '.c-alert', '.b_notification', '#id_d', '.rewards-alert', '[class*="warning"]', '[class*="alert"]']:
            try:
                alerts = page.locator(alert_sel)
                cnt = alerts.count()
                for i in range(cnt):
                    al_text = alerts.nth(i).inner_text().lower()
                    for phrase in warning_phrases:
                        if phrase in al_text:
                            _log_warning(f"[Anti-Ban Shield] Phát hiện cảnh báo trong alert banner: '{phrase}'")
                            return True
            except Exception:
                pass
    except Exception as e:
        _log_debug(f"[Anti-Ban Shield] Lỗi khi kiểm tra cảnh báo giới hạn: {e}")

    return False


def solve_punch_cards(page: Page):
    """Quét và giải các thẻ sự kiện nhiều bước (Punch Cards)."""
    _log_info("--- BẮT ĐẦU: KIỂM TRA PUNCH CARDS (THẺ SỰ KIỆN ĐA BƯỚC) ---")
    
    punchcard_selectors = [
        'div[class*="punchcard"]',
        'div[data-bi-name*="punchcard"]',
        'a[href*="/welcome/punchcard"]'
    ]
    
    try:
        cards = []
        for sel in punchcard_selectors:
            found = page.locator(sel).all()
            if found:
                cards.extend(found)
                
        _log_info(f"[*] Tìm thấy {len(cards)} thẻ sự kiện tiềm năng.")
        
        for idx, card in enumerate(cards[:3]):  # Giới hạn duyệt tối đa 3 card lớn
            try:
                if not card.is_visible():
                    continue
                # Click mở thẻ sự kiện
                with page.expect_popup(timeout=5000) as popup_info:
                    card.click(force=True)
                sub_page = popup_info.value
                sub_page.wait_for_load_state("domcontentloaded")
                sub_page.wait_for_timeout(2000)
                
                # Quét các bước con bên trong Punch Card
                step_buttons = sub_page.locator('button[class*="step"], a[class*="step"], .punchcard-row a, [role="button"]:has-text("+")').all()
                for step_idx, step_btn in enumerate(step_buttons):
                    try:
                        if step_btn.is_visible():
                            step_btn.click(force=True)
                            sub_page.wait_for_timeout(3000)
                    except Exception:
                        pass
                
                sub_page.close()
                page.wait_for_timeout(1500)
                _log_info(f"  [✓] Đã xử lý thẻ sự kiện {idx + 1}")
            except Exception:
                continue
    except Exception as e:
        _log_warning(f"[!] Bỏ qua punch cards do lỗi: {e}")
    finally:
        _log_info("--- KẾT THÚC: PUNCH CARDS ---\n")


def solve_cards_in_section(
    context: BrowserContext,
    page: Page,
    selector: str,
    name: str,
    state_store=None,
) -> int:
    """
    Quét và giải quyết từng thẻ chưa hoàn thành trong một khu vực (Daily Set, More Activities).
    
    Args:
        context: BrowserContext để bắt tab mới
        page: Page object hiện tại
        selector: CSS selector cho các thẻ nhiệm vụ
        name: Tên khu vực (để log)
        state_store: StateStore tùy chọn để bỏ qua thẻ đã thử thất bại trong ngày
        
    Returns:
        Số thẻ đã xử lý thành công
    """
    page.goto(REWARDS_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(random.randint(2500, 3500))
    
    # Dismiss popups after navigation
    dismiss_popups(page)
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    except Exception:
        pass

    cards_locator = page.locator(selector)
    try:
        total_cards = cards_locator.count()
        _log_info(f"[{name}] Tìm thấy {total_cards} thẻ nhiệm vụ.")
    except Exception:
        _log_warning(f"[{name}] Không tìm thấy thẻ nhiệm vụ với selector: {selector}")
        return 0

    completed_count = 0
    
    for i in range(total_cards):
        try:
            card = cards_locator.nth(i)
            # Kiểm tra xem thẻ đã hoàn thành chưa (có dấu tích xanh / svg checkmark)
            completed_indicators = [
                'svg.size-2',
                'svg[class*="text-fgCtrlOnImage"]',
                '.mee-icon-SkypeCircleCheck',
                '.mee-icon-CheckMark',
                'span[class*="check"]',
                'div[class*="completed"]',
                '[data-bi-id*="completed"]',
                '[aria-label*="complete" i]',
                '[aria-label*="done" i]',
            ]
            
            is_completed = False
            for indicator in completed_indicators:
                try:
                    if card.locator(indicator).first.is_visible(timeout=300):
                        is_completed = True
                        break
                except Exception:
                    continue

            # Lấy tiêu đề thẻ để log
            try:
                title_text = card.inner_text(timeout=1000).split("\n")[0][:50].strip()
            except Exception:
                title_text = f"Card {i+1}"

            if is_completed:
                _log_debug(f"  [{i+1}/{total_cards}] [✓] Thẻ '{title_text}' đã hoàn thành, bỏ qua.")
                continue

            if state_store is not None and state_store.is_offer_attempted_today(title_text):
                _log_info(f"  [{i+1}/{total_cards}] [⏭] Thẻ '{title_text}' đã thử hôm nay (skip list), bỏ qua.")
                continue

            _log_info(f"  -> Đang xử lý [{i+1}/{total_cards}]: {title_text}...")

            # Đảm bảo tắt mọi popup/modal cũ trước khi click thẻ
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
            except Exception:
                pass

            # Cuộn thẻ vào tầm nhìn
            try:
                card.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                pass

            sub_tab = None
            # Mở tab thực thi: Khi click vào thẻ, kiểm tra xem có tab mới mở ra không (context.expect_page())
            try:
                with context.expect_page(timeout=5000) as new_tab_info:
                    try:
                        card.click(timeout=3000)
                    except Exception:
                        try:
                            card.click(force=True, timeout=2000)
                        except Exception:
                            card.evaluate("el => el.click()")
                sub_tab = new_tab_info.value
            except Exception:
                pass

            # Xử lý nếu thẻ mở Drawer/Popup thay vì tab mới:
            if sub_tab is None:
                modal = page.locator('[role="dialog"], [role="alertdialog"], .react-aria-Modal, div.fixed.inset-0').first
                if modal.is_visible(timeout=1500):
                    _log_info(f"  [*] Xuất hiện Drawer/Modal cho thẻ '{title_text}'. Đang tìm nút hành động bên trong...")
                    action_selectors = [
                        'button:has-text("Start")', 'a:has-text("Start")',
                        'button:has-text("Go")', 'a:has-text("Go")',
                        'a[href*="bing.com"]',
                        'button:has-text("Open")', 'a:has-text("Open")',
                        'button:has-text("Bắt đầu")', 'a:has-text("Bắt đầu")',
                        'a[target="_blank"]',
                    ]
                    for act_sel in action_selectors:
                        action_btn = modal.locator(act_sel).first
                        if action_btn.is_visible(timeout=600):
                            _log_info(f"  [*] Đã tìm thấy nút kích hoạt ({act_sel}), đang click...")
                            try:
                                with context.expect_page(timeout=6000) as m_tab_info:
                                    action_btn.click()
                                sub_tab = m_tab_info.value
                                break
                            except Exception:
                                try:
                                    action_btn.click(force=True, timeout=2000)
                                except Exception:
                                    pass
                                break

            # Fallback nếu vẫn chưa mở tab mà thẻ có thuộc tính href bing.com
            if sub_tab is None:
                try:
                    href = card.get_attribute("href")
                    if href and ("bing.com" in href or "msn.com" in href or href.startswith("http")):
                        _log_info(f"  [*] Mở tab mới từ link href của thẻ...")
                        sub_tab = context.new_page()
                        sub_tab.goto(href, wait_until="domcontentloaded", timeout=12000)
                except Exception:
                    pass

            if sub_tab is not None:
                try:
                    solve_interactive_tab(sub_tab, card_title=title_text)
                finally:
                    try:
                        sub_tab.close()
                    except Exception:
                        pass
                completed_count += 1
            else:
                # Nếu nhiệm vụ hoàn tất inline mà không cần tab
                time.sleep(random.uniform(2.0, 3.5))
                completed_count += 1

            # Đóng bất kỳ popup/drawer nào sau khi hoàn thành thẻ
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)
            except Exception:
                pass

            # Kiểm tra lại xem thẻ đã hoàn thành chưa; nếu chưa và có state_store thì ghi nhận skip trong ngày
            if state_store is not None:
                still_completed = False
                for indicator in completed_indicators:
                    try:
                        if card.locator(indicator).first.is_visible(timeout=300):
                            still_completed = True
                            break
                    except Exception:
                        continue
                if not still_completed:
                    state_store.mark_offer_attempted_today(title_text)

        except Exception as e:
            _log_error(f"    [!] Lỗi xử lý thẻ {i+1}: {e}")
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            continue

    return completed_count


def activate_mobile_app_card(page: Page) -> bool:
    """
    No-op: CDP Mobile Emulation đã bị gỡ bỏ hoàn toàn.
    Việc click nút trên giao diện web di động không thể điểm danh và gây báo hoàn thành ảo.
    """
    _log_info("[Mobile App Card] CDP Mobile Emulation đã bị gỡ bỏ - bỏ qua kích hoạt thẻ Mobile App.")
    return False


def run_all_dashboard_activities(context: BrowserContext, page: Page, state_store=None) -> None:
    """
    Chạy toàn bộ quy trình Dashboard Activities: Check-in -> Daily Set -> Mobile App -> More Activities.
    
    Args:
        context: BrowserContext để quản lý tab
        page: Page object chính
        state_store: StateStore tùy chọn để bỏ qua thẻ đã thử trong ngày
    """
    _log_info("--- BẮT ĐẦU: DAILY STREAK & ACTIVITIES ---")
    
    page.goto(REWARDS_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(random.randint(3000, 4000))
    
    # Dismiss popups after initial navigation
    dismiss_popups(page)
    
    # Auto-claim any available rewards before starting
    claim_all_available_rewards(page)

    # 1. Điểm danh hàng ngày
    solve_checkin(page)

    # 2. Daily Set (Nhiệm vụ giữ chuỗi Streak - quan trọng nhất)
    solve_cards_in_section(
        context, 
        page, 
        'div[data-rac] a[href*="bing.com"], div[class*="card"] a[href*="bing.com"], div[id*="daily-set"] [role="button"], div[id*="daily-set"] a, .c-cardContent a, a.ds-card-sec, div[data-bi-area="DailySet"] .c-card, .daily-sets .c-card, mee-rewards-daily-set-item .c-card', 
        "Daily Set",
        state_store=state_store,
    )

    # 3. Mobile App card: CDP Mobile Emulation removed - handled by API check-in (type 103)
    pass

    # 4. More Activities (Sự kiện và hoạt động bổ sung)
    more_count = solve_cards_in_section(
        context, 
        page, 
        'div[id*="more-activities"] [role="button"], div[id*="more-activities"] a, div[data-bi-area="MoreActivities"] .c-card, .more-activities-container .c-card, mee-rewards-more-activities-item .c-card', 
        "More Activities",
        state_store=state_store,
    )
    if more_count == 0:
        _log_info("[More Activities] Không tìm thấy thẻ trên Dashboard cũ (mục 'Keep earning' tại /earn sẽ được quét riêng).")
    
    # 5. Các khu vực khác có thể có (Special Offers, v.v.)
    solve_cards_in_section(
        context,
        page,
        'div[data-bi-area="SpecialOffers"] .c-card, .special-offers .c-card, div[id*="special-offers"] [role="button"], div[id*="special-offers"] a',
        "Special Offers",
        state_store=state_store,
    )

    # Final claim sweep after all activities
    claim_all_available_rewards(page)
    
    _log_info("--- HOÀN TẤT: DAILY STREAK & ACTIVITIES ---\n")


def solve_mobile_checkin(page: Page):
    """Điểm danh chuỗi ngày trên giao diện Mobile."""
    _log_info("Đang kiểm tra điểm danh Mobile App...")
    try:
        page.goto("https://rewards.bing.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        
        dismiss_popups(page)
        claim_all_available_rewards(page)
        
        # Selector cho nút điểm danh mobile app / flyout panel
        checkin_locators = [
            'button:has-text("Claim")',
            'button:has-text("Điểm danh")',
            'div[aria-label*="check-in"]:not([aria-disabled="true"])',
            'div[id*="daily-checkin"] button',
            '.daily-checkin-btn'
        ]
        for loc in checkin_locators:
            btn = page.locator(loc).first
            if btn.is_visible(timeout=2000):
                try:
                    btn.click()
                except Exception:
                    try:
                        btn.click(force=True, timeout=2000)
                    except Exception:
                        btn.evaluate("el => el.click()")
                page.wait_for_timeout(2000)
                _log_info("Đã bấm nhận điểm danh Mobile!")
                break
    except Exception as e:
        _log_warning(f"Bỏ qua điểm danh Mobile: {e}")


def run_read_to_earn(page: Page, max_articles: int = 10) -> int:
    """
    Đọc báo tích điểm Read to Earn chuẩn:
    - Lấy link bài nội bộ msn.com có mã /ar- (chứa widget đồng xu).
    - Tự động mở rộng nội dung bài báo ("Continue reading").
    - Duy trì đọc tích lũy (Sustained Reading Loop) tối thiểu 7.0 - 8.0 giây.
    - Chờ và bắt chuẩn widget đồng xu góc (+3 / Rewards / Corner Floating).
    - Chạm đồng xu trực tiếp + fallback click tọa độ góc dưới màn hình.
    """
    logger.info(f"--- BẮT ĐẦU: READ TO EARN ({max_articles} bài báo nội bộ MSN) ---")
    read_count = 0
    article_links = []

    try:
        # 1. Truy cập MSN News để lấy danh sách bài viết nội bộ
        logger.info("[Read to Earn] Đang tải trang tin tức MSN để trích xuất bài viết nội bộ...")
        page.goto("https://www.msn.com/en-us/news", wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(3000)

        # Chạm đồng xu góc ở trang chủ để kích hoạt phiên Read to Earn (nếu có)
        tap_corner_rewards_coin(page, context_name="MSN-Home")

        # Cuộn nhẹ trang 3 lần để bung các feed tin bài trong Shadow DOM
        for _ in range(3):
            page.mouse.wheel(0, 800)
            page.wait_for_timeout(1000)

        # 2. Quét sâu (kể cả Shadow DOM) và LỌC NGHIÊM NGẶT link msn.com có /ar-
        raw_links = page.evaluate("""() => {
            function getLinksDeep(root = document) {
                let links = [];
                const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, null, false);
                let node = walker.nextNode();
                while (node) {
                    if (node.tagName === 'A' && node.href) {
                        links.push(node.href);
                    }
                    if (node.shadowRoot) {
                        links = links.concat(getLinksDeep(node.shadowRoot));
                    }
                    node = walker.nextNode();
                }
                return links;
            }
            return getLinksDeep(document);
        }""")

        valid_set = set()
        for href in raw_links:
            try:
                # Kiểm tra nghiêm ngặt: Phải thuộc msn.com VÀ đường dẫn có chứa /ar-
                if "msn.com" in href and "/ar-" in href:
                    clean_url = href.split("?")[0].split("#")[0]
                    # Loại bỏ hoàn toàn nếu chứa domain đối tác ngoài
                    if not any(blocked in clean_url for blocked in ["macrumors.com", "castanet.net", "news24.com", "cnbc", "businesstoday"]):
                        valid_set.add(clean_url)
            except Exception:
                continue

        article_links = list(valid_set)

        # Fallback qua trang chủ MSN hoặc Bing News nếu quét được ít hơn mục tiêu
        if len(article_links) < max_articles:
            logger.info("[Read to Earn] Bổ sung thêm link bài viết qua Bing News...")
            page.goto("https://www.bing.com/news", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)
            more_links = page.evaluate("""() => {
                return Array.from(document.querySelectorAll('a[href*="msn.com"][href*="/ar-"]'))
                            .map(a => a.href.split('?')[0].split('#')[0]);
            }""")
            for link in more_links:
                if link not in article_links and "/ar-" in link:
                    article_links.append(link)

        logger.info(f"[Read to Earn] Đã chọn lọc được {len(article_links)} bài báo nội bộ MSN hợp lệ.")

        if not article_links:
            logger.warning("[Read to Earn] Không tìm thấy bài báo MSN nào hợp lệ hôm nay!")
            return 0

        # 3. Tiến hành đọc từng bài báo và tương tác với đồng xu ở góc
        for idx, url in enumerate(article_links[:max_articles], start=1):
            try:
                logger.info(f"  -> Đang mở bài [{idx}/{max_articles}]: {url}")
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(2500)

                # Đóng popup / banner cản trở nếu có
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass

                # Tự động mở rộng nội dung bài báo nếu có nút "Continue reading"
                try:
                    cr_btn = page.locator('fluent-button:has-text("Continue reading"), button:has-text("Continue reading"), .article-cont-read-button').first
                    if cr_btn.count() > 0 and cr_btn.is_visible(timeout=1000):
                        cr_btn.click(force=True, timeout=1500)
                        page.wait_for_timeout(800)
                except Exception:
                    pass

                # Bước 1: Duy trì đọc tích lũy (Sustained Reading Loop >= 7.0 giây)
                start_read = time.time()
                while time.time() - start_read < 7.5:
                    scroll_distance = random.randint(250, 400)
                    page.mouse.wheel(0, scroll_distance)
                    page.wait_for_timeout(1000)

                # Bước 2: Chờ tối đa 3-4 giây cho widget đồng xu nổi xuất hiện trên DOM
                page.wait_for_timeout(2500)

                # Bước 3: Quét và phát hiện đồng xu góc (Coin Detection qua Selector & Deep DOM)
                coin_detection = page.evaluate("""() => {
                    function queryAllDeep(root = document) {
                        let els = [];
                        const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, null, false);
                        let n = walker.nextNode();
                        while (n) {
                            els.push(n);
                            if (n.shadowRoot) els = els.concat(queryAllDeep(n.shadowRoot));
                            n = walker.nextNode();
                        }
                        return els;
                    }

                    const winW = window.innerWidth;
                    const winH = window.innerHeight;
                    const all = queryAllDeep(document);

                    // Ưu tiên các selector đặc thù của MSN bài báo
                    for (const el of all) {
                        const text = (el.innerText || el.textContent || '').trim();
                        const aria = (el.getAttribute('aria-label') || '').trim();
                        const id = el.id || '';
                        const cls = (typeof el.className === 'string') ? el.className : '';
                        const tag = el.tagName.toLowerCase();
                        const combined = `${id} ${cls} ${aria} ${text}`.toLowerCase();

                        const isMatch = combined.includes('+3') ||
                                        combined.includes('3 point') ||
                                        (combined.includes('reward') && (combined.includes('coin') || combined.includes('entry') || combined.includes('badge') || combined.includes('flyout') || combined.includes('button'))) ||
                                        id === 'rewards-badge' || id === 'entry-point-hp' || id === 'rewid-f' || id === 'id_rh' || cls.includes('m_rw_c');

                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        const isFixed = style.position === 'fixed' || style.position === 'sticky';
                        const isBottomRight = (rect.bottom >= winH * 0.5) && (rect.right >= winW * 0.4) && rect.width >= 15 && rect.width <= 250 && rect.height >= 15 && rect.height <= 250;

                        if (rect.width > 8 && rect.height > 8 && (isMatch || (isFixed && isBottomRight && isMatch))) {
                            try { el.click(); } catch(e) {}
                            return {
                                matched: true,
                                info: `${tag}#${id}.${cls.slice(0, 30)} (${text.slice(0, 20)})`,
                                x: Math.round(rect.x + rect.width / 2),
                                y: Math.round(rect.y + rect.height / 2)
                            };
                        }
                    }

                    // Fallback kiểm tra bất kỳ thẻ nổi fixed nào ở góc dưới bên phải
                    for (const el of all) {
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        const isFixed = style.position === 'fixed' || style.position === 'sticky';
                        if (isFixed && rect.width >= 20 && rect.width <= 150 && rect.height >= 20 && rect.height <= 150 && rect.bottom >= winH * 0.6 && rect.right >= winW * 0.5) {
                            try { el.click(); } catch(e) {}
                            return {
                                matched: true,
                                info: `Floating-Corner ${el.tagName.toLowerCase()}`,
                                x: Math.round(rect.x + rect.width / 2),
                                y: Math.round(rect.y + rect.height / 2)
                            };
                        }
                    }

                    return {
                        matched: false,
                        x: Math.round(winW - 45),
                        y: Math.round(winH - 75)
                    };
                }""")

                # Bước 4: Chạm đồng xu (Click Action + Fallback tọa độ)
                tapped = False
                if coin_detection and coin_detection.get("matched"):
                    cx, cy = coin_detection["x"], coin_detection["y"]
                    try:
                        page.mouse.click(cx, cy)
                    except Exception:
                        pass
                    info_desc = coin_detection.get("info", "Coin Widget")
                    logger.info(f"  [MSN-Bài-{idx}] Đã phát hiện và chạm đồng xu (+3) thành công! ({info_desc})")
                    tapped = True
                else:
                    # Thử qua tap_corner_rewards_coin
                    tapped = tap_corner_rewards_coin(page, context_name=f"MSN-Bài-{idx}")
                    if tapped:
                        logger.info(f"  [MSN-Bài-{idx}] Đã phát hiện và chạm đồng xu (+3) thành công!")
                    else:
                        # Fallback click tọa độ góc dưới phải màn hình di động
                        cx = coin_detection.get("x", 348) if coin_detection else 348
                        cy = coin_detection.get("y", 585) if coin_detection else 585
                        try:
                            page.mouse.click(cx, cy)
                            logger.info(f"  [MSN-Bài-{idx}] Đã phát hiện và chạm đồng xu (+3) thành công! (Tọa độ: {cx}, {cy})")
                            tapped = True
                        except Exception:
                            pass

                # Quét các iframe flyout bung ra để nhận thêm xu nếu có
                for frame in page.frames:
                    f_url = frame.url.lower()
                    if "panelflyout" in f_url or "rewards" in f_url:
                        try:
                            claim_btns = frame.locator('button:has-text("Claim"), a:has-text("Claim"), [class*="claim" i], [aria-label*="Claim" i]')
                            for c_i in range(min(claim_btns.count(), 3)):
                                c_btn = claim_btns.nth(c_i)
                                if c_btn.is_visible(timeout=600):
                                    c_btn.click(force=True, timeout=1500)
                                    page.wait_for_timeout(800)
                        except Exception:
                            pass

                read_count += 1
                logger.info(f"  [✓] Hoàn thành đọc & tương tác xu bài [{read_count}/{max_articles}]")
                page.wait_for_timeout(random.randint(1000, 1500))

            except Exception as e:
                logger.debug(f"  [!] Bỏ qua bài do tải chậm hoặc lỗi: {e}")
                continue

    except Exception as e:
        logger.warning(f"[Read to Earn] Lỗi trong quá trình thực thi: {e}")

    logger.info(f"--- HOÀN TẤT: ĐÃ ĐỌC {read_count} BÀI BÁO NỘI BỘ MSN ---\n")
    return read_count


def solve_read_to_earn(page: Page, article_count: int = 10) -> int:
    """Tương thích ngược với các lời gọi solve_read_to_earn cũ."""
    return run_read_to_earn(page, max_articles=article_count)


def canonicalize_quest_url(url: str) -> str:
    """Chuẩn hóa URL Quest (/earn/quest/...) bằng cách loại bỏ query parameters và dấu '/' cuối."""
    raw = (url or "").strip()
    if not raw:
        return ""
    parts = urllib.parse.urlsplit(raw)
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"
    return parts.path.rstrip("/")


def solve_all_earn_quests(page: Page, state_store=None):
    """
    Universal Quest Resolver cho trang https://rewards.bing.com/earn
    Hỗ trợ kiến trúc React-Aria (data-rac) + Tailwind CSS mới của Microsoft.
    Tự động xử lý cơ chế Click & Cancel cho các nhiệm vụ App/Taskbar và giải các nhiệm vụ Web/Quiz.
    """
    import re
    import urllib.parse
    from core.tracker import _logger as logger
    from core.activities import dismiss_popups
    
    logger.info("============================================================")
    logger.info("   BẮT ĐẦU: UNIVERSAL QUEST RESOLVER TẠI /EARN")
    logger.info("============================================================")

    earn_url = "https://rewards.bing.com/earn"
    page.on("dialog", lambda dialog: dialog.dismiss())

    try:
        page.goto(earn_url, wait_until="domcontentloaded", timeout=15000)
        dismiss_popups(page)

        # 1. Đợi React mount danh sách thẻ Quest trên trang /earn
        try:
            page.wait_for_selector('a[href*="/earn/quest/"]', timeout=10000)
            page.wait_for_timeout(1500)
        except Exception:
            logger.warning("[!] Không tìm thấy thẻ Quest nào trên /earn sau 10s.")
            return

        # Thu thập danh sách URL Quest và kiểm tra tiến độ ngay từ thẻ ngoài nếu có
        quest_anchors = page.locator('a[href*="/earn/quest/"]').all()
        quest_urls = []
        for el in quest_anchors:
            try:
                href = el.get_attribute("href")
                if not href or "/earn/quest/" not in href:
                    continue
                full_url = urllib.parse.urljoin(earn_url, href)
                canon_url = canonicalize_quest_url(full_url)
                if not canon_url:
                    continue

                # Kiểm tra text trên thẻ ngoài xem đã hoàn thành chưa (VD: 4/4 tasks)
                card_text = el.inner_text()
                m = re.search(r'(\d+)\s*/\s*(\d+)\s*tasks', card_text, re.IGNORECASE)
                if m and int(m.group(1)) == int(m.group(2)) and int(m.group(2)) > 0:
                    logger.info(f"[*] Bỏ qua Quest đã đủ {m.group(0)} ngay từ ngoài: {canon_url}")
                    continue

                if canon_url not in quest_urls:
                    quest_urls.append(canon_url)
            except Exception:
                pass

        logger.info(f"[*] Cần xử lý {len(quest_urls)} Punch Cards chưa hoàn thành.")

        # 2. Duyệt từng trang Quest chi tiết
        for idx, q_url in enumerate(quest_urls):
            logger.info(f"\n[Quest {idx+1}/{len(quest_urls)}] Đang mở: {q_url}")
            try:
                page.goto(q_url, wait_until="domcontentloaded", timeout=15000)
                dismiss_popups(page)

                # Đợi khung Activities của React render xong
                try:
                    page.wait_for_selector('text="Activities"', timeout=10000)
                    page.wait_for_timeout(2000)  # Đệm cho API đổ xong danh sách sub-tasks
                except Exception:
                    logger.warning("   [!] Khung Activities tải chậm, thử quét tiếp...")

                # Kiểm tra chỉ số Status: X/Y tasks
                body_text = page.locator("body").inner_text()
                status_match = re.search(r'Status:\s*(\d+)\s*/\s*(\d+)\s*tasks', body_text, re.IGNORECASE)
                before_done_cnt = int(status_match.group(1)) if status_match else 0
                total_cnt = int(status_match.group(2)) if status_match else 0
                if status_match:
                    logger.info(f"   [*] Tiến độ hiện tại: {before_done_cnt}/{total_cnt} tasks")
                    if before_done_cnt >= total_cnt and total_cnt > 0:
                        logger.info("   [✓] Quest này đã hoàn thành 100%, chuyển sang Quest tiếp theo.")
                        continue

                # 3. Tìm tất cả các nút/link hành động trong khu vực Activities
                # Sử dụng JavaScript đánh dấu các nút hợp lệ bên trong cột Activities để tránh click nhầm Header/Footer
                marked_count = page.evaluate("""() => {
                    // Tìm phần tử tiêu đề "Activities"
                    const headings = Array.from(document.querySelectorAll('h1, h2, h3, h4, div, span'))
                        .filter(el => el.textContent && el.textContent.trim() === 'Activities');
                    
                    let container = document.body;
                    if (headings.length > 0) {
                        // Lấy khung panel chứa chữ Activities và Status:
                        let p = headings[0].parentElement;
                        while (p && p !== document.body) {
                            if (p.innerText && p.innerText.includes('tasks') && p.querySelectorAll('button, a, [role="button"], [data-rac]').length > 0) {
                                container = p;
                                break;
                            }
                            p = p.parentElement;
                        }
                    }

                    // Xóa các đánh dấu cũ nếu có
                    document.querySelectorAll('[data-bot-quest-target]').forEach(el => el.removeAttribute('data-bot-quest-target'));

                    // Tìm các phần tử có thể bấm trong container
                    const candidates = Array.from(container.querySelectorAll('a, button, [role="button"], [data-react-aria-pressable="true"]'));
                    let validIdx = 0;

                    const ignoreTexts = [
                        'rewards app exclusive', 'feedback', 'terms and conditions',
                        'more activities', 'dashboard', 'earn', 'redeem', 'about',
                        'refer and earn', 'accept', 'reject', 'manage cookies', 'privacy'
                    ];

                    for (const el of candidates) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width < 10 || rect.height < 10) continue;
                        if (el.disabled || el.getAttribute('aria-disabled') === 'true' || el.getAttribute('data-disabled') === 'true') continue;

                        const text = (el.innerText || el.getAttribute('aria-label') || '').trim().toLowerCase();
                        if (!text) continue;
                        if (ignoreTexts.some(ig => text.includes(ig))) continue;

                        // Bỏ qua nếu thẻ cha chứa phần tử này cũng nằm trong danh sách (chỉ bấm nút sâu nhất)
                        if (el.querySelector('button, a')) continue;

                        el.setAttribute('data-bot-quest-target', String(validIdx++));
                    }
                    return validIdx;
                }""")

                logger.info(f"   -> Phát hiện {marked_count} nút tác vụ con có thể tương tác.")

                # 4. Lần lượt tương tác từng nút đã đánh dấu
                for t_idx in range(marked_count):
                    try:
                        target = page.locator(f'[data-bot-quest-target="{t_idx}"]').first
                        if not target.is_visible(timeout=1500):
                            continue

                        btn_label = (target.inner_text() or "Task Action").strip().replace("\n", " ")[:40]
                        task_key = f"{q_url}::{btn_label.strip().lower()}"
                        if state_store is not None and state_store.is_quest_task_attempted_today(task_key):
                            logger.info(f"      [{t_idx+1}/{marked_count}] [⏭] Bỏ qua tác vụ đã thử hôm nay: '{btn_label}'")
                            continue

                        logger.info(f"      [{t_idx+1}/{marked_count}] Đang bấm: '{btn_label}'...")

                        target.scroll_into_view_if_needed(timeout=2000)
                        page.wait_for_timeout(400)

                        # Ghi nhận danh sách tab hiện tại trước khi click
                        pages_before = len(page.context.pages)

                        # Thực hiện Trusted Click (kích hoạt chuẩn React-Aria usePress)
                        try:
                            target.click(force=True, timeout=3000)
                        except Exception:
                            # Fallback focus + Enter cho React-Aria
                            try:
                                target.focus()
                                page.keyboard.press("Enter")
                            except Exception:
                                target.evaluate("el => el.click()")

                        # Chờ 2 giây để xem có mở tab mới hoặc popup protocol không
                        page.wait_for_timeout(2000)

                        # Gửi phím Escape để tự động Cancel nếu trình duyệt hiện popup hỏi mở App/Windows Store
                        try:
                            page.keyboard.press("Escape")
                        except Exception:
                            pass

                        # Kiểm tra nếu có tab mới mở ra
                        current_pages = page.context.pages
                        if len(current_pages) > pages_before:
                            sub_tab = current_pages[-1]
                            try:
                                sub_tab.wait_for_load_state("domcontentloaded", timeout=6000)
                                sub_tab.wait_for_timeout(2000)

                                lower_btn = btn_label.lower()
                                is_search_action = any(k in lower_btn for k in ["search", "tìm kiếm", "find", "costume", "decor"])
                                search_box = sub_tab.locator("input[name='q'], textarea[name='q']").first

                                if is_search_action or (search_box.count() > 0 and search_box.is_visible(timeout=2500)):
                                    if search_box.is_visible(timeout=4000):
                                        # Gõ từ khóa thực tế và Enter
                                        keywords = ["halloween costume ideas", "spooky decor trends", "autumn travel destinations"]
                                        chosen_kw = random.choice(keywords)
                                        logger.info(f"       [*] Thực hiện tìm kiếm thật trên tab Quest: '{chosen_kw}'...")
                                        search_box.click()
                                        search_box.fill(chosen_kw)
                                        sub_tab.keyboard.press("Enter")
                                        sub_tab.wait_for_load_state("domcontentloaded", timeout=8000)
                                        sub_tab.wait_for_timeout(random.randint(4000, 6000))
                                        # Cuộn nhẹ trang kết quả để server ghi nhận tương tác
                                        sub_tab.mouse.wheel(0, random.randint(300, 600))
                                        sub_tab.wait_for_timeout(1500)
                                    else:
                                        sub_tab.wait_for_timeout(random.randint(3000, 4500))
                                else:
                                    # Nếu tab mới là bài Quiz/Poll thì giải luôn
                                    from core.activities import solve_interactive_tab
                                    solve_interactive_tab(sub_tab, card_title=btn_label)
                                    sub_tab.mouse.wheel(0, 300)
                                    sub_tab.wait_for_timeout(1500)
                            except Exception as e:
                                logger.warning(f"       [!] Lỗi khi xử lý tab Quest: {e}")
                            finally:
                                try:
                                    sub_tab.close()
                                except Exception:
                                    pass
                            logger.info(f"      [✓] Đã xử lý và đóng tab phụ cho '{btn_label}'.")
                        else:
                            logger.info(f"      [✓] Đã gửi Click & Cancel tại chỗ cho '{btn_label}'.")

                        page.wait_for_timeout(1000)

                        # Nếu click làm trang chính bị chuyển hướng khỏi trang Quest thì quay lại
                        if "/earn/quest/" not in page.url:
                            page.goto(q_url, wait_until="domcontentloaded", timeout=12000)
                            page.wait_for_timeout(2000)

                        # Kiểm tra tiến độ sau khi tương tác nút; nếu không tăng điểm/task thì đánh dấu đã thử hôm nay
                        after_done_cnt = before_done_cnt
                        try:
                            after_body = page.locator("body").inner_text()
                            after_match = re.search(r'Status:\s*(\d+)\s*/\s*(\d+)\s*tasks', after_body, re.IGNORECASE)
                            if after_match:
                                after_done_cnt = int(after_match.group(1))
                        except Exception:
                            pass

                        if state_store is not None and after_done_cnt <= before_done_cnt:
                            state_store.mark_quest_task_attempted_today(task_key)
                        before_done_cnt = max(before_done_cnt, after_done_cnt)

                    except Exception as e:
                        logger.warning(f"      [!] Bỏ qua nút #{t_idx+1}: {e}")

                logger.info(f"   [✓] Hoàn tất xử lý Quest {idx+1}")

            except Exception as e:
                logger.warning(f"   [!] Lỗi khi xử lý Quest {idx+1}: {e}")
                continue

    except Exception as e:
        logger.warning(f"[!] Lỗi trong Universal Quest Resolver: {e}")
    finally:
        logger.info("============================================================")
        logger.info("   HOÀN THÀNH: UNIVERSAL QUEST RESOLVER")
        logger.info("============================================================\n")


handle_universal_quest_resolver = solve_all_earn_quests


def _read_keep_earning_points(page: Page) -> Optional[tuple[int, int]]:
    """Đọc tiến độ điểm (X, Y) của khu vực 'Keep earning' trên /earn."""
    try:
        raw = page.evaluate("""() => {
            const sec = document.querySelector('section#moreactivities') ||
                        document.querySelector('section[id*="moreactivities"]') ||
                        Array.from(document.querySelectorAll('section')).find(s => (s.innerText || '').toLowerCase().includes('keep earning'));
            if (!sec) return null;
            const m = (sec.innerText || '').match(/(\\d+)\\s*\\/\\s*(\\d+)/);
            return m ? [parseInt(m[1], 10), parseInt(m[2], 10)] : null;
        }""")
        if isinstance(raw, (list, tuple)) and len(raw) == 2:
            return int(raw[0]), int(raw[1])
    except Exception:
        pass
    return None


def solve_keep_earning_activities(page: Page, context: Optional[BrowserContext] = None, state_store=None) -> int:
    """
    Quét và giải quyết tất cả các thẻ nhiệm vụ lẻ (+5, +10, +15, +20 điểm)
    trong khu vực 'Keep earning' tại https://rewards.bing.com/earn (hoặc /earn?section=levelup).
    
    Hỗ trợ giao diện React-Aria (Disclosure/Tailwind) mới của Microsoft Rewards.
    Tự động mở rộng danh sách nếu đang thu gọn, lọc các thẻ chưa hoàn thành,
    tương tác qua tab phụ, duy trì 4-6s với 2 nhịp cuộn, giải quiz/poll/search offer nếu có,
    và tự động nhận thưởng (claim).
    """
    from core.tracker import logger, _log_info, _log_warning, _log_error, _log_debug
    from core.activities import dismiss_popups, claim_all_available_rewards, solve_interactive_tab

    global _last_keep_earning_done
    _last_keep_earning_done = False

    if context is None:
        context = page.context

    _log_info("============================================================")
    _log_info("   BẮT ĐẦU: QUÉT MỤC 'KEEP EARNING' TẠI /EARN")
    _log_info("============================================================")

    earn_url = "https://rewards.bing.com/earn"
    page.on("dialog", lambda dialog: dialog.dismiss())

    try:
        # 1. Đảm bảo bot mở đúng trang /earn chính (không phải trang con /earn/quest/...)
        parsed_url = urllib.parse.urlparse(page.url)
        if parsed_url.path.rstrip('/') != "/earn":
            _log_info(f"[Keep Earning] Điều hướng tới {earn_url}...")
            page.goto(earn_url, wait_until="domcontentloaded", timeout=15000)
            dismiss_popups(page)
            page.wait_for_timeout(2000)
        else:
            dismiss_popups(page)
            page.wait_for_timeout(1000)

        # 2. Tìm container "Keep earning" (thường là section#moreactivities)
        sec_selector = "section#moreactivities, section[id*='moreactivities']"
        try:
            page.wait_for_selector(sec_selector, timeout=8000)
        except Exception:
            pass

        sec = page.locator(sec_selector).first
        if not sec.is_visible(timeout=3000):
            sec = page.locator("section:has(h2:has-text('Keep earning')), section:has-text('Keep earning')").first

        if not sec.is_visible(timeout=3000):
            _log_warning("[Keep Earning] Không tìm thấy khu vực 'Keep earning' trên trang /earn.")
            return 0

        # Cuộn trang xuống khu vực "Keep earning"
        try:
            sec.scroll_into_view_if_needed(timeout=3000)
            page.wait_for_timeout(1000)
        except Exception:
            pass

        # Đọc thông tin tiến độ hiện tại (VD: "70/70" hoặc "55/70")
        initial_pts = _read_keep_earning_points(page)
        initial_progress = f"{initial_pts[0]}/{initial_pts[1]}" if initial_pts else ""
        if initial_progress:
            _log_info(f"[Keep Earning] Tiến độ hiện tại: {initial_progress}")

        # 3. Nếu danh sách "Keep earning" đang bị thu gọn, bấm nút mũi tên mở rộng
        is_collapsed = page.evaluate("""() => {
            const sec = document.querySelector('section#moreactivities') || 
                        document.querySelector('section[id*="moreactivities"]') ||
                        Array.from(document.querySelectorAll('section')).find(s => (s.innerText || '').toLowerCase().includes('keep earning'));
            if (!sec) return false;
            const disc = sec.querySelector('.react-aria-Disclosure, [data-rac]');
            if (disc && disc.getAttribute('data-expanded') === 'false') return true;
            const btn = sec.querySelector('button[aria-expanded], [role="button"][aria-expanded]');
            if (btn && btn.getAttribute('aria-expanded') === 'false') return true;
            return false;
        }""")

        if is_collapsed:
            _log_info("[Keep Earning] Danh sách 'Keep earning' đang bị thu gọn, tiến hành mở rộng...")
            disc_btn = sec.locator("button[aria-expanded], [role='button'][aria-expanded]").first
            if disc_btn.is_visible(timeout=2000):
                try:
                    disc_btn.click(force=True, timeout=3000)
                except Exception:
                    try:
                        disc_btn.focus()
                        page.keyboard.press("Enter")
                    except Exception:
                        disc_btn.evaluate("el => el.click()")
                page.wait_for_timeout(1500)

        # 4. Vòng lặp quét và xử lý từng thẻ chưa hoàn thành
        max_attempts = 15
        processed_count = 0
        skipped_titles: set[str] = set(state_store.get_attempted_offers_today()) if state_store is not None else set()
        tried_hrefs: set[str] = set()
        tried_norm_titles: set[str] = set()

        for attempt in range(max_attempts):
            skip_norm_list = list(skipped_titles | tried_norm_titles)
            # Quét DOM và tìm thẻ chưa hoàn thành tiếp theo (bỏ qua thẻ đã thử / nằm trong skip list)
            next_card = page.evaluate(r"""([triedHrefs, skipNormTitles]) => {
                const sec = document.querySelector('section#moreactivities') || 
                            document.querySelector('section[id*="moreactivities"]') || 
                            Array.from(document.querySelectorAll('section')).find(s => (s.innerText || '').toLowerCase().includes('keep earning'));
                if (!sec) return null;

                const triedHrefSet = new Set((triedHrefs || []).map(x => String(x || '').trim()).filter(Boolean));
                const skipTitleSet = new Set((skipNormTitles || []).map(x => String(x || '').trim().toLowerCase()).filter(Boolean));
                const normalizeTitle = (raw) => {
                    return String(raw || '')
                        .replace(/\(\+\d+\s*pts?\)/gi, '')
                        .replace(/\+\d+\s*pts?\b/gi, '')
                        .replace(/\s+/g, ' ')
                        .trim()
                        .toLowerCase();
                };

                // Xóa đánh dấu cũ
                document.querySelectorAll('[data-bot-keep-earning-target]').forEach(el => el.removeAttribute('data-bot-keep-earning-target'));

                const rawCards = Array.from(sec.querySelectorAll('.react-aria-DisclosurePanel a, a[data-rac], [role="group"] a, a.group\\/ctrl, a, [role="button"], [role="link"]'));
                const seenEls = new Set();
                const candidates = [];
                for (const el of rawCards) {
                    if (!seenEls.has(el)) {
                        seenEls.add(el);
                        candidates.push(el);
                    }
                }
                
                for (let i = 0; i < candidates.length; i++) {
                    const el = candidates[i];
                    const href = (el.href || '').trim();
                    const text = (el.innerText || '').trim();
                    const lowerText = text.toLowerCase();
                    const title = text.split('\n')[0].trim() || `Thẻ #${i + 1}`;
                    const normTitle = normalizeTitle(title);

                    if ((href && triedHrefSet.has(href)) || (normTitle && skipTitleSet.has(normTitle))) {
                        continue;
                    }

                    // 1. Kiểm tra đã hoàn thành
                    const isCompleted = lowerText.includes('completed') || 
                                        lowerText.includes('đã hoàn thành') ||
                                        lowerText.includes('hoàn thành') ||
                                        !!el.querySelector('[class*="check" i], [class*="statusSuccess" i], svg[class*="success" i]');
                    if (isCompleted) continue;

                    // 2. Điểm số (badge +5, +10, +15, +20,...)
                    const pointMatch = text.match(/\+(\d+)/);
                    let pts = pointMatch ? parseInt(pointMatch[1], 10) : 0;
                    if (!pts) {
                        const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
                        for (const line of lines) {
                            const m = line.match(/^\+(\d+)$/) || line.match(/^(\d+)$/);
                            if (m && [5, 10, 15, 20, 25, 30, 40, 50].includes(parseInt(m[1], 10))) {
                                pts = parseInt(m[1], 10);
                                break;
                            }
                        }
                    }

                    // 3. Link đặc trưng của Bing Rewards
                    const isTargetUrl = href.includes('PUBL=RewardsDO') || 
                                        href.includes('form=ML2BFU') || 
                                        href.includes('form=ML2W4J') || 
                                        href.includes('form=airwpc') || 
                                        href.includes('form=ML2BF0') || 
                                        href.includes('form=ML2BF1') ||
                                        (href.includes('bing.com') && (href.includes('search') || href.includes('spotlight')));

                    // 4. Bỏ qua các thẻ không cộng điểm trực tiếp
                    const isIgnored = href.startsWith('microsoft-edge:') ||
                                      lowerText.includes('turn referrals into rewards') ||
                                      lowerText.includes('refer and earn') ||
                                      (lowerText.includes('search bar') && !pointMatch);
                    if (isIgnored) continue;

                    if (pts > 0 || isTargetUrl) {
                        el.setAttribute('data-bot-keep-earning-target', 'active');
                        return {
                            title: title,
                            pts: pts,
                            href: href
                        };
                    }
                }
                return null;
            }""", [list(tried_hrefs), skip_norm_list])

            if not next_card:
                _last_keep_earning_done = True
                if attempt == 0:
                    _log_info(f"[Keep Earning] [✓] Tất cả thẻ nhiệm vụ trong 'Keep earning' đã hoàn thành hoặc đã skip ({initial_progress or '70/70'}).")
                else:
                    _log_info("[Keep Earning] [✓] Không còn thẻ nhiệm vụ chưa hoàn thành.")
                break

            title = next_card.get("title", "Task")
            pts = next_card.get("pts", 0)
            pts_str = f"+{pts}" if pts else "+XX"
            href = (next_card.get("href", "") or "").strip()
            norm_title = normalize_offer_title(title)
            combined_skip = skipped_titles | tried_norm_titles

            # Tránh lặp vô hạn nhưng KHÔNG break để các thẻ #2, #3 phía sau vẫn được xử lý
            if (href and href in tried_hrefs) or (norm_title and norm_title in combined_skip):
                _log_warning(f"[Keep Earning] Bỏ qua thẻ đã thử/skip trước đó: {title}")
                if href:
                    tried_hrefs.add(href)
                if norm_title:
                    tried_norm_titles.add(norm_title)
                page.evaluate("() => { const el = document.querySelector('[data-bot-keep-earning-target=\"active\"]'); if (el) el.removeAttribute('data-bot-keep-earning-target'); }")
                continue

            if href:
                tried_hrefs.add(href)
            if norm_title:
                tried_norm_titles.add(norm_title)

            before_pts = _read_keep_earning_points(page)
            _log_info(f"[Keep Earning] Đang xử lý thẻ: {title} ({pts_str} pts)...")

            # Định vị thẻ qua attribute vừa đánh dấu
            card_loc = page.locator('[data-bot-keep-earning-target="active"]').first
            try:
                card_loc.scroll_into_view_if_needed(timeout=2000)
                page.wait_for_timeout(300)
            except Exception:
                pass

            sub_tab = None
            try:
                # Click thẻ hoặc mở trong tab phụ
                try:
                    with context.expect_page(timeout=6000) as new_page_info:
                        try:
                            card_loc.click(force=True, timeout=3000)
                        except Exception:
                            try:
                                card_loc.focus()
                                page.keyboard.press("Enter")
                            except Exception:
                                card_loc.evaluate("el => el.click()")
                    sub_tab = new_page_info.value
                except Exception:
                    if href and href.startswith("http"):
                        _log_debug(f"[Keep Earning] Mở trực tiếp href trong tab phụ: {href[:60]}...")
                        sub_tab = context.new_page()
                        sub_tab.goto(href, wait_until="domcontentloaded", timeout=15000)
                    else:
                        _log_warning(f"[Keep Earning] Không thể mở tab cho thẻ: {title}")
                        if state_store is not None and norm_title:
                            state_store.mark_offer_attempted_today(title)
                            skipped_titles.add(norm_title)
                        continue

                # Tương tác trên tab phụ: Duy trì 4 - 6s, cuộn chuột nhẹ 2 nhịp, giải quiz/poll/search offer nếu có
                if sub_tab:
                    tab_start_time = time.time()
                    target_dwell_sec = random.uniform(3.0, 4.5)
                    try:
                        try:
                            sub_tab.wait_for_load_state("domcontentloaded", timeout=8000)
                        except Exception:
                            pass

                        # Nhịp cuộn 1
                        page.wait_for_timeout(random.randint(800, 1200))
                        try:
                            sub_tab.mouse.wheel(0, random.randint(300, 500))
                        except Exception:
                            pass
                        page.wait_for_timeout(random.randint(800, 1200))

                        # Tự động giải trắc nghiệm / poll / quiz / Bing Search Offer nếu có
                        try:
                            solve_interactive_tab(sub_tab, card_title=title)
                        except Exception as e:
                            _log_debug(f"[Keep Earning] Interactive solver note: {e}")

                        # Nhịp cuộn 2
                        try:
                            sub_tab.mouse.wheel(0, random.randint(200, 400))
                        except Exception:
                            pass

                        # Đảm bảo duy trì đủ 4 - 6 giây trên tab
                        elapsed = time.time() - tab_start_time
                        if elapsed < target_dwell_sec:
                            page.wait_for_timeout(int((target_dwell_sec - elapsed) * 1000))

                    finally:
                        try:
                            sub_tab.close()
                        except Exception:
                            pass

                    processed_count += 1
                    _log_info(f"[Keep Earning] [✓] Đã tương tác xong thẻ: {title} ({pts_str} pts).")

            except Exception as e:
                _log_warning(f"[Keep Earning] [!] Lỗi khi xử lý thẻ '{title}': {e}")
                if sub_tab and not sub_tab.is_closed():
                    try:
                        sub_tab.close()
                    except Exception:
                        pass

            # Quay lại trang /earn và nhận thưởng nếu có popup xu
            try:
                page.bring_to_front()
                page.wait_for_timeout(1500)
                if urllib.parse.urlparse(page.url).path.rstrip('/') != "/earn":
                    page.goto(earn_url, wait_until="domcontentloaded", timeout=12000)
                    page.wait_for_timeout(1500)
                dismiss_popups(page)
                claim_all_available_rewards(page)
            except Exception:
                pass

            # Kiểm tra điểm Keep Earning trước/sau và trạng thái thẻ; nếu không cộng điểm thì đưa vào skip list trong ngày
            after_pts = _read_keep_earning_points(page)
            pts_increased = (
                before_pts is not None
                and after_pts is not None
                and after_pts[0] > before_pts[0]
            )
            card_completed_now = False
            try:
                card_completed_now = bool(page.evaluate(r"""([targetHref, targetNorm]) => {
                    const sec = document.querySelector('section#moreactivities') ||
                                document.querySelector('section[id*="moreactivities"]') ||
                                Array.from(document.querySelectorAll('section')).find(s => (s.innerText || '').toLowerCase().includes('keep earning'));
                    if (!sec) return false;
                    const normalizeTitle = (raw) => String(raw || '')
                        .replace(/\(\+\d+\s*pts?\)/gi, '')
                        .replace(/\+\d+\s*pts?\b/gi, '')
                        .replace(/\s+/g, ' ')
                        .trim()
                        .toLowerCase();
                    const els = Array.from(sec.querySelectorAll('a, [role="button"], [role="link"]'));
                    for (const el of els) {
                        const href = (el.href || '').trim();
                        const text = (el.innerText || '').trim();
                        const title = text.split('\n')[0].trim();
                        if ((targetHref && href === targetHref) || (targetNorm && normalizeTitle(title) === targetNorm)) {
                            const lower = text.toLowerCase();
                            return lower.includes('completed') ||
                                   lower.includes('đã hoàn thành') ||
                                   lower.includes('hoàn thành') ||
                                   !!el.querySelector('[class*="check" i], [class*="statusSuccess" i], svg[class*="success" i]');
                        }
                    }
                    return false;
                }""", [href, norm_title]))
            except Exception:
                pass

            if state_store is not None and not pts_increased and not card_completed_now:
                state_store.mark_offer_attempted_today(title)
                if norm_title:
                    skipped_titles.add(norm_title)
                _log_info(f"[Keep Earning] Thẻ '{title}' chưa tăng điểm sau khi xử lý -> ghi nhận vào skip list hôm nay.")

        # 5. Kiểm tra và ghi nhận tiến độ cuối
        final_pts = _read_keep_earning_points(page)
        final_progress = f"{final_pts[0]}/{final_pts[1]}" if final_pts else ""
        if final_pts and final_pts[1] > 0 and final_pts[0] >= final_pts[1]:
            _last_keep_earning_done = True
        if final_progress:
            _log_info(f"[Keep Earning] Tiến độ sau khi quét: {final_progress}")

        _log_info(f"[Keep Earning] Hoàn tất: Đã xử lý {processed_count} thẻ trong mục 'Keep earning'.")
        return processed_count

    except Exception as e:
        _log_warning(f"[Keep Earning] Lỗi trong quá trình quét Keep Earning: {e}")
        return 0
    finally:
        _log_info("============================================================")
        _log_info("   HOÀN TẤT: QUÉT MỤC 'KEEP EARNING'")
        _log_info("============================================================\n")


scan_keep_earning_section = solve_keep_earning_activities


consecutive_timeouts: int = 0


def reset_consecutive_timeouts() -> None:
    """Reset biến đếm lỗi timeout liên tiếp của các thẻ phụ."""
    global consecutive_timeouts
    consecutive_timeouts = 0


def claim_msn_daily_gaming_tile(page: Page):
    """Quét và click thẻ nhận điểm Daily Tile trên trang MSN Gaming."""
    global consecutive_timeouts
    from core.tracker import _logger as logger
    from core.activities import dismiss_popups
    if consecutive_timeouts >= 2:
        logger.warning("  [!] Bỏ qua Daily Gaming Tile do đã gặp 2 Timeout liên tiếp ở các thẻ phụ.")
        return
    logger.info("--- BẮT ĐẦU: QUÉT DAILY GAMING TILE (MSN PLAY) ---")
    try:
        page.goto("https://www.msn.com/en-us/play", wait_until="commit", timeout=15000)
        dismiss_popups(page)
        page.wait_for_timeout(3500)
        
        tile_selectors = [
            '[data-bi-name*="reward" i]',
            'div:has-text("+10")',
            'div:has-text("+50")',
            'button:has-text("Play to earn")',
            '[aria-label*="rewards" i]'
        ]
        clicked = False
        for sel in tile_selectors:
            tiles = page.locator(sel).all()
            for tile in tiles:
                if tile.is_visible():
                    try:
                        tile.click(force=True, timeout=2000)
                    except Exception:
                        tile.evaluate("el => el.click()")
                    page.wait_for_timeout(2000)
                    clicked = True
                    logger.info("  [✓] Đã click thẻ Daily Gaming Tile.")
                    break
            if clicked:
                break
        if not clicked:
            logger.info("  [*] Không tìm thấy Gaming Tile khả dụng hôm nay.")
        consecutive_timeouts = 0
    except Exception as e:
        if "timeout" in type(e).__name__.lower() or "timeout" in str(e).lower():
            consecutive_timeouts += 1
            logger.warning(f"  [!] TimeoutError tại Daily Gaming Tile (consecutive={consecutive_timeouts}): {e}")
        else:
            consecutive_timeouts = 0
            logger.warning(f"  [!] Bỏ qua Gaming Tile do lỗi: {e}")
    finally:
        logger.info("--- KẾT THÚC: DAILY GAMING TILE ---\n")


def solve_wallpaper_trivia(page: Page):
    """Mở mini-quiz ảnh nền trang chủ Bing và dùng Local AI giải."""
    global consecutive_timeouts
    from core.tracker import _logger as logger
    from core.activities import dismiss_popups
    from core.ai_solver import ask_local_ai
    if consecutive_timeouts >= 2:
        logger.warning("  [!] Bỏ qua Wallpaper Trivia do đã gặp 2 Timeout liên tiếp ở các thẻ phụ.")
        return
    logger.info("--- BẮT ĐẦU: MINI-QUIZ HÌNH NỀN BING ---")
    try:
        page.goto("https://www.bing.com/", wait_until="commit", timeout=12000)
        dismiss_popups(page)
        page.wait_for_timeout(3500)
        
        # Click icon info / trivia góc phải dưới màn hình
        info_btn = page.locator('#sh_cp, #id_p, .trivia-entry, [aria-label*="info" i]').first
        if info_btn.is_visible(timeout=3000):
            info_btn.click(force=True)
            page.wait_for_timeout(1500)
            
            # Kiểm tra xem có câu hỏi trắc nghiệm không
            q_elem = page.locator('.trivia_question, .b_focusTextExtra, [class*="question"]').first
            if q_elem.is_visible(timeout=2000):
                question_text = q_elem.inner_text().strip()
                choices = page.locator('.trivia_choice, input[type="radio"], [role="button"][class*="option"]').all()
                options = [c.inner_text().strip() for c in choices if c.inner_text().strip()]
                
                if options:
                    logger.info(f"  [*] Câu hỏi hình nền: {question_text}")
                    best_ans = ask_local_ai(question_text, options)
                    if best_ans:
                        page.locator(f"text='{best_ans}'").first.click(force=True)
                    else:
                        choices[0].click(force=True)
                    page.wait_for_timeout(2000)
                    logger.info("  [✓] Đã trả lời xong Quiz hình nền.")
        else:
            logger.info("  [*] Không có icon trivia trên ảnh nền hôm nay.")
        consecutive_timeouts = 0
    except Exception as e:
        if "timeout" in type(e).__name__.lower() or "timeout" in str(e).lower():
            consecutive_timeouts += 1
            logger.warning(f"  [!] TimeoutError tại Wallpaper Trivia (consecutive={consecutive_timeouts}): {e}")
        else:
            consecutive_timeouts = 0
            logger.warning(f"  [!] Bỏ qua Wallpaper Trivia: {e}")
    finally:
        logger.info("--- KẾT THÚC: MINI-QUIZ HÌNH NỀN ---\n")


def interact_with_copilot(page: Page, query: str = "Thời tiết hôm nay"):
    """Mở Bing Chat/Copilot và gửi 1 truy vấn tương tác."""
    global consecutive_timeouts
    from core.tracker import _logger as logger
    from core.activities import dismiss_popups
    if consecutive_timeouts >= 2:
        logger.warning("  [!] Bỏ qua Copilot Chat do đã gặp 2 Timeout liên tiếp ở các thẻ phụ.")
        return
    logger.info("--- BẮT ĐẦU: TƯƠNG TÁC COPILOT / BING CHAT ---")
    try:
        page.goto("https://www.bing.com/chat", wait_until="commit", timeout=15000)
        dismiss_popups(page)
        page.wait_for_timeout(3500)
        
        # Tìm ô nhập liệu của Copilot (hỗ trợ giao diện mới)
        chat_selectors = [
            'textarea#userInput',
            'textarea#searchbox',
            'textarea[placeholder*="Message" i]',
            'textarea[placeholder*="Ask" i]',
            'textarea[placeholder*="Hỏi" i]',
            'textarea'
        ]
        chat_box = None
        for sel in chat_selectors:
            try:
                box = page.locator(sel).first
                if box.is_visible(timeout=1500):
                    chat_box = box
                    break
            except Exception:
                continue
        
        if chat_box:
            chat_box.fill(query)
            page.keyboard.press("Enter")
            logger.info(f"  [*] Đã gửi truy vấn Copilot: '{query}'")
            page.wait_for_timeout(6000)
            logger.info("  [✓] Hoàn thành tương tác Copilot.")
        else:
            logger.info("  [*] Không tìm thấy ô chat Copilot.")
        consecutive_timeouts = 0
    except Exception as e:
        if "timeout" in type(e).__name__.lower() or "timeout" in str(e).lower():
            consecutive_timeouts += 1
            logger.warning(f"  [!] TimeoutError tại Copilot Chat (consecutive={consecutive_timeouts}): {e}")
        else:
            consecutive_timeouts = 0
            logger.warning(f"  [!] Bỏ qua tương tác Copilot: {e}")
    finally:
        logger.info("--- KẾT THÚC: COPILOT CHAT ---\n")


def solve_promotional_links(page: Page):
    """Đọc và truy cập các link thưởng khuyến mãi/newsletter từ data/promo_links.json."""
    import json
    from pathlib import Path
    from core.tracker import _logger as logger
    from core.activities import dismiss_popups
    
    promo_file = Path(__file__).resolve().parent.parent / "data" / "promo_links.json"
    if not promo_file.exists():
        return
        
    logger.info("--- BẮT ĐẦU: QUÉT LINK KHUYẾN MÃI / NEWSLETTER ---")
    try:
        with open(promo_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        links = data.get("pending_links", [])
        if not links:
            logger.info("  [*] Không có link khuyến mãi mới.")
            return

        completed = data.get("completed_links", [])
        for link in links:
            try:
                logger.info(f"  -> Đang truy cập promo: {link}")
                page.goto(link, wait_until="domcontentloaded", timeout=10000)
                page.wait_for_timeout(3000)
                completed.append(link)
            except Exception:
                pass
                
        # Cập nhật lại file
        with open(promo_file, "w", encoding="utf-8") as f:
            json.dump({"pending_links": [], "completed_links": completed}, f, indent=2)
        logger.info(f"  [✓] Đã xử lý {len(links)} link khuyến mãi.")
    except Exception as e:
        logger.warning(f"  [!] Lỗi khi xử lý promo links: {e}")
    finally:
        logger.info("--- KẾT THÚC: PROMOTIONAL LINKS ---\n")


_LAST_VS_SCREENSHOT: Optional[Path] = None


def _pick_local_screenshot(exclude_path: Optional[Path] = None) -> Optional[Path]:
    """
    Quét các thư mục ảnh cục bộ (assets/visual_search_images, assets, Screenshots, project root)
    và chọn ngẫu nhiên 1 file screenshot hợp lệ (tránh lặp lại file của lần thử trước).
    """
    global _LAST_VS_SCREENSHOT
    project_root = Path(__file__).resolve().parent.parent
    candidate_dirs = [
        project_root / "assets" / "visual_search_images",
        project_root / "assets",
        project_root / "Screenshots",
        project_root / "screenshots",
        project_root / "screenshot",
        project_root,
    ]

    valid_extensions = {".png", ".jpg", ".jpeg"}
    excluded_names = {"temp_vs_upload.jpg", "temp_viewport_capture.png", "visual_sample.png"}
    all_images: list[Path] = []
    seen_dirs: set[Path] = set()
    seen_files: set[Path] = set()

    for c_dir in candidate_dirs:
        if not (c_dir.exists() and c_dir.is_dir()):
            continue
        resolved_dir = c_dir.resolve()
        if resolved_dir in seen_dirs:
            continue
        seen_dirs.add(resolved_dir)
        is_root = resolved_dir == project_root.resolve()
        for f in c_dir.iterdir():
            if not f.is_file():
                continue
            if f.name.lower() in excluded_names:
                continue
            if f.suffix.lower() not in valid_extensions:
                continue
            if is_root and not (
                f.name.lower().startswith("screenshot")
                or f.suffix.lower() in {".png", ".jpg", ".jpeg"}
            ):
                continue
            try:
                if f.stat().st_size > 0:
                    rf = f.resolve()
                    if rf not in seen_files:
                        seen_files.add(rf)
                        all_images.append(rf)
            except OSError:
                continue

    if not all_images:
        logger.warning("   [Visual Search] Không tìm thấy file screenshot cục bộ nào!")
        return None

    avoid = exclude_path.resolve() if exclude_path is not None else _LAST_VS_SCREENSHOT
    pool = [p for p in all_images if avoid is None or p != avoid]
    if not pool:
        pool = all_images

    selected = random.choice(pool)
    _LAST_VS_SCREENSHOT = selected
    logger.info(
        f"   [Visual Search] Đã chọn ảnh cục bộ: '{selected.name}' "
        f"({round(selected.stat().st_size / 1024, 1)} KB)"
    )
    return selected


def get_random_screenshot_path() -> Optional[Path]:
    """Tương thích ngược: trả về đường dẫn 1 file screenshot cục bộ ngẫu nhiên."""
    return _pick_local_screenshot()


def create_random_sample_png(filepath: str = "data/visual_sample.png") -> str:
    """Tạo một file ảnh PNG 100x100 RGB hợp lệ có họa tiết màu ngẫu nhiên mỗi lần chạy."""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    width, height = 100, 100
    raw_data = bytearray()
    r_base = random.randint(30, 220)
    g_base = random.randint(30, 220)
    b_base = random.randint(30, 220)
    for y in range(height):
        raw_data.append(0)  # Filter type 0
        for x in range(width):
            raw_data.append((r_base + x) % 256)
            raw_data.append((g_base + y) % 256)
            raw_data.append((b_base + x + y) % 256)

    def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
        chunk = chunk_type + data
        return struct.pack(">I", len(data)) + chunk + struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)

    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + png_chunk(b"IDAT", zlib.compress(bytes(raw_data), 9))
        + png_chunk(b"IEND", b"")
    )
    path.write_bytes(png_bytes)
    return str(path.resolve())


def ensure_sample_image(filepath: str = "data/visual_sample.png") -> str:
    """Tạo file ảnh PNG mẫu 100x100 nếu chưa tồn tại (tương thích ngược)."""
    p = Path(filepath)
    if not p.exists() or p.stat().st_size == 0:
        return create_random_sample_png(filepath)
    return str(p.resolve())


def read_visual_search_streak_status(page: Page) -> dict:
    """Đọc chính xác trạng thái thẻ Visual Search Streak trên https://rewards.bing.com/earn."""
    try:
        return page.evaluate(r'''() => {
            // 1. Kiểm tra qua các progressbar (nếu có aria-label tương ứng)
            const bars = document.querySelectorAll('[role="progressbar"]');
            for (const bar of bars) {
                const label = (bar.getAttribute('aria-label') || '').trim().toLowerCase();
                if (label.includes('visual search') || label.includes('trực quan')) {
                    const cur = parseFloat(bar.getAttribute('aria-valuenow') || '0');
                    const max = parseFloat(bar.getAttribute('aria-valuemax') || '1');
                    const valText = (bar.getAttribute('aria-valuetext') || '').trim();
                    const done = (max > 0 && cur >= max) || valText === '100%';
                    return { cur, max, done };
                }
            }

            // 2. Tìm container của thẻ Visual Search Streak (thường có text 'Visual Search Streak' và 'Activity: 1/1')
            const allDivs = document.querySelectorAll('div');
            for (const d of allDivs) {
                const txt = (d.innerText || '').trim();
                if ((txt.startsWith('Visual Search Streak') || txt.startsWith('Tìm kiếm trực quan')) && txt.length < 350) {
                    const matches = Array.from(txt.matchAll(/(\d+)\s*\/\s*(\d+)/g));
                    if (matches.length > 0) {
                        const lastM = matches[matches.length - 1];
                        const c = parseInt(lastM[1]), mx = parseInt(lastM[2]);
                        return { cur: c, max: mx, done: mx > 0 && c >= mx };
                    }
                }
            }

            // 3. Fallback tìm theo substring 'Visual Search' trong body
            const textContent = document.body.innerText;
            const idx = textContent.indexOf('Visual Search');
            if (idx !== -1) {
                const sub = textContent.slice(idx, idx + 300);
                const matches = Array.from(sub.matchAll(/(\d+)\s*\/\s*(\d+)/g));
                if (matches.length > 0) {
                    const lastM = matches[matches.length - 1];
                    const c = parseInt(lastM[1]), mx = parseInt(lastM[2]);
                    return { cur: c, max: mx, done: mx > 0 && c >= mx };
                }
            }

            return { cur: 0, max: 1, done: false };
        }''')
    except Exception:
        return {"cur": 0, "max": 1, "done": False}


def is_valid_visual_search_result(url: str, target_page: Page = None) -> bool:
    """Xác nhận kết quả Visual Search THẬT (chấp nhận iss=sbiupload / bcid= / #vs_insights / .irsikc, từ chối explore.microsoft.com, FORM=IMVNXT, FORM=SBIRDI, bare FORM=SBIHMP, bare iss=sbi)."""
    url_lower = (url or "").lower()
    if "explore.microsoft.com" in url_lower:
        return False
    if "form=sbirdi" in url_lower:
        return False
    if "form=imvnxt" in url_lower:
        return False
    if "iss=sbiupload" in url_lower or "bcid=" in url_lower:
        return True
    if "form=sbihmp" in url_lower:
        return False
    if re.search(r"(?:^|[?&])iss=sbi(?:&|$)", url_lower):
        return False
    if "form=vsmagc" in url_lower and "visualsearch" not in url_lower:
        return True
    if "visualsearch" in url_lower and any(
        tok in url_lower for tok in ("imgurl=", "imgurl:", "sbiupload", "insights", "bcid=")
    ):
        return True
    if target_page is not None:
        try:
            cards = target_page.locator("#vs_insights, .insightsOverlay, .irsikc, #detailCanvas, .mivsb")
            if cards.count() > 0 and cards.first.is_visible(timeout=500):
                return True
        except Exception:
            pass
        try:
            for frame in getattr(target_page, "frames", []) or []:
                f_cards = frame.locator("#detailCanvas, .mivsb, #vs_insights, .irsikc")
                if f_cards.count() > 0 and f_cards.first.is_visible(timeout=300):
                    return True
        except Exception:
            pass
    return False


def _trigger_file_chooser_upload(
    target_page: Page,
    image_path: str,
    selectors: list[str],
    timeout_ms: int = 6000,
) -> bool:
    """Click first visible upload trigger inside #sbi_b to upload via Playwright FileChooser."""
    for browse_sel in selectors:
        try:
            browse_btn = target_page.locator(browse_sel).first
            if browse_btn.count() > 0 and browse_btn.is_visible(timeout=1500):
                logger.info(f"   [*] Kích hoạt File Chooser ({browse_sel})...")
                with target_page.expect_file_chooser(timeout=timeout_ms) as fc_info:
                    browse_btn.click(timeout=5000)
                file_chooser = fc_info.value
                file_chooser.set_files(str(image_path))
                target_page.wait_for_timeout(1200)
                logger.info("   [✓] Upload ảnh thành công qua File Chooser!")
                return True
        except Exception as e:
            logger.debug(f"   [!] expect_file_chooser thất bại với {browse_sel}: {e}")
            continue
    return False


def _set_file_input_upload(target_page: Page, image_path: str, selector: str) -> bool:
    """Fallback direct set_input_files + change event on the matching file input."""
    try:
        direct_inp = target_page.locator(selector).first
        if direct_inp.count() > 0:
            direct_inp.set_input_files(str(image_path))
            try:
                direct_inp.dispatch_event("change")
            except Exception:
                pass
            target_page.wait_for_timeout(1000)
            logger.info("   [✓] Upload ảnh thành công qua direct set_input_files!")
            return True
    except Exception as e:
        logger.debug(f"   [!] Direct set_input_files thất bại ({selector}): {e}")
    return False


def submit_imagebin_post(target_page: Page, image_path: str, timeout_sec: int = 10) -> bool:
    """
    Tier 3: Direct Base64 imageBin Form POST (iss=sbiupload, sbisrc=ImgPicker) trực tiếp từ trang vsstreak.
    Encode image_path sang raw Base64 và submit form POST lên Bing Visual Search:
    <form method="POST" enctype="multipart/form-data"
          action="https://www.bing.com/images/search?view=detailv2&iss=sbiupload&FORM=SBIHMP&sbisrc=ImgPicker">
      <input type="hidden" name="cbir" value="sbi">
      <input type="hidden" name="imageBin" value="<b64_image>">
    </form>
    Đợi tối đa timeout_sec (10s) cho iss=sbiupload / bcid= / #vs_insights.
    """
    try:
        img_p = Path(image_path).resolve()
        if not img_p.exists():
            logger.warning(f"   [!] Không tìm thấy ảnh để Base64 POST: {img_p}")
            return False

        with open(img_p, "rb") as f:
            b64_image = base64.b64encode(f.read()).decode("utf-8")

        logger.info(f"   [*] [Tier 3] Đang gửi form POST imageBin (sbisrc=ImgPicker, {len(b64_image)} ký tự Base64)...")
        cur_url = (target_page.url or "").lower()
        if "bing.com" not in cur_url:
            try:
                target_page.goto("https://www.bing.com/?features=vsstreak,vstooltip&form=ML2XES", wait_until="domcontentloaded", timeout=10000)
            except Exception:
                pass

        target_page.evaluate(r"""(b64) => {
            const form = document.createElement('form');
            form.method = 'POST';
            form.enctype = 'multipart/form-data';
            form.action = 'https://www.bing.com/images/search?view=detailv2&iss=sbiupload&FORM=SBIHMP&sbisrc=ImgPicker';

            const cbir = document.createElement('input');
            cbir.type = 'hidden';
            cbir.name = 'cbir';
            cbir.value = 'sbi';
            form.appendChild(cbir);

            const imageBin = document.createElement('input');
            imageBin.type = 'hidden';
            imageBin.name = 'imageBin';
            imageBin.value = b64;
            form.appendChild(imageBin);

            document.body.appendChild(form);
            form.submit();
        }""", b64_image)

        wait_start = time.time()
        while time.time() - wait_start < timeout_sec:
            if is_valid_visual_search_result(target_page.url, target_page):
                logger.info(f"   [✓] [Tier 3] imageBin POST thành công: {target_page.url}")
                return True
            target_page.wait_for_timeout(500)

        if is_valid_visual_search_result(target_page.url, target_page):
            return True
        logger.warning(f"   [!] Hết {timeout_sec}s chờ sau imageBin POST. URL: {target_page.url}")
        return False
    except Exception as e:
        logger.warning(f"   [!] Lỗi khi thực hiện imageBin POST: {e}")
        return False


def upload_visual_search_image(
    target_page: Page,
    image_path: str,
    prefer_file_chooser: bool = False,
    file_chooser_timeout: int = 5000,
) -> bool:
    """
    Upload ảnh Visual Search:
    - Khi prefer_file_chooser=True (luồng #sbi_b): ưu tiên expect_file_chooser(timeout=6000)
      khi bấm nút/label upload trong #sbi_b, fallback sang set_input_files, và cuối cùng fallback Base64 imageBin POST.
    - Mặc định (tương thích Phase 8.1): ưu tiên direct set_input_files rồi fallback expect_file_chooser(timeout=5000), sau đó fallback Base64 imageBin POST.
    """
    sbi_browse_selectors = [
        '#sb_sbipane #sb_brtext',
        '#sb_brtext',
        '#sbi_b #sb_brs',
        '#sbi_b label[for="sb_fileinput"]',
        '#sbi_b span:has-text("Duyệt")',
        '#sbi_b span:has-text("Browse")',
        '#sbi_b button:has-text("Tải ảnh")',
        '#sbi_b button:has-text("Upload")',
        '#sbi_b button:has-text("Duyệt")',
        '#sbi_b button:has-text("Browse")',
        '#sbi_b a:has-text("Duyệt")',
        '#sbi_b a:has-text("Browse")',
        '#sbi_b #sb_brtext',
        '#sb_brs',
        'label[for="sb_fileinput"]',
        'span:has-text("Duyệt")',
        'span:has-text("Browse")',
        'button:has-text("Tải ảnh")',
        'button:has-text("Upload")',
    ]
    if prefer_file_chooser:
        if _trigger_file_chooser_upload(
            target_page,
            str(image_path),
            sbi_browse_selectors,
            timeout_ms=file_chooser_timeout,
        ):
            return True
        if _set_file_input_upload(
            target_page,
            str(image_path),
            '#sb_fileinput, #sbi_b input[type="file"], input[type="file"]',
        ):
            return True
    else:
        direct_sel = 'input[type="file"][accept*="image"], input#sb_fileinput'
        if _set_file_input_upload(target_page, str(image_path), direct_sel):
            return True

        legacy_browse_selectors = [
            '#sbi_b button:has-text("Duyệt"), #sbi_b button:has-text("Browse"), #sbi_b button:has-text("Tải ảnh lên")',
            'span:has-text("Duyệt"), button:has-text("Browse")',
            '#sb_brtext',
            'label[for="sb_fileinput"]',
            'a:has-text("Browse")',
            'span:has-text("Browse")',
            'a:has-text("Duyệt")',
        ]
        if _trigger_file_chooser_upload(
            target_page,
            str(image_path),
            legacy_browse_selectors,
            timeout_ms=file_chooser_timeout,
        ):
            return True

    # Fallback Community Method: direct Base64 imageBin form POST
    logger.info("   [*] File chooser / input không khả dụng, dùng fallback Base64 imageBin form POST...")
    return submit_imagebin_post(target_page, image_path)


def _upload_via_sbi_surface(target_page: Page, image_path: str) -> bool:
    """Upload ảnh qua dropzone #sbi_b với expect_file_chooser(timeout=6000) ưu tiên."""
    return upload_visual_search_image(
        target_page,
        str(image_path),
        prefer_file_chooser=True,
        file_chooser_timeout=6000,
    )


def _prepare_visual_search_jpeg(
    page: Optional[Page] = None,
    exclude_path: Optional[Path] = None,
) -> Path:
    """
    Chọn ảnh screenshot cục bộ (tránh lặp lại file trước đó), cắt viền ngẫu nhiên 1-6px,
    resize cạnh dài về 520-640px và lưu data/temp_vs_upload.jpg dạng RGB JPEG
    với quality ngẫu nhiên 82-94 để mỗi lần upload có SHA256 hash khác nhau.
    """
    from PIL import Image

    img_path = _pick_local_screenshot(exclude_path=exclude_path)
    if (not img_path or not img_path.exists()) and page is not None:
        fallback_path = Path("./data/temp_viewport_capture.png").resolve()
        fallback_path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(fallback_path))
        img_path = fallback_path
        logger.info(f"   [Visual Search] Sử dụng fallback chụp viewport: '{img_path.name}'")

    upload_img_path = Path("./data/temp_vs_upload.jpg").resolve()
    upload_img_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(img_path) as im:
            if im.mode != "RGB":
                im = im.convert("RGB")
            w, h = im.size
            left = random.randint(1, 6)
            top = random.randint(1, 6)
            right = random.randint(1, 6)
            bottom = random.randint(1, 6)
            if w - (left + right) >= 64 and h - (top + bottom) >= 64:
                im = im.crop((left, top, w - right, h - bottom))
            w, h = im.size
            max_edge = max(w, h)
            target_edge = random.randint(520, 640)
            if max_edge > 0:
                scale = target_edge / float(max_edge)
                im = im.resize(
                    (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
                    Image.LANCZOS,
                )
            quality = random.randint(82, 94)
            im.save(upload_img_path, "JPEG", quality=quality, optimize=True)
        sha = hashlib.sha256(upload_img_path.read_bytes()).hexdigest()[:12]
        size_kb = round(upload_img_path.stat().st_size / 1024, 1)
        logger.info(
            f"   [*] Ảnh upload đã chuẩn hóa JPEG: {upload_img_path.name} "
            f"({size_kb} KB, quality={quality}, sha256={sha}...)"
        )
        return upload_img_path
    except Exception as e:
        logger.warning(f"   [!] Chuẩn hóa ảnh thất bại ({e}), tạo JPEG RGB dự phòng 640x500...")
        try:
            Image.new("RGB", (640, 500), (42, 118, 196)).save(
                upload_img_path, "JPEG", quality=random.randint(82, 94), optimize=True
            )
            return upload_img_path
        except Exception:
            return img_path if img_path is not None else upload_img_path


def _click_with_optional_new_tab(
    page_obj: Page,
    selectors: list[str],
    extra_tabs: list[Page],
    label: str,
) -> tuple[Page, bool]:
    """Click phần tử đầu tiên hiển thị, bắt tab mới qua expect_page(timeout=4000) nếu có."""
    for sel in selectors:
        try:
            loc = page_obj.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=1000) is True:
                try:
                    loc.scroll_into_view_if_needed(timeout=1500)
                except Exception:
                    pass
                logger.info(f"   [*] Click {label} ({sel})...")
                try:
                    with page_obj.context.expect_page(timeout=4000) as new_tab_info:
                        loc.click(timeout=5000)
                    new_tab = new_tab_info.value
                    if new_tab is not None and new_tab is not page_obj:
                        extra_tabs.append(new_tab)
                        try:
                            new_tab.wait_for_load_state("domcontentloaded", timeout=10000)
                        except Exception:
                            pass
                        return new_tab, True
                except Exception:
                    page_obj.wait_for_timeout(1200)
                return page_obj, True
        except Exception:
            continue
    return page_obj, False


def _ensure_sbi_dropzone_mounted(active_page: Page) -> None:
    """Đảm bảo hộp thoại camera #sbi_b / #sb_sbipane đã mount & visible (click #sb_sbip timeout=5000, đợi 2s)."""
    dropzone_sel = '#sbi_b, #sb_sbipane, #sb_brtext, label[for="sb_fileinput"], #sb_brs'
    try:
        if active_page.locator(dropzone_sel).first.is_visible(timeout=800) is True:
            return
    except Exception:
        pass

    cam_selectors = (
        '#sb_sbip',
        '.b_icon[aria-label*="hình ảnh" i]',
        '.b_icon[aria-label*="image" i]',
        '#sb_sbi',
        '#sb_camera',
        '.sbi_b',
    )
    cam_btn = None
    for sel in cam_selectors:
        try:
            cand = active_page.locator(sel).first
            if cand.count() > 0 and cand.is_visible(timeout=600) is True:
                cam_btn = cand
                break
        except Exception:
            continue
    if cam_btn is None:
        try:
            cand = active_page.locator('#sb_sbip').first
            if cand.count() > 0:
                cam_btn = cand
        except Exception:
            pass

    try:
        if cam_btn is not None and cam_btn.count() > 0:
            logger.info("   [*] Bấm #sb_sbip để mở bảng upload #sbi_b / #sb_sbipane...")
            try:
                cam_btn.click(timeout=5000)
            except Exception:
                cam_btn.click(force=True, timeout=5000)
            active_page.wait_for_timeout(2000)
            try:
                active_page.wait_for_selector(dropzone_sel, state="visible", timeout=3000)
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"   [!] Không thể bấm nút Camera Bing: {e}")


VSSTREAK_URL = "https://www.bing.com/?features=vsstreak,vstooltip&form=ML2XES"


def _open_visual_search_surface(page: Page) -> tuple[Page, list[Page]]:
    """
    Mở và giữ nguyên trang tracked vsstreak (features=vsstreak,vstooltip&form=ML2XES):
    1. Click thẻ Visual Search trên /earn -> click CTA modal ([role="dialog"] a[href*="vsstreak"],
       a[href*="features=vsstreak"], [role="dialog"] a[href*="bing.com"]) hoặc bắt tab mới context.pages[-1].
    2. Nếu không mở được modal CTA / tab mới (hoặc rơi vào explore.microsoft.com), điều hướng trực tiếp
       tới https://www.bing.com/?features=vsstreak,vstooltip&form=ML2XES.
    3. Không bao giờ ghi đè trang vsstreak bằng bing.com/images/search?...
    4. Click #sb_sbip (timeout=5000) và đợi 2s để #sbi_b / #sb_sbipane mount.
    """
    extra_tabs: list[Page] = []
    active_page = page
    pages_before = list(getattr(page.context, "pages", []) or [])

    card_selectors = [
        'button:has-text("Visual Search"), button:has-text("Tìm kiếm trực quan")',
        '[aria-label*="Visual Search" i], [aria-label*="Tìm kiếm trực quan" i]',
        'a[href*="visualsearch"], a[href*="visual-search"]',
        'a:has-text("Visual Search"), a:has-text("Tìm kiếm trực quan")',
        'div[role="button"]:has-text("Visual Search"), div[role="button"]:has-text("Tìm kiếm trực quan")',
    ]
    active_page, clicked_card = _click_with_optional_new_tab(
        page, card_selectors, extra_tabs, "thẻ Visual Search trên /earn"
    )

    if clicked_card and "rewards.bing.com" in (active_page.url or "").lower():
        modal_cta_selectors = [
            '[role="dialog"] a[href*="vsstreak"]',
            'a[href*="features=vsstreak"]',
            '[role="dialog"] a[href*="bing.com"]',
            '[role="dialog"] a[href*="ML2XES" i]',
            '[role="dialog"] button:has-text("Search now")',
            '[role="dialog"] button:has-text("Tìm kiếm ngay")',
            '[role="dialog"] a:has-text("Search now")',
            '[role="dialog"] a:has-text("Tìm kiếm ngay")',
        ]
        active_page, _ = _click_with_optional_new_tab(
            active_page, modal_cta_selectors, extra_tabs, "CTA modal Visual Search"
        )

    pages_after = list(getattr(page.context, "pages", []) or [])
    if len(pages_after) > len(pages_before):
        newest_tab = pages_after[-1]
        if newest_tab is not page and newest_tab not in extra_tabs:
            extra_tabs.append(newest_tab)
        if newest_tab is not page:
            active_page = newest_tab

    cur_url_lower = (active_page.url or "").lower()
    on_tracked_vsstreak = (
        "bing.com" in cur_url_lower
        and "rewards.bing.com" not in cur_url_lower
        and "explore.microsoft.com" not in cur_url_lower
        and "form=sbirdi" not in cur_url_lower
    )
    if not on_tracked_vsstreak:
        if active_page is page:
            active_page = page.context.new_page()
            extra_tabs.append(active_page)
        logger.info(f"   [*] Điều hướng trực tiếp tới tracked vsstreak URL: {VSSTREAK_URL}")
        active_page.goto(VSSTREAK_URL, wait_until="domcontentloaded", timeout=15000)

    active_page.wait_for_timeout(1500)
    dismiss_popups(active_page)
    _ensure_sbi_dropzone_mounted(active_page)
    return active_page, extra_tabs


def _is_vs_result_redirect_url(url: str) -> bool:
    """
    Kiểm tra URL đã chuyển hướng sang trang kết quả Visual Search/Bing Search sau khi upload Tier 1
    (chứa `bcid=`, `/images/search` hoặc `/search?q=`), loại trừ các trang landing/sample không hợp lệ.
    """
    url_lower = (url or "").lower()
    if not url_lower:
        return False
    if any(bad in url_lower for bad in ("explore.microsoft.com", "form=sbirdi", "form=imvnxt", "features=vsstreak")):
        return False
    return "bcid=" in url_lower or "/images/search" in url_lower or "/search?q=" in url_lower


def _wait_for_vs_transition(active_page: Page, timeout_sec: float, tier_label: str) -> bool:
    """Đợi tối đa timeout_sec giây cho URL kết quả (`bcid=`, `/images/search`, `/search?q=`, `iss=sbiupload`) hoặc #vs_insights."""
    def _check_page_confirmed(pg) -> Optional[str]:
        cur_url = getattr(pg, "url", "") or ""
        if is_valid_visual_search_result(cur_url, pg) or _is_vs_result_redirect_url(cur_url):
            return cur_url
        return None

    def _scan_context_pages() -> Optional[str]:
        confirmed = _check_page_confirmed(active_page)
        if confirmed:
            return confirmed
        ctx = getattr(active_page, "context", None)
        pages = getattr(ctx, "pages", None) if ctx is not None else None
        if isinstance(pages, (list, tuple)):
            for p_item in pages:
                if p_item is None or p_item is active_page:
                    continue
                try:
                    if hasattr(p_item, "is_closed") and p_item.is_closed() is True:
                        continue
                except Exception:
                    pass
                confirmed = _check_page_confirmed(p_item)
                if confirmed:
                    return confirmed
        return None

    wait_start = time.time()
    while time.time() - wait_start < timeout_sec:
        matched_url = _scan_context_pages()
        if matched_url:
            logger.info(f"   [✓] [{tier_label}] Đã xác nhận kết quả Visual Search: {matched_url}")
            return True
        active_page.wait_for_timeout(500)

    matched_url = _scan_context_pages()
    if matched_url:
        logger.info(f"   [✓] [{tier_label}] Đã xác nhận kết quả Visual Search: {matched_url}")
        return True
    return False


def _dispatch_datatransfer_drop(active_page: Page, image_path: str) -> bool:
    """
    Tier 2: Dựng File trong DataTransfer qua page.evaluate(...) và phát sự kiện
    dragenter, dragover, drop lên #sbi_dropzone, #sbi_b, #sb_sbipane.
    """
    try:
        img_p = Path(image_path).resolve()
        if not img_p.exists():
            return False
        b64_image = base64.b64encode(img_p.read_bytes()).decode("utf-8")
        logger.info("   [*] [Tier 2] Đang phát sự kiện DataTransfer drop lên #sbi_dropzone / #sbi_b...")
        dispatched = active_page.evaluate(
            r"""([b64, filename]) => {
                const target = document.querySelector('#sbi_dropzone, #sbi_b, #sb_sbipane');
                if (!target) return false;
                const bin = atob(b64);
                const arr = new Uint8Array(bin.length);
                for (let i = 0; i < bin.length; i++) {
                    arr[i] = bin.charCodeAt(i);
                }
                const file = new File([arr], filename, { type: 'image/jpeg' });
                const dt = new DataTransfer();
                dt.items.add(file);
                for (const evName of ['dragenter', 'dragover', 'drop']) {
                    const ev = new DragEvent(evName, {
                        bubbles: true,
                        cancelable: true,
                        dataTransfer: dt
                    });
                    target.dispatchEvent(ev);
                }
                return true;
            }""",
            [b64_image, img_p.name],
        )
        return bool(dispatched)
    except Exception as e:
        logger.debug(f"   [!] [Tier 2] DataTransfer drop thất bại: {e}")
        return False


def _trigger_detail_v2_visual_search(active_page: Page) -> bool:
    """
    Fallback đã xác minh trên live DOM khi /images/kblob trả 400:
    Mở https://www.bing.com/images?FORM=SBIVSP, click thẻ carousel detailV2 (FORM=IMVNXT / idpview=singleimage),
    và bấm nút 'Tìm kiếm Trực quan' (.mivsb) trong detail iframe.
    """
    try:
        cur_url_lower = (active_page.url or "").lower()
        if "bing.com/images" not in cur_url_lower or "view=detailv2" in cur_url_lower:
            sbivsp_url = "https://www.bing.com/images?FORM=SBIVSP"
            logger.info(f"   [*] Mở {sbivsp_url} để kích hoạt Visual Search detailV2...")
            active_page.goto(sbivsp_url, wait_until="domcontentloaded", timeout=15000)
            active_page.wait_for_timeout(2000)
            dismiss_popups(active_page)

        card_selectors = (
            'a[href*="/images/search?view=detailV2"]',
            'a[href*="view=detailV2"]',
            'a[href*="FORM=IMVNXT"]',
        )
        clicked = False
        for sel in card_selectors:
            try:
                loc = active_page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=1500) is True:
                    logger.info(f"   [*] Click thẻ Visual Search detailV2 ({sel})...")
                    loc.click(timeout=5000)
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            return False

        active_page.wait_for_timeout(2500)
        for frame in getattr(active_page, "frames", []) or []:
            try:
                mivsb = frame.locator('.mivsb, [aria-label*="Tìm kiếm Trực quan" i], [aria-label*="Visual Search" i]').first
                if mivsb.count() > 0 and mivsb.is_visible(timeout=1000) is True:
                    logger.info("   [*] Bấm nút 'Tìm kiếm Trực quan' (.mivsb) trong detailV2...")
                    mivsb.click(timeout=4000)
                    active_page.wait_for_timeout(1500)
                    break
            except Exception:
                continue

        return is_valid_visual_search_result(active_page.url, active_page)
    except Exception as e:
        logger.debug(f"   [!] Fallback detailV2 Visual Search lỗi: {e}")
        return False


def _close_extra_tabs(main_page: Page, extra_tabs: list[Page]) -> None:
    for tab in extra_tabs:
        if tab is not None and tab is not main_page:
            try:
                if not tab.is_closed():
                    tab.close()
            except Exception:
                pass


def handle_visual_search_streak(page: Page, state_store=None) -> bool:
    """
    Thực hiện Visual Search Streak 100% bằng ảnh screenshot cục bộ trên trang tracked vsstreak:
    1. Mở /earn -> click thẻ Visual Search -> CTA modal hoặc tab mới -> fallback về
       https://www.bing.com/?features=vsstreak,vstooltip&form=ML2XES (không ghi đè trang này).
    2. Click #sb_sbip và đợi #sbi_b / #sb_sbipane mount.
    3. Thực thi upload 3 tầng trực tiếp trên trang vsstreak:
       - Tier 1: File Chooser (timeout=4500) / #sb_fileinput set_input_files + change (đợi 7s).
       - Tier 2: Synthetic DataTransfer drop lên #sbi_dropzone, #sbi_b, #sb_sbipane (đợi 8s).
       - Tier 3: multipart/form-data imageBin Form POST với sbisrc=ImgPicker (đợi 10s).
    4. Click vào kết quả visual match (.irsikc a, #vs_insights a, .richImgLnk, .iusc),
       cuộn & dwell 7s, đóng tab phụ, đợi 3s và kiểm tra get_daily_search_progress(page).
    """
    logger.info("--- BẮT ĐẦU: VISUAL SEARCH STREAK (LOCAL SCREENSHOT FLOW) ---")
    max_attempts = 3

    if state_store is not None and state_store.get_visual_search_attempts_today() >= max_attempts:
        logger.info(
            f"   [*] Visual Search đã đạt giới hạn {max_attempts}/{max_attempts} lần thử trong ngày. Bỏ qua."
        )
        logger.info("--- KẾT THÚC: VISUAL SEARCH STREAK ---\n")
        return False

    try:
        for attempt in range(1, max_attempts + 1):
            if state_store is not None:
                if state_store.get_visual_search_attempts_today() >= max_attempts:
                    logger.info(f"   [*] Visual Search đã đạt tối đa {max_attempts} lần thử hôm nay.")
                    break
                state_store.increment_visual_search_attempts_today(1)

            logger.info(f"[*] Visual Search - Lần thử {attempt}/{max_attempts}...")

            try:
                if "rewards.bing.com/earn" not in (page.url or ""):
                    page.goto("https://rewards.bing.com/earn", wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(2000)
                dismiss_popups(page)

                status_initial = read_visual_search_streak_status(page)
                if status_initial and status_initial.get("done"):
                    logger.info(
                        f"   [✓] Thẻ Visual Search đã hoàn thành ({status_initial.get('cur')}/{status_initial.get('max')})! Bỏ qua."
                    )
                    logger.info("--- KẾT THÚC: VISUAL SEARCH STREAK ---\n")
                    return True

                upload_img_path = _prepare_visual_search_jpeg(page)
                extra_tabs: list[Page] = []
                try:
                    # 1. Mở trang tracked vsstreak & mount #sbi_b / #sb_sbipane
                    active_page, extra_tabs = _open_visual_search_surface(page)
                    result_url_confirmed = False

                    # Tier 1: File Chooser (timeout=4500) / #sb_fileinput trên trang vsstreak
                    tier1_browse_selectors = [
                        '#sb_brs',
                        'label[for="sb_fileinput"]',
                        'span:has-text("Duyệt")',
                        'span:has-text("Browse")',
                        '#sb_sbipane #sb_brtext',
                        '#sb_brtext',
                    ]
                    tier1_sent = _trigger_file_chooser_upload(
                        active_page,
                        str(upload_img_path),
                        tier1_browse_selectors,
                        timeout_ms=4500,
                    )
                    if not tier1_sent:
                        tier1_sent = _set_file_input_upload(
                            active_page,
                            str(upload_img_path),
                            '#sbi_b input[type="file"], input#sb_fileinput',
                        )
                    if tier1_sent:
                        result_url_confirmed = _wait_for_vs_transition(active_page, 7.0, "Tier 1")
                        if result_url_confirmed:
                            logger.info(
                                "   [✓] [Tier 1] Trang đã điều hướng sang kết quả Visual Search -> bỏ qua [Tier 2] DataTransfer và [Tier 3] imageBin POST."
                            )

                    # Tier 2: Synthetic DataTransfer Drop lên #sbi_dropzone, #sbi_b, #sb_sbipane
                    if not result_url_confirmed:
                        if _dispatch_datatransfer_drop(active_page, str(upload_img_path)):
                            result_url_confirmed = _wait_for_vs_transition(active_page, 8.0, "Tier 2")

                    # Tier 3: multipart/form-data imageBin Form POST với sbisrc=ImgPicker
                    if not result_url_confirmed:
                        result_url_confirmed = submit_imagebin_post(
                            active_page, str(upload_img_path), timeout_sec=10
                        )

                    if not result_url_confirmed:
                        logger.warning(f"   [!] Không xác nhận được kết quả Visual Search. URL hiện tại: {active_page.url}")
                        if attempt < max_attempts:
                            logger.info("   [*] Nghỉ 3 giây trước khi thử lại...")
                            page.wait_for_timeout(3000)
                            continue
                        logger.warning("[WARNING] Visual Search chưa nổ điểm sau 3 lần thử")
                        logger.info("--- KẾT THÚC: VISUAL SEARCH STREAK ---\n")
                        return False

                    # 4. Mandatory Visual-Match Result Click & Telemetry Dwell (7s)
                    for rc_sel in ('.irsikc a, #vs_insights a, .richImgLnk, .iusc', '.irsikc a', '#vs_insights a', '.richImgLnk', '.iusc'):
                        try:
                            rc_elem = active_page.locator(rc_sel).first
                            if rc_elem.count() > 0 and rc_elem.is_visible(timeout=1000):
                                rc_elem.scroll_into_view_if_needed(timeout=1000)
                                active_page, _ = _click_with_optional_new_tab(
                                    active_page, [rc_sel], extra_tabs, "kết quả visual match"
                                )
                                break
                        except Exception:
                            continue

                    dwell_sec = 7
                    logger.info(f"   [*] Đã vào trang kết quả Visual Search, cuộn nhẹ và dwell {dwell_sec}s để beacon telemetry ghi nhận...")
                    active_page.wait_for_timeout(3500)
                    try:
                        active_page.mouse.wheel(0, 300)
                    except Exception:
                        pass
                    active_page.wait_for_timeout(3500)
                finally:
                    _close_extra_tabs(page, extra_tabs)

                logger.info("   [*] Đóng tab phụ, đợi 12 giây để telemetry đồng bộ và kiểm tra lại qua get_daily_search_progress()...")
                page.wait_for_timeout(12000)
                if "rewards.bing.com/earn" not in (page.url or ""):
                    page.goto("https://rewards.bing.com/earn", wait_until="domcontentloaded", timeout=15000)
                dismiss_popups(page)
                claim_all_available_rewards(page)

                def _is_vs_progress_read_failed(prog: dict) -> bool:
                    if not isinstance(prog, dict):
                        return True
                    if prog.get("scrape_error") or prog.get("found") is False:
                        return True
                    if (
                        "found" in prog
                        and prog.get("balance") == 0
                        and not prog.get("found_pc")
                        and not prog.get("found_mobile")
                    ):
                        return True
                    return False

                progress_check = get_daily_search_progress(page)
                vs_act = (progress_check.get("activities") or {}).get("Visual Search")
                status_check = read_visual_search_streak_status(page)
                vs_done = bool((vs_act and vs_act.get("done")) or (status_check and status_check.get("done")))

                if vs_done:
                    cur_v = (vs_act or status_check or {}).get("cur", 1)
                    max_v = (vs_act or status_check or {}).get("max", 1)
                    logger.info(f"   [✓] Visual Search đã nổ điểm thành công: {cur_v}/{max_v} [✓]!")
                    logger.info("--- KẾT THÚC: VISUAL SEARCH STREAK ---\n")
                    return True

                if _is_vs_progress_read_failed(progress_check):
                    logger.warning(
                        "[WARNING] Đọc Dashboard sau Visual Search thất bại (found=False / scrape_error) -> hủy các lần thử còn lại để tránh spam."
                    )
                    logger.info("--- KẾT THÚC: VISUAL SEARCH STREAK ---\n")
                    return False

                logger.info("   [*] Chờ thêm 8 giây và kiểm tra lại trạng thái Visual Search trước khi thử lại...")
                page.wait_for_timeout(8000)
                progress_recheck = get_daily_search_progress(page)
                vs_act_recheck = (progress_recheck.get("activities") or {}).get("Visual Search")
                status_recheck = read_visual_search_streak_status(page)
                vs_done_recheck = bool(
                    (vs_act_recheck and vs_act_recheck.get("done"))
                    or (status_recheck and status_recheck.get("done"))
                )
                if vs_done_recheck:
                    cur_v = (vs_act_recheck or status_recheck or {}).get("cur", 1)
                    max_v = (vs_act_recheck or status_recheck or {}).get("max", 1)
                    logger.info(f"   [✓] Visual Search đã nổ điểm thành công sau đồng bộ trễ: {cur_v}/{max_v} [✓]!")
                    logger.info("--- KẾT THÚC: VISUAL SEARCH STREAK ---\n")
                    return True

                if _is_vs_progress_read_failed(progress_recheck):
                    logger.warning(
                        "[WARNING] Đọc lại Dashboard sau Visual Search thất bại (found=False / scrape_error) -> hủy các lần thử còn lại."
                    )
                    logger.info("--- KẾT THÚC: VISUAL SEARCH STREAK ---\n")
                    return False

                cur_v = (vs_act_recheck or vs_act or status_recheck or status_check or {}).get("cur", 0)
                max_v = (vs_act_recheck or vs_act or status_recheck or status_check or {}).get("max", 1)
                logger.warning(f"   [✗] Thẻ Visual Search vẫn {cur_v}/{max_v} [✗] sau lần thử {attempt}.")
                if attempt < max_attempts:
                    logger.info("   [*] Nghỉ 3 giây trước khi thử lại...")
                    page.wait_for_timeout(3000)

            except (TargetClosedError, Exception) as attempt_exc:
                is_crash = (
                    isinstance(attempt_exc, TargetClosedError)
                    or "targetclosed" in type(attempt_exc).__name__.lower()
                    or "target closed" in str(attempt_exc).lower()
                    or "page crashed" in str(attempt_exc).lower()
                    or "crashed" in str(attempt_exc).lower()
                )
                if is_crash:
                    logger.warning(
                        f"   [!] Tab Visual Search bị crash/TargetClosedError ở lần thử {attempt}/{max_attempts}: {attempt_exc}"
                    )
                    try:
                        if page is not None and hasattr(page, "is_closed") and not page.is_closed():
                            page.close()
                    except Exception:
                        pass
                    try:
                        context = getattr(page, "context", None)
                        if context is not None:
                            page = context.new_page()
                    except Exception as ctx_err:
                        logger.warning(f"   [!] Không thể mở tab mới từ context: {ctx_err}")
                    if attempt < max_attempts:
                        logger.info("   [*] Nghỉ 2s rồi tiếp tục chạy attempt kế tiếp...")
                        time.sleep(2)
                        continue
                    else:
                        logger.warning("[WARNING] Visual Search chưa nổ điểm sau 3 lần thử do crash tab cuối.")
                        logger.info("--- KẾT THÚC: VISUAL SEARCH STREAK ---\n")
                        return False
                else:
                    raise attempt_exc

        logger.warning("[WARNING] Visual Search chưa nổ điểm sau 3 lần thử")
        logger.info("--- KẾT THÚC: VISUAL SEARCH STREAK ---\n")
        return False

    except Exception as e:
        logger.warning(f"   [!] Lỗi trong quá trình xử lý Visual Search: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        logger.warning("[WARNING] Visual Search chưa nổ điểm sau 3 lần thử")
        logger.info("--- KẾT THÚC: VISUAL SEARCH STREAK ---\n")
        return False


solve_visual_search_streak = handle_visual_search_streak


def _format_bing_star_result(raw: object) -> dict:
    """Chuẩn hóa kết quả Bing Star bonus và luôn định dạng text theo kiểu `3 / 2,100`."""
    default = {"current": 0, "max": 2100, "text": "0 / 2,100", "found": False}
    if not isinstance(raw, dict) or not raw.get("found"):
        return default
    try:
        cur = int(raw.get("current", 0) or 0)
        mx = int(raw.get("max", 2100) or 2100) or 2100
        return {
            "current": cur,
            "max": mx,
            "text": f"{cur} / {mx:,}",
            "found": True,
        }
    except Exception:
        return default


def _scan_bing_star_dom(page: Page) -> dict:
    """Quét DOM tìm chỉ số Bing Star bonus (progressbar dialog/toàn trang, card/dialog text, body text)."""
    try:
        raw_res = page.evaluate(r"""() => {
            const parseNum = (s) => parseInt(String(s || '0').replace(/[,\.]/g, ''), 10) || 0;
            const parseBar = (bar) => {
                const cur = Math.round(parseFloat(bar.getAttribute('aria-valuenow') || '0')) || 0;
                const mx = Math.round(parseFloat(bar.getAttribute('aria-valuemax') || '2100')) || 2100;
                return { current: cur, max: mx, found: true };
            };

            // 1. Ưu tiên [role="dialog"] [role="progressbar"] trước, sau đó mọi [role="progressbar"]
            const dialogBars = document.querySelectorAll('[role="dialog"] [role="progressbar"]');
            for (const bar of dialogBars) {
                const label = (bar.getAttribute('aria-label') || '').toLowerCase();
                const valText = (bar.getAttribute('aria-valuetext') || '').toLowerCase();
                const dialogTxt = ((bar.closest('[role="dialog"]') || {}).innerText || '').toLowerCase();
                if (label.includes('star') || valText.includes('star') || /bing\s*star|star\s*bonus/.test(dialogTxt)) {
                    return parseBar(bar);
                }
            }

            const bars = document.querySelectorAll('[role="progressbar"]');
            for (const bar of bars) {
                const label = (bar.getAttribute('aria-label') || '').toLowerCase();
                const valText = (bar.getAttribute('aria-valuetext') || '').toLowerCase();
                if (label.includes('star') || valText.includes('star')) {
                    return parseBar(bar);
                }
            }

            // 2. Quét các thẻ card, dialog hoặc button chứa 'star bonus' hoặc 'bing star' (length < 1500)
            const cards = document.querySelectorAll('[role="dialog"], div, section, article, a, button, mee-rewards-points-breakdown-card');
            for (const card of cards) {
                const txt = (card.innerText || '').trim();
                if (/bing\s*star\s*bonus|star\s*bonus|bing\s*star/i.test(txt) && txt.length < 1500) {
                    const m = txt.match(/(\d[\d,\.]*)\s*(?:\/|of|trên)\s*(\d[\d,\.]*)/i);
                    if (m) {
                        const c = parseNum(m[1]);
                        const mx = parseNum(m[2]) || 2100;
                        return { current: c, max: mx, found: true };
                    }
                }
            }

            // 3. Fallback quét toàn bộ document.body.innerText (hỗ trợ cả dấu phẩy và dấu chấm hàng nghìn)
            const bodyText = (document.body && document.body.innerText) ? document.body.innerText : '';
            const mBody = bodyText.match(/(?:Bing\s*Star\s*bonus|Star\s*bonus|Bing\s*Star)[\s\S]{0,200}?(\d[\d,\.]*)\s*(?:\/|of|trên)\s*(\d[\d,\.]*)/i);
            if (mBody) {
                const c = parseNum(mBody[1]);
                const mx = parseNum(mBody[2]) || 2100;
                return { current: c, max: mx, found: true };
            }

            return { current: 0, max: 2100, found: false };
        }""")
        return _format_bing_star_result(raw_res)
    except Exception:
        return {"current": 0, "max": 2100, "text": "0 / 2,100", "found": False}


def extract_bing_star_bonus(page: Page) -> dict:
    """
    Trích xuất chỉ số 'Bing Star bonus' (tối đa 2,100 điểm/tháng) trên trang /earn hoặc Dashboard.
    Hỗ trợ cả UI tiếng Anh và tiếng Việt, dấu phân cách hàng nghìn (',' và '.'),
    progressbar aria trong dialog và ngoài trang, thẻ card và popup.
    Returns:
        dict: {"current": int, "max": int, "text": str, "found": bool}
    """
    result = {"current": 0, "max": 2100, "text": "0 / 2,100", "found": False}
    try:
        scanned = _scan_bing_star_dom(page)
        if scanned.get("found"):
            logger.info(f"[Bing Star Bonus] Đã trích xuất tiến độ: {scanned['text']} pts")
            return scanned

        # Nếu chưa tìm thấy, click nhẹ vào thẻ Bing Star nếu có để mở drawer/dialog rồi quét lại đầy đủ
        try:
            star_loc = page.locator('text=/Bing Star|Star bonus/i').first
            if star_loc.count() > 0 and star_loc.is_visible(timeout=1000):
                star_loc.click(timeout=1500)
                page.wait_for_timeout(1000)
                retry_res = _scan_bing_star_dom(page)
                try:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)
                except Exception:
                    pass
                if retry_res.get("found"):
                    logger.info(f"[Bing Star Bonus] Đã trích xuất tiến độ sau khi mở thẻ: {retry_res['text']} pts")
                    return retry_res
        except Exception:
            pass

    except Exception as e:
        logger.debug(f"[Bing Star Bonus] Lỗi khi trích xuất Bing Star bonus: {e}")

    return result


def _apply_userinfo_fallback(progress: dict, userinfo: dict) -> None:
    """Bổ sung balance và search counters từ payload `/api/getuserinfo` nếu DOM thiếu."""
    if not isinstance(userinfo, dict) or not userinfo:
        return
    root = userinfo.get("dashboard", userinfo)
    if not isinstance(root, dict):
        root = userinfo
    user_status = root.get("userStatus") if isinstance(root.get("userStatus"), dict) else root

    for candidate_obj in (user_status, root, userinfo):
        if progress.get("balance") is None and isinstance(candidate_obj, dict):
            avail = candidate_obj.get("availablePoints")
            if avail is None:
                avail = candidate_obj.get("balance")
            if avail is not None:
                try:
                    progress["balance"] = int(avail)
                    break
                except (TypeError, ValueError):
                    pass

    counters = None
    for candidate_obj in (user_status, root, userinfo):
        if isinstance(candidate_obj, dict) and isinstance(candidate_obj.get("counters"), dict):
            counters = candidate_obj["counters"]
            break
    if not isinstance(counters, dict):
        return

    def _sum_counter_items(entry) -> Optional[tuple[int, int]]:
        items = entry if isinstance(entry, list) else ([entry] if isinstance(entry, dict) else [])
        cur_sum, max_sum, matched = 0, 0, False
        for item in items:
            if not isinstance(item, dict):
                continue
            c = item.get("pointProgress", item.get("progress", item.get("cur")))
            m = item.get("pointProgressMax", item.get("max"))
            if c is None or m is None:
                continue
            try:
                cur_sum += int(c)
                max_sum += int(m)
                matched = True
            except (TypeError, ValueError):
                continue
        return (cur_sum, max_sum) if matched else None

    for key, val in counters.items():
        kl = str(key).lower()
        pair = _sum_counter_items(val)
        if not pair:
            continue
        cur, mx = pair
        if ("pc" in kl or "desktop" in kl) and not progress.get("found_pc"):
            progress["pc_cur"] = progress["pc_current"] = cur
            progress["pc_max"] = mx
            progress["found_pc"] = True
        elif ("mobile" in kl or "mob" in kl) and not progress.get("found_mobile"):
            progress["mobile_cur"] = progress["mobile_current"] = cur
            progress["mobile_max"] = mx
            progress["found_mobile"] = True


def get_daily_search_progress(
    page: Page,
    dapi_url: str = None,
    access_token: str = None,
    earn_url: str = "https://rewards.bing.com/earn",
    use_dapi: bool = True,
    state_store: Optional[object] = None,
) -> dict:
    """
    Đọc tiến độ tìm kiếm thực tế và các thẻ nhiệm vụ từ Microsoft Rewards.
    Ưu tiên nguồn dữ liệu JSON từ `dapi/me` (API-Primary), fallback sang DOM CSS scraping
    trên `rewards.bing.com/earn` (kết hợp lắng nghe `/api/getuserinfo`) khi không có token hoặc API thiếu counters.
    Phân biệt rõ `status="empty"` (0 nhiệm vụ còn lại) với `status="error"` (lỗi mạng/parse).
    Đồng bộ giá trị đơn điệu max(scraped_star, api_star, stored_star) trong ngày để tránh tụt điểm Bing Star.
    """
    logger.info("[Dashboard] Đang đọc tiến độ điểm tìm kiếm thực tế từ tài khoản...")
    stored_star = 0
    if state_store is not None and hasattr(state_store, "get_bing_star_points"):
        try:
            stored_star = int(state_store.get_bing_star_points() or 0)
        except Exception:
            stored_star = 0

    progress = {
        "status": "error",
        "source": "dom",
        "pc_cur": 0, "pc_current": 0, "pc_max": 90,
        "mobile_cur": 0, "mobile_current": 0, "mobile_max": 60,
        "found": False,
        "found_pc": False,
        "found_mobile": False,
        "scrape_error": False,
        "all_maxed": False,
        "activities": {},
        "promotions": [],
        "punch_cards": [],
        "balance": None,
        "all_activities_done": False,
    }

    dapi_status = "unavailable"
    if use_dapi:
        try:
            from core.mobile_app_api import fetch_dapi_me_profile, parse_dapi_me_progress

            dapi_res = fetch_dapi_me_profile(
                page=page if access_token is None else None,
                dapi_url=dapi_url,
                access_token=access_token,
            )
            dapi_status = dapi_res.get("status", "unavailable")
            if dapi_status == "ok" and isinstance(dapi_res.get("data"), dict):
                parsed = parse_dapi_me_progress(dapi_res["data"])
                if parsed.get("found_pc") and parsed.get("found_mobile"):
                    if "bing_star_bonus" not in parsed or not (parsed.get("bing_star_bonus") or {}).get("found"):
                        if page is not None:
                            try:
                                parsed["bing_star_bonus"] = extract_bing_star_bonus(page)
                            except Exception:
                                parsed["bing_star_bonus"] = {"current": 0, "max": 2100, "text": "0 / 2,100", "found": False}
                        else:
                            parsed["bing_star_bonus"] = {"current": 0, "max": 2100, "text": "0 / 2,100", "found": False}

                    api_star = int((parsed.get("bing_star_bonus") or {}).get("current", 0) or 0)
                    scraped_star = 0
                    if page is not None and (not api_star or not (parsed.get("bing_star_bonus") or {}).get("found")):
                        try:
                            scraped_star = int((extract_bing_star_bonus(page) or {}).get("current", 0) or 0)
                        except Exception:
                            pass
                    final_star = max(scraped_star, api_star, stored_star)
                    if state_store is not None and hasattr(state_store, "set_bing_star_points") and final_star >= stored_star:
                        try:
                            state_store.set_bing_star_points(final_star)
                        except Exception:
                            pass
                    max_star = (parsed.get("bing_star_bonus") or {}).get("max", 2100) or 2100
                    parsed["bing_star_bonus"] = {
                        "current": final_star,
                        "max": max_star,
                        "text": f"{final_star:,} / {max_star:,}",
                        "found": final_star > 0 or (parsed.get("bing_star_bonus") or {}).get("found", False),
                    }
                    logger.info(
                        f"[DAPI] Đọc tiến độ thành công từ dapi/me: "
                        f"Desktop {parsed['pc_cur']}/{parsed['pc_max']} | "
                        f"Mobile {parsed['mobile_cur']}/{parsed['mobile_max']} | "
                        f"status={parsed['status']}"
                    )
                    return parsed
                # Giữ lại balance/promotions từ DAPI nếu cần fallback DOM cho counters
                progress["balance"] = parsed.get("balance")
                progress["promotions"] = parsed.get("promotions", [])
                progress["punch_cards"] = parsed.get("punch_cards", [])
        except Exception as dapi_exc:
            dapi_status = "error"
            logger.debug(f"[DAPI] Lỗi khi đọc dapi/me, fallback sang DOM: {dapi_exc}")

    if page is None:
        progress["status"] = "error"
        progress["scrape_error"] = True
        return progress

    pre_star = None
    captured_userinfo: dict = {}
    listener_attached = False

    def _on_userinfo_response(resp) -> None:
        try:
            r_url = (getattr(resp, "url", "") or "").lower()
            if "/api/getuserinfo" not in r_url:
                return
            status_code = getattr(resp, "status", 200)
            if isinstance(status_code, int) and status_code >= 400:
                return
            payload = resp.json()
            if isinstance(payload, dict) and payload:
                captured_userinfo.update(payload)
        except Exception:
            pass

    def _detach_userinfo_listener() -> None:
        nonlocal listener_attached
        if not listener_attached:
            return
        listener_attached = False
        try:
            if hasattr(page, "remove_listener"):
                page.remove_listener("response", _on_userinfo_response)
            elif hasattr(page, "off"):
                page.off("response", _on_userinfo_response)
        except Exception:
            pass

    try:
        cur_page_url = (getattr(page, "url", "") or "").lower()
        if "rewards.bing.com" in cur_page_url and "/earn" not in cur_page_url:
            try:
                pre_star = extract_bing_star_bonus(page)
            except Exception:
                pre_star = None

        try:
            if hasattr(page, "on"):
                page.on("response", _on_userinfo_response)
                listener_attached = True
        except Exception:
            pass

        # Bước A: Điều hướng tới earn_url (retry tối đa 3 lần khi TimeoutError)
        nav_ok = False
        last_nav_err = None
        for nav_attempt in range(1, 4):
            try:
                if "rewards.bing.com/earn" not in page.url and earn_url not in page.url:
                    page.goto(earn_url, wait_until="domcontentloaded", timeout=25000)
                else:
                    page.reload(wait_until="domcontentloaded", timeout=25000)
                nav_ok = True
                break
            except Exception as nav_err:
                last_nav_err = nav_err
                logger.warning(f"[Dashboard] Lỗi tải /earn (lần {nav_attempt}/3): {nav_err}")
                if nav_attempt < 3:
                    try:
                        page.wait_for_timeout(2000)
                    except Exception:
                        pass

        if not nav_ok:
            _detach_userinfo_listener()
            logger.warning(f"[Dashboard] Không thể tải /earn sau 3 lần thử: {last_nav_err}")
            progress["found"] = False
            progress["scrape_error"] = True
            progress["status"] = "error"
            return progress

        try:
            page.wait_for_selector(
                '[role="progressbar"], section#moreactivities, mee-rewards-user-status-item, #id_rc',
                timeout=15000,
            )
        except Exception:
            pass
        page.wait_for_timeout(2500)
        _detach_userinfo_listener()
        dismiss_popups(page)

        # 1. Đọc 5 thẻ nhiệm vụ / chuỗi streak ngày (Bing Search, Daily Set, Edge Browsing, Mobile App, Visual Search)
        act_data = page.evaluate(r"""() => {
            const result = {
                activities: {
                    'Bing Search': { cur: 0, max: 1, done: false },
                    'Daily Set': { cur: 0, max: 3, done: false },
                    'Edge Browsing': { cur: 0, max: 30, done: false },
                    'Mobile App': { cur: 0, max: 1, done: false },
                    'Visual Search': { cur: 0, max: 1, done: false }
                },
                found_count: 0
            };

            // 1. Kiểm tra qua các progressbar (React-Aria UI mới)
            const bars = document.querySelectorAll('[role="progressbar"]');
            bars.forEach(bar => {
                const label = (bar.getAttribute('aria-label') || '').trim().toLowerCase();
                const cur = parseFloat(bar.getAttribute('aria-valuenow') || '0');
                const max = parseFloat(bar.getAttribute('aria-valuemax') || '0');
                const valText = (bar.getAttribute('aria-valuetext') || '').trim();
                const is100 = (max > 0 && cur >= max) || valText === '100%';

                if (label.includes('bing') && !label.includes('star')) {
                    result.activities['Bing Search'] = { cur, max, done: is100 };
                    result.found_count++;
                } else if (label.includes('daily set') || label.includes('bộ hằng ngày')) {
                    result.activities['Daily Set'] = { cur, max, done: is100 };
                    result.found_count++;
                } else if (label.includes('edge')) {
                    result.activities['Edge Browsing'] = { cur, max, done: is100 };
                    result.found_count++;
                } else if (label.includes('mobile app') || label.includes('bing app') || label.includes('ứng dụng')) {
                    result.activities['Mobile App'] = { cur, max, done: is100 };
                    result.found_count++;
                } else if (label.includes('visual search') || label.includes('trực quan')) {
                    result.activities['Visual Search'] = { cur, max, done: is100 };
                    result.found_count++;
                }
            });

            // 2. Fallback text content cho 5 thẻ Streak
            const textContent = document.body.innerText;
            if (result.found_count < 5) {
                const bingM = textContent.match(/(?:Bing Search Streak|Bing Search|Tìm kiếm Bing)[^\d]*(\d+)\s*\/\s*(\d+)/i) ||
                              textContent.match(/Search:\s*(\d+)\s*\/\s*(\d+)/i);
                if (bingM && !result.activities['Bing Search'].done) {
                    const c = parseInt(bingM[1]), m = parseInt(bingM[2]);
                    result.activities['Bing Search'] = { cur: c, max: m, done: c >= m && m > 0 };
                    result.found_count++;
                }
                const dsM = textContent.match(/(?:Daily Set Streak|Daily Set|Bộ hằng ngày)[^\d]*(\d+)\s*\/\s*(\d+)/i) ||
                            textContent.match(/Activity:\s*(\d+)\s*\/\s*(\d+)/i);
                if (dsM && !result.activities['Daily Set'].done) {
                    const c = parseInt(dsM[1]), m = parseInt(dsM[2]);
                    result.activities['Daily Set'] = { cur: c, max: m, done: c >= m && m > 0 };
                    result.found_count++;
                }
                const edgeM = textContent.match(/(?:Edge Browsing Streak|Edge Browsing|Duyệt web Edge)[^\d]*(\d+)\s*\/\s*(\d+)/i) ||
                              textContent.match(/Minutes:\s*(\d+)\s*\/\s*(\d+)/i);
                if (edgeM && !result.activities['Edge Browsing'].done) {
                    const c = parseInt(edgeM[1]), m = parseInt(edgeM[2]);
                    result.activities['Edge Browsing'] = { cur: c, max: m, done: c >= m && m > 0 };
                    result.found_count++;
                }
                const mobM = textContent.match(/(?:Bing App Streak|Mobile App|Ứng dụng di động)[^\d]*(\d+)\s*\/\s*(\d+)/i) ||
                             textContent.match(/Check-in:\s*(\d+)\s*\/\s*(\d+)/i);
                if (mobM && !result.activities['Mobile App'].done) {
                    const c = parseInt(mobM[1]), m = parseInt(mobM[2]);
                    result.activities['Mobile App'] = { cur: c, max: m, done: c >= m && m > 0 };
                    result.found_count++;
                }
                if (!result.activities['Visual Search'].done) {
                    let visM = null;
                    const allDivs = document.querySelectorAll('div');
                    for (const d of allDivs) {
                        const txt = (d.innerText || '').trim();
                        if ((txt.startsWith('Visual Search Streak') || txt.startsWith('Tìm kiếm trực quan')) && txt.length < 350) {
                            const matches = Array.from(txt.matchAll(/(\d+)\s*\/\s*(\d+)/g));
                            if (matches.length > 0) {
                                visM = matches[matches.length - 1];
                                break;
                            }
                        }
                    }
                    if (!visM) {
                        const idx = textContent.indexOf('Visual Search');
                        if (idx !== -1) {
                            const sub = textContent.slice(idx, idx + 300);
                            const matches = Array.from(sub.matchAll(/(\d+)\s*\/\s*(\d+)/g));
                            if (matches.length > 0) {
                                visM = matches[matches.length - 1];
                            }
                        }
                    }
                    if (visM) {
                        const c = parseInt(visM[1]), m = parseInt(visM[2]);
                        result.activities['Visual Search'] = { cur: c, max: m, done: c >= m && m > 0 };
                        result.found_count++;
                    }
                }
            }
            return result;
        }""")

        progress["activities"] = (act_data or {}).get("activities", {})
        all_act = progress["activities"]
        all_five_done = (
            len(all_act) == 5 and
            all(a.get("done", False) for a in all_act.values())
        )
        # CHỈ coi all_activities_done là True khi 4 nhiệm vụ cốt lõi (Bing Search, Daily Set, Edge Browsing, Visual Search) đạt 100%
        # Loại trừ thẻ Mobile App trên Dashboard web vì điểm danh Mobile App được xử lý qua API (type 103)
        primary_four_done = (
            all_act.get('Bing Search', {}).get('done', False) and
            all_act.get('Daily Set', {}).get('done', False) and
            all_act.get('Edge Browsing', {}).get('done', False) and
            all_act.get('Visual Search', {}).get('done', False)
        )
        progress["all_activities_done"] = primary_four_done
        progress["primary_four_done"] = primary_four_done
        progress["mobile_app_done"] = all_act.get("Mobile App", {}).get("done", False)

        for act_name, act_info in progress["activities"].items():
            status_icon = "✓" if act_info["done"] else "✗"
            logger.info(f"[Dashboard] Thẻ {act_name}: {act_info['cur']}/{act_info['max']} [{status_icon}]")

        if not progress["all_activities_done"]:
            uncompleted = [k for k, v in all_act.items() if not v.get("done", False)]
            # Filter out Mobile App as it's not a required activity for daily lock
            uncompleted_required = [k for k in uncompleted if k != "Mobile App"]
            logger.info(f"[Dashboard] Còn {len(uncompleted_required)} thẻ cốt lõi chưa hoàn thành: {', '.join(uncompleted_required)} -> all_activities_done = False")

        # Bước B: Mở drawer/popup Points breakdown để trích xuất dữ liệu thực tế
        try:
            raw_bt = page.inner_text("body")
            body_text = raw_bt if isinstance(raw_bt, str) else str(raw_bt or "")
        except Exception:
            body_text = ""
        has_breakdown_text = any(k in body_text for k in [
            "Desktop Bing search", "Mobile Bing search",
            "Tìm kiếm trên máy tính", "Tìm kiếm trên điện thoại"
        ])

        if not has_breakdown_text:
            clicked = page.evaluate("""() => {
                const buttons = Array.from(document.querySelectorAll('button, div[role="button"], a'));
                for (const btn of buttons) {
                    const txt = (btn.innerText || '').toLowerCase();
                    if (txt.includes('points breakdown') || txt.includes('chi tiết điểm')) {
                        btn.click();
                        return true;
                    }
                }
                return false;
            }""")
            if clicked:
                page.wait_for_timeout(1500)
                try:
                    raw_bt = page.inner_text("body")
                    body_text = raw_bt if isinstance(raw_bt, str) else str(raw_bt or "")
                except Exception:
                    body_text = ""

        # Regex quét chính xác chuỗi dạng (\d+)\s*/\s*(\d+) đi kèm ngay sau các từ khóa trạng thái
        pc_pattern = r"(?:Desktop Bing search|Tìm kiếm trên máy tính|Tìm kiếm trên PC|PC search|Desktop search|PC Bing search)[\s\S]{0,100}?(\d+)\s*/\s*(\d+)"
        mob_pattern = r"(?:Mobile Bing search|Tìm kiếm trên điện thoại|Tìm kiếm trên thiết bị di động|Tìm kiếm trên di động|Mobile search|Mobile App search)[\s\S]{0,100}?(\d+)\s*/\s*(\d+)"

        m_pc = re.search(pc_pattern, body_text, re.IGNORECASE)
        if m_pc:
            c = int(m_pc.group(1))
            mx = int(m_pc.group(2))
            progress["pc_cur"] = c
            progress["pc_current"] = c
            progress["pc_max"] = mx
            progress["found_pc"] = True
        else:
            # Fallback DOM thẻ cũ (nếu có)
            fallback_pc = page.evaluate(r"""() => {
                const cards = document.querySelectorAll('div[class*="pointsDetail"], mee-rewards-points-breakdown-card');
                for (const card of cards) {
                    const txt = card.innerText || '';
                    if (txt.toLowerCase().includes('pc') || txt.toLowerCase().includes('máy tính')) {
                        const m = txt.match(/(\d+)\s*\/\s*(\d+)/);
                        if (m) return { cur: parseInt(m[1]), max: parseInt(m[2]) };
                    }
                }
                return null;
            }""")
            if fallback_pc:
                progress["pc_cur"] = fallback_pc["cur"]
                progress["pc_current"] = fallback_pc["cur"]
                progress["pc_max"] = fallback_pc["max"]
                progress["found_pc"] = True
            else:
                logger.warning("[Dashboard] Không tìm thấy DOM thống kê 'Desktop Bing search' trong Points breakdown!")

        m_mob = re.search(mob_pattern, body_text, re.IGNORECASE)
        if m_mob:
            c = int(m_mob.group(1))
            mx = int(m_mob.group(2))
            progress["mobile_cur"] = c
            progress["mobile_current"] = c
            progress["mobile_max"] = mx
            progress["found_mobile"] = True
        else:
            # Fallback DOM thẻ cũ (nếu có)
            fallback_mob = page.evaluate(r"""() => {
                const cards = document.querySelectorAll('div[class*="pointsDetail"], mee-rewards-points-breakdown-card');
                for (const card of cards) {
                    const txt = card.innerText || '';
                    if (txt.toLowerCase().includes('mobile') || txt.toLowerCase().includes('điện thoại')) {
                        const m = txt.match(/(\d+)\s*\/\s*(\d+)/);
                        if (m) return { cur: parseInt(m[1]), max: parseInt(m[2]) };
                    }
                }
                return null;
            }""")
            if fallback_mob:
                progress["mobile_cur"] = fallback_mob["cur"]
                progress["mobile_current"] = fallback_mob["cur"]
                progress["mobile_max"] = fallback_mob["max"]
                progress["found_mobile"] = True
            else:
                logger.warning("[Dashboard] Không tìm thấy DOM thống kê 'Mobile Bing search' trong Points breakdown!")

        # Fallback từ /api/getuserinfo nếu DOM thiếu counters hoặc balance
        if captured_userinfo:
            _apply_userinfo_fallback(progress, captured_userinfo)

        # Đóng nhẹ drawer nếu đang mở để trả lại giao diện sạch
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass

        # Trích xuất chỉ số Bing Star bonus (tối đa 2,100 pts/tháng)
        star_bonus = extract_bing_star_bonus(page)
        effective_star = star_bonus if star_bonus.get("found") else (pre_star if pre_star and pre_star.get("found") else star_bonus)

        scraped_star = int((effective_star or {}).get("current", 0) or 0)
        api_star = int((progress.get("bing_star_bonus") or {}).get("current", 0) or 0)
        final_star = max(scraped_star, api_star, stored_star)
        if state_store is not None and hasattr(state_store, "set_bing_star_points") and final_star >= stored_star:
            try:
                state_store.set_bing_star_points(final_star)
            except Exception:
                pass
        max_star = (effective_star or {}).get("max", 2100) or 2100
        effective_star = {
            "current": final_star,
            "max": max_star,
            "text": f"{final_star:,} / {max_star:,}",
            "found": final_star > 0 or bool(effective_star.get("found")),
        }
        progress["bing_star_bonus"] = effective_star

        # CHỈ COI LÀ ALL_MAXED KHI VÀ CHỈ KHI DỮ LIỆU THỰC TẾ XÁC NHẬN CẢ PC VÀ MOBILE ĐÃ ĐẠT ĐỈNH
        progress["found"] = bool(progress["found_pc"] or progress["found_mobile"])
        if progress["found_pc"] and progress["found_mobile"]:
            if progress["pc_cur"] >= progress["pc_max"] and progress["mobile_cur"] >= progress["mobile_max"]:
                progress["all_maxed"] = True

        if not progress["found"]:
            progress["status"] = "error" if dapi_status == "error" else "empty"
        elif progress["all_maxed"] and progress["all_activities_done"]:
            progress["status"] = "empty"
        else:
            progress["status"] = "active"

        logger.info(f"[Dashboard] Dữ liệu thực tế: Desktop: {progress['pc_cur']}/{progress['pc_max']} (found={progress['found_pc']}) | Mobile: {progress['mobile_cur']}/{progress['mobile_max']} (found={progress['found_mobile']}) | Bing Star: {effective_star.get('text', '0 / 2,100')} | all_maxed={progress['all_maxed']}")

    except Exception as e:
        _detach_userinfo_listener()
        logger.warning(f"[Dashboard] Lỗi khi kiểm tra tiến độ ngày: {e}")
        progress["found"] = False
        progress["scrape_error"] = True
        progress["all_maxed"] = False
        progress["status"] = "error"

    return progress


if __name__ == "__main__":
    # Test nhanh (cần profile đã đăng nhập)
    from patchright.sync_api import sync_playwright
    from pathlib import Path
    
    PROFILE_DIR = Path(__file__).resolve().parent.parent / "profiles" / "desktop"
    
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="msedge",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        page = context.pages[0] if context.pages else context.new_page()
        # Set default timeout
        page.set_default_timeout(DEFAULT_TIMEOUT)
        run_all_dashboard_activities(context, page)
        context.close()