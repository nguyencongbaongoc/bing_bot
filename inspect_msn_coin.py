#!/usr/bin/env python3
"""
inspect_msn_coin.py
Script chẩn đoán DOM của widget đồng xu Rewards trên bài báo MSN (Mobile).
"""

import sys
import time
import random
from pathlib import Path
from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT_DIR = Path(__file__).resolve().parent
MOBILE_PROFILE = ROOT_DIR / "profiles" / "mobile"

def run_inspection():
    print("=" * 60)
    print("   CHẨN ĐOÁN WIDGET ĐỒNG XU MSN REWARDS (MOBILE)")
    print("=" * 60)

    headless = "--debug" not in sys.argv

    with sync_playwright() as p:
        mobile_device = dict(p.devices["iPhone 14 Pro"])
        mobile_device.pop("default_browser_type", None)

        print(f"[*] Khởi tạo mobile context (headless={headless})...")
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(MOBILE_PROFILE),
            channel="msedge",
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            **mobile_device,
            locale="vi-VN",
            timezone_id="Asia/Ho_Chi_Minh",
        )

        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(25000)

        # 1. Mở trang MSN News để lấy link bài báo nội bộ có /ar-
        print("[*] Tải danh mục tin tức MSN...")
        page.goto("https://www.msn.com/en-us/news", wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(3500)

        # Cuộn nhẹ để nạp các bài báo trong Shadow DOM
        for _ in range(2):
            page.mouse.wheel(0, 700)
            page.wait_for_timeout(800)

        article_links = page.evaluate("""() => {
            function getLinksDeep(root = document) {
                let res = [];
                const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, null, false);
                let n = walker.nextNode();
                while (n) {
                    if (n.tagName === 'A' && n.href) res.push(n.href);
                    if (n.shadowRoot) res = res.concat(getLinksDeep(n.shadowRoot));
                    n = walker.nextNode();
                }
                return res;
            }
            const raw = getLinksDeep(document);
            const valid = new Set();
            for (const h of raw) {
                try {
                    const u = new URL(h);
                    if (u.hostname.endsWith('msn.com') && u.pathname.includes('/ar-')) {
                        const clean = u.origin + u.pathname;
                        if (!clean.includes('macrumors') && !clean.includes('news24') && !clean.includes('cnbc')) {
                            valid.add(clean);
                        }
                    }
                } catch (e) {}
            }
            return Array.from(valid);
        }""")

        if not article_links:
            print("[!] Không tìm thấy bài qua trang chủ, dùng bài mặc định...")
            article_links = ["https://www.msn.com/en-us/sports/soccer/kane-wins-2026-ballon-d-or-result-leaked-as-rankings-announced/ar-AA2g7rsk"]

        target_url = article_links[0]
        print(f"[*] Đã chọn bài báo mục tiêu: {target_url}")

        # 2. Điều hướng tới bài báo
        page.goto(target_url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(3000)

        # Đóng popup / banner nếu có
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass

        # Bấm mở rộng "Continue reading" nếu có nút che bài
        try:
            cr_btn = page.locator('fluent-button:has-text("Continue reading"), button:has-text("Continue reading"), .article-cont-read-button').first
            if cr_btn.count() > 0 and cr_btn.is_visible(timeout=1000):
                print("[*] Đã phát hiện nút 'Continue reading', tiến hành click mở rộng...")
                cr_btn.click(force=True)
                page.wait_for_timeout(1000)
        except Exception:
            pass

        # 3. Cuộn từng nhịp nhẹ xuống trong 6-7 giây (mỗi giây cuộn 150-250px)
        print("[*] Bắt đầu cuộn đọc duy trì (Sustained Reading Loop >= 7s)...")
        start_time = time.time()
        step = 1
        while time.time() - start_time < 7.5:
            scroll_px = random.randint(180, 260)
            page.mouse.wheel(0, scroll_px)
            elapsed = time.time() - start_time
            print(f"   [Nhịp {step:02d}] Cuộn {scroll_px}px (trôi qua {elapsed:.1f}s)")
            page.wait_for_timeout(1000)
            step += 1

        # 4. Đợi thêm 2 giây để client telemetry kích hoạt đồng xu
        print("[*] Đợi thêm 2.5s để telemetry kích hoạt widget đồng xu...")
        page.wait_for_timeout(2500)

        # Lưu ảnh chụp màn hình xác nhận vị trí
        debug_png = ROOT_DIR / "msn_coin_debug.png"
        page.screenshot(path=str(debug_png))
        print(f"[✓] Đã lưu ảnh chụp màn hình: {debug_png}")

        # 5. Quét DOM tìm các phần tử mới xuất hiện ở góc màn hình
        print("\n[*] Quét tìm phần tử đồng xu Rewards...")
        detection = page.evaluate("""() => {
            function queryAllDeep(root = document) {
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

            const winH = window.innerHeight;
            const winW = window.innerWidth;
            const all = queryAllDeep(document);
            const found = [];

            for (const el of all) {
                const text = (el.innerText || el.textContent || '').trim();
                const aria = (el.getAttribute('aria-label') || '').trim();
                const id = el.id || '';
                const cls = (typeof el.className === 'string') ? el.className : '';
                const tag = el.tagName.toLowerCase();
                const combined = `${id} ${cls} ${aria} ${text}`.toLowerCase();

                // 1. Selector đặc thù chứa từ khóa coin / rewards / +3
                const hasCoinKw = combined.includes('+3') ||
                                  combined.includes('3 point') ||
                                  combined.includes('reward') ||
                                  combined.includes('coin') ||
                                  combined.includes('badge') ||
                                  id === 'id_rh' || id === 'rewid-f' || cls.includes('m_rw_c');

                // 2. Thẻ nổi fixed / sticky ở nửa dưới hoặc các góc màn hình
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                const isFixed = style.position === 'fixed' || style.position === 'sticky';
                const isCorner = isFixed && rect.width >= 15 && rect.width <= 250 && rect.height >= 15 && rect.height <= 250 &&
                                 (rect.bottom >= winH * 0.5 || rect.top <= 100);

                if (hasCoinKw || isCorner) {
                    // Kiểm tra phần tử thực sự hiển thị
                    if (rect.width > 5 && rect.height > 5 && rect.top < winH + 50 && rect.left < winW + 50) {
                        found.push({
                            tag: tag,
                            id: id,
                            cls: cls.slice(0, 50),
                            text: text.slice(0, 40),
                            aria: aria.slice(0, 40),
                            pos: style.position,
                            rect: {
                                x: Math.round(rect.x),
                                y: Math.round(rect.y),
                                w: Math.round(rect.width),
                                h: Math.round(rect.height),
                                cx: Math.round(rect.x + rect.width / 2),
                                cy: Math.round(rect.y + rect.height / 2)
                            },
                            hasCoinKw: hasCoinKw
                        });
                    }
                }
            }
            return found;
        }""")

        print(f"[*] Quét hoàn tất: Tìm thấy {len(detection)} phần tử:")
        for idx, item in enumerate(detection, 1):
            flag = "[COIN-KW]" if item['hasCoinKw'] else "[FLOATING]"
            print(f"  {idx:2d}. {flag} <{item['tag']}> id='{item['id']}' cls='{item['cls']}' text='{item['text']}' aria='{item['aria']}' rect={item['rect']} pos={item['pos']}")

        context.close()
        print("\n[✓] Script inspect_msn_coin.py đã hoàn thành thành công.")

if __name__ == "__main__":
    run_inspection()

