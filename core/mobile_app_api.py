"""
Module tích hợp tính năng "Read to Earn" (30 điểm/ngày - 10 bài báo x 3 điểm)
thông qua Bing Mobile App OAuth API chính thức, không cần giả lập Android.
"""

import datetime
import json
import random
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Optional

import requests
from playwright.sync_api import Page

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

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
OAUTH_FILE = DATA_DIR / "mobile_oauth.json"


def get_mobile_access_token(page: Optional[Page] = None) -> Optional[str]:
    """
    Lấy hoặc làm mới Mobile Access Token cho Bing Mobile App API.
    1. Nếu data/mobile_oauth.json đã có refresh_token -> làm mới qua TOKEN_URL.
    2. Nếu chưa có hoặc refresh hết hạn -> dùng Playwright page mở AUTH_URL lấy code và đổi token.
    """
    # 1. Thử làm mới từ refresh_token sẵn có
    if OAUTH_FILE.exists():
        try:
            with open(OAUTH_FILE, "r", encoding="utf-8") as f:
                oauth_cache = json.load(f)
            
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
                    if new_access:
                        oauth_data = {
                            "access_token": new_access,
                            "refresh_token": new_refresh,
                            "updated_at": datetime.datetime.now().isoformat(),
                        }
                        with open(OAUTH_FILE, "w", encoding="utf-8") as f:
                            json.dump(oauth_data, f, indent=2)
                        _log_info("[Mobile OAuth] Làm mới Access Token thành công.")
                        return new_access
                else:
                    _log_warning(
                        f"[Mobile OAuth] Làm mới token thất bại (HTTP {res.status_code}). Sẽ thử xác thực qua trình duyệt."
                    )
        except Exception as e:
            _log_warning(f"[Mobile OAuth] Không thể đọc cache token: {e}")

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
            if access_token:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                oauth_data = {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "updated_at": datetime.datetime.now().isoformat(),
                }
                with open(OAUTH_FILE, "w", encoding="utf-8") as f:
                    json.dump(oauth_data, f, indent=2)
                _log_info("[Mobile OAuth] Lấy OAuth token mới thành công và đã lưu vào data/mobile_oauth.json!")
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


def solve_mobile_app_daily_checkin(page: Optional[Page] = None, country: str = "vn") -> int:
    """
    Thực hiện Điểm danh 7 ngày (Daily Check-in - type 103) qua Bing Mobile App OAuth API.
    Trả về số điểm nhận thêm (nếu có).
    """
    logger.info("--- BẮT ĐẦU: DAILY CHECK-IN (BING MOBILE APP API - TYPE 103) ---")
    access_token = get_mobile_access_token(page)
    if not access_token:
        logger.warning("[App Check-in] Không lấy được OAuth access_token, bỏ qua bước này.")
        return 0

    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "Bing/28.8.420227017 (Pixel 7; Android 14) Sapphire/1.0",
        "Content-Type": "application/json",
        "X-Rewards-Country": country.lower(),
        "X-Rewards-Language": "vi",
    }

    payload = {
        "amount": 1,
        "attributes": {},
        "id": str(uuid.uuid4()),
        "type": 103,
        "country": country.lower(),
        "risk_context": {},
        "channel": "BingApp",
    }

    try:
        resp = requests.post(
            ACTIVITIES_API_URL,
            json=payload,
            headers=headers,
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            balance = data.get("response", {}).get("balance")
            logger.info(f"[App Check-in] Gửi tín hiệu điểm danh App Bing (type 103) thành công! Balance hiện tại: {balance}")
            return 1
        else:
            logger.debug(f"[App Check-in] Phản hồi HTTP {resp.status_code}: {resp.text[:150]}")
    except Exception as e:
        logger.warning(f"[App Check-in] Lỗi khi gửi request điểm danh API: {e}")

    return 0


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

    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "Bing/28.8.420227017 (Pixel 7; Android 14) Sapphire/1.0",
        "Content-Type": "application/json",
        "X-Rewards-Country": country.lower(),
        "X-Rewards-Language": "vi",
    }

    url = "https://prod.rewardsplatform.microsoft.com/dapi/me?channel=BingApp&options=613"
    claimed_count = 0

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            resp_body = data.get("response", {})
            promotions = resp_body.get("promotions") or []
            
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


def solve_mobile_app_read_to_earn(page: Optional[Page] = None, country: str = "vn") -> int:
    """
    Thực hiện nhiệm vụ "Read to Earn" trên App Bing Mobile (tối đa 10 bài x 3 điểm = 30 điểm/ngày).
    Gọi trực tiếp API https://prod.rewardsplatform.microsoft.com/dapi/me/activities.
    """
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
