"""
Proactive Remote (Telegram) & Local (Windows Toast/Balloon) Alerting.
Phase 8.5 - MS Rewards Bot (Zero-spam, per-day deduplicated).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from core.state_store import StateStore
from core.tracker import _log_debug, _log_info, _log_warning

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = ROOT_DIR / ".env"
REDEMPTION_THRESHOLDS: tuple[int, ...] = (1500, 3400)


def _load_dotenv_vars(env_path: Path = DEFAULT_ENV_FILE) -> dict[str, str]:
    """Lightweight .env parser without external dependencies."""
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    try:
        for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                values[key] = val
    except Exception as exc:
        _log_debug(f"[Notifier] Không thể đọc {env_path.name}: {exc}")
    return values


def get_telegram_credentials(env_path: Path = DEFAULT_ENV_FILE) -> tuple[str, str]:
    """Return (bot_token, chat_id) from environment variables or .env."""
    dotenv = _load_dotenv_vars(env_path)
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or dotenv.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or dotenv.get("TELEGRAM_CHAT_ID", "")
    return token.strip(), chat_id.strip()


def send_telegram_message(
    text: str,
    bot_token: str,
    chat_id: str,
    timeout: int = 5,
) -> bool:
    """Send a Telegram message via Bot API using urllib.request with 5s timeout."""
    if not bot_token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                body = json.loads(resp.read().decode("utf-8", errors="ignore") or "{}")
                return bool(body.get("ok", True))
            return False
    except Exception as exc:
        _log_warning(f"[Notifier] Gửi Telegram thất bại: {exc}")
        return False


def send_windows_toast(title: str, message: str) -> bool:
    """
    Non-blocking Windows Toast / NotifyIcon balloon fallback via PowerShell.
    Never blocks the pipeline.
    """
    if sys.platform != "win32":
        _log_info(f"[Notifier][Local Fallback] {title}: {message}")
        return True

    safe_title = title.replace("'", "''").replace('"', '`"')
    safe_msg = message.replace("'", "''").replace('"', '`"')
    ps_script = (
        "try { "
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
        "$tpl = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        "$nodes = $tpl.GetElementsByTagName('text'); "
        f"$nodes.Item(0).AppendChild($tpl.CreateTextNode('{safe_title}')) | Out-Null; "
        f"$nodes.Item(1).AppendChild($tpl.CreateTextNode('{safe_msg}')) | Out-Null; "
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($tpl); "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('MS Rewards Bot').Show($toast); "
        "} catch { "
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$n = New-Object System.Windows.Forms.NotifyIcon; "
        "$n.Icon = [System.Drawing.SystemIcons]::Information; "
        "$n.Visible = $true; "
        f"$n.ShowBalloonTip(5000, '{safe_title}', '{safe_msg}', [System.Windows.Forms.ToolTipIcon]::Warning); "
        "Start-Sleep -Milliseconds 500; $n.Dispose(); "
        "}"
    )
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        return True
    except Exception as exc:
        _log_debug(f"[Notifier] Không thể bật Windows Toast: {exc}")
        return False


class Notifier:
    """Actionable event dispatcher with per-day deduplication via StateStore."""

    def __init__(
        self,
        account_id: str = "default",
        state_store: Optional[StateStore] = None,
        env_path: Path = DEFAULT_ENV_FILE,
        telegram_sender=None,
        toast_sender=None,
    ):
        self.account_id = account_id
        self.state_store = state_store
        self.env_path = env_path
        self._telegram_sender = telegram_sender or send_telegram_message
        self._toast_sender = toast_sender or send_windows_toast

    def notify(
        self,
        event_key: str,
        title: str,
        message: str,
        force: bool = False,
    ) -> bool:
        """
        Dispatch an alert once per day per `event_key`.
        Returns True if dispatched, False if suppressed as duplicate or failed.
        """
        if not force and self.state_store is not None:
            if self.state_store.has_alert_sent_today(event_key):
                _log_debug(f"[Notifier] Bỏ qua cảnh báo trùng trong ngày: {event_key}")
                return False

        full_title = f"[{self.account_id}] {title}"
        bot_token, chat_id = get_telegram_credentials(self.env_path)

        dispatched = False
        if bot_token and chat_id:
            dispatched = bool(self._telegram_sender(f"{full_title}\n{message}", bot_token, chat_id, 5))
        if not dispatched:
            dispatched = bool(self._toast_sender(full_title, message))

        if dispatched and self.state_store is not None:
            self.state_store.mark_alert_sent_today(event_key)
        return dispatched

    def notify_auth_expired(self, reason: str) -> bool:
        return self.notify(
            event_key="auth_expired",
            title="Phiên đăng nhập Microsoft đã hết hạn",
            message=f"Tài khoản '{self.account_id}' cần đăng nhập lại: {reason}",
        )

    def notify_account_restricted(self, reason: str) -> bool:
        return self.notify(
            event_key="account_restricted",
            title="Cảnh báo tài khoản (CAPTCHA / Unusual Activity)",
            message=f"Phát hiện giới hạn trên tài khoản '{self.account_id}': {reason}",
        )

    def notify_consecutive_failures(self, count: int) -> bool:
        if count < 3:
            return False
        return self.notify(
            event_key="consecutive_failures",
            title="Pipeline thất bại 3 lần liên tiếp",
            message=f"Tài khoản '{self.account_id}' đã gặp {count} phiên lỗi liên tiếp. Vui lòng kiểm tra logs.",
        )

    def check_and_notify_milestones(self, balance: int) -> list[int]:
        triggered: list[int] = []
        for threshold in REDEMPTION_THRESHOLDS:
            if balance >= threshold:
                sent = self.notify(
                    event_key=f"milestone_{threshold}",
                    title=f"Đạt mốc đổi quà {threshold:,} pts!",
                    message=f"Tài khoản '{self.account_id}' hiện có {balance:,} pts (vượt mốc {threshold:,} pts).",
                )
                if sent:
                    triggered.append(threshold)
        return triggered
