"""
Pure HTTP Mobile App API module for MS Rewards Bot (Phase 9.0 Polyglot).
Handles Mobile OAuth refresh, Daily Check-in (Type 103), App Promotions,
Read to Earn (30/30 pts cap with asyncio.sleep 6-9s), and DAPI /dapi/me progress.
Never imports or launches any browser.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import random
import uuid
from pathlib import Path
from typing import Any, Optional

import requests

from core.state_db import StateDB, atomic_write_json, safe_read_json

CLIENT_ID = "0000000040170455"
SCOPE = "service::prod.rewardsplatform.microsoft.com::MBI_SSL"
TOKEN_URL = "https://login.live.com/oauth20_token.srf"
ACTIVITIES_API_URL = "https://prod.rewardsplatform.microsoft.com/dapi/me/activities"
REWARDS_API_BASE = "https://prod.rewardsplatform.microsoft.com/dapi"

SAPPHIRE_HEADERS = {
    "User-Agent": "BingSapphire/29.5.2110003554 (Linux; U; Android 14; en_US; Pixel 8; Build/UP1A.231005.007)",
    "X-Requested-With": "com.microsoft.bing",
    "X-Rewards-PartnerId": "startapp",
    "X-Rewards-Country": "vn",
    "X-Rewards-Language": "vi",
    "X-Rewards-IsMobile": "true",
    "Accept": "application/json",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
    "Content-Type": "application/json",
}


def get_oauth_file_path(account: str, data_dir: Path) -> Path:
    clean = (account or "default").strip() or "default"
    filename = "mobile_oauth.json" if clean == "default" else f"mobile_oauth_{clean}.json"
    return Path(data_dir) / filename


def _build_sapphire_headers(access_token: str, country: str = "vn") -> dict[str, str]:
    headers = dict(SAPPHIRE_HEADERS)
    headers["Authorization"] = f"Bearer {access_token}"
    headers["X-Rewards-Country"] = country.upper()
    headers["X-Rewards-Language"] = "vi"
    return headers


def refresh_mobile_access_token(oauth_file: Path) -> Optional[str]:
    """
    Return a valid mobile OAuth access_token from cache or refresh via refresh_token.
    Preserves existing refresh_token atomically.
    """
    cache = safe_read_json(oauth_file, default={})
    if not isinstance(cache, dict):
        cache = {}

    now_ts = datetime.datetime.now().timestamp()
    cached_access = cache.get("access_token")
    expires_at = cache.get("expires_at")
    if (
        isinstance(cached_access, str)
        and cached_access.strip()
        and isinstance(expires_at, (int, float))
        and not isinstance(expires_at, bool)
        and now_ts < float(expires_at) - 300
    ):
        return cached_access

    refresh_token = cache.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        print("[API] [Mobile OAuth] Không có refresh_token trong cache.", flush=True)
        return None

    payload = {
        "client_id": CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token.strip(),
        "scope": SCOPE,
    }
    try:
        res = requests.post(TOKEN_URL, data=payload, timeout=15)
        if res.status_code != 200:
            print(f"[API] [Mobile OAuth] Refresh thất bại (HTTP {res.status_code}).", flush=True)
            return None
        tokens = res.json()
        new_access = tokens.get("access_token")
        rotated_refresh = tokens.get("refresh_token") or refresh_token
        expires_in = int(tokens.get("expires_in") or 3600)
        if new_access:
            updated = dict(cache)
            updated.update(
                {
                    "access_token": new_access,
                    "refresh_token": rotated_refresh,
                    "expires_at": now_ts + expires_in,
                    "updated_at": datetime.datetime.now().isoformat(),
                }
            )
            atomic_write_json(oauth_file, updated)
            print("[API] [Mobile OAuth] Làm mới Access Token thành công.", flush=True)
            return str(new_access)
    except Exception as exc:
        print(f"[API] [Mobile OAuth] Lỗi khi làm mới token: {exc}", flush=True)
    return None


def _extract_balance(payload: Any) -> Optional[int]:
    if not isinstance(payload, dict):
        return None
    candidates = [
        payload.get("balance"),
        payload.get("response", {}).get("balance") if isinstance(payload.get("response"), dict) else None,
        payload.get("userStatus", {}).get("balance") if isinstance(payload.get("userStatus"), dict) else None,
    ]
    for val in candidates:
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return int(val)
    return None


def _extract_counter_pair(entry: Any) -> Optional[tuple[int, int]]:
    if not isinstance(entry, dict):
        return None
    cur = entry.get("pointProgress", entry.get("progress", entry.get("cur")))
    mx = entry.get("pointProgressMax", entry.get("max"))
    if cur is None or mx is None:
        return None
    try:
        return int(cur), int(mx)
    except (TypeError, ValueError):
        return None


def fetch_dapi_me_progress(access_token: str, country: str = "vn") -> dict[str, Any]:
    """Fetch and parse user balance, PC/Mobile search counters, promotions, and Bing Star bonus."""
    result: dict[str, Any] = {
        "ok": False,
        "balance": None,
        "pc_cur": 0,
        "pc_max": 90,
        "mobile_cur": 0,
        "mobile_max": 60,
        "found_pc": False,
        "found_mobile": False,
        "bing_star_points": 0,
        "promotions": [],
        "raw": {},
    }
    headers = _build_sapphire_headers(access_token, country)
    url = f"{REWARDS_API_BASE}/me?channel=BingApp&options=613"
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return result
        payload = resp.json()
        body = payload.get("response", payload) if isinstance(payload, dict) else {}
        if not isinstance(body, dict):
            return result
        result["ok"] = True
        result["raw"] = body
        result["balance"] = _extract_balance(body)

        counters = body.get("counters")
        if isinstance(counters, dict):
            for key, val in counters.items():
                kl = str(key).lower()
                pair = _extract_counter_pair(val)
                if not pair:
                    continue
                cur, mx = pair
                if any(tok in kl for tok in ("pc", "desktop")):
                    result["pc_cur"], result["pc_max"] = cur, mx
                    result["found_pc"] = True
                elif any(tok in kl for tok in ("mobile", "mob")):
                    result["mobile_cur"], result["mobile_max"] = cur, mx
                    result["found_mobile"] = True
                if "star" in kl and mx == 2100:
                    result["bing_star_points"] = max(result["bing_star_points"], cur)
        elif isinstance(counters, list):
            for item in counters:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or item.get("title") or item.get("id") or "").lower()
                pair = _extract_counter_pair(item)
                if not pair:
                    continue
                cur, mx = pair
                if any(tok in name for tok in ("pc", "desktop")):
                    result["pc_cur"], result["pc_max"] = cur, mx
                    result["found_pc"] = True
                elif any(tok in name for tok in ("mobile", "mob")):
                    result["mobile_cur"], result["mobile_max"] = cur, mx
                    result["found_mobile"] = True
                if "star" in name and mx == 2100:
                    result["bing_star_points"] = max(result["bing_star_points"], cur)

        promos: list[dict[str, Any]] = []
        for k in ("promotions", "punchCards", "streak", "morePromotions"):
            items = body.get(k)
            if isinstance(items, list):
                promos.extend([p for p in items if isinstance(p, dict)])
        result["promotions"] = promos

        for p in promos:
            attrs = p.get("attributes") if isinstance(p.get("attributes"), dict) else {}
            meta = " ".join(str(p.get(k, "")) for k in ("name", "title", "id", "type")).lower()
            if "star" in meta:
                pair = _extract_counter_pair(p) or _extract_counter_pair(attrs)
                if pair and pair[1] == 2100:
                    result["bing_star_points"] = max(result["bing_star_points"], pair[0])
                    break
    except Exception as exc:
        print(f"[API] [DAPI] Lỗi đọc /dapi/me: {exc}", flush=True)
    return result


def _extract_checkin_promo(promotions: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    keywords = ("checkin", "check-in", "check_in", "daily_streak", "sapphire_checkin", "103")
    for p in promotions:
        meta = " ".join(str(p.get(k, "")) for k in ("name", "title", "promotionName", "offerId", "id", "type")).lower()
        if any(kw in meta for kw in keywords) or "103" in str(p.get("type", "")).lower():
            return p
    return None


def run_daily_checkin(
    access_token: str,
    db: StateDB,
    date_key: str,
    account: str,
    promotions: Optional[list[dict[str, Any]]] = None,
    country: str = "vn",
) -> int:
    """Execute Daily Check-in (Type 103); skip in 0s if already marked done in StateDB."""
    prog = db.get_daily_progress(date_key, account)
    if prog["checkin_done"]:
        print("[API] [Check-in] Đã hoàn tất hôm nay trong SQLite -> bỏ qua (0s).", flush=True)
        return 0

    headers = _build_sapphire_headers(access_token, country)
    promo = _extract_checkin_promo(promotions or [])
    offer_id = "sapphire_checkin"
    if promo:
        attrs = promo.get("attributes") if isinstance(promo.get("attributes"), dict) else {}
        if attrs.get("complete") in (True, "True", "true"):
            db.update_daily_fields(date_key, account, checkin_done=True)
            print("[API] [Check-in] Server xác nhận đã điểm danh trước đó.", flush=True)
            return 0
        offer_id = str(attrs.get("offerid") or attrs.get("offerId") or promo.get("id") or promo.get("name") or offer_id)

    before_bal = fetch_dapi_me_progress(access_token, country).get("balance")
    payload = {
        "id": str(uuid.uuid4()),
        "type": 103,
        "amount": 1,
        "country": country.lower(),
        "risk_context": {"app": "BingSapphire", "platform": "Android"},
        "attributes": {"offerid": offer_id},
    }
    try:
        resp = requests.post(ACTIVITIES_API_URL, json=payload, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"[API] [Check-in] HTTP {resp.status_code}", flush=True)
            return 0
        data = resp.json()
        awarded = 0
        act = (data.get("response") or data).get("activity") if isinstance(data, dict) else None
        if isinstance(act, dict):
            for k in ("p", "points", "pointValue", "awarded"):
                v = act.get(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    awarded = int(v)
                    break
        after_bal = _extract_balance(data)
        if before_bal is not None and after_bal is not None and after_bal > before_bal:
            awarded = max(awarded, after_bal - before_bal)
        db.update_daily_fields(date_key, account, checkin_done=True)
        print(f"[API] [Check-in] Hoàn tất Type 103 (+{awarded} pts).", flush=True)
        return awarded
    except Exception as exc:
        print(f"[API] [Check-in] Lỗi: {exc}", flush=True)
        return 0


async def run_app_promotions(
    access_token: str,
    promotions: list[dict[str, Any]],
    country: str = "vn",
) -> int:
    """Claim any incomplete mobile app promotions from /dapi/me."""
    headers = _build_sapphire_headers(access_token, country)
    claimed = 0
    for promo in promotions:
        mx = int(promo.get("max", 1) or 1)
        cur = int(promo.get("progress", 0) or 0)
        is_complete = bool(promo.get("complete", False)) or (mx > 0 and cur >= mx)
        pts = int(promo.get("pointValue") or promo.get("points") or 0)
        if is_complete or pts <= 0:
            continue
        promo_id = promo.get("id") or promo.get("name")
        if not promo_id:
            continue
        payload = {
            "amount": 1,
            "attributes": {"offerid": promo_id},
            "country": country.lower(),
            "id": str(uuid.uuid4()),
            "type": promo.get("type", 101),
            "channel": "BingApp",
        }
        try:
            resp = requests.post(ACTIVITIES_API_URL, json=payload, headers=headers, timeout=15)
            if resp.status_code == 200:
                claimed += 1
                print(f"[API] [App Promo] Claimed {promo_id} (+{pts} pts).", flush=True)
        except Exception:
            pass
        await asyncio.sleep(random.uniform(1.5, 3.0))
    return claimed


async def run_read_to_earn(
    access_token: str,
    db: StateDB,
    date_key: str,
    account: str,
    country: str = "vn",
) -> int:
    """Execute Read to Earn (max 30/30 pts, 6-9s asyncio.sleep between articles); skip in 0s if done."""
    prog = db.get_daily_progress(date_key, account)
    if prog["read_to_earn_done"]:
        print("[API] [Read to Earn] Đã đạt 30/30 hôm nay trong SQLite -> bỏ qua (0s).", flush=True)
        return 0

    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "Bing/28.8.420227017 (Pixel 7; Android 14) Sapphire/1.0",
        "Content-Type": "application/json",
        "X-Rewards-Country": country.lower(),
        "X-Rewards-Language": "vi",
    }

    last_balance: Optional[int] = None
    consecutive_no_increase = 0
    consecutive_zero_gain_200 = 0
    articles_read = 0
    earned_in_session = 0

    for idx in range(1, 11):
        if earned_in_session >= 30:
            break
        payload = {
            "amount": 1,
            "country": country.lower(),
            "id": str(uuid.uuid4()),
            "type": 101,
            "attributes": {"offerid": "ENUS_readarticle3_30points"},
        }
        try:
            resp = requests.post(ACTIVITIES_API_URL, json=payload, headers=headers, timeout=15)
        except Exception as exc:
            print(f"[API] [Read to Earn] Lỗi bài {idx}/10: {exc}", flush=True)
            consecutive_no_increase += 1
            if consecutive_no_increase >= 2:
                break
            continue

        if resp.status_code == 200:
            data = resp.json() if resp.content else {}
            new_balance = _extract_balance(data)
            points_awarded = 0
            resp_obj = data.get("response") if isinstance(data, dict) else None
            if isinstance(resp_obj, dict) and isinstance(resp_obj.get("activity"), dict):
                points_awarded = int(resp_obj["activity"].get("p", 0) or 0)

            increased = False
            if points_awarded > 0:
                increased = True
                earned_in_session += points_awarded
            elif last_balance is not None and new_balance is not None and new_balance > last_balance:
                increased = True
                earned_in_session += new_balance - last_balance

            if new_balance is not None:
                last_balance = new_balance

            if increased:
                consecutive_no_increase = 0
                consecutive_zero_gain_200 = 0
                articles_read += 1
                print(f"[API] [Read to Earn] Bài {idx}/10 (+3 pts) -> Tổng: {new_balance}", flush=True)
            else:
                consecutive_no_increase += 1
                consecutive_zero_gain_200 += 1
                print(f"[API] [Read to Earn] Bài {idx}/10 không tăng điểm ({consecutive_zero_gain_200}/2).", flush=True)

            if earned_in_session >= 30 or consecutive_zero_gain_200 >= 2:
                break
            if idx < 10:
                await asyncio.sleep(random.uniform(6.0, 9.0))
        elif resp.status_code == 401:
            print("[API] [Read to Earn] Token hết hạn (HTTP 401).", flush=True)
            break
        else:
            consecutive_no_increase += 1
            if consecutive_no_increase >= 2:
                break

    if articles_read >= 10 or earned_in_session >= 30 or consecutive_zero_gain_200 >= 2:
        db.update_daily_fields(date_key, account, read_to_earn_done=True)
    return earned_in_session


async def run_all_api_tasks(
    db: StateDB,
    date_key: str,
    account: str,
    data_dir: Path,
    country: str = "vn",
) -> dict[str, Any]:
    """Execute pure-HTTP tasks and return live DAPI progress summary."""
    oauth_file = get_oauth_file_path(account, data_dir)
    access_token = refresh_mobile_access_token(oauth_file)
    if not access_token:
        return {"dapi_ok": False, "balance": None, "found_pc": False, "found_mobile": False}

    dapi_before = fetch_dapi_me_progress(access_token, country=country)
    promotions = dapi_before.get("promotions") or []

    checkin_pts = run_daily_checkin(access_token, db, date_key, account, promotions=promotions, country=country)
    promo_count = await run_app_promotions(access_token, promotions, country=country)
    rte_pts = await run_read_to_earn(access_token, db, date_key, account, country=country)

    dapi_after = fetch_dapi_me_progress(access_token, country=country)
    if not dapi_after.get("ok"):
        dapi_after = dapi_before

    if dapi_after.get("ok"):
        updates: dict[str, Any] = {}
        if dapi_after.get("balance") is not None:
            updates["last_points"] = int(dapi_after["balance"])
        if dapi_after.get("found_pc"):
            updates["pc_cur"] = dapi_after["pc_cur"]
            updates["pc_max"] = dapi_after["pc_max"]
        if dapi_after.get("found_mobile"):
            updates["mobile_cur"] = dapi_after["mobile_cur"]
            updates["mobile_max"] = dapi_after["mobile_max"]
        if dapi_after.get("bing_star_points"):
            db.update_bing_star_monotonic(date_key, account, int(dapi_after["bing_star_points"]))
        if updates:
            db.update_daily_fields(date_key, account, **updates)

    return {
        "dapi_ok": bool(dapi_after.get("ok")),
        "balance": dapi_after.get("balance"),
        "found_pc": bool(dapi_after.get("found_pc")),
        "found_mobile": bool(dapi_after.get("found_mobile")),
        "pc_cur": int(dapi_after.get("pc_cur", 0)),
        "pc_max": int(dapi_after.get("pc_max", 90)),
        "mobile_cur": int(dapi_after.get("mobile_cur", 0)),
        "mobile_max": int(dapi_after.get("mobile_max", 60)),
        "bing_star_points": int(dapi_after.get("bing_star_points", 0)),
        "checkin_pts": checkin_pts,
        "promo_claimed": promo_count,
        "rte_pts": rte_pts,
    }
