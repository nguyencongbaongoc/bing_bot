"""
Account Onboarding - Phase 7.3
1-click interactive OAuth onboarding for new accounts.
Launches visible browser, intercepts OAuth redirect, exchanges code for tokens, stores in SQLite.
"""

import urllib.parse
import uuid
from typing import Optional
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from patchright.sync_api import sync_playwright
from core.database import upsert_account, save_oauth_tokens, get_db
from core.fingerprint import inject_stealth_fingerprint
from core.mobile_app_api import (
    CLIENT_ID, SCOPE, REDIRECT_URI, AUTH_URL, TOKEN_URL,
    _build_sapphire_headers, _get_balance
)


def launch_mobile_oauth_onboarding(
    account_id: str,
    display_name: str = "",
    proxy_url: Optional[str] = None,
    device_profile: str = "Pixel 7",
    headless: bool = False,
):
    """
    Launch visible browser for manual login, intercept OAuth redirect,
    exchange code for tokens, and store in database.
    
    Args:
        account_id: Unique identifier for the account
        display_name: Human-readable name
        proxy_url: Optional proxy (http:// or socks5://)
        device_profile: Mobile device to emulate
        headless: If True, run headless (for testing)
    """
    # Ensure account exists in database
    profile_base = ROOT_DIR / "profiles" / account_id
    desktop_profile = profile_base / "desktop"
    mobile_profile = profile_base / "mobile"
    desktop_profile.mkdir(parents=True, exist_ok=True)
    mobile_profile.mkdir(parents=True, exist_ok=True)
    
    upsert_account(
        account_id=account_id,
        display_name=display_name or account_id,
        profile_dir=str(profile_base.relative_to(ROOT_DIR)),
        proxy_url=proxy_url,
        enabled=1,
    )
    
    # Build OAuth URL with state parameter containing account_id
    state_data = {"account_id": account_id, "nonce": str(uuid.uuid4())[:8]}
    import base64
    state = base64.urlsafe_b64encode(str(state_data).encode()).decode()
    
    oauth_url = f"{AUTH_URL}&state={state}"
    
    with sync_playwright() as p:
        # Launch visible browser with mobile emulation
        mobile_device = dict(p.devices[device_profile])
        mobile_device.pop("default_browser_type", None)
        
        # Standardize user agent
        ua = mobile_device.get("user_agent", "")
        if "EdgA" not in ua:
            chrome_ver = "131.0.0.0"
            mobile_device["user_agent"] = f"Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_ver} Mobile Safari/537.36 EdgA/{chrome_ver}"
        
        args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-gpu-vsync",
            "--frame-rate-limit=15",
            "--disable-background-timer-throttling",
        ]
        
        if proxy_url:
            args.append(f"--proxy-server={proxy_url}")
        
        extra_headers = {
            "Sec-CH-UA-Platform": '"Android"',
            "Sec-CH-UA-Mobile": "?1",
            "Sec-CH-UA-Model": f'"{device_profile}"',
        }
        
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(mobile_profile),
            channel="msedge",
            headless=headless,
            args=args,
            **mobile_device,
            locale="vi-VN",
            timezone_id="Asia/Ho_Chi_Minh",
            extra_http_headers=extra_headers,
        )
        
        # Apply fingerprint
        inject_stealth_fingerprint(context, account_id)
        
        page = context.pages[0] if context.pages else context.new_page()
        
        # Navigate to OAuth URL
        print(f"[Onboarding] Opening OAuth URL for {account_id}...")
        page.goto(oauth_url, wait_until="domcontentloaded", timeout=60000)
        
        # Wait for redirect with code
        print("[Onboarding] Waiting for user to complete login and authorize...")
        print("[Onboarding] Please complete login in the browser window...")
        
        code = None
        start_wait = time.time()
        max_wait = 300  # 5 minutes
        
        while time.time() - start_wait < max_wait:
            try:
                curr_url = page.url
                if "code=" in curr_url:
                    parsed = urllib.parse.urlparse(curr_url)
                    qs = urllib.parse.parse_qs(parsed.query)
                    if "code" in qs and qs["code"]:
                        code = qs["code"][0]
                        print("[Onboarding] Authorization code received!")
                        break
                
                # Check fragment too
                if page.url.startswith("https://login.live.com/oauth20_desktop.srf"):
                    # Might have fragment
                    content = page.content()
                    if "code=" in content:
                        # Try to extract from page
                        pass
                        
            except Exception as e:
                print(f"[Onboarding] Error checking URL: {e}")
            
            # Auto-click consent buttons if visible
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
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=500):
                        btn.click()
                        page.wait_for_timeout(1000)
                        break
            except Exception:
                pass
            
            page.wait_for_timeout(1000)
        
        if not code:
            print("[Onboarding] Timeout waiting for authorization code")
            context.close()
            return False
        
        # Exchange code for tokens
        print("[Onboarding] Exchanging code for tokens...")
        import requests
        import datetime as dt
        
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
                expires_at = dt.datetime.now() + dt.timedelta(seconds=expires_in)
            
            if access_token and refresh_token:
                save_oauth_tokens(
                    account_id=account_id,
                    access_token=access_token,
                    refresh_token=refresh_token,
                    expires_at=expires_at.isoformat() if expires_at else None,
                    client_id=CLIENT_ID
                )
                print("[Onboarding] Tokens saved successfully!")
                
                # Verify by checking balance
                headers = _build_sapphire_headers(access_token)
                balance = _get_balance(access_token)
                if balance:
                    print(f"[Onboarding] Verified - Current balance: {balance:,.0f} pts")
                
                context.close()
                return True
        
        print(f"[Onboarding] Token exchange failed: {res.status_code} {res.text[:200]}")
        context.close()
        return False


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Onboard new MS Rewards account")
    parser.add_argument("--account-id", required=True, help="Account ID (e.g., acc_02)")
    parser.add_argument("--display-name", default="", help="Display name")
    parser.add_argument("--proxy", default=None, help="Proxy URL")
    parser.add_argument("--device", default="Pixel 7", help="Mobile device profile")
    parser.add_argument("--headless", action="store_true", help="Run headless")
    
    args = parser.parse_args()
    
    success = launch_mobile_oauth_onboarding(
        account_id=args.account_id,
        display_name=args.display_name,
        proxy_url=args.proxy,
        device_profile=args.device,
        headless=args.headless,
    )
    
    sys.exit(0 if success else 1)