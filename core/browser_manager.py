"""
Browser Context Management, Stealth Scripts, Route Blocking, Self-Healing Recovery, and SSD Guard.
Phase 8.5 - MS Rewards Bot.
"""

from __future__ import annotations

import importlib
import random
import shutil
import time
import urllib.parse
from pathlib import Path
from typing import Callable, Optional

try:
    import psutil
except ImportError:
    psutil = None

from core.fingerprint import get_deterministic_hardware_profile, inject_stealth_fingerprint
from core.state_store import EDGE_BROWSING_CAP_SECONDS, StateStore
from core.tracker import _log_debug, _log_error, _log_info, _log_warning, logger

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_TIMEOUT = 8000

BROWSER_ARGS = [
    "--disable-gpu-vsync",
    "--frame-rate-limit=15",
    "--blink-settings=imagesEnabled=true",
]

DUPLICATE_PATCHRIGHT_FLAGS = {
    "--disable-blink-features=AutomationControlled",
    "--disable-search-engine-choice-screen",
    "--disable-background-timer-throttling",
}

SYNTHETIC_FLAGS = {
    "--no-sandbox",
    "--use-mock-keychain",
    "--password-store=basic",
    "--disable-application-cache",
}

SYNTHETIC_FLAG_PREFIXES = (
    "--disk-cache-size",
    "--media-cache-size",
)

FIRST_PARTY_ALLOWLIST_DOMAINS = (
    "bing.com",
    "microsoft.com",
    "live.com",
    "msn.com",
    "bing.net",
    "msecnd.net",
    "microsoftonline.com",
    "azureedge.net",
    "aspnetcdn.com",
    "sfx.ms",
    "office.com",
    "office.net",
    "sharepoint.com",
)

BLOCKED_AD_TRACKER_DOMAINS = (
    "doubleclick.net",
    "googlesyndication.com",
    "taboola.com",
    "outbrain.com",
)


def build_launch_args(headless: bool, is_mobile: bool = False) -> list[str]:
    """Xây dựng danh sách launch args đã loại bỏ trùng lặp, cờ synthetic và xung đột cờ headless."""
    raw_args = list(BROWSER_ARGS)
    if headless:
        raw_args.extend([
            "--headless=new",
            "--disable-gpu",
            "--disable-software-rasterizer",
        ])
    elif not is_mobile:
        raw_args.append("--start-maximized")

    deduped: list[str] = []
    seen: set[str] = set()
    for flag in raw_args:
        if flag in DUPLICATE_PATCHRIGHT_FLAGS or flag in SYNTHETIC_FLAGS:
            continue
        if any(flag.startswith(prefix) for prefix in SYNTHETIC_FLAG_PREFIXES):
            continue
        if headless and flag == "--headless":
            continue
        if flag not in seen:
            seen.add(flag)
            deduped.append(flag)
    return deduped


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


def _host_matches_domain(hostname: str, domains: tuple) -> bool:
    if not hostname:
        return False
    host = hostname.lower().strip(".")
    return any(host == d or host.endswith(f".{d}") for d in domains)


def should_abort_route(url: str, resource_type: str) -> bool:
    """
    Chặn tài nguyên media (video/audio) và tên miền quảng cáo/tracker bên thứ 3,
    nhưng luôn giữ nguyên 100% request tới bing.com, microsoft.com, live.com, msn.com và CDN của Microsoft.
    """
    try:
        hostname = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        hostname = ""

    if _host_matches_domain(hostname, FIRST_PARTY_ALLOWLIST_DOMAINS):
        return False

    if (resource_type or "").lower() == "media":
        return True

    if _host_matches_domain(hostname, BLOCKED_AD_TRACKER_DOMAINS):
        return True

    return False


def attach_resource_blocker(context) -> None:
    """Gắn route interceptor nhẹ lên context để tiết kiệm băng thông/RAM mà không ảnh hưởng telemetry Microsoft."""
    def _route_handler(route):
        try:
            req = route.request
            if should_abort_route(req.url, req.resource_type):
                route.abort()
            else:
                route.continue_()
        except Exception:
            try:
                route.continue_()
            except Exception:
                pass

    context.route("**/*", _route_handler)


def apply_stealth_scripts(
    context,
    is_mobile: bool = False,
    cpu_threads: Optional[int] = None,
    account_id: str = "default",
) -> None:
    """Tiêm script xóa dấu vết tự động hóa của Patchright/CDP và đồng bộ User-Agent Client Hints."""
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
    Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});
    Object.defineProperty(navigator, 'platform', {{ get: () => '{platform_val}' }});
    Object.defineProperty(navigator, 'languages', {{ get: () => ['vi-VN', 'vi', 'en-US', 'en'] }});
    Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {hw_concurrency} }});
    Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {device_memory} }});
    if (window.outerWidth === 0 || window.outerHeight === 0) {{
        Object.defineProperty(window, 'outerWidth', {{ get: () => window.innerWidth }});
        Object.defineProperty(window, 'outerHeight', {{ get: () => window.innerHeight + 85 }});
    }}
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
    inject_stealth_fingerprint(context, account_id=account_id, is_mobile=is_mobile)


def create_desktop_context(
    playwright,
    headless: bool,
    account_id: str = "default",
    profile_dir: Optional[Path] = None,
):
    """Tạo Desktop persistent context với chế độ ngụy trang (Stealth)."""
    target_dir = profile_dir or (ROOT_DIR / "profiles" / account_id / "desktop")
    target_dir.mkdir(parents=True, exist_ok=True)
    args = build_launch_args(headless=headless, is_mobile=False)

    extra_headers = {
        "Sec-CH-UA-Platform": '"Windows"',
        "Sec-CH-UA-Mobile": "?0",
    }

    launch_kwargs = {
        "user_data_dir": str(target_dir),
        "channel": "msedge",
        "headless": headless,
        "args": args,
        "no_viewport": not headless,
        "locale": "vi-VN",
        "timezone_id": "Asia/Ho_Chi_Minh",
        "extra_http_headers": extra_headers,
    }
    if headless:
        launch_kwargs["ignore_default_args"] = ["--headless"]

    context = playwright.chromium.launch_persistent_context(**launch_kwargs)
    apply_stealth_scripts(context, is_mobile=False, account_id=account_id)
    attach_resource_blocker(context)
    return context


def create_mobile_context(
    playwright,
    headless: bool,
    account_id: str = "default",
    profile_dir: Optional[Path] = None,
):
    """Tạo Mobile emulation persistent context giả lập Android (Pixel 7)."""
    target_dir = profile_dir or (ROOT_DIR / "profiles" / account_id / "mobile")
    target_dir.mkdir(parents=True, exist_ok=True)
    mobile_device = dict(playwright.devices["Pixel 7"])
    mobile_device.pop("default_browser_type", None)
    mobile_device["viewport"] = {"width": 412, "height": 915}
    mobile_device["screen"] = {"width": 412, "height": 915}
    mobile_device["device_scale_factor"] = 2.625
    mobile_device["is_mobile"] = True
    mobile_device["has_touch"] = True

    ua = mobile_device.get("user_agent", "")
    if "EdgA" not in ua:
        chrome_ver = "131.0.0.0"
        mobile_device["user_agent"] = (
            f"Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{chrome_ver} Mobile Safari/537.36 EdgA/{chrome_ver}"
        )

    args = build_launch_args(headless=headless, is_mobile=True)

    extra_headers = {
        "Sec-CH-UA-Platform": '"Android"',
        "Sec-CH-UA-Mobile": "?1",
        "Sec-CH-UA-Model": '"Pixel 7"',
    }

    launch_kwargs = {
        "user_data_dir": str(target_dir),
        "channel": "msedge",
        "headless": headless,
        "args": args,
        **mobile_device,
        "locale": "vi-VN",
        "timezone_id": "Asia/Ho_Chi_Minh",
        "extra_http_headers": extra_headers,
    }
    if headless:
        launch_kwargs["ignore_default_args"] = ["--headless"]

    context = playwright.chromium.launch_persistent_context(**launch_kwargs)
    apply_stealth_scripts(context, is_mobile=True, account_id=account_id)
    attach_resource_blocker(context)
    return context


def _is_recoverable_browser_error(err: Exception) -> bool:
    """Kiểm tra lỗi TargetClosedError, Page crashed hoặc exitCode=21 cần dọn lock profile."""
    err_name = type(err).__name__.lower()
    err_msg = str(err).lower()
    return (
        "targetclosed" in err_name
        or "targetclosederror" in err_msg
        or "page crashed" in err_msg
        or "exitcode=21" in err_msg
        or "exit code 21" in err_msg
        or "browser has been closed" in err_msg
    )


def _cleanup_profile_locks_and_processes(profile_dir: Path) -> None:
    """Xóa SingletonLock/Cookie/Socket và đóng tiến trình Edge mồ côi gắn với profile_dir."""
    resolved_profile = str(profile_dir.resolve()).lower().replace("/", "\\")
    if psutil is not None:
        try:
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    name = (proc.info.get("name") or "").lower()
                    if name == "msedge.exe":
                        cmdline = " ".join(proc.info.get("cmdline") or []).lower().replace("/", "\\")
                        if "--user-data-dir" in cmdline and resolved_profile in cmdline:
                            proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception as exc:
            _log_debug(f"[Auto-Recovery] Lỗi khi dọn tiến trình Edge cho {profile_dir.name}: {exc}")

    for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock_path = profile_dir / lock_name
        try:
            if lock_path.exists() or lock_path.is_symlink():
                lock_path.unlink(missing_ok=True)
        except Exception as exc:
            _log_debug(f"[Auto-Recovery] Không thể xóa {lock_path}: {exc}")


def ensure_context_page(
    context,
    playwright_instance,
    headless: bool,
    profile_dir: Path,
    create_fn: Callable,
    label: str,
    account_id: str = "default",
    cleanup_fn: Optional[Callable[[Path], None]] = None,
):
    """Đảm bảo lấy được page hợp lệ với cơ chế tự phục hồi 3 lần thử (backoff 2s, 5s, 10s)."""
    try:
        if context is not None:
            if context.pages:
                page = context.pages[0]
                if not page.is_closed():
                    page.evaluate("1")
                    page.set_default_timeout(DEFAULT_TIMEOUT)
                    return context, page
                try:
                    page.close()
                except Exception:
                    pass
            new_page = context.new_page()
            new_page.evaluate("1")
            new_page.set_default_timeout(DEFAULT_TIMEOUT)
            return context, new_page
    except Exception as exc:
        _log_warning(f"[Runner] {label} context/page bị crash hoặc mất kết nối ({exc}). Đang khởi tạo lại context mới...")
        try:
            if context is not None:
                for p_item in list(getattr(context, "pages", []) or []):
                    try:
                        p_item.close()
                    except Exception:
                        pass
                context.close()
        except Exception:
            pass

    backoffs = (2, 5, 10)
    last_err = None
    cleaner = cleanup_fn or _cleanup_profile_locks_and_processes
    for attempt in range(1, 4):
        try:
            try:
                new_context = create_fn(
                    playwright_instance,
                    headless,
                    account_id=account_id,
                    profile_dir=profile_dir,
                )
            except TypeError:
                new_context = create_fn(playwright_instance, headless, account_id=account_id)
            new_page = new_context.pages[0] if new_context.pages else new_context.new_page()
            new_page.set_default_timeout(DEFAULT_TIMEOUT)
            return new_context, new_page
        except Exception as err:
            last_err = err
            _log_warning(f"[Runner] Khởi tạo {label} context thất bại (lần {attempt}/3): {err}")
            if _is_recoverable_browser_error(err):
                cleaner(profile_dir)
            if attempt < 3:
                wait_s = backoffs[attempt - 1]
                _log_info(f"[Runner] Chờ {wait_s}s trước khi thử khởi tạo lại {label} context...")
                time.sleep(wait_s)

    _log_error(f"[Runner] Không thể khởi tạo lại {label} context sau 3 lần thử: {last_err}")
    raise last_err


def perform_warmup_visit(page, duration_range: tuple[float, float] = (5.0, 8.0)) -> bool:
    """Thực hiện lượt ghé thăm khởi động nhẹ (MSN / Bing News 5-8s) ở đầu phiên."""
    if page is None:
        return False
    urls = [
        "https://www.msn.com/vi-vn",
        "https://www.bing.com/news",
    ]
    target_url = random.choice(urls)
    try:
        logger.info(f"[Warmup] Ghé thăm nhẹ {target_url} làm ấm phiên duyệt web...")
        page.goto(target_url, wait_until="domcontentloaded", timeout=10000)
        total_wait = random.uniform(*duration_range)
        scroll_steps = random.randint(2, 3)
        step_sleep = total_wait / (scroll_steps + 1)
        for idx in range(scroll_steps):
            time.sleep(step_sleep)
            delta_y = -random.randint(80, 150) if (idx > 0 and random.random() < 0.25) else random.randint(200, 450)
            try:
                page.mouse.wheel(0, delta_y)
            except Exception:
                pass
        time.sleep(step_sleep)
        return True
    except Exception as exc:
        logger.debug(f"[Warmup] Bỏ qua warmup visit ({target_url}): {exc}")
        return False


def active_edge_browsing_cooldown(
    page,
    total_seconds: int = 900,
    initial_seconds: int = 0,
    state_store: Optional[StateStore] = None,
    update_fn: Optional[Callable[[int, bool], None]] = None,
):
    """
    Duy trì phiên duyệt web Edge trong thời gian Cooldown để hoàn thành Edge Browsing Streak (30 phút/ngày).
    Giới hạn cứng 1860s/ngày và lưu tiến độ mỗi 60s qua StateStore.
    """
    def _persist(secs: int, done: bool = False) -> None:
        if update_fn is not None:
            update_fn(secs, done)
        elif state_store is not None:
            state_store.update_edge_browsing_state(secs, done=done)

    remaining_cap = max(0, EDGE_BROWSING_CAP_SECONDS - initial_seconds)
    target_seconds = min(total_seconds, remaining_cap)
    if target_seconds <= 0:
        _persist(EDGE_BROWSING_CAP_SECONDS, True)
        return page, 0

    logger.info(
        f"[Edge Browsing] Bắt đầu phiên duyệt web ngầm tích lũy thời gian "
        f"({target_seconds // 60}m {target_seconds % 60}s, đã tích lũy {initial_seconds}s/{EDGE_BROWSING_CAP_SECONDS}s)..."
    )

    context = getattr(page, "context", None)

    try:
        if page is None or page.is_closed():
            if context and context.pages:
                page = context.pages[0]
            elif context:
                page = context.new_page()
    except Exception:
        pass

    try:
        if page and not page.is_closed():
            page.goto("https://www.bing.com/news", wait_until="domcontentloaded", timeout=12000)
    except Exception:
        pass

    start_time = time.time()
    last_persisted_at = start_time
    last_scroll_at = start_time
    scroll_interval = random.randint(120, 180)

    while True:
        elapsed = int(time.time() - start_time)
        cumulative = initial_seconds + elapsed
        if elapsed >= target_seconds or cumulative >= EDGE_BROWSING_CAP_SECONDS:
            final_secs = min(EDGE_BROWSING_CAP_SECONDS, cumulative)
            _persist(final_secs, final_secs >= EDGE_BROWSING_CAP_SECONDS)
            break

        if time.time() - last_persisted_at >= 60:
            _persist(cumulative, False)
            last_persisted_at = time.time()

        if time.time() - last_scroll_at >= scroll_interval:
            try:
                if page and not page.is_closed():
                    scroll_y = random.choice([300, 500, -200, 400])
                    page.mouse.wheel(0, scroll_y)
                    page.wait_for_timeout(1000)
            except Exception:
                pass
            last_scroll_at = time.time()
            scroll_interval = random.randint(120, 180)

        remaining = min(target_seconds - elapsed, EDGE_BROWSING_CAP_SECONDS - cumulative)
        sleep_chunk = min(60, remaining)
        if sleep_chunk > 0:
            logger.info(
                f"[Edge Browsing Streak] Đang tích lũy ({cumulative}/{EDGE_BROWSING_CAP_SECONDS}s)... "
                f"Còn lại đợt này: {remaining // 60}m {remaining % 60}s"
            )
            time.sleep(sleep_chunk)

    try:
        if context:
            open_pages = [p for p in context.pages if not p.is_closed()]
            if not open_pages:
                blank_page = context.new_page()
                blank_page.goto("about:blank")
    except Exception:
        pass

    actual_elapsed = int(time.time() - start_time)
    logger.info("[Edge Browsing] Hoàn thành đợt tích lũy thời gian Cooldown.")
    return page, actual_elapsed


def _get_dir_size_bytes(folder: Path) -> int:
    total = 0
    try:
        for entry in folder.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def purge_profile_caches(
    profiles_dir: str = "profiles",
    max_age_days: int = 7,
    max_total_mb: int = 500,
) -> None:
    """
    Smart SSD Guard: Giữ warm cache trong profiles/ để trình duyệt có cache tự nhiên qua từng ngày.
    Chỉ xóa thư mục cache khi cũ hơn max_age_days hoặc tổng dung lượng > max_total_mb.
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

    now = time.time()
    max_age_seconds = max_age_days * 86400
    max_total_bytes = max_total_mb * 1024 * 1024

    junk_dirs = [folder for folder in profiles_path.rglob("*") if folder.is_dir() and folder.name in junk_folder_names]
    freed_count = 0
    surviving: list[tuple[float, int, Path]] = []

    for folder in junk_dirs:
        if not folder.exists():
            continue
        try:
            mtime = folder.stat().st_mtime
        except OSError:
            mtime = now
        if (now - mtime) > max_age_seconds:
            try:
                shutil.rmtree(folder, ignore_errors=True)
                freed_count += 1
            except Exception:
                pass
        else:
            surviving.append((mtime, _get_dir_size_bytes(folder), folder))

    total_bytes = sum(size for _, size, _ in surviving)
    if total_bytes > max_total_bytes:
        surviving.sort(key=lambda item: item[0])
        remaining: list[tuple[float, int, Path]] = []
        for mtime, size, folder in surviving:
            if total_bytes > max_total_bytes and folder.exists():
                try:
                    shutil.rmtree(folder, ignore_errors=True)
                    freed_count += 1
                    total_bytes -= size
                except Exception:
                    remaining.append((mtime, size, folder))
            else:
                remaining.append((mtime, size, folder))
        surviving = remaining

    total_mb = total_bytes / (1024 * 1024)
    if freed_count > 0:
        logger.info(
            f"[SSD Guard] Đã dọn {freed_count} thư mục cache (quá {max_age_days} ngày hoặc >{max_total_mb} MB). "
            f"Còn lại {total_mb:.1f} MB warm cache."
        )
    else:
        logger.info(
            f"[SSD Guard] Giữ nguyên warm cache ({total_mb:.1f} MB <= {max_total_mb} MB, < {max_age_days} ngày)."
        )


def cleanup_zombie_browsers(profiles_dir: Optional[Path] = None) -> None:
    """Đóng các tiến trình msedge.exe headless bị treo từ phiên trước trỏ vào profiles/."""
    _log_info("[Process Guard] Kiểm tra và dọn dẹp các tiến trình Edge headless treo trong profiles/...")
    killed_count = 0
    profiles_root = str((profiles_dir or (ROOT_DIR / "profiles")).resolve()).lower().replace("/", "\\")
    try:
        if psutil:
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    if proc.info["name"] and proc.info["name"].lower() == "msedge.exe":
                        cmdline = " ".join(proc.info.get("cmdline") or []).lower().replace("/", "\\")
                        if "--user-data-dir" in cmdline and profiles_root in cmdline:
                            proc.kill()
                            killed_count += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
    except Exception as exc:
        _log_debug(f"[Process Guard] Lỗi khi dọn tiến trình Edge: {exc}")

    if killed_count > 0:
        _log_info(f"[Process Guard] Đã dọn dẹp {killed_count} tiến trình msedge.exe giải phóng khóa profiles.")
    else:
        _log_info("[Process Guard] Không có tiến trình Edge bot nào bị treo.")


# ==================== LOCKING, NETWORK & POWER GUARDS ====================
import ctypes
import datetime
import json
import os
import socket
import subprocess
import sys

CIRCADIAN_START_HOUR, CIRCADIAN_START_MINUTE = 7, 0
CIRCADIAN_END_HOUR, CIRCADIAN_END_MINUTE = 22, 30


def _read_lock_pid(lock_path: Path) -> str:
    try:
        raw = lock_path.read_text(encoding="utf-8", errors="ignore").strip()
        if raw:
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("pid"):
                return str(data["pid"])
    except Exception:
        pass
    return "unknown"


def acquire_runner_lock(lock_path: Path, account_id: str = "default"):
    """Acquire an exclusive non-blocking OS file lock."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_path, "a+", encoding="utf-8")
    try:
        f.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        pid = _read_lock_pid(lock_path)
        _log_info(
            f"[Process Guard] Another bot instance (PID {pid}, account={account_id}) is already running. "
            "Exiting cleanly without killing active browsers."
        )
        try:
            f.close()
        except Exception:
            pass
        return None
    try:
        f.seek(0)
        f.truncate(0)
        f.write(json.dumps({
            "pid": os.getpid(),
            "account_id": account_id,
            "started_at": datetime.datetime.now().isoformat(),
        }))
        f.flush()
    except Exception:
        pass
    return f


def release_runner_lock(lock_handle) -> None:
    if lock_handle is None:
        return
    try:
        lock_handle.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        lock_handle.close()
    except Exception:
        pass


def is_sibling_runner_active() -> bool:
    if not psutil:
        return False
    my_pid = os.getpid()
    try:
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                pid = proc.info.get("pid")
                if not pid or pid == my_pid:
                    continue
                name = (proc.info.get("name") or "").lower()
                if name in ("python.exe", "pythonw.exe", "python", "python3"):
                    if "runner.py" in " ".join(proc.info.get("cmdline") or []).lower():
                        return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception:
        pass
    return False


def wait_for_internet(host: str = "www.bing.com", port: int = 443, timeout: int = 60) -> bool:
    _log_info(f"[Network Guard] Kiểm tra kết nối Internet ({host}:{port})...")
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with socket.create_connection((host, port), timeout=3.0):
                _log_info(f"[Network Guard] Kết nối Internet ổn định ({time.time() - start_time:.1f}s)!")
                return True
        except (socket.timeout, OSError):
            if time.time() - start_time < timeout:
                time.sleep(3)
    _log_warning(f"[Network Guard] Hết thời gian chờ mạng ({timeout}s)!")
    return False


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def is_user_active(threshold_seconds: int = 120) -> bool:
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


def put_pc_to_sleep() -> None:
    if is_sibling_runner_active() or is_user_active(threshold_seconds=120):
        logger.info("[Auto-Sleep] Hủy lệnh Sleep để không làm gián đoạn công việc!")
        return
    try:
        purge_profile_caches()
    except Exception:
        pass
    for remaining in range(10, 0, -1):
        if is_user_active(threshold_seconds=5):
            return
        logger.info(f"[Power] Đang đếm ngược đưa máy vào Sleep: {remaining}s...")
        time.sleep(1)
    cmd = 'powershell -Command "Add-Type -Assembly System.Windows.Forms; [System.Windows.Forms.Application]::SetSuspendState([System.Windows.Forms.PowerState]::Suspend, $false, $false)"'
    subprocess.run(cmd, shell=True)


def is_within_circadian_window(now: Optional[datetime.datetime] = None) -> bool:
    current = now if now is not None else datetime.datetime.now()
    current_minutes = current.hour * 60 + current.minute
    return (CIRCADIAN_START_HOUR * 60 + CIRCADIAN_START_MINUTE) <= current_minutes <= (
        CIRCADIAN_END_HOUR * 60 + CIRCADIAN_END_MINUTE
    )
