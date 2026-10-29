#!/usr/bin/env python3
"""
Script GUI để người dùng đăng nhập tài khoản Microsoft Rewards lần đầu.
Tạo Persistent Context cho Desktop (Edge) và Mobile (iPhone/Pixel Emulation).
Chạy một lần duy nhất, sau đó session được lưu trong profiles/desktop và profiles/mobile.
"""

import sys
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

# Use PYTHONUTF8=1 environment variable instead of manual wrapping
# This avoids issues with stream wrapping in subprocesses


# Thư mục gốc project
ROOT_DIR = Path(__file__).parent.resolve()
PROFILES_DIR = ROOT_DIR / "profiles"
DESKTOP_PROFILE = PROFILES_DIR / "desktop"
MOBILE_PROFILE = PROFILES_DIR / "mobile"

# URL trang rewards
REWARDS_URL = "https://rewards.bing.com"

# Danh sách device preset cho mobile (ưu tiên hệ sinh thái Android để khớp 100% nhân Chromium Blink)
MOBILE_DEVICES = {
    "1": ("Pixel 7 (Android - Khuyên dùng)", "Pixel 7"),
    "2": ("Galaxy S24 (Android)", "Galaxy S24"),
    "3": ("iPhone 14 Pro", "iPhone 14 Pro"),
    "4": ("iPhone 15 Pro", "iPhone 15 Pro"),
}


def apply_stealth_scripts(context, is_mobile: bool = False):
    """Tiêm script xóa dấu vết tự động hóa của Playwright/CDP và đồng bộ User-Agent Client Hints."""
    platform_val = "Linux armv8l" if is_mobile else "Win32"
    ua_platform = "Android" if is_mobile else "Windows"
    is_mob_js = "true" if is_mobile else "false"
    model_val = "Pixel 7" if is_mobile else ""
    platform_version = "14.0.0" if is_mobile else "15.0.0"
    arch_val = "arm" if is_mobile else "x86"

    stealth_js = f"""
    // 1. Xóa cờ webdriver
    Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});

    // 2. Đồng bộ platform chuẩn với thiết bị
    Object.defineProperty(navigator, 'platform', {{ get: () => '{platform_val}' }});

    // 3. Giả lập languages tự nhiên
    Object.defineProperty(navigator, 'languages', {{ get: () => ['vi-VN', 'vi', 'en-US', 'en'] }});

    // 4. Đảm bảo kích thước outerWidth/outerHeight hợp lệ khi chạy ngầm
    if (window.outerWidth === 0 || window.outerHeight === 0) {{
        Object.defineProperty(window, 'outerWidth', {{ get: () => window.innerWidth }});
        Object.defineProperty(window, 'outerHeight', {{ get: () => window.innerHeight + 85 }});
    }}

    // 5. Đồng bộ User-Agent Client Hints (navigator.userAgentData)
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


def print_banner():
    """In banner chương trình."""
    print("=" * 60)
    print("  Microsoft Rewards - Setup Login (Persistent Context)")
    print("=" * 60)


def print_menu():
    """In menu lựa chọn."""
    print("Chọn chế độ đăng nhập:")
    print("  1. Desktop - Microsoft Edge (profiles/desktop)")
    print("  2. Mobile Emulation - iPhone/Pixel (profiles/mobile)")
    print("  3. Cả hai (Desktop + Mobile)")
    print("  4. Hoàn tất đăng nhập & Tiếp tục cài đặt (Khuyên dùng sau khi xong)")
    print("  0. Thoát")
    print("=" * 60)
    print()


def get_mobile_device_choice() -> str:
    """Lấy lựa chọn device mobile từ người dùng."""
    print("\nChọn thiết bị mobile để giả lập:")
    for key, (name, _) in MOBILE_DEVICES.items():
        print(f"  {key}. {name}")
    print("  0. Quay lại")
    
    while True:
        choice = input("\nNhập lựa chọn (0-4): ").strip()
        if choice in MOBILE_DEVICES or choice == "0":
            return choice
        print("❌ Lựa chọn không hợp lệ!")


def launch_desktop_login() -> bool:
    """
    Khởi chạy Desktop Edge persistent context để đăng nhập.
    
    Returns:
        True nếu thành công, False nếu lỗi
    """
    print("\n🖥️  Khởi chạy Microsoft Edge (Desktop)...")
    print(f"   Profile directory: {DESKTOP_PROFILE}")
    print(f"   URL: {REWARDS_URL}")
    print("\n📋 HƯỚNG DẪN:")
    print("   1. Trình duyệt sẽ mở ra trang rewards.bing.com")
    print("   2. Đăng nhập tài khoản Microsoft của bạn")
    print("   3. Quan trọng: Tick chọn 'Keep me signed in' / 'Giữ đăng nhập'")
    print("   4. Sau khi đăng nhập xong, ĐÓNG TRÌNH DUYỆT BẰNG CÁCH NHẤN NÚT X (góc trên cùng)")
    print("   5. Script sẽ tự động lưu session và thoát")
    print()
    input("   Nhấn Enter để tiếp tục...")
    
    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(DESKTOP_PROFILE),
                channel="msedge",
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized",
                ],
                no_viewport=True,  # Sử dụng kích thước cửa sổ thật
                locale="vi-VN",
                timezone_id="Asia/Ho_Chi_Minh",
            )
            apply_stealth_scripts(context, is_mobile=True)
            apply_stealth_scripts(context, is_mobile=False)
            
            page = context.new_page()
            page.goto(REWARDS_URL, wait_until="domcontentloaded")
            
            # Giữ context mở cho đến khi người dùng đóng trình duyệt
            print("\n⏳ Đang chờ bạn đăng nhập và đóng trình duyệt...")
            print("   (Script sẽ tự động tiếp tục khi bạn đóng cửa sổ Edge)")
            
            # Chờ page/context bị đóng
            context.on("close", lambda: None)
            page.wait_for_event("close", timeout=0)  # Chờ vô hạn
            
            context.close()
            
        print("\n✅ Đã lưu session Desktop thành công!")
        print(f"   Dữ liệu lưu tại: {DESKTOP_PROFILE}")
        return True
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Đã hủy bởi người dùng.")
        return False
    except Exception as e:
        print(f"\n❌ Lỗi khi chạy Desktop: {e}")
        return False


def launch_mobile_login(device_name: str, device_preset: str) -> bool:
    """
    Khởi chạy Mobile Emulation persistent context để đăng nhập.
    
    Args:
        device_name: Tên hiển thị (ví dụ: "iPhone 14 Pro")
        device_preset: Tên preset Playwright (ví dụ: "iPhone 14 Pro")
        
    Returns:
        True nếu thành công, False nếu lỗi
    """
    print(f"\n📱 Khởi chạy Mobile Emulation ({device_name})...")
    print(f"   Profile directory: {MOBILE_PROFILE}")
    print(f"   Device preset: {device_preset}")
    print(f"   URL: {REWARDS_URL}")
    print("\n📋 HƯỚNG DẪN:")
    print("   1. Trình duyệt sẽ mở ra trang rewards.bing.com ở chế độ mobile")
    print("   2. Đăng nhập tài khoản Microsoft của bạn")
    print("   3. Quan trọng: Tick chọn 'Keep me signed in' / 'Giữ đăng nhập'")
    print("   4. Sau khi đăng nhập xong, ĐÓNG TRÌNH DUYỆT BẰNG CÁCH NHẤN NÚT X")
    print("   5. Script sẽ tự động lưu session và thoát")
    print()
    input("   Nhấn Enter để tiếp tục...")
    
    try:
        with sync_playwright() as p:
            # Lấy device preset và loại bỏ key 'default_browser_type' không được chấp nhận
            device = dict(p.devices[device_preset])
            device.pop('default_browser_type', None)
            
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(MOBILE_PROFILE),
                channel="msedge",
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                ],
                **device,  # Áp dụng viewport, user-agent, is_mobile, has_touch, etc.
                locale="vi-VN",
                timezone_id="Asia/Ho_Chi_Minh",
            )
            apply_stealth_scripts(context, is_mobile=True)
            
            page = context.new_page()
            page.goto(REWARDS_URL, wait_until="domcontentloaded")
            
            print(f"\n⏳ Đang chờ bạn đăng nhập trên {device_name} và đóng trình duyệt...")
            print("   (Script sẽ tự động tiếp tục khi bạn đóng cửa sổ Edge)")
            
            page.wait_for_event("close", timeout=0)
            
            context.close()
            
        print(f"\n✅ Đã lưu session Mobile ({device_name}) thành công!")
        print(f"   Dữ liệu lưu tại: {MOBILE_PROFILE}")
        return True
        
    except KeyError:
        print(f"\n❌ Device preset '{device_preset}' không tồn tại trong Playwright.")
        return False
    except KeyboardInterrupt:
        print("\n\n⚠️  Đã hủy bởi người dùng.")
        return False
    except Exception as e:
        print(f"\n❌ Lỗi khi chạy Mobile: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Setup login for MS Rewards Bot - Persistent Context",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ví dụ sử dụng:
  python setup_login.py           # Menu tương tác
  python setup_login.py --desktop # Chỉ chạy Desktop
  python setup_login.py --mobile  # Chỉ chạy Mobile (Pixel 7 Android mặc định)
  python setup_login.py --both    # Chạy cả hai
        """
    )
    parser.add_argument("--desktop", action="store_true", help="Chạy đăng nhập Desktop")
    parser.add_argument("--mobile", action="store_true", help="Chạy đăng nhập Mobile")
    parser.add_argument("--both", action="store_true", help="Chạy cả Desktop và Mobile")
    parser.add_argument("--device", type=str, default="Pixel 7", 
                        help="Device preset cho mobile (mặc định: Pixel 7)")
    
    args = parser.parse_args()
    
    # Nếu có tham số dòng lệnh, chạy trực tiếp
    if args.desktop or args.mobile or args.both:
        if args.desktop or args.both:
            launch_desktop_login()
        if args.mobile or args.both:
            device_preset = args.device if args.device in [d[1] for d in MOBILE_DEVICES.values()] else "Pixel 7"
            device_display = next((d[0] for d in MOBILE_DEVICES.values() if d[1] == device_preset), device_preset)
            launch_mobile_login(device_display, device_preset)
        return
    
    # Menu tương tác
    while True:
        print_banner()
        print_menu()
        
        choice = input("Nhập lựa chọn (0-4): ").strip()
        
        if choice == "4":
            print("\n✅ Hoàn tất đăng nhập! Tiếp tục các bước cài đặt tiếp theo...")
            sys.exit(0)
        elif choice == "0":
            print("\n👋 Thoát cài đặt đăng nhập. Tiếp tục các bước cài đặt...")
            sys.exit(0)
        elif choice == "1":
            launch_desktop_login()
            input("\nNhấn Enter để quay lại menu...")
        elif choice == "2":
            device_choice = get_mobile_device_choice()
            if device_choice != "0":
                device_display, device_preset = MOBILE_DEVICES[device_choice]
                launch_mobile_login(device_display, device_preset)
                input("\nNhấn Enter để quay lại menu...")
        elif choice == "3":
            print("\n🔄 Chạy cả Desktop và Mobile...")
            launch_desktop_login()
            device_display, device_preset = MOBILE_DEVICES["1"]  # Mặc định Pixel 7 (Android)
            launch_mobile_login(device_display, device_preset)
            print("\n-> Đã lưu xong cả Desktop & Mobile. Hãy nhập số 4 để tiếp tục bước cài đặt tiếp theo.")
            input("\nNhấn Enter để quay lại menu...")
        else:
            print("❌ Lựa chọn không hợp lệ!")
            input("Nhấn Enter để tiếp tục...")


if __name__ == "__main__":
    main()