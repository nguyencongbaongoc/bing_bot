"""
Module tự động hóa hoàn thành Daily Set, Check-in, Polls, Quizzes trên Microsoft Rewards.
Xử lý các loại nhiệm vụ: Daily Check-in, Daily Set (giữ Streak), More Activities (Poll, Quiz, Link).
"""

import random
import time
import urllib.parse
import zlib
import struct
from pathlib import Path
from typing import Optional
from playwright.sync_api import Page, BrowserContext
from core.humanizer import human_delay
from core.tracker import _log_info, _log_debug, _log_warning, _log_error, _log_exception, logger
from core.ai_solver import ask_local_ai, solve_quiz_with_ai, unload_model_from_vram

# Use PYTHONUTF8=1 environment variable instead of manual wrapping
# This avoids issues with stream wrapping in subprocesses


REWARDS_URL = "https://rewards.bing.com/"

DEFAULT_TIMEOUT = 8000  # 8 seconds default timeout


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
    """
    claimed_count = 0
    try:
        # Bước 1: Nếu đang có popup modal nổi (z-40 / role="dialog"), ưu tiên bấm nút bên trong trước
        modal_selectors = [
            'div.fixed.inset-0 button:has-text("Claim points")',
            '[role="dialog"] button:has-text("Claim points")',
            'button:has-text("Claim points")',
            'span:text-is("Claim points")',
        ]
        for sel in modal_selectors:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=1000):
                loc.click(force=True, timeout=3000)
                claimed_count += 1
                logger.info(f"[Coin] Đã bấm nhận xu trong popup với selector: {sel}")
                page.wait_for_timeout(1500)

        # Bước 2: Tìm các nút "Ready to claim" trên mặt trang Dashboard
        ready_buttons = page.locator('button:has-text("Ready to claim"), a:has-text("Ready to claim")')
        count = min(ready_buttons.count(), 10)
        for i in range(count):
            try:
                btn = ready_buttons.nth(i)
                if btn.is_visible(timeout=1000):
                    btn.scroll_into_view_if_needed(timeout=2000)
                    btn.click(timeout=3000)
                    logger.info("[Coin] Đã chạm thẻ 'Ready to claim', đang kiểm tra popup nhận xu...")
                    page.wait_for_timeout(1500)
                    claimed_count += 1

                    # Ngay sau khi bấm "Ready to claim", nếu popup "Claim points" hiện ra thì bấm xác nhận ngay
                    confirm_btn = page.locator('button:has-text("Claim points"), [role="dialog"] button:has-text("Claim")').first
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
    Tìm và chạm vào biểu tượng 'Đồng xu Rewards ở góc màn hình' (góc dưới phải hoặc góc trên phải)
    trên Bing.com và MSN.com (hỗ trợ cả DOM thường, Shadow DOM và Iframe panelflyout).
    Bao gồm click trực tiếp và fallback click theo tọa độ con trỏ chuột.
    """
    tapped = False
    try:
        # 1. Quét và click đồng xu ở góc thông qua JS (xuyên cả Shadow DOM trên MSN và Bing)
        clicked_data = page.evaluate("""() => {
            // Hàm đệ quy tìm phần tử xuyên qua mọi lớp Shadow DOM
            function queryAllDeep( root = document ) {
                let elements = [];
                const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, null, false);
                let node = walker.nextNode();
                while (node) {
                    elements.push(node);
                    if (node.shadowRoot) {
                        elements = elements.concat(queryAllDeep(node.shadowRoot));
                    }
                    node = walker.nextNode();
                }
                return elements;
            }

            const winW = window.innerWidth;
            const winH = window.innerHeight;

            // Danh sách selector ưu tiên ở góc màn hình (Bing & MSN)
            const directSelectors = [
                'button:has-text("+3")',
                'div:has-text("+3")',
                'span:has-text("+3")',
                '[aria-label*="3 point" i]',
                '[aria-label*="+3" i]',
                '[class*="rewards" i][class*="entry" i]',
                '[class*="rewards-badge" i]',
                '[class*="coin" i]',
                '#rewards-badge',
                '#entry-point-hp',
                '#id_rh',                       // Đồng xu góc trên phải Bing PC
                '#rewid-f',                     // Đồng xu nổi Bing
                '.b_rew_btn',                   // Nút Rewards Bing
                '#hb_r',                        // Rewards Mobile Bing
                '.m_rw_c',                      // Mobile Rewards Coin container
                'a[aria-label*="Rewards" i]',
                'button[aria-label*="Rewards" i]',
                '[data-bi-id*="rewards" i]',
                '[class*="coin" i][class*="reward" i]'
            ];

            for (const sel of directSelectors) {
                try {
                    const el = document.querySelector(sel);
                    if (el && el.offsetParent !== null) {
                        const rect = el.getBoundingClientRect();
                        el.click();
                        return {
                            matched: true,
                            info: "Direct: " + sel,
                            x: Math.round(rect.x + rect.width / 2),
                            y: Math.round(rect.y + rect.height / 2)
                        };
                    }
                } catch (e) {}
            }

            // Quét sâu vào Shadow DOM (dành cho MSN.com Corner Coin / Start Floating Badge)
            const allNodes = queryAllDeep(document);
            for (const el of allNodes) {
                const idCls = ((el.id || '') + ' ' + (el.className || '') + ' ' + (el.getAttribute('aria-label') || '') + ' ' + (el.innerText || '')).toLowerCase();
                const tag = el.tagName.toLowerCase();
                if (
                    idCls.includes('+3') ||
                    idCls.includes('3 point') ||
                    tag.includes('reward') ||
                    tag.includes('entry-point') ||
                    (idCls.includes('reward') && (idCls.includes('coin') || idCls.includes('badge') || idCls.includes('flyout') || idCls.includes('button')))
                ) {
                    const rect = el.getBoundingClientRect();
                    // Kiểm tra phần tử nằm ở các góc màn hình hoặc nửa dưới và đang hiển thị
                    if (rect.width >= 15 && rect.height >= 15 && rect.width <= 250 && rect.height <= 250) {
                        try { el.click(); } catch (e) {}
                        return {
                            matched: true,
                            info: "ShadowDOM: " + tag + " (" + idCls.slice(0, 35) + ")",
                            x: Math.round(rect.x + rect.width / 2),
                            y: Math.round(rect.y + rect.height / 2)
                        };
                    }
                }
            }

            // Kiểm tra thẻ nổi cố định góc dưới bên phải màn hình
            for (const el of allNodes) {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                const isFixed = style.position === 'fixed' || style.position === 'sticky';
                if (isFixed && rect.width >= 20 && rect.width <= 150 && rect.height >= 20 && rect.height <= 150 && rect.bottom >= winH * 0.6 && rect.right >= winW * 0.5) {
                    try { el.click(); } catch (e) {}
                    return {
                        matched: true,
                        info: "Corner-Fixed: " + el.tagName.toLowerCase(),
                        x: Math.round(rect.x + rect.width / 2),
                        y: Math.round(rect.y + rect.height / 2)
                    };
                }
            }

            return null;
        }""")

        if clicked_data and clicked_data.get("matched"):
            tapped = True
            info_str = clicked_data.get("info", "Coin Element")
            logger.info(f"[{context_name} Coin] Đã chạm đồng xu ở góc màn hình ({info_str})")
            
            # Fallback click tọa độ bổ sung bằng Playwright mouse để đảm bảo native event
            cx, cy = clicked_data.get("x"), clicked_data.get("y")
            if cx and cy:
                try:
                    page.mouse.click(cx, cy)
                except Exception:
                    pass

            page.wait_for_timeout(2000)

            # 2. Sau khi chạm đồng xu ở góc, kiểm tra các iframe flyout vừa bung ra để bấm nhận xu bên trong
            for frame in page.frames:
                f_url = frame.url.lower()
                if "panelflyout" in f_url or "rewards" in f_url:
                    try:
                        # Tìm các nút nhận điểm (+5, +10, Claim) bên trong khung đồng xu
                        claim_btns = frame.locator('button:has-text("Claim"), a:has-text("Claim"), [class*="claim" i], [aria-label*="Claim" i]')
                        for i in range(min(claim_btns.count(), 5)):
                            btn = claim_btns.nth(i)
                            if btn.is_visible(timeout=800):
                                btn.click(force=True, timeout=2000)
                                logger.info(f"[{context_name} Coin] -> Đã thu thập xu thưởng bên trong Flyout!")
                                page.wait_for_timeout(1000)
                    except Exception:
                        pass

            # Đóng nhẹ menu flyout bằng phím Escape để trả lại màn hình thông thoáng
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)

    except Exception as e:
        logger.debug(f"[{context_name} Coin] Bỏ qua chạm đồng xu góc: {e}")

    return tapped


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


def solve_interactive_tab(page: Page, timeout_sec: int = 5):
    """
    Xử lý tab con của Daily Set: Chỉ giải nếu thực sự có câu hỏi Quiz/Poll.
    Nếu là trang tìm kiếm thông thường (UrlOffer), lướt nhẹ và thoát ngay trong 4 giây.
    """
    from core.tracker import logger
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass

    # Cuộn nhẹ trang để kích hoạt beacon
    try:
        page.mouse.wheel(0, 300)
        page.wait_for_timeout(1000)
    except Exception:
        pass

    # Kiểm tra xem có phần tử câu hỏi trắc nghiệm thật sự hay không (timeout ngắn 2s)
    quiz_selectors = [
        ".wk_question",
        "#QuestionText",
        ".b_focusTextExtra",
        "[class*='quizQuestion']"
    ]
    is_real_quiz = False
    try:
        if page.locator(", ".join(quiz_selectors)).first.is_visible(timeout=1500):
            is_real_quiz = True
    except Exception:
        pass

    if not is_real_quiz:
        for sel in quiz_selectors:
            try:
                if page.locator(sel).first.is_visible(timeout=500):
                    is_real_quiz = True
                    break
            except Exception:
                continue

    if is_real_quiz:
        logger.info("   [*] Phát hiện Quiz thật sự, đang gọi AI giải...")
        try:
            from core.ai_solver import solve_quiz_with_ai
            solve_quiz_with_ai(page)
        except Exception as e:
            logger.warning(f"   [!] Lỗi khi giải quiz: {e}")
    else:
        # Nếu chỉ là trang tìm kiếm Bing thông thường (UrlOffer)
        logger.info("   [*] Thẻ dạng UrlOffer/Search, duy trì trang 2-3s để nhận điểm...")
        page.wait_for_timeout(2500)


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


def solve_cards_in_section(context: BrowserContext, page: Page, selector: str, name: str) -> int:
    """
    Quét và giải quyết từng thẻ chưa hoàn thành trong một khu vực (Daily Set, More Activities).
    
    Args:
        context: BrowserContext để bắt tab mới
        page: Page object hiện tại
        selector: CSS selector cho các thẻ nhiệm vụ
        name: Tên khu vực (để log)
        
    Returns:
        Số thẻ đã xử lý thành công
    """
    page.goto(REWARDS_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(random.randint(3000, 4000))
    
    # Dismiss popups after navigation
    dismiss_popups(page)
    
    # Auto-claim any available rewards on the dashboard
    claim_all_available_rewards(page)

    try:
        cards = page.locator(selector).all()
        _log_info(f"[{name}] Tìm thấy {len(cards)} thẻ nhiệm vụ.")
    except Exception:
        _log_warning(f"[{name}] Không tìm thấy thẻ nhiệm vụ với selector: {selector}")
        return 0

    completed_count = 0
    
    for i, card in enumerate(cards):
        try:
            # Kiểm tra xem thẻ đã hoàn thành chưa (có dấu tích xanh)
            completed_indicators = [
                '.mee-icon-SkypeCircleCheck',
                '.mee-icon-CheckMark',
                'span[class*="check"]',
                'div[class*="completed"]',
                '[data-bi-id*="completed"]',
            ]
            
            is_completed = False
            for indicator in completed_indicators:
                try:
                    if card.locator(indicator).is_visible(timeout=500):
                        is_completed = True
                        break
                except Exception:
                    continue
            
            if is_completed:
                _log_debug(f"  [{i+1}/{len(cards)}] Đã hoàn thành, bỏ qua.")
                continue

            # Lấy tiêu đề thẻ để log
            try:
                title_text = card.inner_text(timeout=1000).split("\n")[0][:50]
            except Exception:
                title_text = f"Card {i+1}"
            
            _log_info(f"  -> Đang xử lý [{i+1}/{len(cards)}]: {title_text}...")

            # Click thẻ và bắt tab mới nếu có
            try:
                with context.expect_page(timeout=8000) as new_tab_info:
                    try:
                        card.click()
                    except Exception:
                        try:
                            card.click(force=True, timeout=3000)
                        except Exception:
                            card.evaluate("el => el.click()")
                sub_tab = new_tab_info.value
                try:
                    solve_interactive_tab(sub_tab)
                finally:
                    try:
                        sub_tab.close()
                    except Exception:
                        pass
                page.wait_for_timeout(1000)
                completed_count += 1
            except Exception:
                # Nếu không mở tab mới (xử lý inline), đợi một chút rồi quay lại trang chính
                human_delay(mean=4.0, std_dev=1.0, min_val=3.0)
                page.goto(REWARDS_URL, wait_until="domcontentloaded")
                page.wait_for_timeout(2000)
                dismiss_popups(page)
                claim_all_available_rewards(page)
                completed_count += 1

        except Exception as e:
            _log_error(f"    [!] Lỗi xử lý thẻ {i+1}: {e}")
            continue

    return completed_count


def run_all_dashboard_activities(context: BrowserContext, page: Page) -> None:
    """
    Chạy toàn bộ quy trình Dashboard Activities: Check-in -> Daily Set -> More Activities.
    
    Args:
        context: BrowserContext để quản lý tab
        page: Page object chính
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
        "Daily Set"
    )

    # 3. More Activities (Sự kiện và hoạt động bổ sung)
    more_count = solve_cards_in_section(
        context, 
        page, 
        'div[id*="more-activities"] [role="button"], div[id*="more-activities"] a, div[data-bi-area="MoreActivities"] .c-card, .more-activities-container .c-card, mee-rewards-more-activities-item .c-card', 
        "More Activities"
    )
    if more_count == 0:
        _log_info("[More Activities] Không tìm thấy thẻ trên Dashboard cũ. Sẽ quét mục 'Keep earning' tại /earn...")
        try:
            solve_keep_earning_activities(page, context)
        except Exception as e:
            _log_warning(f"[More Activities] Lỗi khi quét Keep Earning: {e}")
    
    # 4. Các khu vực khác có thể có (Special Offers, v.v.)
    solve_cards_in_section(
        context,
        page,
        'div[data-bi-area="SpecialOffers"] .c-card, .special-offers .c-card, div[id*="special-offers"] [role="button"], div[id*="special-offers"] a',
        "Special Offers"
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


def solve_all_earn_quests(page: Page):
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

                # Kiểm tra text trên thẻ ngoài xem đã hoàn thành chưa (VD: 4/4 tasks)
                card_text = el.inner_text()
                m = re.search(r'(\d+)\s*/\s*(\d+)\s*tasks', card_text, re.IGNORECASE)
                if m and int(m.group(1)) == int(m.group(2)) and int(m.group(2)) > 0:
                    logger.info(f"[*] Bỏ qua Quest đã đủ {m.group(0)} ngay từ ngoài: {full_url}")
                    continue

                if full_url not in quest_urls:
                    quest_urls.append(full_url)
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
                if status_match:
                    done_cnt, total_cnt = int(status_match.group(1)), int(status_match.group(2))
                    logger.info(f"   [*] Tiến độ hiện tại: {done_cnt}/{total_cnt} tasks")
                    if done_cnt >= total_cnt and total_cnt > 0:
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
                                # Nếu tab mới là bài Quiz/Poll thì giải luôn
                                from core.activities import solve_interactive_tab
                                solve_interactive_tab(sub_tab)
                                sub_tab.mouse.wheel(0, 300)
                                sub_tab.wait_for_timeout(1500)
                            except Exception:
                                pass
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

                    except Exception as e:
                        logger.warning(f"      [!] Bỏ qua nút #{t_idx+1}: {e}")

                logger.info(f"   [✓] Hoàn tất xử lý Quest {idx+1}")

            except Exception as e:
                logger.warning(f"   [!] Lỗi khi xử lý Quest {idx+1}: {e}")
                continue

        # Sau khi xong các Quest dạng Punch Card, quét luôn mục "Keep earning" tại /earn
        try:
            solve_keep_earning_activities(page, page.context)
        except Exception as e:
            logger.warning(f"[!] Lỗi khi quét Keep Earning trong Universal Quest Resolver: {e}")

    except Exception as e:
        logger.warning(f"[!] Lỗi trong Universal Quest Resolver: {e}")
    finally:
        logger.info("============================================================")
        logger.info("   HOÀN THÀNH: UNIVERSAL QUEST RESOLVER")
        logger.info("============================================================\n")


def solve_keep_earning_activities(page: Page, context: Optional[BrowserContext] = None) -> int:
    """
    Quét và giải quyết tất cả các thẻ nhiệm vụ lẻ (+5, +10, +15, +20 điểm)
    trong khu vực 'Keep earning' tại https://rewards.bing.com/earn (hoặc /earn?section=levelup).
    
    Hỗ trợ giao diện React-Aria (Disclosure/Tailwind) mới của Microsoft Rewards.
    Tự động mở rộng danh sách nếu đang thu gọn, lọc các thẻ chưa hoàn thành,
    tương tác qua tab phụ, duy trì 4-6s với 2 nhịp cuộn, giải quiz/poll nếu có,
    và tự động nhận thưởng (claim).
    """
    from core.tracker import logger, _log_info, _log_warning, _log_error, _log_debug
    from core.activities import dismiss_popups, claim_all_available_rewards, solve_interactive_tab

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
        initial_progress = page.evaluate("""() => {
            const sec = document.querySelector('section#moreactivities') || 
                        document.querySelector('section[id*="moreactivities"]') ||
                        Array.from(document.querySelectorAll('section')).find(s => (s.innerText || '').toLowerCase().includes('keep earning'));
            if (!sec) return '';
            const m = (sec.innerText || '').match(/(\\d+)\\s*\\/\\s*(\\d+)/);
            return m ? m[0] : '';
        }""")
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
        tried_urls = set()

        for attempt in range(max_attempts):
            # Quét DOM và tìm thẻ chưa hoàn thành tiếp theo
            next_card = page.evaluate(r"""() => {
                const sec = document.querySelector('section#moreactivities') || 
                            document.querySelector('section[id*="moreactivities"]') || 
                            Array.from(document.querySelectorAll('section')).find(s => (s.innerText || '').toLowerCase().includes('keep earning'));
                if (!sec) return null;

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
                    const href = el.href || '';
                    const text = (el.innerText || '').trim();
                    const lowerText = text.toLowerCase();

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
                        const title = text.split('\n')[0].trim() || `Thẻ #${i + 1}`;
                        return {
                            title: title,
                            pts: pts,
                            href: href
                        };
                    }
                }
                return null;
            }""")

            if not next_card:
                if attempt == 0:
                    _log_info(f"[Keep Earning] [✓] Tất cả thẻ nhiệm vụ trong 'Keep earning' đã hoàn thành ({initial_progress or '70/70'}).")
                else:
                    _log_info("[Keep Earning] [✓] Không còn thẻ nhiệm vụ chưa hoàn thành.")
                break

            title = next_card.get("title", "Task")
            pts = next_card.get("pts", 0)
            pts_str = f"+{pts}" if pts else "+XX"
            href = next_card.get("href", "")

            # Tránh lặp vô hạn nếu một thẻ bị lỗi không cộng điểm
            if href in tried_urls:
                _log_warning(f"[Keep Earning] Bỏ qua thẻ đã thử trước đó nhưng chưa hoàn thành: {title}")
                page.evaluate("() => { const el = document.querySelector('[data-bot-keep-earning-target=\"active\"]'); if (el) el.removeAttribute('data-bot-keep-earning-target'); }")
                break
            tried_urls.add(href)

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
                        continue

                # Tương tác trên tab phụ: Duy trì 4 - 6s, cuộn chuột nhẹ 2 nhịp, giải quiz/poll nếu có
                if sub_tab:
                    tab_start_time = time.time()
                    target_dwell_sec = random.uniform(4.0, 6.0)
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

                        # Tự động giải trắc nghiệm / poll / quiz nếu có trên trang Bing
                        try:
                            solve_interactive_tab(sub_tab)
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

        # 5. Kiểm tra và ghi nhận tiến độ cuối
        final_progress = page.evaluate("""() => {
            const sec = document.querySelector('section#moreactivities') || 
                        document.querySelector('section[id*="moreactivities"]') ||
                        Array.from(document.querySelectorAll('section')).find(s => (s.innerText || '').toLowerCase().includes('keep earning'));
            if (!sec) return '';
            const m = (sec.innerText || '').match(/(\\d+)\\s*\\/\\s*(\\d+)/);
            return m ? m[0] : '';
        }""")
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


def claim_msn_daily_gaming_tile(page: Page):
    """Quét và click thẻ nhận điểm Daily Tile trên trang MSN Gaming."""
    from core.tracker import _logger as logger
    from core.activities import dismiss_popups
    logger.info("--- BẮT ĐẦU: QUÉT DAILY GAMING TILE (MSN PLAY) ---")
    try:
        page.goto("https://www.msn.com/en-us/play", wait_until="domcontentloaded", timeout=15000)
        dismiss_popups(page)
        page.wait_for_timeout(2500)
        
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
    except Exception as e:
        logger.warning(f"  [!] Bỏ qua Gaming Tile do lỗi: {e}")
    finally:
        logger.info("--- KẾT THÚC: DAILY GAMING TILE ---\n")


def solve_wallpaper_trivia(page: Page):
    """Mở mini-quiz ảnh nền trang chủ Bing và dùng Local AI giải."""
    from core.tracker import _logger as logger
    from core.activities import dismiss_popups
    from core.ai_solver import ask_local_ai
    logger.info("--- BẮT ĐẦU: MINI-QUIZ HÌNH NỀN BING ---")
    try:
        page.goto("https://www.bing.com", wait_until="domcontentloaded", timeout=12000)
        dismiss_popups(page)
        page.wait_for_timeout(1500)
        
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
    except Exception as e:
        logger.warning(f"  [!] Bỏ qua Wallpaper Trivia: {e}")
    finally:
        logger.info("--- KẾT THÚC: MINI-QUIZ HÌNH NỀN ---\n")


def interact_with_copilot(page: Page, query: str = "Thời tiết hôm nay"):
    """Mở Bing Chat/Copilot và gửi 1 truy vấn tương tác."""
    from core.tracker import _logger as logger
    from core.activities import dismiss_popups
    logger.info("--- BẮT ĐẦU: TƯƠNG TÁC COPILOT / BING CHAT ---")
    try:
        page.goto("https://www.bing.com/chat", wait_until="domcontentloaded", timeout=15000)
        dismiss_popups(page)
        page.wait_for_timeout(2000)
        
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
    except Exception as e:
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


def create_random_sample_png(filepath: str = "data/visual_sample.png") -> str:
    """Tạo một file ảnh PNG 200x200 RGB hợp lệ có họa tiết màu ngẫu nhiên mỗi lần chạy."""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    width, height = 200, 200
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


def ensure_sample_image() -> str:
    """Tạo file ảnh PNG mẫu nếu chưa tồn tại (tương thích ngược)."""
    return create_random_sample_png()


def solve_visual_search_streak(page: Page):
    """
    Thực hiện Visual Search Streak theo chuẩn 4 bước của người dùng thật:
    1. Kích hoạt từ thẻ Visual Search trên Dashboard (rewards.bing.com) để nhận campaign token.
    2. Mở hộp thoại Visual Search (Camera/Lens) trên thanh tìm kiếm bing.com.
    3. Tải file ảnh thật (dynamic PNG) lên qua <input type="file"> hoặc click ảnh mẫu (tuyệt đối không redirect URL).
    4. Tương tác trên trang kết quả, chạm đồng xu và quay lại Dashboard nhận thưởng.
    """
    logger.info("--- BẮT ĐẦU: VISUAL SEARCH STREAK (TRIGGER TỪ DASHBOARD & UPLOAD ẢNH THẬT) ---")
    try:
        from core.activities import dismiss_popups, tap_corner_rewards_coin, claim_all_available_rewards

        # Bước 1: Kích hoạt từ thẻ Visual Search trên Dashboard (rewards.bing.com)
        logger.info("   [1/4] Kiểm tra và kích hoạt nhiệm vụ từ Rewards Dashboard...")
        if "rewards.bing.com" not in page.url or "pointsbreakdown" in page.url:
            page.goto("https://rewards.bing.com/", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2500)
        dismiss_popups(page)

        # Kiểm tra xem thẻ đã hoàn thành từ trước chưa
        is_already_done = page.evaluate('''() => {
            const pb = document.querySelector('[aria-label="Visual Search"], [aria-label*="trực quan" i]');
            if (pb) {
                const cur = parseFloat(pb.getAttribute('aria-valuenow') || '0');
                const max = parseFloat(pb.getAttribute('aria-valuemax') || '1');
                const valText = (pb.getAttribute('aria-valuetext') || '').trim();
                return (max > 0 && cur >= max) || valText === '100%';
            }
            return false;
        }''')
        if is_already_done:
            logger.info("   [✓] Thẻ Visual Search đã hoàn thành (1/1) từ trước! Bỏ qua.")
            return

        # Tìm thẻ nhiệm vụ Visual Search trên Dashboard
        target_page = page
        context = page.context
        card_selectors = [
            'button:has-text("Visual Search")',
            '[role="button"]:has-text("Visual Search")',
            'button:has-text("Use an image to search")',
            'button:has-text("Tìm kiếm bằng hình ảnh")',
            'button:has-text("Tìm kiếm trực quan")',
            'button:has([aria-label*="Visual Search" i])',
        ]

        card_clicked = False
        for c_sel in card_selectors:
            loc = page.locator(c_sel).first
            if loc.count() > 0 and loc.is_visible(timeout=1000):
                loc.scroll_into_view_if_needed(timeout=2000)
                loc.click()
                card_clicked = True
                logger.info(f"   [*] Đã click thẻ Visual Search: {c_sel}")
                page.wait_for_timeout(1500)
                break

        # Nếu popup modal bung ra ("Search now" / "Tìm kiếm ngay"), click để chuyển tab
        search_now_loc = page.locator('a:has-text("Search now"), a:has-text("Tìm kiếm ngay"), a[href*="vsstreak"], a[href*="bing.com"]').first
        if search_now_loc.count() > 0 and search_now_loc.is_visible(timeout=2000):
            logger.info("   [*] Phát hiện nút 'Search now' kèm tracking link chiến dịch, đang mở tab Bing...")
            try:
                with context.expect_page(timeout=5000) as new_page_info:
                    search_now_loc.click()
                target_page = new_page_info.value
            except Exception:
                if len(context.pages) > 1 and context.pages[-1] != page:
                    target_page = context.pages[-1]
                else:
                    href = search_now_loc.get_attribute("href") or "https://www.bing.com/?features=vsstreak,vstooltip&form=ML2XES"
                    target_page.goto(href, wait_until="domcontentloaded")
        elif "bing.com" not in target_page.url:
            logger.info("   [*] Chuyển hướng sang Bing với tracking token Visual Search Streak...")
            target_page.goto("https://www.bing.com/?features=vsstreak,vstooltip&form=ML2XES", wait_until="domcontentloaded")

        target_page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(2000)
        dismiss_popups(target_page)

        # Bước 2: Mở hộp thoại Visual Search (Camera / Lens) trên bing.com
        logger.info("   [2/4] Mở hộp thoại Visual Search (Camera) trên Bing...")
        cam_selectors = [
            '#sb_sbi',
            '#sbi_b',
            '.sbi_b',
            '#sb_form_camera',
            'div[aria-label="Tìm kiếm bằng hình ảnh"]',
            'div[aria-label="Search using an image"]',
            '[aria-label*="Search using an image" i]',
        ]

        cam_opened = False
        for cam_sel in cam_selectors:
            cam_btn = target_page.locator(cam_sel).first
            if cam_btn.count() > 0 and cam_btn.is_visible(timeout=1000):
                cam_btn.hover()
                cam_btn.click(force=True)
                cam_opened = True
                logger.info(f"   [*] Đã click nút camera: {cam_sel}")
                target_page.wait_for_timeout(2000)
                break

        if not cam_opened:
            logger.warning("   [!] Không tìm thấy nút Camera trên Bing, thử mở trực tiếp qua opensbi...")
            target_page.goto("https://www.bing.com/?features=vsstreak,vstooltip&form=ML2XES&opensbi=1", wait_until="domcontentloaded")
            target_page.wait_for_timeout(2500)

        # Bước 3: Thực hiện Visual Search bằng file ảnh thật (Tuyệt đối không redirect URL)
        logger.info("   [3/4] Tải file ảnh mẫu thật lên Bing Visual Search...")
        sample_img_path = create_random_sample_png()

        search_success = False

        # Phương thức 1: Upload ảnh thật qua <input type="file">
        file_input = target_page.locator('#sb_fileinput, #sb_sbidialog input[type="file"], input[type="file"][accept*="image"]').first
        if file_input.count() > 0:
            logger.info(f"   [*] Nạp file ảnh {sample_img_path} vào input[type=file]...")
            file_input.set_input_files(sample_img_path)
            target_page.wait_for_timeout(2000)

            # Thử thêm qua file chooser nếu cần kích hoạt nút tải lên
            upload_btn = target_page.locator('#sb_brtext, #sb_upload, .sbi_upload, span:has-text("tải lên"), span:has-text("upload")').first
            if upload_btn.is_visible(timeout=1000):
                try:
                    with target_page.expect_file_chooser(timeout=3000) as fc_info:
                        upload_btn.click()
                    fc_info.value.set_files(sample_img_path)
                    target_page.wait_for_timeout(2000)
                except Exception:
                    pass

            # Xử lý tự động gửi form POST lên endpoint /images/search nếu script kblob bị nghẽn
            target_page.evaluate('''() => {
                const form = document.querySelector('#sbi_form');
                if (form) {
                    form.action = "/images/search?view=detailv2&iss=sbiupload&FORM=SBIHMP";
                    form.method = "POST";
                    form.submit();
                }
            }''')
            target_page.wait_for_timeout(4000)
            search_success = True

        # Phương thức 2 (Dự phòng): Click ảnh mẫu (Sample Image) có sẵn trong popup
        if not search_success or ("detailv2" not in target_page.url and "images" not in target_page.url):
            logger.info("   [*] Thử phương thức dự phòng: Click ảnh mẫu (Sample Image)...")
            sample_img = target_page.locator('#sb_sbidialog img:not(.clsbtnimg):not(.infbtnimg), .sbi_sam_img, .sbi_try_item, [class*="sample"] img').first
            if sample_img.count() > 0 and sample_img.is_visible(timeout=2000):
                sample_img.click()
                target_page.wait_for_timeout(3000)
                search_success = True

        # Phương thức 3 (Dự phòng 2): Nhập link ảnh vào ô Paste URL và gõ phím thật + Enter
        if not search_success or ("detailv2" not in target_page.url and "images" not in target_page.url):
            logger.info("   [*] Thử phương thức dự phòng 2: Dán URL ảnh vào ô input...")
            pst_input = target_page.locator('#sb_imgpst, #sb_sbi_ipt, input[placeholder*="dán" i], input[placeholder*="paste" i]').first
            if pst_input.count() > 0 and pst_input.is_visible(timeout=2000):
                sample_url = "https://images.unsplash.com/photo-1546527868-ccb7ee7dfa6a"
                pst_input.click()
                target_page.keyboard.type(sample_url, delay=40)
                target_page.wait_for_timeout(500)
                target_page.keyboard.press("Enter")
                target_page.wait_for_timeout(3000)

        # Bước 4: Tương tác trên trang kết quả Visual Search & Chốt điểm
        logger.info(f"   [4/4] Tương tác trang kết quả Visual Search (URL hiện tại: {target_page.url[:80]})...")
        target_page.wait_for_load_state("domcontentloaded")
        # Giữ trang 5-6 giây để telemetry beacon của Bing Visual Search gửi xong
        target_page.wait_for_timeout(5500)

        # Cuộn nhẹ trang kết quả
        target_page.mouse.wheel(0, 300)
        target_page.wait_for_timeout(1000)

        # Click vào 1 ảnh kết quả tương tự nếu có
        matched_img = target_page.locator('.iusc, a[href*="detailv2"], a.iusc, #b_content img, .imgpt a, .mimg, [class*="visual-match"]').first
        if matched_img.count() > 0 and matched_img.is_visible(timeout=2000):
            try:
                matched_img.click(timeout=3000)
                logger.info("   [*] Đã click 1 ảnh kết quả tương tự (Visual Match).")
                target_page.wait_for_timeout(3000)
            except Exception:
                pass

        # Chạm đồng xu Rewards ở góc màn hình
        tap_corner_rewards_coin(target_page, context_name="Visual-Search")
        target_page.wait_for_timeout(2000)

        # Đóng tab phụ nếu có mở tab mới
        if target_page != page:
            logger.info("   [*] Đóng tab phụ Visual Search...")
            try:
                target_page.close()
            except Exception:
                pass

        # Quay về Dashboard, reload trang và Claim điểm thưởng
        logger.info("   [*] Quay về Rewards Dashboard để nhận thưởng...")
        page.goto("https://rewards.bing.com/", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(3000)
        dismiss_popups(page)

        claimed_pts = claim_all_available_rewards(page)
        if claimed_pts > 0:
            logger.info(f"   [✓] Đã claim thành công {claimed_pts} phần thưởng từ Dashboard!")

        # Kiểm tra lại trạng thái thẻ
        final_check = page.evaluate('''() => {
            const pb = document.querySelector('[aria-label="Visual Search"], [aria-label*="trực quan" i]');
            if (pb) {
                return {
                    val: pb.getAttribute('aria-valuenow'),
                    max: pb.getAttribute('aria-valuemax'),
                    text: pb.getAttribute('aria-valuetext')
                };
            }
            return null;
        }''')
        if final_check:
            status_icon = "✓" if (final_check.get("val") == final_check.get("max") or final_check.get("text") == "100%") else "✗"
            logger.info(f"   [{status_icon}] Tiến độ Thẻ Visual Search sau cùng: {final_check.get('val')}/{final_check.get('max')} ({final_check.get('text')})")

        logger.info("   [✓] Hoàn thành luồng Visual Search Streak chuẩn xác!")

    except Exception as e:
        logger.warning(f"   [!] Bỏ qua Visual Search do lỗi: {e}")
    finally:
        logger.info("--- KẾT THÚC: VISUAL SEARCH STREAK ---\n")


def get_daily_search_progress(page: Page) -> dict:
    """
    Đọc tiến độ tìm kiếm thực tế và 5 thẻ nhiệm vụ ngày từ Rewards Dashboard & Points Breakdown.
    Trả về: {"pc_cur": int, "pc_max": int, "mobile_cur": int, "mobile_max": int, "all_maxed": bool, "activities": dict, "all_activities_done": bool}
    """
    logger.info("[Dashboard] Đang đọc tiến độ điểm tìm kiếm thực tế từ tài khoản...")
    progress = {
        "pc_cur": 0, "pc_max": 90,
        "mobile_cur": 0, "mobile_max": 60,
        "all_maxed": False,
        "activities": {},
        "all_activities_done": False
    }

    try:
        # Bước A: Kiểm tra 5 thẻ nhiệm vụ ngày trên Dashboard chính (rewards.bing.com)
        if "rewards.bing.com" not in page.url or "modal" in page.url:
            page.goto("https://rewards.bing.com/", wait_until="domcontentloaded", timeout=12000)
            page.wait_for_timeout(2000)
        dismiss_popups(page)

        act_data = page.evaluate(r"""() => {
            const result = {
                activities: {
                    'Bing Search': { cur: 0, max: 1, done: false },
                    'Daily Set': { cur: 0, max: 3, done: false },
                    'Edge Browsing': { cur: 0, max: 30, done: false },
                    'Mobile App': { cur: 0, max: 1, done: false },
                    'Visual Search': { cur: 0, max: 1, done: false }
                },
                found_count: 0,
                all_done: false
            };

            // 1. Kiểm tra qua các progressbar (React-Aria UI mới)
            const bars = document.querySelectorAll('[role="progressbar"]');
            bars.forEach(bar => {
                const label = (bar.getAttribute('aria-label') || '').trim();
                const cur = parseFloat(bar.getAttribute('aria-valuenow') || '0');
                const max = parseFloat(bar.getAttribute('aria-valuemax') || '0');
                const valText = bar.getAttribute('aria-valuetext') || '';
                const is100 = (max > 0 && cur >= max) || valText.trim() === '100%';

                const labelLower = label.toLowerCase();
                if (labelLower.includes('bing') && !labelLower.includes('star')) {
                    result.activities['Bing Search'] = { cur, max, done: is100 };
                    result.found_count++;
                } else if (labelLower.includes('daily set') || labelLower.includes('bộ hằng ngày')) {
                    result.activities['Daily Set'] = { cur, max, done: is100 };
                    result.found_count++;
                } else if (labelLower.includes('edge')) {
                    result.activities['Edge Browsing'] = { cur, max, done: is100 };
                    result.found_count++;
                } else if (labelLower.includes('mobile app') || labelLower.includes('ứng dụng')) {
                    result.activities['Mobile App'] = { cur, max, done: is100 };
                    result.found_count++;
                } else if (labelLower.includes('visual search') || labelLower.includes('trực quan')) {
                    result.activities['Visual Search'] = { cur, max, done: is100 };
                    result.found_count++;
                }
            });

            // 2. Fallback text content cho 5 thẻ
            const textContent = document.body.innerText;
            if (result.found_count < 5) {
                const bingM = textContent.match(/Bing[^\d]*Search:\s*(\d+)\s*\/\s*(\d+)/i) ||
                              textContent.match(/Bing[^\d]*Tìm kiếm:\s*(\d+)\s*\/\s*(\d+)/i);
                if (bingM && !result.activities['Bing Search'].done) {
                    const c = parseInt(bingM[1]), m = parseInt(bingM[2]);
                    result.activities['Bing Search'] = { cur: c, max: m, done: c >= m && m > 0 };
                    result.found_count++;
                }
                const dsM = textContent.match(/Daily Set[^\d]*Activity:\s*(\d+)\s*\/\s*(\d+)/i) ||
                            textContent.match(/Bộ hằng ngày[^\d]*(?:Hoạt động|Nhiệm vụ):\s*(\d+)\s*\/\s*(\d+)/i);
                if (dsM && !result.activities['Daily Set'].done) {
                    const c = parseInt(dsM[1]), m = parseInt(dsM[2]);
                    result.activities['Daily Set'] = { cur: c, max: m, done: c >= m && m > 0 };
                    result.found_count++;
                }
                const edgeM = textContent.match(/Edge[^\d]*Minutes:\s*(\d+)\s*\/\s*(\d+)/i) ||
                              textContent.match(/Edge[^\d]*Phút:\s*(\d+)\s*\/\s*(\d+)/i);
                if (edgeM && !result.activities['Edge Browsing'].done) {
                    const c = parseInt(edgeM[1]), m = parseInt(edgeM[2]);
                    result.activities['Edge Browsing'] = { cur: c, max: m, done: c >= m && m > 0 };
                    result.found_count++;
                }
                const mobM = textContent.match(/Mobile App[^\d]*Check-in:\s*(\d+)\s*\/\s*(\d+)/i) ||
                             textContent.match(/Ứng dụng di động[^\d]*Điểm danh:\s*(\d+)\s*\/\s*(\d+)/i);
                if (mobM && !result.activities['Mobile App'].done) {
                    const c = parseInt(mobM[1]), m = parseInt(mobM[2]);
                    result.activities['Mobile App'] = { cur: c, max: m, done: c >= m && m > 0 };
                    result.found_count++;
                }
                const visM = textContent.match(/Visual Search[^\d]*Activity:\s*(\d+)\s*\/\s*(\d+)/i) ||
                             textContent.match(/Tìm kiếm trực quan[^\d]*(?:Hoạt động|Nhiệm vụ):\s*(\d+)\s*\/\s*(\d+)/i);
                if (visM && !result.activities['Visual Search'].done) {
                    const c = parseInt(visM[1]), m = parseInt(visM[2]);
                    result.activities['Visual Search'] = { cur: c, max: m, done: c >= m && m > 0 };
                    result.found_count++;
                }
            }

            // Chỉ coi là all_done khi TẤT CẢ 5 thẻ nhiệm vụ ngày đều đã đạt done === true
            const allDone = Object.values(result.activities).every(a => a.done);
            result.all_done = (result.found_count >= 5) && allDone;
            return result;
        }""")

        progress["activities"] = act_data.get("activities", {})

        # Sửa lại điều kiện all_activities_done: Chỉ True khi TẤT CẢ 5 thẻ (bao gồm Visual Search) đều đạt [✓]
        all_act = progress["activities"]
        all_five_done = (
            len(all_act) == 5 and
            all(a.get("done", False) for a in all_act.values())
        )
        progress["all_activities_done"] = all_five_done

        for act_name, act_info in progress["activities"].items():
            status_icon = "✓" if act_info["done"] else "✗"
            logger.info(f"[Dashboard] Thẻ {act_name}: {act_info['cur']}/{act_info['max']} [{status_icon}]")

        if not progress["all_activities_done"]:
            uncompleted = [k for k, v in all_act.items() if not v.get("done", False)]
            logger.info(f"[Dashboard] Còn {len(uncompleted)} thẻ chưa hoàn thành: {', '.join(uncompleted)} -> all_activities_done = False")

        # Bước B: Kiểm tra points breakdown qua popup / URL pointsbreakdown
        try:
            page.goto("https://rewards.bing.com/pointsbreakdown", wait_until="domcontentloaded", timeout=12000)
            page.wait_for_timeout(2000)
            dismiss_popups(page)

            # Trích xuất các chỉ số điểm x/y qua evaluate DOM
            breakdown_data = page.evaluate(r"""() => {
                const result = {};
                const textContent = document.body.innerText;

                // Tìm các đoạn dạng PC search 90 / 90 hoặc 90/90
                const pcMatch = textContent.match(/(?:PC search|Tìm kiếm trên PC|Tìm kiếm trên máy tính)[^\d]*(\d+)\s*\/\s*(\d+)/i);
                if (pcMatch) {
                    result.pc_cur = parseInt(pcMatch[1]);
                    result.pc_max = parseInt(pcMatch[2]);
                }

                // Tìm Mobile search
                const mobMatch = textContent.match(/(?:Mobile search|Tìm kiếm trên thiết bị di động|Tìm kiếm trên di động)[^\d]*(\d+)\s*\/\s*(\d+)/i);
                if (mobMatch) {
                    result.mob_cur = parseInt(mobMatch[1]);
                    result.mob_max = parseInt(mobMatch[2]);
                }

                // Kiểm tra các phần tử card cụ thể nếu regex text không bắt được
                const cards = document.querySelectorAll('div[class*="pointsDetail"], div[class*="progress"], mee-rewards-points-breakdown-card');
                cards.forEach(card => {
                    const txt = card.innerText || '';
                    const m = txt.match(/(\d+)\s*\/\s*(\d+)/);
                    if (m) {
                        const c = parseInt(m[1]), mx = parseInt(m[2]);
                        if (txt.toLowerCase().includes('pc') && !result.pc_cur) {
                            result.pc_cur = c; result.pc_max = mx;
                        } else if ((txt.toLowerCase().includes('mobile') || txt.toLowerCase().includes('di động')) && !result.mob_cur) {
                            result.mob_cur = c; result.mob_max = mx;
                        }
                    }
                });

                return result;
            }""")

            found_pc = False
            found_mob = False

            if "pc_cur" in breakdown_data and "pc_max" in breakdown_data:
                progress["pc_cur"] = breakdown_data["pc_cur"]
                progress["pc_max"] = breakdown_data["pc_max"]
                found_pc = True

            if "mob_cur" in breakdown_data and "mob_max" in breakdown_data:
                progress["mobile_cur"] = breakdown_data["mob_cur"]
                progress["mobile_max"] = breakdown_data["mob_max"]
                found_mob = True

            # Nếu giao diện không hiển thị bảng số PC/Mobile riêng biệt (giao diện mới 2026),
            # đối chiếu theo 5 thẻ nhiệm vụ ngày:
            if not found_pc and not found_mob:
                bing_done = progress["activities"].get("Bing Search", {}).get("done", False)
                mob_done = progress["activities"].get("Mobile App", {}).get("done", False)

                if progress["all_activities_done"]:
                    progress["pc_cur"] = progress["pc_max"]
                    progress["mobile_cur"] = progress["mobile_max"]
                    logger.info("[Dashboard] Giao diện mới: Tất cả 5 thẻ nhiệm vụ ngày đã hoàn thành 100% -> Điểm tìm kiếm đạt tối đa.")
                else:
                    if bing_done:
                        progress["pc_cur"] = progress["pc_max"]
                    if mob_done:
                        progress["mobile_cur"] = progress["mobile_max"]

        except Exception as e:
            logger.warning(f"[Dashboard] Không thể đọc points breakdown: {e}")

        logger.info(f"[Dashboard] Tiến độ điểm PC: {progress['pc_cur']}/{progress['pc_max']} | Mobile: {progress['mobile_cur']}/{progress['mobile_max']}")

        # Nếu cả 2 đều đã chạm mốc tối đa
        if progress["pc_cur"] >= progress["pc_max"] and progress["mobile_cur"] >= progress["mobile_max"]:
            progress["all_maxed"] = True

    except Exception as e:
        logger.warning(f"[Dashboard] Lỗi khi kiểm tra tiến độ ngày: {e}")

    return progress


if __name__ == "__main__":
    # Test nhanh (cần profile đã đăng nhập)
    from playwright.sync_api import sync_playwright
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