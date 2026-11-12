"""
Network Guard - Phase 7.3
Handles proxy configuration, IP leak protection, timezone alignment, and IP correlation monitoring.
"""

import re
import requests
from typing import Optional, Dict, List, Tuple
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from core.database import list_accounts, get_db


# Proxy validation regex
HTTP_PROXY_REGEX = re.compile(r'^https?://(?:[^:@/]+(?::[^@/]+)?@)?(?:[^:/?#]+)(?::\d+)?')
SOCKS_PROXY_REGEX = re.compile(r'^socks5://(?:[^:@/]+(?::[^@/]+)?@)?(?:[^:/?#]+)(?::\d+)?')


def validate_proxy_url(proxy_url: str) -> bool:
    """Validate proxy URL format."""
    if not proxy_url:
        return True
    return bool(HTTP_PROXY_REGEX.match(proxy_url) or SOCKS_PROXY_REGEX.match(proxy_url))


def parse_proxy(proxy_url: str) -> Dict:
    """Parse proxy URL into components for Playwright."""
    if not proxy_url:
        return {}
    
    # http://user:pass@host:port or socks5://user:pass@host:port
    match = re.match(
        r'^(?P<scheme>https?|socks5)://(?:(?P<user>[^:@]+)(?::(?P<password>[^@]+))?@)?(?P<host>[^:/?#]+)(?::(?P<port>\d+))?',
        proxy_url
    )
    
    if not match:
        return {}
    
    groups = match.groupdict()
    return {
        "server": f"{groups['scheme']}://{groups['host']}:{groups['port'] or (443 if groups['scheme'] == 'https' else 1080)}",
        "username": groups.get('user'),
        "password": groups.get('password'),
    }


def get_proxy_playwright_args(proxy_url: str) -> List[str]:
    """Get Playwright launch args for proxy."""
    if not proxy_url:
        return []
    
    parsed = parse_proxy(proxy_url)
    if not parsed:
        return []
    
    args = [f"--proxy-server={parsed['server']}"]
    
    # WebRTC IP leak protection
    args.append("--force-webrtc-ip-handling-policy=disable_non_proxied_udp")
    args.append("--disable-webrtc-multiple-routes")
    args.append("--disable-webrtc-hw-encoding")
    
    return args


def get_proxy_context_options(proxy_url: str) -> Dict:
    """Get Playwright browser context options for proxy."""
    if not proxy_url:
        return {}
    
    parsed = parse_proxy(proxy_url)
    if not parsed:
        return {}
    
    options = {
        "proxy": {
            "server": parsed["server"],
        }
    }
    
    if parsed.get("username"):
        options["proxy"]["username"] = parsed["username"]
    if parsed.get("password"):
        options["proxy"]["password"] = parsed["password"]
    
    return options


def get_egress_ip(proxy_url: Optional[str] = None) -> Optional[str]:
    """Get current egress IP (uses proxy if provided)."""
    proxies = None
    if proxy_url:
        proxies = {
            "http": proxy_url,
            "https": proxy_url,
        }
    
    try:
        resp = requests.get("https://api.ipify.org", proxies=proxies, timeout=10)
        if resp.status_code == 200:
            return resp.text.strip()
    except Exception:
        pass
    
    try:
        resp = requests.get("https://ifconfig.me", proxies=proxies, timeout=10)
        if resp.status_code == 200:
            return resp.text.strip()
    except Exception:
        pass
    
    return None


def get_ip_geolocation(ip: str) -> Optional[Dict]:
    """Get geolocation info for an IP (timezone, country, etc)."""
    try:
        resp = requests.get(f"http://ip-api.com/json/{ip}", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                return {
                    "timezone": data.get("timezone"),
                    "country": data.get("countryCode"),
                    "country_name": data.get("country"),
                    "region": data.get("regionName"),
                    "city": data.get("city"),
                    "isp": data.get("isp"),
                    "lat": data.get("lat"),
                    "lon": data.get("lon"),
                }
    except Exception:
        pass
    return None


def align_timezone_to_proxy(proxy_url: Optional[str], default_tz: str = "Asia/Ho_Chi_Minh") -> str:
    """Get timezone that aligns with proxy egress IP."""
    if not proxy_url:
        return default_tz
    
    ip = get_egress_ip(proxy_url)
    if not ip:
        return default_tz
    
    geo = get_ip_geolocation(ip)
    if geo and geo.get("timezone"):
        return geo["timezone"]
    
    return default_tz


def check_ip_correlation() -> List[Dict]:
    """
    Check for IP correlation across accounts.
    Returns list of warnings with accounts sharing same egress IP.
    """
    accounts = list_accounts(enabled_only=True)
    ip_to_accounts: Dict[str, List[Dict]] = {}
    
    for acc in accounts:
        proxy = acc.get("proxy_url")
        if proxy:
            ip = get_egress_ip(proxy)
            if ip:
                if ip not in ip_to_accounts:
                    ip_to_accounts[ip] = []
                ip_to_accounts[ip].append({
                    "account_id": acc["account_id"],
                    "display_name": acc.get("display_name", acc["account_id"]),
                    "proxy_url": proxy,
                })
    
    warnings = []
    for ip, accs in ip_to_accounts.items():
        if len(accs) > 2:
            warnings.append({
                "ip": ip,
                "count": len(accs),
                "accounts": accs,
                "severity": "high" if len(accs) > 3 else "medium",
                "message": f"{len(accs)} accounts sharing egress IP {ip} - high correlation risk"
            })
        elif len(accs) == 2:
            warnings.append({
                "ip": ip,
                "count": 2,
                "accounts": accs,
                "severity": "low",
                "message": f"2 accounts sharing egress IP {ip} - monitor for correlation"
            })
    
    return warnings


def get_account_proxy_config(account_id: str) -> Dict:
    """Get complete proxy configuration for an account including Playwright args."""
    from core.database import get_account
    
    account = get_account(account_id)
    if not account:
        return {}
    
    proxy_url = account.get("proxy_url")
    
    return {
        "proxy_url": proxy_url,
        "playwright_args": get_proxy_playwright_args(proxy_url) if proxy_url else [],
        "context_options": get_proxy_context_options(proxy_url) if proxy_url else {},
        "timezone": account.get("timezone") or align_timezone_to_proxy(proxy_url),
        "egress_ip": get_egress_ip(proxy_url) if proxy_url else None,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Network Guard utilities")
    parser.add_argument("--check-correlation", action="store_true", help="Check IP correlation across accounts")
    parser.add_argument("--get-ip", action="store_true", help="Get current egress IP")
    parser.add_argument("--proxy", type=str, help="Proxy URL for --get-ip")
    parser.add_argument("--align-timezone", action="store_true", help="Get timezone aligned to proxy")
    
    args = parser.parse_args()
    
    if args.check_correlation:
        warnings = check_ip_correlation()
        for w in warnings:
            print(f"[{w['severity'].upper()}] {w['message']}")
            for acc in w['accounts']:
                print(f"  - {acc['account_id']} ({acc['display_name']}) via {acc['proxy_url']}")
    
    if args.get_ip:
        ip = get_egress_ip(args.proxy)
        print(f"Egress IP: {ip}")
    
    if args.align_timezone:
        tz = align_timezone_to_proxy(args.proxy)
        print(f"Aligned timezone: {tz}")