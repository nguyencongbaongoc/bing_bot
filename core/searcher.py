"""
Search module handling natural human-like typing and search interactions on Bing.
Tối ưu hóa hành vi tìm kiếm tự nhiên (Organic Search Engine) nhằm tối đa hóa Bing Star bonus (2,100 pts/tháng):
- Chế độ 1 (Chain từ Related Searches - 30% tỷ lệ): Cuộn trang kết quả, click trực tiếp thẻ Related searches.
- Chế độ 2 (Autocomplete Selection - 30% tỷ lệ): Gõ 60-70% từ khóa, chọn gợi ý Bing Autosuggest (#sa_ul li).
- Chế độ 3 (Natural Full Typing - 40% tỷ lệ): Gõ toàn bộ từ khóa với mô phỏng typo 5% phím sai rồi Backspace.
- Tương tác đọc kết quả (Stealth CTR 55%): Click kết quả tự nhiên với Dwell time 5.5 - 11.0s, cuộn trang đích.
- Tuyệt đối không dùng direct URL search (page.goto search?q=...), tương tác 100% qua ô input trên trang.
"""
import math
import random
import time
from playwright.sync_api import Page
from core.logger import logger
from core.trends import save_searched_keyword

# Bộ nhớ vị trí con trỏ chuột gần nhất trên màn hình
_last_mouse_pos = {"x": None, "y": None}


def human_bezier_move(
    page: Page,
    start_x: float = None,
    start_y: float = None,
    end_x: float = None,
    end_y: float = None,
    steps: int = None,
) -> None:
    """
    Di chuyển chuột theo quỹ đạo cong Bézier bậc 2 tự nhiên kèm vi rung (micro-jitter)
    và gia tốc / giảm tốc Fitts's law, thay thế đường thẳng tuyến tính mặc định của Playwright.
    Hỗ trợ các cách gọi:
      - human_bezier_move(page, start_x, start_y, end_x, end_y)
      - human_bezier_move(page, end_x=tx, end_y=ty)
      - human_bezier_move(page, tx, ty)
    """
    global _last_mouse_pos

    try:
        # Xử lý các dạng tham số truyền vào
        if end_x is None and end_y is None and start_x is not None and start_y is not None:
            target_x, target_y = float(start_x), float(start_y)
            orig_sx, orig_sy = None, None
        else:
            target_x = float(end_x) if end_x is not None else 0.0
            target_y = float(end_y) if end_y is not None else 0.0
            orig_sx = float(start_x) if start_x is not None else None
            orig_sy = float(start_y) if start_y is not None else None

        # Tọa độ điểm bắt đầu
        if orig_sx is not None and orig_sy is not None:
            sx, sy = orig_sx, orig_sy
        elif _last_mouse_pos["x"] is not None and _last_mouse_pos["y"] is not None:
            sx, sy = _last_mouse_pos["x"], _last_mouse_pos["y"]
        else:
            viewport = page.viewport_size or {"width": 1280, "height": 800}
            sx = float(random.randint(int(viewport["width"] * 0.2), int(viewport["width"] * 0.7)))
            sy = float(random.randint(int(viewport["height"] * 0.1), int(viewport["height"] * 0.4)))

        ex, ey = target_x, target_y
        dx = ex - sx
        dy = ey - sy
        dist = math.hypot(dx, dy)

        if dist < 4:
            page.mouse.move(ex, ey)
            _last_mouse_pos["x"], _last_mouse_pos["y"] = ex, ey
            return

        # Tính điểm điều khiển (ctrl_x, ctrl_y) vuông góc với đoạn thẳng để tạo độ cong
        mid_x = (sx + ex) / 2.0
        mid_y = (sy + ey) / 2.0
        nx = -dy / dist
        ny = dx / dist

        curve_factor = random.uniform(-0.25, 0.25) * min(dist, 250)
        ctrl_x = mid_x + nx * curve_factor + random.uniform(-8, 8)
        ctrl_y = mid_y + ny * curve_factor + random.uniform(-8, 8)

        if steps is None:
            steps = random.randint(14, 22)

        for i in range(1, steps + 1):
            u = i / steps
            # Easing hàm sin (chậm lúc đầu, nhanh ở giữa, chậm dần khi tới đích)
            t = 0.5 * (1.0 - math.cos(math.pi * u))
            # Nội suy Bézier bậc 2
            bx = (1.0 - t) ** 2 * sx + 2.0 * (1.0 - t) * t * ctrl_x + t ** 2 * ex
            by = (1.0 - t) ** 2 * sy + 2.0 * (1.0 - t) * t * ctrl_y + t ** 2 * ey

            if i < steps:
                # Vi rung nhẹ ngẫu nhiên (micro-jitter)
                bx += random.uniform(-0.6, 0.6)
                by += random.uniform(-0.6, 0.6)
            else:
                bx, by = ex, ey

            page.mouse.move(bx, by)
            time.sleep(random.uniform(0.008, 0.020))

        _last_mouse_pos["x"], _last_mouse_pos["y"] = ex, ey
    except Exception as e:
        logger.debug(f"[Bezier Move] Lỗi khi di chuột cong: {e}")
        try:
            page.mouse.move(target_x, target_y)
            _last_mouse_pos["x"], _last_mouse_pos["y"] = target_x, target_y
        except Exception:
            pass


def smooth_scroll(page: Page, y_px: int, is_mobile: bool = False):
    """
    Cuộn trang mượt mà theo đúng đặc tính thiết bị:
    - Mobile: Giả lập thao tác vuốt cảm ứng tự nhiên (Touch Swipe) qua CDP để không lộ mousewheel.
    - Desktop: Sử dụng bánh xe cuộn chuột (Mouse Wheel).
    """
    try:
        if is_mobile:
            try:
                viewport = page.viewport_size or {"width": 412, "height": 839}
                cx = viewport["width"] // 2 + random.randint(-30, 30)
                start_y = viewport["height"] * 0.7
                end_y = max(80, start_y - y_px)

                cdp = page.context.new_cdp_session(page)
                cdp.send("Input.dispatchTouchEvent", {
                    "type": "touchStart",
                    "touchPoints": [{"x": cx, "y": start_y}]
                })
                steps = random.randint(6, 12)
                for i in range(1, steps + 1):
                    cy = start_y + (end_y - start_y) * (i / steps)
                    cdp.send("Input.dispatchTouchEvent", {
                        "type": "touchMove",
                        "touchPoints": [{"x": cx, "y": cy}]
                    })
                    time.sleep(0.01)
                cdp.send("Input.dispatchTouchEvent", {
                    "type": "touchEnd",
                    "touchPoints": []
                })
                cdp.detach()
            except Exception:
                page.mouse.wheel(0, y_px)
        else:
            page.mouse.wheel(0, y_px)
    except Exception:
        pass


def simulate_organic_click(page: Page, is_mobile: bool = False):
    """
    Mô phỏng người dùng click vào một kết quả tìm kiếm tự nhiên (Organic CTR ~55%).
    Đọc trang 5.5 - 11.0 giây (Dwell time), cuộn chuột 1-2 lần trên trang đích rồi quay lại hoặc đóng tab.
    """
    try:
        results = page.locator("#b_results h2 a, #b_results .b_algo a")
        count = results.count()
        if count == 0:
            return

        # Chọn ngẫu nhiên 1 trong 3 kết quả đầu tiên đang hiển thị
        choice_limit = min(3, count)
        idx = random.randint(0, choice_limit - 1)
        target = results.nth(idx)

        try:
            target.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass

        if not target.is_visible():
            return

        logger.info(f"[Stealth CTR 55%] Click tự nhiên vào kết quả tìm kiếm #{idx + 1}...")

        initial_pages = list(page.context.pages)
        new_page = None

        # Bắt sự kiện nếu link mở ra tab mới
        try:
            with page.expect_popup(timeout=2500) as popup_info:
                box = target.bounding_box()
                if box and not is_mobile:
                    tx = box["x"] + box["width"] * random.uniform(0.2, 0.8)
                    ty = box["y"] + box["height"] * random.uniform(0.25, 0.75)
                    human_bezier_move(page, end_x=tx, end_y=ty)
                    page.wait_for_timeout(random.randint(60, 150))
                    page.mouse.click(tx, ty)
                else:
                    target.click(timeout=3000)
            new_page = popup_info.value
        except Exception:
            pass

        # Kiểm tra thêm nếu popup không bắt được qua sự kiện nhưng tab mới đã mở trong context
        if new_page is None:
            new_pages = [p for p in page.context.pages if p not in initial_pages and not p.is_closed()]
            if new_pages:
                new_page = new_pages[-1]

        dwell = random.uniform(5.5, 11.0)
        if new_page:
            # Trường hợp mở ra tab mới
            try:
                new_page.wait_for_load_state("domcontentloaded", timeout=6000)
            except Exception:
                pass
            logger.info(f"[Stealth CTR] Dừng đọc tab mới trong {dwell:.1f}s (Dwell time)...")
            scroll_count = random.randint(1, 2)
            for _ in range(scroll_count):
                time.sleep(dwell / (scroll_count + 1))
                smooth_scroll(new_page, random.randint(180, 400), is_mobile=is_mobile)
            time.sleep(1.0)
            try:
                new_page.close()
            except Exception:
                pass
        else:
            # Trường hợp mở ngay trong tab hiện tại
            logger.info(f"[Stealth CTR] Dừng đọc trang đích trong {dwell:.1f}s (Dwell time)...")
            scroll_count = random.randint(1, 2)
            for _ in range(scroll_count):
                time.sleep(dwell / (scroll_count + 1))
                smooth_scroll(page, random.randint(180, 400), is_mobile=is_mobile)
            time.sleep(1.0)
            try:
                logger.debug("[Stealth CTR] Nhấn Back quay về kết quả tìm kiếm Bing...")
                page.go_back(wait_until="domcontentloaded", timeout=8000)
                page.wait_for_timeout(random.randint(1000, 1800))
            except Exception as e:
                logger.debug(f"[Stealth CTR] Không thể go_back, tải lại Bing: {e}")
                page.goto("https://www.bing.com", wait_until="domcontentloaded", timeout=10000)

    except Exception as e:
        logger.debug(f"[Stealth CTR] Bỏ qua tương tác CTR: {e}")


def _perform_post_search_dwell_and_ctr(page: Page, is_mobile: bool = False):
    """
    Thực hiện tương tác tự nhiên sau khi có kết quả tìm kiếm:
    - Cuộn trang đọc 2-3 nhịp
    - Headline Hovering (di chuột nhẹ qua tiêu đề bài viết trên PC)
    - High Organic CTR (55% xác suất click kết quả tự nhiên với Dwell time 5.5 - 11.0s)
    - Cuộn nhẹ ngược lên đầu trang chuẩn bị cho lượt tìm kiếm tiếp theo
    """
    try:
        # 1. Cuộn trang đọc kết quả tự nhiên (2 - 3 nhịp)
        scroll_times = random.randint(2, 3)
        for _ in range(scroll_times):
            scroll_px = random.randint(250, 500)
            smooth_scroll(page, scroll_px, is_mobile=is_mobile)
            page.wait_for_timeout(random.randint(900, 1600))

        # 2. Headline Hovering: Di chuột nhẹ lướt qua 1 tiêu đề bài viết trong #b_results h2
        if not is_mobile:
            try:
                headlines = page.locator("#b_results h2 a, #b_results .b_algo h2 a, #b_results h2")
                h_count = headlines.count()
                if h_count > 0:
                    h_idx = random.randint(0, min(2, h_count - 1))
                    h_target = headlines.nth(h_idx)
                    if h_target.is_visible():
                        h_box = h_target.bounding_box()
                        if h_box:
                            hx = h_box["x"] + h_box["width"] * random.uniform(0.2, 0.7)
                            hy = h_box["y"] + h_box["height"] * random.uniform(0.3, 0.7)
                            logger.debug(f"[Headline Hovering] Di chuột nhẹ lướt qua tiêu đề #{h_idx + 1}...")
                            human_bezier_move(page, end_x=hx, end_y=hy)
                            page.wait_for_timeout(random.randint(400, 800))
            except Exception as e:
                logger.debug(f"[Headline Hovering] Bỏ qua hover tiêu đề: {e}")

        # 3. Mô phỏng Click vào kết quả tìm kiếm (Organic Click-Through: 55% tỷ lệ)
        if random.random() < 0.55:
            simulate_organic_click(page, is_mobile=is_mobile)

        # 4. Cuộn nhẹ ngược lên đầu trang
        smooth_scroll(page, -random.randint(300, 600), is_mobile=is_mobile)
        page.wait_for_timeout(random.randint(800, 1500))
    except Exception as e:
        logger.debug(f"[Post-Search] Lỗi tương tác sau tìm kiếm: {e}")


def perform_related_cluster_search(page: Page, is_mobile: bool = False) -> str:
    """
    Chế độ 1: Chain từ Related Searches (30% cơ hội nếu đang ở trang kết quả Bing).
    Tìm các link trong khu vực tìm kiếm liên quan:
    .b_rs ul li a, .b_rich .b_vPanel a, .b_rs a, .b_vList a, [aria-label*="Related" i] a
    Cuộn tới phần tử đó, di chuột theo đường cong Bézier và click trực tiếp để chuyển sang trang kết quả mới.
    Trả về từ khóa liên quan đã click (hoặc None nếu không tìm thấy/lỗi).
    """
    try:
        if "bing.com" not in page.url or ("/search" not in page.url and "q=" not in page.url):
            return None

        # Cuộn nhẹ xuống dưới để khu vực Related Searches được render
        smooth_scroll(page, random.randint(400, 700), is_mobile=is_mobile)
        page.wait_for_timeout(random.randint(600, 1000))

        related_selectors = [
            ".b_rs ul li a",
            ".b_rich .b_vPanel a",
            ".b_rs a",
            ".b_vList a",
            '[aria-label*="Related" i] a',
            "#b_results .b_rs a",
            ".b_rs .b_rsv3 a",
            '[class*="relatedSearches" i] a',
            '.b_ans a[href*="search?q="]',
            '#b_results .b_ans a[href*="q="]',
        ]

        found_links = []
        for sel in related_selectors:
            try:
                links = page.locator(sel).all()
                for link in links:
                    if link.is_visible():
                        txt = link.inner_text().strip()
                        if txt and len(txt) > 2 and "\n" not in txt:
                            found_links.append((link, txt))
            except Exception:
                continue
            if found_links:
                break

        if not found_links:
            logger.debug("[Related Searches] Không tìm thấy link tìm kiếm liên quan trên trang hiện tại.")
            return None

        target_link, related_keyword = random.choice(found_links[:5])
        logger.info(f"[Mode 1: Related Chaining] Phát hiện thẻ liên quan! Click: '{related_keyword}'...")

        try:
            target_link.scroll_into_view_if_needed(timeout=2000)
            page.wait_for_timeout(random.randint(300, 600))
        except Exception:
            pass

        box = target_link.bounding_box()
        if box and not is_mobile:
            tx = box["x"] + box["width"] * random.uniform(0.2, 0.8)
            ty = box["y"] + box["height"] * random.uniform(0.3, 0.7)
            human_bezier_move(page, end_x=tx, end_y=ty)
            page.wait_for_timeout(random.randint(80, 200))
            page.mouse.click(tx, ty)
        elif is_mobile and box:
            tx = box["x"] + box["width"] * random.uniform(0.2, 0.8)
            ty = box["y"] + box["height"] * random.uniform(0.3, 0.7)
            try:
                page.touchscreen.tap(tx, ty)
            except Exception:
                target_link.click(timeout=3000)
        else:
            target_link.click(timeout=3000)

        # Chờ trang kết quả liên quan tải xong
        try:
            page.wait_for_load_state("domcontentloaded", timeout=12000)
        except Exception:
            pass
        page.wait_for_timeout(random.randint(1500, 2500))

        # Lưu từ khóa đã tìm kiếm
        save_searched_keyword(related_keyword)

        # Thực hiện tương tác sau tìm kiếm (cuộn 2-3 nhịp, headline hover, Stealth CTR 55%, cuộn lên lại)
        _perform_post_search_dwell_and_ctr(page, is_mobile=is_mobile)

        return related_keyword
    except Exception as e:
        logger.debug(f"[Related Searches] Lỗi: {e}")
        return None


def human_type_and_search(page: Page, keyword: str, is_mobile: bool = False) -> bool:
    """
    Thực hiện tìm kiếm tự nhiên trên Bing theo 3 chế độ luân phiên (Organic Search Engine):
    1. Chế độ 1 (Chain từ Related Searches - 30% cơ hội nếu đang ở trang kết quả Bing):
       Cuộn xuống cuối trang kết quả, click trực tiếp vào thẻ Related searches mà không cần gõ phím.
    2. Chế độ 2 (Gõ từ khóa + Chọn Autocomplete - 30% cơ hội):
       Gõ 60-70% từ khóa (độ trễ 0.08 - 0.22s/ký tự), đợi 0.8-1.4s dropdown #sa_ul li xuất hiện,
       click gợi ý thứ 1 hoặc thứ 2. Nếu không hiện gợi ý -> gõ nốt và nhấn Enter.
    3. Chế độ 3 (Gõ tự nhiên toàn bộ từ khóa - 40% cơ hội):
       Gõ toàn bộ từ khóa với tốc độ người thật (5% xác suất typo gõ nhầm 1 phím rồi Backspace),
       nghỉ 0.4-0.8s rồi nhấn Enter.
    Tuyệt đối không dùng page.goto(f"https://www.bing.com/search?q={query}") trừ khi trang bị lỗi hoàn toàn.
    """
    search_selectors = [
        "#sb_form_q",
        'textarea[name="q"]',
        'input[name="q"]',
        'input[type="search"]',
    ]

    try:
        roll = random.random()
        is_search_results = ("bing.com" in page.url and ("/search" in page.url or "q=" in page.url))

        # ===== CHẾ ĐỘ 1: Chain từ Related Searches (30% cơ hội) =====
        if roll < 0.30 and is_search_results:
            logger.info("[Organic Search] Chế độ 1: Related Search Chaining (30% tỷ lệ)...")
            cluster_kw = perform_related_cluster_search(page, is_mobile=is_mobile)
            if cluster_kw:
                return True
            logger.debug("[Organic Search] Không có Related searches khả dụng, chuyển sang chế độ gõ bàn phím...")

        # Xác định Chế độ 2 vs Chế độ 3
        # Phân phối: Mode 2 chiếm 30%, Mode 3 chiếm 40% (nếu Mode 1 không chạy thì chia theo tỷ lệ 3:4)
        use_autocomplete = (roll >= 0.30 and roll < 0.60) or (roll < 0.30 and random.random() < (30.0 / 70.0))

        # 1. Nếu chưa ở trên trang Bing hoặc không thấy thanh tìm kiếm, mở trang chủ Bing
        if "bing.com" not in page.url:
            page.goto("https://www.bing.com", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(random.randint(1200, 2000))

        # Đóng nhanh popup cookie nếu vô tình che ô tìm kiếm
        for accept_sel in ['#bnp_btn_accept', 'button:has-text("Accept")', 'button:has-text("Chấp nhận")']:
            try:
                btn = page.locator(accept_sel).first
                if btn.count() > 0 and btn.is_visible(timeout=500):
                    btn.click(timeout=2000)
                    page.wait_for_timeout(500)
            except Exception:
                pass

        # 2. Tìm ô nhập liệu tìm kiếm
        search_box = None
        for sel in search_selectors:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                search_box = loc
                break

        if search_box is None:
            page.goto("https://www.bing.com", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(1500)
            for sel in search_selectors:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    search_box = loc
                    break

        if search_box is None:
            # Fallback cực hạn: nếu hoàn toàn không tìm thấy ô input
            logger.warning(f"Không thể tìm thấy ô input tìm kiếm trên trang! Fallback điều hướng trực tiếp cho '{keyword}'...")
            page.goto(f"https://www.bing.com/search?q={keyword}", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)
            save_searched_keyword(keyword)
            _perform_post_search_dwell_and_ctr(page, is_mobile=is_mobile)
            return True

        # 3. Di chuột tự nhiên theo đường cong Bézier và click lệch tâm vào ô tìm kiếm
        try:
            search_box.scroll_into_view_if_needed(timeout=3000)
            box = search_box.bounding_box()
            if box:
                target_x = box["x"] + box["width"] * random.uniform(0.2, 0.8)
                target_y = box["y"] + box["height"] * random.uniform(0.25, 0.75)
                if is_mobile:
                    try:
                        page.touchscreen.tap(target_x, target_y)
                    except Exception:
                        page.mouse.click(target_x, target_y)
                else:
                    human_bezier_move(page, end_x=target_x, end_y=target_y)
                    page.wait_for_timeout(random.randint(60, 150))
                    page.mouse.click(target_x, target_y)
            else:
                search_box.click(timeout=3000)
        except Exception:
            try:
                search_box.click(force=True, timeout=3000)
            except Exception:
                search_box.evaluate("el => el.click()")
        page.wait_for_timeout(random.randint(200, 450))

        # Xóa chữ cũ
        page.keyboard.press("Control+A")
        page.wait_for_timeout(random.randint(100, 250))
        page.keyboard.press("Backspace")
        page.wait_for_timeout(random.randint(150, 300))
        try:
            search_box.fill("")
        except Exception:
            pass

        # ===== CHẾ ĐỘ 2: Gõ từ khóa + Chọn Autocomplete (30% cơ hội) =====
        if use_autocomplete and len(keyword) >= 4:
            logger.info("[Organic Search] Chế độ 2: Autocomplete Selection (30% tỷ lệ)...")
            cutoff = max(2, int(len(keyword) * random.uniform(0.60, 0.70)))

            # Gõ N ký tự đầu tiên với độ trễ biến thiên 0.08 - 0.22s/ký tự
            for ch in keyword[:cutoff]:
                delay_sec = random.uniform(0.08, 0.22)
                page.keyboard.type(ch, delay=int(delay_sec * 1000))
                if ch == " ":
                    page.wait_for_timeout(random.randint(120, 250))

            # Đợi 0.8 - 1.4s cho dropdown gợi ý #sa_ul li xuất hiện
            pause_wait = random.uniform(0.8, 1.4)
            page.wait_for_timeout(int(pause_wait * 1000))

            autocomplete_clicked = False
            try:
                suggestions = page.locator("#sa_ul li.sa_sg, #sa_ul li, .sa_sg, .sa_drw li, [role='listbox'] [role='option']")
                count = suggestions.count()
                if count > 0 and suggestions.first.is_visible(timeout=1000):
                    choice_idx = 0 if count == 1 else random.choice([0, 1])
                    target_sug = suggestions.nth(choice_idx)
                    sug_text = target_sug.inner_text().strip().replace("\n", " ") if target_sug.count() > 0 else ""
                    logger.info(f"[Mode 2: Autocomplete] Đã chọn gợi ý Bing #{choice_idx + 1}: '{sug_text[:40]}'...")

                    box = target_sug.bounding_box()
                    if box and not is_mobile:
                        tx = box["x"] + box["width"] * random.uniform(0.2, 0.8)
                        ty = box["y"] + box["height"] * random.uniform(0.3, 0.7)
                        human_bezier_move(page, end_x=tx, end_y=ty)
                        page.wait_for_timeout(random.randint(80, 180))
                        page.mouse.click(tx, ty)
                    elif is_mobile and box:
                        tx = box["x"] + box["width"] * random.uniform(0.2, 0.8)
                        ty = box["y"] + box["height"] * random.uniform(0.3, 0.7)
                        try:
                            page.touchscreen.tap(tx, ty)
                        except Exception:
                            target_sug.click(timeout=2000)
                    else:
                        target_sug.click(timeout=2000)

                    save_searched_keyword(sug_text or keyword)
                    autocomplete_clicked = True
            except Exception as e:
                logger.debug(f"[Mode 2: Autocomplete] Lỗi chọn gợi ý: {e}")

            if not autocomplete_clicked:
                logger.debug("[Mode 2: Autocomplete] Gợi ý không hiện, gõ nốt phần từ khóa còn lại...")
                for ch in keyword[cutoff:]:
                    delay_sec = random.uniform(0.08, 0.22)
                    page.keyboard.type(ch, delay=int(delay_sec * 1000))
                    if ch == " ":
                        page.wait_for_timeout(random.randint(120, 250))
                page.wait_for_timeout(int(random.uniform(0.4, 0.8) * 1000))
                page.keyboard.press("Enter")
                save_searched_keyword(keyword)

        # ===== CHẾ ĐỘ 3: Gõ tự nhiên toàn bộ từ khóa (40% cơ hội) =====
        else:
            logger.info("[Organic Search] Chế độ 3: Natural Full Typing (40% tỷ lệ)...")
            # 5% xác suất gõ nhầm 1 phím rồi bấm Backspace sửa lại
            has_typo = (random.random() < 0.05 and len(keyword) > 4)
            typo_idx = random.randint(2, len(keyword) - 2) if has_typo else -1

            for idx, ch in enumerate(keyword):
                if idx == typo_idx and ch.isalpha():
                    typo_char = random.choice("abcdefghijklmnopqrstuvwxyz")
                    page.keyboard.type(typo_char, delay=int(random.uniform(0.08, 0.22) * 1000))
                    page.wait_for_timeout(random.randint(150, 350))
                    page.keyboard.press("Backspace")
                    page.wait_for_timeout(random.randint(100, 250))

                delay_sec = random.uniform(0.08, 0.22)
                page.keyboard.type(ch, delay=int(delay_sec * 1000))
                if ch == " ":
                    page.wait_for_timeout(random.randint(120, 250))

            # Nghỉ 0.4 - 0.8s rồi nhấn Enter
            page.wait_for_timeout(int(random.uniform(0.4, 0.8) * 1000))
            page.keyboard.press("Enter")
            save_searched_keyword(keyword)

        # Chờ trang kết quả tìm kiếm phản hồi
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(random.randint(1500, 2500))

        # Tương tác tự nhiên sau tìm kiếm (cuộn 2-3 nhịp, headline hover, Stealth CTR 55%, cuộn lên lại)
        _perform_post_search_dwell_and_ctr(page, is_mobile=is_mobile)

        return True

    except Exception as e:
        logger.error(f"Lỗi khi thực hiện tìm kiếm tự nhiên cho '{keyword}': {e}")
        return False


def execute_single_search(page: Page, query: str, device_type: str = "desktop") -> bool:
    """
    Thực hiện một lượt tìm kiếm tự nhiên trên Bing bằng cơ chế gõ phím giống người thật
    và luân phiên 3 chế độ tìm kiếm hữu cơ.
    """
    is_mobile = (device_type.lower() == "mobile")
    return human_type_and_search(page, query, is_mobile=is_mobile)




