"""
Module tích hợp tính năng "Read to Earn" (30 điểm/ngày - 10 bài báo x 3 điểm)
thông qua Bing Mobile App OAuth API chính thức, không cần giả lập Android.
"""

import datetime
import json
import logging
import os
import random
import sys
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Optional

import requests
from patchright.sync_api import Page

from core.tracker import (
    _log_debug,
    _log_error,
    _log_info,
    _log_warning,
    logger,
)

# Cấu hình OAuth Bing Mobile App
CLIENT_ID = "0000000040170455"
SCOPE = "service::prod.rewardsplatform.microsoft.com::MBI_SSL"
REDIRECT_URI = "https://login.live.com/oauth20_desktop.srf"
AUTH_URL = (
    f"https://login.live.com/oauth20_authorize.srf"
    f"?client_id={CLIENT_ID}&scope={SCOPE}&response_type=code&redirect_uri={REDIRECT_URI}"
)
TOKEN_URL = "https://login.live.com/oauth20_token.srf"
ACTIVITIES_API_URL = "https://prod.rewardsplatform.microsoft.com/dapi/me/activities"
REWARDS_API_BASE = "https://prod.rewardsplatform.microsoft.com/dapi"

SAPPHIRE_HEADERS = {
    "User-Agent": "BingSapphire/29.5.2110003554 (Linux; U; Android 14; en_US; Pixel 8; Build/UP1A.231005.007)",
    "X-Requested-With": "com.microsoft.bing",
    "X-Rewards-PartnerId": "startapp",
    "X-Rewards-Country": "vn",
    "X-Rewards-Language": "en",
    "X-Rewards-IsMobile": "true",
    "Accept": "application/json",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
    "Content-Type": "application/json",
}

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
OAUTH_FILE = DATA_DIR / "mobile_oauth.json"
DEBUG_DAPI_FILE = DATA_DIR / "debug_dapi_me.json"

_oauth_cache_mem: dict[str, dict] = {}
_oauth_browser_attempted: bool = False
_oauth_browser_failed: bool = False

_SENSITIVE_OAUTH_PARAMS = {
    "code",
    "access_token",
    "refresh_token",
    "id_token",
    "state",
    "session_state",
    "login_hint",
    "username",
}

_OAUTH_BLOCKER_SELECTORS = (
    'input[type="password"]',
    'input[name="passwd"]',
    "#idDiv_SAOTCS_Proofs",
    "#idTxtBx_SAOTCC_OTC",
    '[data-bind*="PhoneAppNotification"]',
)

_OAUTH_LOGIN_INPUT_SELECTORS = (
    'input[type="email"]',
    'input[name="loginfmt"]',
    "#i0116",
)

_OAUTH_BLOCKER_KEYWORDS = (
    "fido",
    "passkey",
    "recover",
    "verify your identity",
    "xác minh danh tính",
    "two-step verification",
    "authenticator",
)

_OAUTH_LOGIN_KEYWORDS = (
    "sign in",
    "đăng nhập",
    "enter password",
    "nhập mật khẩu",
    "ppsecure",
    "login.srf",
)

_OAUTH_ACCOUNT_PICKER_SELECTORS = (
    'div[data-test-id]:not(#otherTile)',
    '.table[role="button"]:not(#otherTile)',
    'div.tile [role="button"]:not(#otherTile)',
    '[role="listitem"] [role="button"]',
)

_OAUTH_INTERSTITIAL_SELECTORS = (
    "#idSIButton9",
    "#acceptButton",
    'button:has-text("Yes")',
    'button:has-text("Có")',
    'input[value="Yes"]',
    'input[value="Có"]',
    "#idBtn_Accept",
    'button:has-text("Accept")',
    'button:has-text("Continue")',
    'button:has-text("Đồng ý")',
    'button:has-text("Tiếp tục")',
)


def set_oauth_file(path: Path | str) -> None:
    """Configure per-account OAuth token cache file."""
    global OAUTH_FILE
    OAUTH_FILE = Path(path)


def clear_oauth_memory_cache() -> None:
    """Clear the in-memory OAuth token cache."""
    _oauth_cache_mem.clear()


def reset_oauth_circuit_breaker() -> None:
    """Reset the OAuth browser circuit-breaker flags."""
    global _oauth_browser_attempted, _oauth_browser_failed
    _oauth_browser_attempted = False
    _oauth_browser_failed = False


def _atomic_save_oauth(path: Path, payload: dict) -> None:
    """Atomically write OAuth JSON to disk and update `_oauth_cache_mem`."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(f"{target}.tmp")
    serialized = json.dumps(payload, indent=2, ensure_ascii=False)
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(serialized)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, target)
    _oauth_cache_mem[str(target.resolve())] = dict(payload)


def _load_oauth_cache(path: Path) -> dict:
    """Synchronize `_oauth_cache_mem` with disk (`OAUTH_FILE`), returning `{}` if missing/corrupt."""
    target = Path(path)
    key = str(target.resolve())
    if not target.exists():
        _oauth_cache_mem.pop(key, None)
        return {}
    try:
        raw = target.read_text(encoding="utf-8")
        if not raw.strip():
            raise ValueError("empty OAuth cache file")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("OAuth cache root must be a JSON object")
        _oauth_cache_mem[key] = dict(data)
        return dict(data)
    except Exception as exc:
        _oauth_cache_mem.pop(key, None)
        _log_warning(f"[Mobile OAuth] Không thể đọc cache token ({target.name}): {exc}")
        return {}


def _extract_oauth_code(url: str) -> Optional[str]:
    """Extract OAuth authorization `code` from query or fragment when present."""
    if not url or not isinstance(url, str) or "code=" not in url:
        return None
    try:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get("code") and qs["code"][0]:
            return qs["code"][0]
        if parsed.fragment:
            frag_qs = urllib.parse.parse_qs(parsed.fragment)
            if frag_qs.get("code") and frag_qs["code"][0]:
                return frag_qs["code"][0]
    except Exception:
        return None
    return None


def _sanitize_oauth_url(url: str) -> str:
    """Redact sensitive query/fragment parameters from an OAuth URL before logging."""
    if not url or not isinstance(url, str):
        return ""
    try:
        parsed = urllib.parse.urlparse(url)

        def _redact_part(raw_part: str) -> str:
            if not raw_part or "=" not in raw_part:
                return raw_part
            pairs = urllib.parse.parse_qsl(raw_part, keep_blank_values=True)
            if not pairs:
                return raw_part
            redacted = []
            for k, v in pairs:
                enc_k = urllib.parse.quote(k, safe="")
                if k.lower() in _SENSITIVE_OAUTH_PARAMS:
                    redacted.append(f"{enc_k}=[REDACTED]")
                else:
                    redacted.append(f"{enc_k}={urllib.parse.quote(v, safe='')}")
            return "&".join(redacted)

        return urllib.parse.urlunparse(
            parsed._replace(
                query=_redact_part(parsed.query),
                fragment=_redact_part(parsed.fragment),
            )
        )
    except Exception:
        return "<unparseable-url>"


def _extract_oauth_error(res) -> tuple[str, str]:
    """Safely extract `(error, error_description)` from an OAuth HTTP error response."""
    err = ""
    err_desc = ""
    try:
        body = res.json()
        if isinstance(body, dict):
            err = str(body.get("error") or "")
            err_desc = str(body.get("error_description") or "")
    except Exception:
        pass
    raw_text = ""
    try:
        raw_text = str(getattr(res, "text", "") or "")
    except Exception:
        pass
    if not err and not err_desc:
        return "unknown", raw_text[:200]
    if not err_desc:
        err_desc = raw_text[:200]
    return (err or "unknown"), err_desc


def _safe_page_url(auth_page: Optional[Page]) -> str:
    if auth_page is None:
        return ""
    try:
        raw = getattr(auth_page, "url", "")
        return raw if isinstance(raw, str) else ""
    except Exception:
        return ""


def _safe_page_title(auth_page: Optional[Page]) -> str:
    if auth_page is None:
        return ""
    try:
        raw = auth_page.title()
        return raw if isinstance(raw, str) else ""
    except Exception:
        return ""


def _is_selector_visible(auth_page: Page, selector: str, timeout_ms: int = 200) -> bool:
    try:
        return auth_page.locator(selector).first.is_visible(timeout=timeout_ms) is True
    except Exception:
        return False


def _save_oauth_fail_debug(auth_page: Optional[Page], reason: str) -> None:
    """Best-effort screenshot capture and sanitized URL/title warning on OAuth failure."""
    logs_dir = ROOT_DIR / "logs"
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        if auth_page is not None:
            auth_page.screenshot(path=str(logs_dir / "debug_oauth_fail.png"))
    except Exception:
        pass
    safe_url = _sanitize_oauth_url(_safe_page_url(auth_page))
    title = _safe_page_title(auth_page)
    _log_warning(f"[Mobile OAuth] {reason} | url={safe_url} | title={title}")


def _detect_oauth_blocker(auth_page: Page, elapsed: float) -> Optional[str]:
    """Detect interactive password/2FA/passkey/recovery blockers for fast-fail."""
    for sel in _OAUTH_BLOCKER_SELECTORS:
        if _is_selector_visible(auth_page, sel):
            return f"Phát hiện màn hình yêu cầu mật khẩu/2FA ({sel}), dừng sớm"

    curr_url_l = _safe_page_url(auth_page).lower()
    body_text_l = ""
    try:
        raw_text = auth_page.inner_text("body", timeout=300)
        if isinstance(raw_text, str):
            body_text_l = raw_text.lower()
    except TypeError:
        try:
            raw_text = auth_page.inner_text("body")
            if isinstance(raw_text, str):
                body_text_l = raw_text.lower()
        except Exception:
            pass
    except Exception:
        pass

    combined = f"{curr_url_l}\n{body_text_l}"
    for kw in _OAUTH_BLOCKER_KEYWORDS:
        if kw in combined:
            return f"Phát hiện bước xác minh/passkey/recovery ({kw}), dừng sớm"

    if elapsed >= 5.0:
        for sel in _OAUTH_LOGIN_INPUT_SELECTORS:
            if _is_selector_visible(auth_page, sel):
                return f"Quá 5s vẫn kẹt tại màn hình đăng nhập ({sel}), dừng sớm"
        for kw in _OAUTH_LOGIN_KEYWORDS:
            if kw in combined:
                return f"Quá 5s vẫn kẹt tại màn hình login/2FA ({kw}), dừng sớm"

    return None


def _click_microsoft_interstitial(auth_page: Page) -> bool:
    """Click the first visible enabled Microsoft Account Picker, KMSI, or Consent button."""
    selector_groups = (
        (True, _OAUTH_ACCOUNT_PICKER_SELECTORS),
        (False, _OAUTH_INTERSTITIAL_SELECTORS),
    )
    for is_account_picker, selectors in selector_groups:
        for sel in selectors:
            try:
                loc = auth_page.locator(sel)
                try:
                    all_els = loc.all()
                    candidates = all_els if isinstance(all_els, list) else [loc.first]
                except Exception:
                    candidates = [loc.first]

                for el in candidates:
                    try:
                        if el.is_visible(timeout=200) is not True:
                            continue
                        try:
                            if el.is_enabled(timeout=200) is False:
                                continue
                        except Exception:
                            pass
                        if is_account_picker:
                            txt_l = ""
                            try:
                                raw_txt = el.inner_text(timeout=200)
                                if isinstance(raw_txt, str):
                                    txt_l = raw_txt.lower()
                            except Exception:
                                pass
                            if "use another account" in txt_l or "tài khoản khác" in txt_l:
                                continue
                        el.click()
                        return True
                    except Exception:
                        continue
            except Exception:
                continue
    return False


def get_mobile_access_token(page: Optional[Page] = None) -> Optional[str]:
    """
    Lấy hoặc làm mới Mobile Access Token cho Bing Mobile App API.
    1. Kiểm tra TTL access_token trong cache (0 HTTP calls nếu còn hạn > 300s).
    2. Kiểm tra circuit breaker `_oauth_browser_failed` (trả về None ngay lập tức nếu đã fail).
    3. Làm mới từ refresh_token qua TOKEN_URL nếu có.
    4. Dùng Playwright page mở AUTH_URL, bắt code qua network/URL với fast-fail <= 5s.
    """
    global _oauth_browser_attempted, _oauth_browser_failed

    # 1. TTL Check FIRST (0 network calls)
    cache = _load_oauth_cache(OAUTH_FILE)
    cached_access = cache.get("access_token")
    expires_at = cache.get("expires_at")
    if (
        isinstance(cached_access, str)
        and cached_access.strip()
        and isinstance(expires_at, (int, float))
        and not isinstance(expires_at, bool)
        and time.time() < float(expires_at) - 300
    ):
        return cached_access

    # 2. Circuit Breaker Check SECOND
    if _oauth_browser_failed:
        _log_debug("[Mobile OAuth] Circuit breaker đang mở (_oauth_browser_failed=True), bỏ qua OAuth.")
        return None

    # 3. Refresh Token Flow
    refresh_token = cache.get("refresh_token")
    if refresh_token:
        try:
            _log_debug("[Mobile OAuth] Thử làm mới token từ refresh_token...")
            refresh_data = {
                "client_id": CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": SCOPE,
            }
            res = requests.post(TOKEN_URL, data=refresh_data, timeout=15)
            if res.status_code == 200:
                tokens = res.json()
                expires_in = int(tokens.get("expires_in") or 3600)
                new_expires_at = time.time() + expires_in
                rotated_refresh = tokens.get("refresh_token")
                new_refresh = rotated_refresh or refresh_token
                new_access = tokens.get("access_token")
                if new_access:
                    oauth_data = {
                        "access_token": new_access,
                        "refresh_token": new_refresh,
                        "expires_at": new_expires_at,
                        "updated_at": datetime.datetime.now().isoformat(),
                    }
                    _atomic_save_oauth(OAUTH_FILE, oauth_data)
                    _log_info("[Mobile OAuth] Làm mới Access Token thành công.")
                    return new_access
                if rotated_refresh:
                    rotated_payload = dict(cache)
                    rotated_payload["refresh_token"] = rotated_refresh
                    rotated_payload["updated_at"] = datetime.datetime.now().isoformat()
                    _atomic_save_oauth(OAUTH_FILE, rotated_payload)
            else:
                err, err_desc = _extract_oauth_error(res)
                _log_warning(
                    f"[Mobile OAuth] Làm mới token thất bại (HTTP {res.status_code}): "
                    f"error={err} | error_description={err_desc}"
                )
        except Exception as e:
            _log_warning(f"[Mobile OAuth] Lỗi khi làm mới token: {e}")

    # 4. Xác thực qua trình duyệt (không trip circuit breaker nếu page is None)
    if page is None:
        _log_warning("[Mobile OAuth] Không có phiên trình duyệt để lấy authorization code mới.")
        return None

    _oauth_browser_attempted = True
    auth_page = None
    try:
        _log_info("[Mobile OAuth] Đang mở luồng xác thực OAuth App Bing qua phiên Microsoft hiện tại...")
        try:
            auth_page = page.context.new_page()
        except Exception:
            auth_page = page

        captured: dict[str, Optional[str]] = {"code": None}

        def _try_capture(url_val: Optional[str]) -> None:
            if captured["code"] or not url_val:
                return
            extracted = _extract_oauth_code(str(url_val))
            if extracted:
                captured["code"] = extracted

        def _on_request(req) -> None:
            try:
                _try_capture(getattr(req, "url", None))
            except Exception:
                pass

        def _on_response(resp) -> None:
            try:
                _try_capture(getattr(resp, "url", None))
                headers = getattr(resp, "headers", None)
                if isinstance(headers, dict):
                    _try_capture(headers.get("location") or headers.get("Location"))
                elif hasattr(resp, "header_value"):
                    _try_capture(resp.header_value("location"))
            except Exception:
                pass

        try:
            auth_page.on("request", _on_request)
        except Exception:
            pass
        try:
            auth_page.on("response", _on_response)
        except Exception:
            pass

        try:
            auth_page.goto(AUTH_URL, timeout=15000, wait_until="domcontentloaded")
        except Exception as nav_exc:
            _log_debug(f"[Mobile OAuth] Điều hướng AUTH_URL có ngoại lệ (tiếp tục kiểm tra code): {nav_exc}")

        code: Optional[str] = None
        start_wait = time.time()
        while time.time() - start_wait < 20.0:
            code = captured["code"] or _extract_oauth_code(_safe_page_url(auth_page))
            if code:
                break

            blocker_reason = _detect_oauth_blocker(auth_page, time.time() - start_wait)
            if blocker_reason:
                _save_oauth_fail_debug(auth_page, blocker_reason)
                _oauth_browser_failed = True
                return None

            _click_microsoft_interstitial(auth_page)

            try:
                auth_page.wait_for_timeout(1000)
            except Exception:
                time.sleep(1.0)

        if not code:
            code = captured["code"] or _extract_oauth_code(_safe_page_url(auth_page))

        if not code:
            _save_oauth_fail_debug(auth_page, "Không trích xuất được authorization code sau 20s")
            _oauth_browser_failed = True
            return None

        token_payload = {
            "client_id": CLIENT_ID,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
        }
        res = requests.post(TOKEN_URL, data=token_payload, timeout=15)
        if res.status_code == 200:
            tokens = res.json()
            access_token = tokens.get("access_token")
            refresh_token = tokens.get("refresh_token")
            expires_in = int(tokens.get("expires_in") or 3600)
            new_expires_at = time.time() + expires_in
            if access_token:
                oauth_data = {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "expires_at": new_expires_at,
                    "updated_at": datetime.datetime.now().isoformat(),
                }
                _atomic_save_oauth(OAUTH_FILE, oauth_data)
                _oauth_browser_failed = False
                _log_info("[Mobile OAuth] Lấy OAuth token mới thành công và đã lưu vào cache!")
                return access_token
            _save_oauth_fail_debug(auth_page, "Phản hồi đổi token HTTP 200 nhưng thiếu access_token")
            _oauth_browser_failed = True
            return None

        err, err_desc = _extract_oauth_error(res)
        _save_oauth_fail_debug(
            auth_page,
            f"Đổi code thất bại (HTTP {res.status_code}): error={err} | error_description={err_desc}",
        )
        _oauth_browser_failed = True
        return None

    except Exception as e:
        _save_oauth_fail_debug(auth_page, f"Lỗi trong quá trình lấy OAuth token: {e}")
        _oauth_browser_failed = True
        return None
    finally:
        if auth_page and auth_page != page:
            try:
                auth_page.close()
            except Exception:
                pass


def _build_sapphire_headers(access_token: str, country: str = "vn") -> dict:
    headers = dict(SAPPHIRE_HEADERS)
    headers["Authorization"] = f"Bearer {access_token}"
    headers["X-Rewards-Country"] = country.upper()
    headers["X-Rewards-Language"] = "vi"
    return headers


def _extract_balance(payload) -> Optional[float]:
    if not isinstance(payload, dict):
        return None
    candidates = [
        payload.get("balance"),
        payload.get("response", {}).get("balance") if isinstance(payload.get("response"), dict) else None,
        payload.get("userStatus", {}).get("balance") if isinstance(payload.get("userStatus"), dict) else None,
        payload.get("counters", {}).get("points", {}).get("balance")
        if isinstance(payload.get("counters"), dict)
        and isinstance(payload["counters"].get("points"), dict)
        else None,
    ]
    for value in candidates:
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _activity_awarded_points(data) -> int:
    if not isinstance(data, dict):
        return 0
    body = data.get("response", data)
    if not isinstance(body, dict):
        return 0
    activity = body.get("activity")
    if isinstance(activity, dict):
        for key in ("p", "points", "pointValue", "awarded"):
            value = activity.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return int(value)
    return 0


def _get_balance(access_token: str, country: str = "vn") -> Optional[float]:
    headers = _build_sapphire_headers(access_token, country)
    # Use DAPI endpoint with options=613 for complete user data
    resp = requests.get(f"{REWARDS_API_BASE}/me?channel=BingApp&options=613", headers=headers, timeout=15)
    if resp.status_code != 200:
        _log_warning(f"[App Check-in] Đọc balance thất bại (HTTP {resp.status_code}).")
        return None
    return _extract_balance(resp.json())


def _extract_promotion_id(promotions) -> Optional[str]:
    for promo in promotions:
        if not isinstance(promo, dict):
            continue
        name_parts = " ".join(
            str(promo.get(key, ""))
            for key in ("name", "title", "promotionName", "offerId", "id", "type")
        ).lower()
        # Check for check-in related keywords and type 103
        keywords = ("checkin", "check-in", "check_in", "daily_streak", "sapphire_checkin", "103")
        if any(keyword in name_parts for keyword in keywords):
            return promo.get("id") or promo.get("name") or promo.get("offerId") or promo.get("offerid")
        # Also check type field specifically for 103
        ptype = str(promo.get("type", "")).lower()
        if "103" in ptype:
            return promo.get("id") or promo.get("name") or promo.get("offerId") or promo.get("offerid")
    return None


def _extract_checkin_day(promotions, promotion_id: str) -> int:
    for promo in promotions:
        if not isinstance(promo, dict) or (promo.get("id") or promo.get("name")) != promotion_id:
            continue
        attrs = promo.get("attributes") or {}
        for key in ("checkinDay", "currentDay", "streakDay", "day"):
            value = attrs.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return int(value)
        if isinstance(attrs.get("streak"), dict):
            value = attrs["streak"].get("current")
            if isinstance(value, (int, float)) and value > 0:
                return int(value)
        progress = int(promo.get("progress") or 0)
        if progress > 0:
            return progress + 1
    return 1


def _is_dapi_debug_enabled() -> bool:
    """Only dump raw DAPI JSON when --debug CLI flag or DEBUG_DAPI=1 is active."""
    return ("--debug" in sys.argv) or (os.environ.get("DEBUG_DAPI") == "1")


def _get_checkin_promotion(access_token: str, country: str = "vn") -> dict:
    headers = _build_sapphire_headers(access_token, country)
    # Primary DAPI endpoint with options=613 as per protocol
    endpoints = (
        f"{REWARDS_API_BASE}/me?channel=BingApp&options=613",
        f"{REWARDS_API_BASE}/me?channel=StartApp&options=613",
    )
    promotions = []
    raw_json = None
    for url in endpoints:
        try:
            resp = requests.get(url, headers=headers, timeout=15)
        except Exception as e:
            _log_debug(f"[App Check-in] Lỗi GET promotions ({url}): {e}")
            continue
        if resp.status_code != 200:
            _log_debug(f"[App Check-in] Đọc promotions thất bại ({url}, HTTP {resp.status_code}).")
            continue
        raw_json = resp.json()
        
        # Debug JSON logging when --debug flag or DEBUG_DAPI=1 is active
        if _is_dapi_debug_enabled():
            try:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                with open(DEBUG_DAPI_FILE, "w", encoding="utf-8") as f:
                    json.dump(raw_json, f, indent=2, ensure_ascii=False)
                _log_info(f"[DEBUG] DAPI raw JSON saved to {DEBUG_DAPI_FILE}")
                
                # Log summary of root keys and promotions
                root = raw_json.get("response", raw_json) if isinstance(raw_json, dict) else raw_json
                if isinstance(root, dict):
                    _log_debug(f"[DEBUG] [DAPI Root Keys] {list(root.keys())}")
                    promos = root.get("promotions", [])
                    if isinstance(promos, list):
                        for p in promos:
                            if isinstance(p, dict):
                                name = p.get("name") or p.get("title") or p.get("id") or "unknown"
                                offerid = p.get("offerid") or p.get("offerId") or p.get("attributes", {}).get("offerid") or ""
                                ptype = p.get("type", "")
                                complete = p.get("complete", False)
                                progress = p.get("progress", 0)
                                max_val = p.get("max", 1)
                                _log_debug(f"[DEBUG] [DAPI Promo] name={name} | offerid={offerid} | type={ptype} | complete={complete} | progress={progress}/{max_val}")
            except Exception as e:
                _log_debug(f"[DEBUG] Failed to write debug JSON: {e}")
        
        if isinstance(raw_json, list):
            promotions = [p for p in raw_json if isinstance(p, dict)]
        else:
            body = raw_json.get("response", raw_json) if isinstance(raw_json, dict) else {}
            raw_promotions = body.get("promotions") if isinstance(body, dict) else None
            promotions = [p for p in (raw_promotions or []) if isinstance(p, dict)]
            
            # Also check streak, punchCards, morePromotions if available
            for extra_key in ("streak", "punchCards", "morePromotions"):
                extra = body.get(extra_key)
                if isinstance(extra, list):
                    promotions.extend([p for p in extra if isinstance(p, dict)])
                    
        if promotions:
            break
    
    promotion_id = _extract_promotion_id(promotions)
    if not promotion_id:
        return {}
    return {
        "promotion_id": promotion_id,
        "checkin_day": _extract_checkin_day(promotions or [], promotion_id),
        "attributes": next(
            (p.get("attributes") or {} for p in (promotions or [])
             if (p.get("id") or p.get("name")) == promotion_id),
            {},
        ),
    }


def _submit_checkin_activity(headers: dict, country: str, offer_id: Optional[str]) -> dict:
    """
    Gửi payload check-in chuẩn DAPI type 103.
    Payload: id (uuid4), type: 103, amount: 1, country, risk_context, attributes.offerid
    """
    payload = {
        "id": str(uuid.uuid4()),
        "type": 103,
        "amount": 1,
        "country": country.lower(),
        "risk_context": {
            "app": "BingSapphire",
            "platform": "Android"
        },
        "attributes": {
            "offerid": offer_id
        }
    }
    resp = requests.post(ACTIVITIES_API_URL, json=payload, headers=headers, timeout=15)
    if resp.status_code != 200:
        return {"ok": False, "status": resp.status_code, "body": resp.text[:150]}
    data = resp.json()
    return {
        "ok": True,
        "status": resp.status_code,
        "balance": _extract_balance(data),
        "points_awarded": _activity_awarded_points(data),
        "offer_id": offer_id,
        "duplicate": bool(isinstance(data, dict) and data.get("response", {}).get("isDuplicate")),
    }


def solve_mobile_app_daily_checkin(
    page: Optional[Page] = None,
    country: str = "vn",
    state_store=None,
) -> int:
    """
    Thực hiện Điểm danh 7 ngày (Daily Check-in - type 103) qua Bing Mobile App OAuth API.
    Trích xuất promotion_id/checkinDay động, đối soát biến động điểm thực tế trước và sau khi claim.
    Trả về số điểm nhận thêm (nếu có).
    """
    if state_store is not None and state_store.is_mobile_checkin_done():
        logger.info("[App Check-in] [✓] Hôm nay đã điểm danh Mobile Check-in trước đó (StateStore). Bỏ qua payload 103 dư thừa.")
        return 0

    logger.info("--- BẮT ĐẦU: DAILY CHECK-IN (BING MOBILE APP API - TYPE 103) ---")
    access_token = get_mobile_access_token(page)
    if not access_token:
        logger.warning("[App Check-in] Không lấy được OAuth access_token, bỏ qua bước này.")
        return 0

    headers = _build_sapphire_headers(access_token, country)

    # 3.1 Warm-up & đọc balance trước khi claim
    start_balance = None
    try:
        start_balance = _get_balance(access_token, country)
        if start_balance is not None:
            logger.info(f"[App Check-in] Số dư trước điểm danh: {start_balance:,.0f} pts")
        else:
            logger.debug("[App Check-in] Không đọc được balance trước check-in.")
    except Exception as e:
        logger.debug(f"[App Check-in] Không thể đọc balance trước check-in: {e}")

    # 3.2 Bóc tách promotion động (promotion_id, current_day, attributes)
    promotion = {}
    try:
        promotion = _get_checkin_promotion(access_token, country)
        if promotion.get("promotion_id"):
            logger.info(
                f"[App Check-in] Trích xuất nhiệm vụ động: promotion_id={promotion['promotion_id']}, "
                f"checkin_day={promotion['checkin_day']}"
            )
            attrs = promotion.get("attributes", {})
            # Check if already completed today
            complete_val = attrs.get("complete")
            if complete_val in ("True", True):
                logger.info(f"[App Check-in] [✓] Hôm nay đã điểm danh trên hệ thống trước đó (attributes.complete={complete_val}).")
                if state_store is not None:
                    state_store.mark_mobile_checkin_done(True)
                return 0
        else:
            logger.info("[App Check-in] Không tìm thấy promotion check-in/streak, dùng payload 103 tiêu chuẩn.")
    except Exception as e:
        logger.debug(f"[App Check-in] Không thể trích xuất promotion động: {e}")

    # 3.3 Gửi payload claim chuẩn (dynamic khi có promotion_id, fallback 103)
    # Extract offer_id from promotion attributes
    offer_id = None
    if promotion.get("attributes"):
        offer_id = promotion["attributes"].get("offerid") or promotion["attributes"].get("offerId")
    if not offer_id:
        offer_id = promotion.get("promotion_id") or "sapphire_checkin"
    
    country_code = country.lower()
    
    result = {}
    try:
        result = _submit_checkin_activity(headers, country_code, offer_id)
    except Exception as e:
        logger.warning(f"[App Check-in] Lỗi khi gửi request điểm danh API: {e}")
        return 0

    if not result.get("ok"):
        logger.debug(
            f"[App Check-in] Phản hồi HTTP {result.get('status')}: {result.get('body', '')}"
        )
        return 0

    # 3.4 Nghỉ 3s và đối soát biến động điểm thực tế
    time.sleep(3.0)
    end_balance = None
    try:
        end_balance = _get_balance(access_token, country)
        if end_balance is not None:
            logger.info(f"[App Check-in] Số dư sau điểm danh: {end_balance:,.0f} pts")
    except Exception as e:
        logger.debug(f"[App Check-in] Không thể đối soát balance sau check-in: {e}")

    delta = 0
    if start_balance is not None and end_balance is not None:
        delta = int(round(end_balance - start_balance))
    elif result.get("points_awarded"):
        delta = int(result["points_awarded"])

    if state_store is not None:
        state_store.mark_mobile_checkin_done(True)

    if delta > 0:
        logger.info(f"[App Check-in] [✓] Đã kích hoạt điểm danh thành công (+{delta} pts)!")
        return delta

    logger.info("[App Check-in] [*] Điểm danh không tăng điểm (đã điểm danh trước đó hoặc cần claim qua Mobile UI fallback).")
    return 0


claim_daily_checkin = solve_mobile_app_daily_checkin


def solve_mobile_app_promotions(page: Optional[Page] = None, country: str = "vn") -> int:
    """
    Quét danh sách Promotions trên Bing Mobile App qua GET /dapi/me?channel=BingApp&options=613.
    Kiểm tra các nhiệm vụ App chưa hoàn thành và claim qua API nếu có.
    """
    logger.info("--- BẮT ĐẦU: QUÉT APP PROMOTIONS (BING MOBILE APP API) ---")
    access_token = get_mobile_access_token(page)
    if not access_token:
        logger.warning("[App Promotions] Không lấy được OAuth access_token, bỏ qua bước này.")
        return 0

    headers = _build_sapphire_headers(access_token, country)

    url = "https://prod.rewardsplatform.microsoft.com/dapi/me?channel=BingApp&options=613"
    claimed_count = 0

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            resp_body = data.get("response", {})
            promotions = resp_body.get("promotions") or []
            
            # Also check streak, punchCards, morePromotions if available
            for extra_key in ("streak", "punchCards", "morePromotions"):
                extra = resp_body.get(extra_key)
                if isinstance(extra, list):
                    promotions.extend([p for p in extra if isinstance(p, dict)])
            
            # Debug JSON logging when --debug flag or DEBUG_DAPI=1 is active
            if _is_dapi_debug_enabled():
                try:
                    DATA_DIR.mkdir(parents=True, exist_ok=True)
                    with open(DEBUG_DAPI_FILE, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                    _log_info(f"[DEBUG] DAPI raw JSON saved to {DEBUG_DAPI_FILE}")
                    
                    root = data.get("response", data) if isinstance(data, dict) else data
                    if isinstance(root, dict):
                        _log_debug(f"[DEBUG] [DAPI Root Keys] {list(root.keys())}")
                        for p in promotions:
                            if isinstance(p, dict):
                                name = p.get("name") or p.get("title") or p.get("id") or "unknown"
                                offerid = p.get("offerid") or p.get("offerId") or p.get("attributes", {}).get("offerid") or ""
                                ptype = p.get("type", "")
                                complete = p.get("complete", False)
                                progress = p.get("progress", 0)
                                max_val = p.get("max", 1)
                                _log_debug(f"[DEBUG] [DAPI Promo] name={name} | offerid={offerid} | type={ptype} | complete={complete} | progress={progress}/{max_val}")
                except Exception as e:
                    _log_debug(f"[DEBUG] Failed to write debug JSON: {e}")

            # Lọc các promotions chưa hoàn thành
            uncompleted_promos = []
            for p in promotions:
                is_complete = p.get("complete", False) or (p.get("progress", 0) >= p.get("max", 1) and p.get("max", 1) > 0)
                point_val = p.get("pointValue") or p.get("points") or 0
                if not is_complete and point_val > 0:
                    uncompleted_promos.append(p)

            logger.info(f"[App Promotions] Quét được {len(promotions)} promotions ({len(uncompleted_promos)} nhiệm vụ chưa hoàn thành).")
            
            for promo in uncompleted_promos:
                title = promo.get("title") or promo.get("name") or promo.get("id") or "Promotion"
                pts = promo.get("pointValue") or promo.get("points") or 0
                promo_id = promo.get("id") or promo.get("name")
                logger.info(f"[App Promotions] Đang thử claim nhiệm vụ: {title} (+{pts} pts)...")
                
                # Gửi claim tới activities API
                claim_payload = {
                    "amount": 1,
                    "attributes": {
                        "offerid": promo_id,
                    },
                    "country": country.lower(),
                    "id": str(uuid.uuid4()),
                    "type": promo.get("type", 101),
                    "channel": "BingApp",
                }
                try:
                    c_resp = requests.post(ACTIVITIES_API_URL, json=claim_payload, headers=headers, timeout=15)
                    if c_resp.status_code == 200:
                        logger.info(f"[App Promotions] [✓] Claim thành công: {title} (+{pts} pts)")
                        claimed_count += 1
                    else:
                        logger.debug(f"[App Promotions] Không thể claim {title} qua API (HTTP {c_resp.status_code})")
                except Exception as ce:
                    logger.debug(f"[App Promotions] Lỗi khi gửi claim {title}: {ce}")

                time.sleep(random.uniform(2.0, 4.0))

            if not uncompleted_promos:
                logger.info("[App Promotions] [✓] Tất cả App Promotions khả dụng đã hoàn thành.")

            return claimed_count
        else:
            logger.warning(f"[App Promotions] Yêu cầu GET /dapi/me thất bại (HTTP {resp.status_code})")
    except Exception as e:
        logger.warning(f"[App Promotions] Lỗi khi quét App Promotions: {e}")

    return claimed_count


def solve_mobile_app_read_to_earn(
    page: Optional[Page] = None,
    country: str = "vn",
    state_store=None,
) -> int:
    """
    Thực hiện nhiệm vụ "Read to Earn" trên App Bing Mobile (tối đa 10 bài x 3 điểm = 30 điểm/ngày).
    Gọi trực tiếp API https://prod.rewardsplatform.microsoft.com/dapi/me/activities.
    """
    if state_store is not None and state_store.is_read_to_earn_done():
        _log_info("[Read to Earn] [✓] Hôm nay đã hoàn tất Read to Earn trong StateStore, bỏ qua.")
        return 0

    _log_info("--- BẮT ĐẦU: READ TO EARN (BING MOBILE APP API) ---")
    access_token = get_mobile_access_token(page)
    if not access_token:
        _log_warning("[Read to Earn] Không lấy được mobile access token. Bỏ qua Read to Earn.")
        return 0

    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "Bing/28.8.420227017 (Pixel 7; Android 14) Sapphire/1.0",
        "Content-Type": "application/json",
        "X-Rewards-Country": country.lower(),
        "X-Rewards-Language": "vi",
    }

    last_balance = None
    consecutive_no_increase = 0
    consecutive_zero_gain_200 = 0
    articles_read = 0
    earned_in_session = 0

    for i in range(1, 11):
        if earned_in_session >= 30:
            break
        payload = {
            "amount": 1,
            "country": country.lower(),
            "id": str(uuid.uuid4()),
            "type": 101,
            "attributes": {
                "offerid": "ENUS_readarticle3_30points",
            },
        }

        try:
            resp = requests.post(
                ACTIVITIES_API_URL,
                json=payload,
                headers=headers,
                timeout=15,
            )
        except Exception as e:
            _log_warning(f"[Read to Earn] Lỗi khi gửi request bài báo {i}/10: {e}")
            consecutive_no_increase += 1
            if consecutive_no_increase >= 2:
                _log_info("[Read to Earn] Gặp lỗi liên tiếp, dừng vòng lặp Read to Earn.")
                break
            continue

        if resp.status_code == 200:
            try:
                res_data = resp.json()
            except Exception:
                res_data = {}

            # Trích xuất balance và điểm bài viết
            new_balance = None
            points_awarded = 0

            if isinstance(res_data, dict):
                resp_obj = res_data.get("response")
                if isinstance(resp_obj, dict):
                    new_balance = resp_obj.get("balance")
                    activity_obj = resp_obj.get("activity")
                    if isinstance(activity_obj, dict):
                        points_awarded = activity_obj.get("p", 0)
                if new_balance is None and "balance" in res_data:
                    new_balance = res_data["balance"]

            # Kiểm tra xem điểm có tăng không
            increased = False
            if points_awarded > 0:
                increased = True
                earned_in_session += points_awarded
            elif last_balance is not None and new_balance is not None and new_balance > last_balance:
                increased = True
                earned_in_session += (new_balance - last_balance)

            if increased:
                consecutive_no_increase = 0
                consecutive_zero_gain_200 = 0
                articles_read += 1
                _log_info(
                    f"[Read to Earn] Đã đọc bài báo {i}/10 (+3 pts) -> Tổng điểm: {new_balance if new_balance is not None else 'N/A'}"
                )
            else:
                consecutive_no_increase += 1
                consecutive_zero_gain_200 += 1
                _log_info(
                    f"[Read to Earn] Bài báo {i}/10 không tăng thêm điểm (Lần {consecutive_no_increase}/2)."
                )

            if new_balance is not None:
                last_balance = new_balance

            # Dừng ngay khi đạt ngưỡng 30 điểm phiên hoặc 2 lần liên tiếp HTTP 200 không tăng điểm
            if earned_in_session >= 30:
                _log_info(f"[Read to Earn] Đã đạt {earned_in_session}/30 điểm trong phiên, dừng vòng lặp.")
                break
            if consecutive_zero_gain_200 >= 2:
                _log_info("[Read to Earn] Hôm nay đã đạt tối đa 30/30 điểm đọc báo!")
                break

            # Nghỉ ngẫu nhiên an toàn giữa các bài đọc (6.0 - 9.5 giây)
            if i < 10 and consecutive_no_increase < 2:
                sleep_time = random.uniform(6.0, 9.5)
                _log_info(f"[Read to Earn] Đợi {sleep_time:.1f}s trước bài đọc tiếp theo...")
                time.sleep(sleep_time)

        elif resp.status_code == 401:
            _log_warning("[Read to Earn] Access Token đã hết hạn (HTTP 401).")
            break
        else:
            _log_warning(f"[Read to Earn] Gửi bài {i}/10 không thành công (HTTP {resp.status_code}): {resp.text[:100]}")
            consecutive_no_increase += 1
            if consecutive_no_increase >= 2:
                break

    if state_store is not None and (
        articles_read >= 10 or earned_in_session >= 30 or consecutive_zero_gain_200 >= 2
    ):
        state_store.mark_read_to_earn_done(True)

    _log_info(f"[Read to Earn] Kết thúc: Đã hoàn thành {articles_read} bài (+{earned_in_session} pts).")
    return earned_in_session


def fetch_dapi_me_profile(
    page: Optional[Page] = None,
    country: str = "vn",
    dapi_url: Optional[str] = None,
    access_token: Optional[str] = None,
) -> dict:
    """
    Fetch structured rewards profile from `/dapi/me`.
    Returns:
      {"status": "ok" | "unavailable" | "error", "data": dict | None, "error": str | None}
    """
    token = access_token or get_mobile_access_token(page)
    if not token:
        return {"status": "unavailable", "data": None, "error": "no_token"}

    headers = _build_sapphire_headers(token, country)
    url = dapi_url or "https://prod.rewardsplatform.microsoft.com/dapi/me?channel=BingApp&options=613"
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return {"status": "error", "data": None, "error": f"HTTP {resp.status_code}"}
        payload = resp.json()
        if not isinstance(payload, dict):
            return {"status": "error", "data": None, "error": "invalid_json_root"}
        body = payload.get("response", payload)
        if not isinstance(body, dict):
            return {"status": "error", "data": None, "error": "invalid_response_body"}
        return {"status": "ok", "data": body, "error": None}
    except Exception as exc:
        return {"status": "error", "data": None, "error": str(exc)}


def _extract_counter_pair(entry: dict) -> Optional[tuple[int, int]]:
    if not isinstance(entry, dict):
        return None
    cur = entry.get("pointProgress")
    if cur is None:
        cur = entry.get("progress")
    if cur is None:
        cur = entry.get("cur")
    mx = entry.get("pointProgressMax")
    if mx is None:
        mx = entry.get("max")
    if cur is None or mx is None:
        return None
    try:
        return int(cur), int(mx)
    except (TypeError, ValueError):
        return None


def parse_dapi_me_progress(dapi_data: dict) -> dict:
    """
    Extract structured progress from `dapi/me` JSON payload:
    `counters`, `activities`, `promotions`, `balance`.
    Explicitly distinguishes `status="empty"` (0 tasks remaining) from `status="error"`.
    """
    if not isinstance(dapi_data, dict):
        return {"status": "error", "found": False, "found_pc": False, "found_mobile": False, "scrape_error": True}

    result: dict = {
        "status": "active",
        "source": "dapi",
        "balance": None,
        "pc_cur": 0,
        "pc_current": 0,
        "pc_max": 90,
        "mobile_cur": 0,
        "mobile_current": 0,
        "mobile_max": 60,
        "found": False,
        "found_pc": False,
        "found_mobile": False,
        "scrape_error": False,
        "all_maxed": False,
        "activities": {},
        "promotions": [],
        "punch_cards": [],
        "all_activities_done": False,
        "primary_four_done": False,
        "bing_star_bonus": {"current": 0, "max": 2100, "text": "0 / 2,100", "found": False},
    }

    bal = dapi_data.get("balance")
    if bal is not None:
        try:
            result["balance"] = int(bal)
        except (TypeError, ValueError):
            pass

    counters = dapi_data.get("counters")
    if isinstance(counters, dict):
        for key, val in counters.items():
            kl = str(key).lower()
            pair = _extract_counter_pair(val)
            if not pair:
                continue
            cur, mx = pair
            if any(tok in kl for tok in ("pc", "desktop")):
                result["pc_cur"] = result["pc_current"] = cur
                result["pc_max"] = mx
                result["found_pc"] = True
            elif any(tok in kl for tok in ("mobile", "mob")):
                result["mobile_cur"] = result["mobile_current"] = cur
                result["mobile_max"] = mx
                result["found_mobile"] = True
            if not result["bing_star_bonus"]["found"] and mx == 2100:
                meta = kl
                if isinstance(val, dict):
                    meta += " " + " ".join(
                        str(val.get(k, "")) for k in ("name", "title", "type", "id")
                    ).lower()
                if "star" in meta:
                    result["bing_star_bonus"] = {
                        "current": cur,
                        "max": mx,
                        "text": f"{cur:,} / {mx:,}",
                        "found": True,
                    }
    elif isinstance(counters, list):
        for item in counters:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("title") or item.get("type") or item.get("id") or "").lower()
            pair = _extract_counter_pair(item)
            if not pair:
                continue
            cur, mx = pair
            if any(tok in name for tok in ("pc", "desktop")):
                result["pc_cur"] = result["pc_current"] = cur
                result["pc_max"] = mx
                result["found_pc"] = True
            elif any(tok in name for tok in ("mobile", "mob")):
                result["mobile_cur"] = result["mobile_current"] = cur
                result["mobile_max"] = mx
                result["found_mobile"] = True
            if not result["bing_star_bonus"]["found"] and "star" in name and mx == 2100:
                result["bing_star_bonus"] = {
                    "current": cur,
                    "max": mx,
                    "text": f"{cur:,} / {mx:,}",
                    "found": True,
                }

    # Collect promotions and punch cards
    raw_promos: list[dict] = []
    if isinstance(dapi_data.get("promotions"), list):
        raw_promos.extend([p for p in dapi_data["promotions"] if isinstance(p, dict)])
    if isinstance(dapi_data.get("punchCards"), list):
        for pc in dapi_data["punchCards"]:
            if isinstance(pc, dict):
                result["punch_cards"].append(pc)
                raw_promos.append(pc)
    for extra_key in ("streak", "morePromotions"):
        extra = dapi_data.get(extra_key)
        if isinstance(extra, list):
            raw_promos.extend([p for p in extra if isinstance(p, dict)])

    if not result["bing_star_bonus"]["found"]:
        for p in raw_promos:
            attrs = p.get("attributes") if isinstance(p.get("attributes"), dict) else {}
            promo_meta = " ".join(
                str(p.get(k, ""))
                for k in ("name", "title", "promotionName", "offerId", "offerid", "id", "type", "description")
            ).lower()
            if attrs:
                promo_meta += " " + " ".join(
                    str(attrs.get(k, ""))
                    for k in ("title", "name", "offerid", "offerId", "description")
                ).lower()
            if "star" not in promo_meta:
                continue
            pair = _extract_counter_pair(p) or _extract_counter_pair(attrs)
            if pair and pair[1] == 2100:
                cur, mx = pair
                result["bing_star_bonus"] = {
                    "current": cur,
                    "max": mx,
                    "text": f"{cur:,} / {mx:,}",
                    "found": True,
                }
                break

    raw_activities = dapi_data.get("activities")
    if isinstance(raw_activities, dict):
        for k, v in raw_activities.items():
            if isinstance(v, dict):
                cur = int(v.get("cur", v.get("progress", 0)) or 0)
                mx = int(v.get("max", 1) or 1)
                done = bool(v.get("done", v.get("complete", mx > 0 and cur >= mx)))
                result["activities"][k] = {"cur": cur, "max": mx, "done": done}
    elif isinstance(raw_activities, list):
        for item in raw_activities:
            if isinstance(item, dict):
                title = item.get("title") or item.get("name") or "Activity"
                cur = int(item.get("progress", item.get("cur", 0)) or 0)
                mx = int(item.get("max", 1) or 1)
                done = bool(item.get("complete", item.get("done", mx > 0 and cur >= mx)))
                result["activities"][title] = {"cur": cur, "max": mx, "done": done}

    active_promos = []
    for p in raw_promos:
        is_complete = bool(p.get("complete", False)) or (
            int(p.get("max", 1) or 1) > 0 and int(p.get("progress", 0) or 0) >= int(p.get("max", 1) or 1)
        )
        pts = int(p.get("pointValue") or p.get("points") or 0)
        if not is_complete and pts > 0:
            active_promos.append(p)
    result["promotions"] = active_promos

    result["found"] = bool(result["found_pc"] or result["found_mobile"])
    if result["found_pc"] and result["found_mobile"]:
        result["all_maxed"] = bool(
            result["pc_cur"] >= result["pc_max"] and result["mobile_cur"] >= result["mobile_max"]
        )

    acts = result["activities"]
    if acts:
        primary_four = (
            acts.get("Bing Search", {}).get("done", False)
            and acts.get("Daily Set", {}).get("done", False)
            and acts.get("Edge Browsing", {}).get("done", False)
            and acts.get("Visual Search", {}).get("done", False)
        )
        result["primary_four_done"] = primary_four
        result["all_activities_done"] = primary_four
    elif not active_promos and result["all_maxed"]:
        result["all_activities_done"] = True

    if result["all_maxed"] and result["all_activities_done"] and not active_promos:
        result["status"] = "empty"
    elif not result["found"] and not active_promos and not acts:
        result["status"] = "empty"
    else:
        result["status"] = "active"

    return result
