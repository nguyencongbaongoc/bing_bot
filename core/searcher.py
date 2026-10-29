"""
Search module handling natural human-like typing and search interactions on Bing.
Nâng cấp ngụy trang chống phát hiện:
- Di chuột theo đường cong Bézier bậc 2 tự nhiên kèm vi rung (micro-jitter) & Click lệch tâm.
- Tương tác thông minh với gợi ý tìm kiếm của Bing (Autosuggest Click ~25%).
- Cuộn trang tự nhiên hỗ trợ cả chuột Desktop và cảm ứng vuốt màn hình (Touch swipe) trên Mobile.
- Tỷ lệ Click kết quả tự nhiên (Organic CTR ~30%) tránh bẫy 0% Click-Through Rate của Bing.
"""
import math
import random
import time
from playwright.sync_api import Page
from core.logger import logger

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
    Mô phỏng người dùng click vào một kết quả tìm kiếm tự nhiên (Organic CTR ~30%).
    Đọc trang 3-5 giây, cuộn nhẹ rồi quay lại (go_back) hoặc đóng tab phụ.
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

        logger.info(f"[Stealth CTR] Click tự nhiên vào kết quả tìm kiếm #{idx + 1}...")

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

        if new_page:
            # Trường hợp mở ra tab mới
            try:
                new_page.wait_for_load_state("domcontentloaded", timeout=6000)
            except Exception:
                pass
            dwell = random.uniform(3.0, 5.0)
            logger.debug(f"[Stealth CTR] Dừng đọc tab mới trong {dwell:.1f}s...")
            time.sleep(dwell)
            try:
                new_page.close()
            except Exception:
                pass
        else:
            # Trường hợp mở ngay trong tab hiện tại
            dwell = random.uniform(3.0, 5.0)
            logger.debug(f"[Stealth CTR] Dừng đọc trang đích trong {dwell:.1f}s...")
            scroll_count = random.randint(1, 2)
            for _ in range(scroll_count):
                time.sleep(dwell / (scroll_count + 1))
                smooth_scroll(page, random.randint(150, 350), is_mobile=is_mobile)
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


def human_type_and_search(page: Page, keyword: str, is_mobile: bool = False) -> bool:
    """
    Thực hiện tìm kiếm trên Bing bằng cách click vào thanh tìm kiếm (di chuột Bézier + lệch tâm),
    gõ từng ký tự như người thật, hỗ trợ click gợi ý Bing Autosuggest (~25%),
    cuộn đọc kết quả và tạo Organic CTR (~30%).
    """
    search_selectors = [
        "#sb_form_q",
        'textarea[name="q"]',
        'input[name="q"]',
        'input[type="search"]',
    ]

    try:
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

        # Nếu trên trang hiện tại không thấy thanh tìm kiếm, quay về trang chủ bing.com
        if search_box is None:
            page.goto("https://www.bing.com", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(1500)
            for sel in search_selectors:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    search_box = loc
                    break

        if search_box is None:
            logger.error(f"Không tìm thấy ô tìm kiếm trên trang cho từ khóa '{keyword}'")
            return False

        # 3. Di chuột tự nhiên theo đường cong Bézier và click lệch tâm vào ô tìm kiếm
        try:
            search_box.scroll_into_view_if_needed(timeout=3000)
            box = search_box.bounding_box()
            if box:
                # Tọa độ ngẫu nhiên trong vùng an toàn của ô tìm kiếm (20%-80% width, 25%-75% height)
                target_x = box["x"] + box["width"] * random.uniform(0.2, 0.8)
                target_y = box["y"] + box["height"] * random.uniform(0.25, 0.75)
                if is_mobile:
                    # Trên thiết bị cảm ứng: chạm trực tiếp tại tọa độ ngẫu nhiên
                    try:
                        page.touchscreen.tap(target_x, target_y)
                    except Exception:
                        page.mouse.click(target_x, target_y)
                else:
                    # Trên Desktop: di chuyển chuột theo đường cong Bézier bậc 2 có gia tốc & vi rung
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

        # Xóa nội dung cũ bằng tổ hợp phím Ctrl+A -> Backspace (kèm fill("") dự phòng)
        page.keyboard.press("Control+A")
        page.wait_for_timeout(random.randint(100, 250))
        page.keyboard.press("Backspace")
        page.wait_for_timeout(random.randint(150, 300))
        try:
            search_box.fill("")
        except Exception:
            pass

        # 4. Gõ từng ký tự với độ trễ biến thiên giống người gõ phím & Tương tác Bing Autosuggest
        logger.debug(f"Đang gõ tự nhiên từ khóa: '{keyword}'")

        use_autosuggest = len(keyword) >= 6 and random.random() < 0.25
        cutoff = max(3, int(len(keyword) * random.uniform(0.60, 0.75))) if use_autosuggest else -1
        autosuggest_clicked = False

        for idx, ch in enumerate(keyword):
            if use_autosuggest and idx == cutoff:
                # Tạm dừng nhịp 0.6s - 1.1s quan sát bảng gợi ý bung ra
                pause_ms = random.randint(600, 1100)
                page.wait_for_timeout(pause_ms)

                try:
                    suggestions = page.locator("#sa_ul li.sa_sg, #sa_ul li, .sa_drw li, [role='listbox'] [role='option']")
                    count = suggestions.count()
                    if count > 0 and suggestions.first.is_visible(timeout=800):
                        choice_idx = random.randint(0, min(2, count - 1))
                        target_sug = suggestions.nth(choice_idx)
                        sug_text = target_sug.inner_text().strip().replace("\n", " ") if target_sug.count() > 0 else ""
                        logger.info(f"[Stealth] Đã chọn gợi ý tìm kiếm tự động (Autosuggest): '{sug_text[:40]}' (Bỏ qua gõ nốt & Enter)")

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

                        autosuggest_clicked = True
                        break
                except Exception as e:
                    logger.debug(f"[Stealth Autosuggest] Gợi ý không khả dụng, tiếp tục gõ bình thường: {e}")

            delay_ms = random.randint(55, 145)
            page.keyboard.type(ch, delay=delay_ms)
            # Nghỉ nhịp ngắn khi gõ xong một từ (dấu cách)
            if ch == " ":
                page.wait_for_timeout(random.randint(120, 280))

        # 5. Nếu không click autosuggest, chờ 0.5 - 1.1s sau khi gõ xong rồi nhấn Enter
        if not autosuggest_clicked:
            page.wait_for_timeout(random.randint(500, 1100))
            page.keyboard.press("Enter")

        # Chờ trang kết quả tìm kiếm phản hồi
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(random.randint(1500, 2500))

        # 6. Cuộn đọc trang kết quả tìm kiếm tự nhiên (2 - 4 nhịp)
        scroll_times = random.randint(2, 4)
        for _ in range(scroll_times):
            scroll_px = random.randint(250, 550)
            smooth_scroll(page, scroll_px, is_mobile=is_mobile)
            page.wait_for_timeout(random.randint(900, 1600))

        # Headline Hovering: Di chuột nhẹ (human_bezier_move) lướt qua 1 tiêu đề bài viết trong #b_results h2
        if not is_mobile:
            try:
                headlines = page.locator("#b_results h2 a, #b_results .b_algo h2 a, #b_results h2")
                h_count = headlines.count()
                if h_count > 0:
                    h_idx = random.randint(0, min(3, h_count - 1))
                    h_target = headlines.nth(h_idx)
                    if h_target.is_visible():
                        h_box = h_target.bounding_box()
                        if h_box:
                            hx = h_box["x"] + h_box["width"] * random.uniform(0.2, 0.7)
                            hy = h_box["y"] + h_box["height"] * random.uniform(0.3, 0.7)
                            logger.info(f"[Headline Hovering] Di chuột nhẹ lướt qua tiêu đề #{h_idx + 1}...")
                            human_bezier_move(page, end_x=hx, end_y=hy)
                            page.wait_for_timeout(random.randint(400, 900))
            except Exception as e:
                logger.debug(f"[Headline Hovering] Lỗi khi hover tiêu đề: {e}")

        # 7. Mô phỏng Click vào kết quả tìm kiếm (Organic Click-Through ~30%)
        if random.random() < 0.30:
            simulate_organic_click(page, is_mobile=is_mobile)

        # 8. Cuộn nhẹ ngược lên đầu trang để chuẩn bị thấy lại thanh tìm kiếm cho câu sau
        smooth_scroll(page, -random.randint(300, 600), is_mobile=is_mobile)
        page.wait_for_timeout(random.randint(800, 1500))

        return True

    except Exception as e:
        logger.error(f"Lỗi khi gõ tìm kiếm '{keyword}': {e}")
        return False


def perform_related_cluster_search(page: Page, is_mobile: bool = False) -> str:
    """
    Cơ chế Cluster Search (~30% xác suất):
    Kiểm tra khu vực "Tìm kiếm liên quan" trên trang kết quả Bing:
    (#b_results .b_rs a, .b_rich .b_vList a, [class*="relatedSearches"] a)
    Click trực tiếp vào một từ khóa gợi ý liên quan để tạo chuỗi tìm kiếm nối tiếp có ngữ cảnh tự nhiên.
    Trả về từ khóa liên quan đã click (hoặc None nếu không tìm thấy/lỗi).
    """
    try:
        related_selectors = [
            '#b_results .b_rs a',
            '.b_rich .b_vList a',
            '[class*="relatedSearches"] a',
            '.b_rs .b_rsv3 a',
            '.b_ans a[href*="search?q="]',
            '#b_results .b_ans a[href*="q="]',
        ]

        found_links = []
        for sel in related_selectors:
            links = page.locator(sel)
            cnt = links.count()
            for i in range(cnt):
                link = links.nth(i)
                try:
                    if link.is_visible(timeout=500):
                        txt = link.inner_text().strip()
                        if txt and len(txt) > 2 and "\n" not in txt:
                            found_links.append((link, txt))
                except Exception:
                    continue
            if found_links:
                break

        if not found_links:
            logger.debug("[Cluster Search] Không tìm thấy liên kết 'Tìm kiếm liên quan' trên trang hiện tại.")
            return None

        # Chọn ngẫu nhiên 1 trong các liên kết tìm kiếm liên quan
        target_link, related_keyword = random.choice(found_links[:5])
        logger.info(f"[Cluster Search] Phát hiện cụm chủ đề! Click từ khóa liên quan: '{related_keyword}'...")

        try:
            target_link.scroll_into_view_if_needed(timeout=2000)
            page.wait_for_timeout(random.randint(300, 700))
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

        # Cuộn đọc kết quả tự nhiên
        scroll_times = random.randint(1, 3)
        for _ in range(scroll_times):
            smooth_scroll(page, random.randint(200, 450), is_mobile=is_mobile)
            page.wait_for_timeout(random.randint(800, 1400))

        # Headline Hovering trên trang kết quả cụm
        if not is_mobile:
            try:
                h_links = page.locator("#b_results h2 a, #b_results h2")
                if h_links.count() > 0:
                    h_target = h_links.nth(0)
                    if h_target.is_visible():
                        h_box = h_target.bounding_box()
                        if h_box:
                            human_bezier_move(page, end_x=h_box["x"] + 50, end_y=h_box["y"] + 10)
                            page.wait_for_timeout(random.randint(400, 800))
            except Exception:
                pass

        # Cuộn nhẹ lên lại
        smooth_scroll(page, -random.randint(250, 500), is_mobile=is_mobile)
        page.wait_for_timeout(random.randint(600, 1200))

        return related_keyword

    except Exception as e:
        logger.debug(f"[Cluster Search] Lỗi khi thực hiện tìm kiếm cụm: {e}")
        return None



