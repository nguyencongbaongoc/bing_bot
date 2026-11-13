"""
Module tích hợp tính năng "Read to Earn" (30 điểm/ngày - 10 bài báo x 3 điểm)
thông qua Bing Mobile App OAuth API chính thức, không cần giả lập Android.
Phase 7.2: Multi-account support with SQLite database.
"""

import datetime
import json
import logging
import random
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
from core.database import get_oauth_tokens, save_oauth_tokens, save_dapi_debug_cache

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


def get_mobile_access_token(account_id: str = "acc_primary", page: Optional[Page] = None) -> Optional[str]:
    if oauth_cache:
        refresh_token = oauth_cache.get("refresh_token")
        if refresh_token:
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
                new_access = tokens.get("access_token")
                new_refresh = tokens.get("refresh_token", refresh_token)
                expires_in = tokens.get("expires_in")
                expires_at = None
                if expires_in:
                    expires_at = datetime.datetime.now() + datetime.timedelta(seconds=expires_in)
                if new_access:
                    save_oauth_tokens(
                        account_id=account_id,
                        access_token=new_access,
                        refresh_token=new_refresh,
                        expires_at=expires_at.isoformat() if expires_at else None,
                        client_id=CLIENT_ID
                    )
                    _log_info("[Mobile OAuth] Làm mới Access Token thành công.")
                    return new_access
            else:
                _log_warning(
                    f"[Mobile OAuth] Làm mới token thất bại (HTTP {res.status_code}). Sẽ thử xác thực qua trình duyệt."
                )

    # 2. Xác thực lần đầu hoặc khi refresh_token hết hạn bằng phiên đăng nhập trình duyệt
    if page is None:
        _log_warning("[Mobile OAuth] Không có phiên trình duyệt để lấy authorization code mới.")
        return None

    auth_page = None
    try:
        _log_info("[Mobile OAuth] Đang mở luồng xác thực OAuth App Bing qua phiên Microsoft hiện tại...")
        try:
            auth_page = page.context.new_page()
        except Exception:
            auth_page = page

        auth_page.goto(AUTH_URL, timeout=30000, wait_until="domcontentloaded")

        code = None
        start_wait = time.time()
        while time.time() - start_wait < 25:
            curr_url = auth_page.url
            if "code=" in curr_url:
                parsed = urllib.parse.urlparse(curr_url)
                qs = urllib.parse.parse_qs(parsed.query)
                if "code" in qs and qs["code"]:
                    code = qs["code"][0]
                    break
                if parsed.fragment:
                    frag_qs = urllib.parse.parse_qs(parsed.fragment)
                    if "code" in frag_qs and frag_qs["code"]:
                        code = frag_qs["code"][0]
                        break

            # Tự động nhấn nút xác nhận nếu hiển thị (Yes / Accept / Đồng ý)
            try:
                for sel in [
                    "#acceptButton",
                    "#idSIButton9",
                    "input[type='submit']",
                    "button[type='submit']",
                    "button:has-text('Yes')",
                    "button:has-text('Accept')",
                    "button:has-text('Đồng ý')",
                ]:
                    btn = auth_page.locator(sel).first
                    if btn.is_visible(timeout=300):
                        btn.click()
                        auth_page.wait_for_timeout(1000)
                        break
            except Exception:
                pass

            auth_page.wait_for_timeout(500)

        if not code:
            _log_warning("[Mobile OAuth] Không trích xuất được authorization code từ URL.")
            return None

        # Đổi code lấy access_token & refresh_token
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
            expires_in = tokens.get("expires_in")
            expires_at = None
            if expires_in:
                expires_at = datetime.datetime.now() + datetime.timedelta(seconds=expires_in)
            if access_token:
                save_oauth_tokens(
                    account_id=account_id,
                    access_token=access_token,
                    refresh_token=refresh_token,
                    expires_at=expires_at.isoformat() if expires_at else None,
                    client_id=CLIENT_ID
                )
                _log_info("[Mobile OAuth] Lấy OAuth token mới thành công và đã lưu vào database!")
                return access_token
        else:
            _log_warning(f"[Mobile OAuth] Đổi code thất bại (HTTP {res.status_code}): {res.text[:100]}")
            return None

    except Exception as e:
        _log_warning(f"[Mobile OAuth] Lỗi trong quá trình lấy OAuth token: {e}")
        return None
    finally:
        if auth_page and auth_page != page:
            try:
                auth_page.close()
            except Exception:
                pass

    return None


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


def _get_checkin_promotion(access_token: str, country: str = "vn") -> dict:
    headers = _build_sapphire_headers(access_token, country)
    # Primary DAPI endpoint with options=613 as per protocol
    endpoints = (
        f"{REWARDS_API_BASE}/me?channel=BingApp&options=613",
        f"{REWARDS_API_BASE}/me?channel=StartApp&options=613",
        f"{REWARDS_API_BASE}/me/promotions?channel=BingApp",
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
        
        # Debug JSON logging when --debug flag is active
        if logger.isEnabledFor(logging.DEBUG):
            try:
                raw_json_str = json.dumps(raw_json, ensure_ascii=False)
                save_dapi_debug_cache(account_id, "BingApp", raw_json_str)
                _log_info(f"[DEBUG] DAPI raw JSON saved to database for {account_id}")
            except Exception as e:
                _log_debug(f"[DEBUG] Failed to save debug JSON to database: {e}")
        
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


def solve_mobile_app_daily_checkin(page: Optional[Page] = None, country: str = "vn", account_id: str = "acc_primary") -> int:
    """
    Thực hiện Điểm danh 7 ngày (Daily Check-in - type 103) qua Bing Mobile App OAuth API.
    Trích xuất promotion_id/checkinDay động, đối soát biến động điểm thực tế trước và sau khi claim.
    Trả về số điểm nhận thêm (nếu có).
    """
    logger.info("--- BẮT ĐẦU: DAILY CHECK-IN (BING MOBILE APP API - TYPE 103) ---")
    access_token = get_mobile_access_token(account_id, page)
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

    if delta > 0:
        logger.info(f"[App Check-in] [✓] Đã kích hoạt điểm danh thành công (+{delta} pts)!")
        return delta

    logger.info("[App Check-in] [*] Điểm danh không tăng điểm (đã điểm danh trước đó hoặc cần claim qua Mobile UI fallback).")
    return 0


claim_daily_checkin = solve_mobile_app_daily_checkin


def solve_mobile_app_promotions(page: Optional[Page] = None, country: str = "vn", account_id: str = "acc_primary") -> int:
    """
    Quét danh sách Promotions trên Bing Mobile App qua GET /dapi/me?channel=BingApp&options=613.
    Kiểm tra các nhiệm vụ App chưa hoàn thành và claim qua API nếu có.
    """
    logger.info("--- BẮT ĐẦU: QUÉT APP PROMOTIONS (BING MOBILE APP API) ---")
    access_token = get_mobile_access_token(account_id, page)
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
            
            # Debug JSON logging when --debug flag is active
            if logger.isEnabledFor(logging.DEBUG):
                try:
                    raw_json_str = json.dumps(data, ensure_ascii=False)
                    save_dapi_debug_cache(account_id, "BingApp", raw_json_str)
                    _log_info(f"[DEBUG] DAPI raw JSON saved to database for {account_id}")
                except Exception as e:
                    _log_debug(f"[DEBUG] Failed to save debug JSON to database: {e}")

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


def solve_mobile_app_read_to_earn(page: Optional[Page] = None, country: str = "vn", account_id: str = "acc_primary") -> int:
    """
    Thực hiện nhiệm vụ "Read to Earn" trên App Bing Mobile (tối đa 10 bài x 3 điểm = 30 điểm/ngày).
    Gọi trực tiếp API https://prod.rewardsplatform.microsoft.com/dapi/me/activities.
    """
    _log_info("--- BẮT ĐẦU: READ TO EARN (BING MOBILE APP API) ---")
    access_token = get_mobile_access_token(account_id, page)
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
    articles_read = 0
    total_points_gained = 0

    for i in range(1, 11):
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
                total_points_gained += points_awarded
            elif last_balance is not None and new_balance is not None and new_balance > last_balance:
                increased = True
                total_points_gained += (new_balance - last_balance)

            if increased:
                consecutive_no_increase = 0
                articles_read += 1
                _log_info(
                    f"[Read to Earn] Đã đọc bài báo {i}/10 (+3 pts) -> Tổng điểm: {new_balance if new_balance is not None else 'N/A'}"
                )
            else:
                consecutive_no_increase += 1
                _log_info(
                    f"[Read to Earn] Bài báo {i}/10 không tăng thêm điểm (Lần {consecutive_no_increase}/2)."
                )

            if new_balance is not None:
                last_balance = new_balance

            # Dừng nếu 2 lần liên tiếp không tăng điểm (đã max 30/30)
            if consecutive_no_increase >= 2:
                _log_info("[Read to Earn] Hôm nay đã đạt tối đa 30/30 điểm đọc báo!")
                break

            # Nghỉ ngẫu nhiên giữa các bài đọc (12.0 - 22.0 giây)
            if i < 10 and consecutive_no_increase < 2:
                sleep_time = random.uniform(12.0, 22.0)
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

    _log_info(f"[Read to Earn] Kết thúc: Đã hoàn thành {articles_read} bài (+{total_points_gained} pts).")
    return total_points_gained
