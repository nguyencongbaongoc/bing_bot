"""
Master Stability & Production Fixes Verification Suite (tests/test_master_stability_fixes.py).

Covers all 12 stability fixes across:
1. OAuth & Read to Earn (`core/mobile_app_api.py`, `core/state_store.py`)
2. Profile & Process Guards (`core/browser_manager.py`, `runner.py`)
3. Dashboard, Points Sanity, Cooldown & Bing Star (`runner.py`, `core/tracker.py`, `core/cooldown_controller.py`, `core/activities.py`)
4. Quests, Keep Earning, Visual Search Telemetry & Edge Browsing (`core/activities.py`, `core/browser_manager.py`, `runner.py`)
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import runner
from core import activities, browser_manager, mobile_app_api, tracker
from core.cooldown_controller import CooldownController
from core.state_store import EDGE_BROWSING_CAP_SECONDS, StateStore


# ==============================================================================
# 1. OAuth & Read to Earn Tests
# ==============================================================================
class TestOAuthAndReadToEarnFixes(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmpdir.name).resolve()
        self._orig_oauth_file = mobile_app_api.OAUTH_FILE
        self._orig_root_dir = mobile_app_api.ROOT_DIR
        self.oauth_file = self.tmp_path / "data" / "mobile_oauth.json"
        mobile_app_api.set_oauth_file(self.oauth_file)
        mobile_app_api.ROOT_DIR = self.tmp_path
        mobile_app_api.clear_oauth_memory_cache()
        mobile_app_api.reset_oauth_circuit_breaker()

    def tearDown(self):
        mobile_app_api.clear_oauth_memory_cache()
        mobile_app_api.reset_oauth_circuit_breaker()
        mobile_app_api.OAUTH_FILE = self._orig_oauth_file
        mobile_app_api.ROOT_DIR = self._orig_root_dir
        self._tmpdir.cleanup()

    def test_1a_oauth_ttl_cache_hit_zero_http_calls(self):
        self.oauth_file.parent.mkdir(parents=True, exist_ok=True)
        self.oauth_file.write_text(
            json.dumps({
                "access_token": "valid_cached_token_abc",
                "refresh_token": "refresh_abc",
                "expires_at": time.time() + 3600,
            }),
            encoding="utf-8",
        )

        with patch.object(mobile_app_api.requests, "post") as mock_post:
            tok = mobile_app_api.get_mobile_access_token(page=None)

        self.assertEqual(tok, "valid_cached_token_abc")
        mock_post.assert_not_called()

    def test_1b_oauth_atomic_write_rotated_refresh_and_400_logging(self):
        self.oauth_file.parent.mkdir(parents=True, exist_ok=True)
        self.oauth_file.write_text(
            json.dumps({
                "access_token": "expired_tok",
                "refresh_token": "old_refresh_token_1",
                "expires_at": time.time() - 120,
            }),
            encoding="utf-8",
        )

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {
            "access_token": "new_access_token_2",
            "refresh_token": "rotated_refresh_token_2",
            "expires_in": 3600,
        }

        with patch.object(mobile_app_api.requests, "post", return_value=ok_resp) as mock_post:
            tok = mobile_app_api.get_mobile_access_token(page=None)

        self.assertEqual(tok, "new_access_token_2")
        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args[0][0], mobile_app_api.TOKEN_URL)
        self.assertEqual(mock_post.call_args[1]["data"]["refresh_token"], "old_refresh_token_1")

        # Atomic write: .tmp removed after os.replace, disk + memory cache synced
        self.assertFalse(Path(f"{self.oauth_file}.tmp").exists())
        saved = json.loads(self.oauth_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["access_token"], "new_access_token_2")
        self.assertEqual(saved["refresh_token"], "rotated_refresh_token_2")
        self.assertGreater(saved["expires_at"], time.time() + 3000)

        mem_entry = mobile_app_api._oauth_cache_mem[str(self.oauth_file.resolve())]
        self.assertEqual(mem_entry["access_token"], "new_access_token_2")
        self.assertEqual(mem_entry["refresh_token"], "rotated_refresh_token_2")

        # HTTP 400 logs Microsoft JSON error + error_description
        mobile_app_api.clear_oauth_memory_cache()
        self.oauth_file.write_text(
            json.dumps({
                "access_token": "expired_tok",
                "refresh_token": "revoked_refresh",
                "expires_at": time.time() - 10,
            }),
            encoding="utf-8",
        )
        err_resp = MagicMock()
        err_resp.status_code = 400
        err_resp.json.return_value = {
            "error": "invalid_grant",
            "error_description": "AADSTS70000: The refresh token has expired.",
        }
        err_resp.text = json.dumps(err_resp.json.return_value)

        warnings: list[str] = []
        with (
            patch.object(mobile_app_api.requests, "post", return_value=err_resp),
            patch.object(mobile_app_api, "_log_warning", side_effect=warnings.append),
        ):
            tok_fail = mobile_app_api.get_mobile_access_token(page=None)

        self.assertIsNone(tok_fail)
        joined = "\n".join(warnings)
        self.assertIn("invalid_grant", joined)
        self.assertIn("AADSTS70000: The refresh token has expired.", joined)

    def test_2a_network_response_location_code_interception(self):
        page = MagicMock()
        auth_page = MagicMock()
        auth_page.url = mobile_app_api.AUTH_URL
        auth_page.title.return_value = "Signing in"
        page.context.new_page.return_value = auth_page

        listeners: dict[str, list] = {"request": [], "response": []}
        auth_page.on.side_effect = lambda ev, fn: listeners.setdefault(ev, []).append(fn)

        def fake_goto(url, timeout=None, wait_until=None):
            resp = MagicMock()
            resp.url = url
            resp.headers = {"Location": "https://login.live.com/oauth20_desktop.srf?code=AUTH_CODE_123"}
            for fn in listeners["response"]:
                fn(resp)

        auth_page.goto.side_effect = fake_goto

        token_resp = MagicMock()
        token_resp.status_code = 200
        token_resp.json.return_value = {
            "access_token": "tok_from_location",
            "refresh_token": "ref_from_location",
            "expires_in": 3600,
        }

        with patch.object(mobile_app_api.requests, "post", return_value=token_resp) as mock_post:
            tok = mobile_app_api.get_mobile_access_token(page)

        self.assertEqual(tok, "tok_from_location")
        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args[1]["data"]["grant_type"], "authorization_code")
        self.assertEqual(mock_post.call_args[1]["data"]["code"], "AUTH_CODE_123")
        self.assertFalse(mobile_app_api._oauth_browser_failed)
        auth_page.close.assert_called_once()

    def test_2b_interstitial_and_account_picker_clicked_in_polling_loop(self):
        # Case 1: #idSIButton9 KMSI interstitial clicked in 1s polling loop
        page = MagicMock()
        auth_page = MagicMock()
        auth_page.url = "https://login.live.com/ppsecure/post.srf"
        auth_page.title.return_value = "Stay signed in?"
        auth_page.inner_text.return_value = "Stay signed in? Do this to reduce the number of times you are asked."
        page.context.new_page.return_value = auth_page

        kmsi_btn = MagicMock()
        kmsi_btn.is_visible.return_value = True
        kmsi_btn.is_enabled.return_value = True

        def click_kmsi():
            auth_page.url = "https://login.live.com/oauth20_desktop.srf?code=KMSI_CODE_456"

        kmsi_btn.click.side_effect = click_kmsi

        hidden_el = MagicMock()
        hidden_el.is_visible.return_value = False
        hidden_el.is_enabled.return_value = False

        def locator_side_effect(sel):
            loc = MagicMock()
            if sel == "#idSIButton9":
                loc.all.return_value = [kmsi_btn]
                loc.first = kmsi_btn
            else:
                loc.all.return_value = []
                loc.first = hidden_el
            return loc

        auth_page.locator.side_effect = locator_side_effect

        tok_resp = MagicMock()
        tok_resp.status_code = 200
        tok_resp.json.return_value = {
            "access_token": "tok_kmsi",
            "refresh_token": "ref_kmsi",
            "expires_in": 3600,
        }

        with patch.object(mobile_app_api.requests, "post", return_value=tok_resp) as mock_post:
            tok = mobile_app_api.get_mobile_access_token(page)

        self.assertEqual(tok, "tok_kmsi")
        kmsi_btn.click.assert_called_once()
        self.assertEqual(mock_post.call_args[1]["data"]["code"], "KMSI_CODE_456")

        # Case 2: Account Picker skips "Use another account" and clicks real account tile
        mobile_app_api.clear_oauth_memory_cache()
        mobile_app_api.reset_oauth_circuit_breaker()
        self.oauth_file.unlink(missing_ok=True)

        auth_page2 = MagicMock()
        auth_page2.url = "https://login.live.com/oauth20_authorize.srf"
        auth_page2.title.return_value = "Pick an account"
        auth_page2.inner_text.return_value = "Pick an account"
        page.context.new_page.return_value = auth_page2

        other_tile = MagicMock()
        other_tile.is_visible.return_value = True
        other_tile.is_enabled.return_value = True
        other_tile.inner_text.return_value = "Use another account"

        real_tile = MagicMock()
        real_tile.is_visible.return_value = True
        real_tile.is_enabled.return_value = True
        real_tile.inner_text.return_value = "rewarduser@outlook.com"
        real_tile.click.side_effect = lambda: setattr(
            auth_page2, "url", "https://login.live.com/oauth20_desktop.srf?code=PICKER_CODE_789"
        )

        def picker_locator(sel):
            loc = MagicMock()
            if sel == 'div[data-test-id]:not(#otherTile)':
                loc.all.return_value = [other_tile, real_tile]
                loc.first = other_tile
            else:
                loc.all.return_value = []
                loc.first = hidden_el
            return loc

        auth_page2.locator.side_effect = picker_locator

        with patch.object(mobile_app_api.requests, "post", return_value=tok_resp) as mock_post2:
            tok2 = mobile_app_api.get_mobile_access_token(page)

        self.assertEqual(tok2, "tok_kmsi")
        other_tile.click.assert_not_called()
        real_tile.click.assert_called_once()
        self.assertEqual(mock_post2.call_args[1]["data"]["code"], "PICKER_CODE_789")

    def test_2c_and_3_password_fast_fail_redacts_url_writes_debug_png_and_trips_circuit_breaker(self):
        self.assertEqual(
            mobile_app_api._sanitize_oauth_url(
                "https://login.live.com/oauth20_desktop.srf?code=SECRET_CODE_VALUE&access_token=SECRET_TOK_VALUE&lc=1033"
            ),
            "https://login.live.com/oauth20_desktop.srf?code=[REDACTED]&access_token=[REDACTED]&lc=1033",
        )

        page = MagicMock()
        auth_page = MagicMock()
        auth_page.url = (
            "https://login.live.com/oauth20_authorize.srf"
            "?access_token=SECRET_TOK_VALUE&login_hint=SECRET_USER_HINT&lc=1033"
        )
        auth_page.title.return_value = "Sign in to your account"
        auth_page.inner_text.return_value = "Enter password"
        page.context.new_page.return_value = auth_page

        def fake_screenshot(path):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(b"\x89PNG\r\n\x1a\nFAKEPNG")

        auth_page.screenshot.side_effect = fake_screenshot

        pw_field = MagicMock()
        pw_field.is_visible.return_value = True
        hidden_el = MagicMock()
        hidden_el.is_visible.return_value = False

        def blocker_locator(sel):
            loc = MagicMock()
            loc.first = pw_field if sel == 'input[type="password"]' else hidden_el
            return loc

        auth_page.locator.side_effect = blocker_locator

        warnings: list[str] = []
        t0 = time.monotonic()
        with (
            patch.object(mobile_app_api.requests, "post") as mock_post,
            patch.object(mobile_app_api, "_log_warning", side_effect=warnings.append),
        ):
            tok1 = mobile_app_api.get_mobile_access_token(page)
            elapsed = time.monotonic() - t0

            self.assertIsNone(tok1)
            self.assertLessEqual(elapsed, 5.0)
            auth_page.wait_for_timeout.assert_not_called()
            self.assertTrue(mobile_app_api._oauth_browser_failed)

            debug_png = self.tmp_path / "logs" / "debug_oauth_fail.png"
            self.assertTrue(debug_png.exists())

            warn_text = "\n".join(warnings)
            self.assertNotIn("SECRET_TOK_VALUE", warn_text)
            self.assertNotIn("SECRET_USER_HINT", warn_text)
            self.assertIn("access_token=[REDACTED]", warn_text)
            self.assertIn("login_hint=[REDACTED]", warn_text)

            # Circuit Breaker: calls #2-#5 return None immediately with 0 new_page() and 0 HTTP calls
            self.assertEqual(page.context.new_page.call_count, 1)
            for _ in range(4):
                self.assertIsNone(mobile_app_api.get_mobile_access_token(page))
            self.assertEqual(page.context.new_page.call_count, 1)
            mock_post.assert_not_called()

        # Reset circuit breaker restores ability to launch OAuth page
        mobile_app_api.reset_oauth_circuit_breaker()
        self.assertFalse(mobile_app_api._oauth_browser_failed)
        self.assertFalse(mobile_app_api._oauth_browser_attempted)
        with patch.object(mobile_app_api, "_log_warning"):
            mobile_app_api.get_mobile_access_token(page)
        self.assertEqual(page.context.new_page.call_count, 2)

    def test_4_read_to_earn_statestore_lock_zero_gain_and_10_articles(self):
        page = MagicMock()

        # Case A: Two consecutive HTTP 200 responses with +0 pts mark read_to_earn_done=True
        store_zero = StateStore(
            account_id="rte_zero",
            data_dir=self.tmp_path / "data_zero",
            profiles_dir=self.tmp_path / "profiles_zero",
        )
        zero_resp = MagicMock()
        zero_resp.status_code = 200
        zero_resp.json.return_value = {"response": {"balance": 2000, "activity": {"p": 0}}}

        with (
            patch.object(mobile_app_api, "get_mobile_access_token", return_value="tok_rte") as mock_get_tok,
            patch.object(mobile_app_api.requests, "post", return_value=zero_resp) as mock_post,
            patch("time.sleep"),
        ):
            gained = mobile_app_api.solve_mobile_app_read_to_earn(page, state_store=store_zero)

        self.assertEqual(gained, 0)
        self.assertEqual(mock_post.call_count, 2)
        self.assertTrue(store_zero.is_read_to_earn_done())

        # Subsequent same-day call skips execution with 0 get_mobile_access_token and 0 requests.post
        with (
            patch.object(mobile_app_api, "get_mobile_access_token") as mock_tok_skip,
            patch.object(mobile_app_api.requests, "post") as mock_post_skip,
        ):
            gained_skip = mobile_app_api.solve_mobile_app_read_to_earn(page, state_store=store_zero)

        self.assertEqual(gained_skip, 0)
        mock_tok_skip.assert_not_called()
        mock_post_skip.assert_not_called()

        # Case B: 10 successful articles (+3 pts each) also mark read_to_earn_done=True
        store_ten = StateStore(
            account_id="rte_ten",
            data_dir=self.tmp_path / "data_ten",
            profiles_dir=self.tmp_path / "profiles_ten",
        )
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"response": {"balance": 2030, "activity": {"p": 3}}}

        with (
            patch.object(mobile_app_api, "get_mobile_access_token", return_value="tok_rte"),
            patch.object(mobile_app_api.requests, "post", return_value=ok_resp) as mock_post_10,
            patch("time.sleep"),
        ):
            gained_10 = mobile_app_api.solve_mobile_app_read_to_earn(page, state_store=store_ten)

        self.assertEqual(gained_10, 30)
        self.assertEqual(mock_post_10.call_count, 10)
        self.assertTrue(store_ten.is_read_to_earn_done())


# ==============================================================================
# 2. Profile & Process Tests
# ==============================================================================
class TestProfileAndProcessGuards(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmpdir.name).resolve()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_5_ssd_guard_allowlist_and_denylist(self):
        tmp_profiles = self.tmp_path / "profiles"
        prof_default = tmp_profiles / "acc_ssd" / "desktop" / "Default"
        prof_default.mkdir(parents=True, exist_ok=True)

        # Protected denylist files and directories
        protected_files = [
            prof_default / "Cookies",
            prof_default / "Network" / "Cookies",
            prof_default / "Local Storage" / "leveldb" / "000001.ldb",
            prof_default / "Session Storage" / "000003.log",
            prof_default / "IndexedDB" / "https_bing.indexeddb.leveldb" / "LOCK",
            prof_default / "Login Data",
            prof_default / "Preferences",
            prof_default / "Secure Preferences",
            prof_default / "Web Data",
            prof_default / "Extension State" / "LOG",
        ]
        for fpath in protected_files:
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(f"protected:{fpath.name}", encoding="utf-8")

        # Allowlisted cache directories aged > 7 days
        cache_dirs = [
            prof_default / "Cache",
            prof_default / "Cache_Data",
            prof_default / "Code Cache",
            prof_default / "GPUCache",
            prof_default / "Service Worker" / "CacheStorage",
        ]
        old_ts = time.time() - (10 * 86400)
        for cdir in cache_dirs:
            cdir.mkdir(parents=True, exist_ok=True)
            dummy = cdir / "cache_blob.bin"
            dummy.write_bytes(b"X" * 1024)
            os.utime(dummy, (old_ts, old_ts))
            os.utime(cdir, (old_ts, old_ts))

        # Direct unit checks on is_safe_cache_dir_to_purge
        for cdir in cache_dirs:
            self.assertTrue(
                browser_manager.is_safe_cache_dir_to_purge(cdir, tmp_profiles),
                f"Expected allowlisted cache dir to be purgeable: {cdir}",
            )
        for pdir in (
            prof_default / "Network",
            prof_default / "Local Storage",
            prof_default / "Local Storage" / "leveldb",
            prof_default / "Session Storage",
            prof_default / "IndexedDB",
            prof_default / "Extension State",
            prof_default / "Service Worker",
            self.tmp_path / "outside_Cache",
        ):
            self.assertFalse(
                browser_manager.is_safe_cache_dir_to_purge(pdir, tmp_profiles),
                f"Expected protected/non-cache dir NOT to be purgeable: {pdir}",
            )

        # Even a Cache folder containing a denylisted file name must be rejected
        poisoned_cache = prof_default / "GrShaderCache"
        poisoned_cache.mkdir(parents=True, exist_ok=True)
        (poisoned_cache / "Cookies").write_text("nested-cookie", encoding="utf-8")
        self.assertFalse(browser_manager.is_safe_cache_dir_to_purge(poisoned_cache, tmp_profiles))

        browser_manager.purge_profile_caches(str(tmp_profiles), max_age_days=7, max_total_mb=500)

        # ALL denylisted auth/session files & dirs survive 100% intact
        for fpath in protected_files:
            self.assertTrue(fpath.exists(), f"Protected file deleted: {fpath}")
            self.assertEqual(fpath.read_text(encoding="utf-8"), f"protected:{fpath.name}")

        # ALL old allowlisted cache dirs are pruned
        for cdir in cache_dirs:
            self.assertFalse(cdir.exists(), f"Old cache dir was not pruned: {cdir}")

    def test_6_profile_scoped_process_cleanup_lockfiles_and_timeout(self):
        target_desktop = self.tmp_path / "profiles" / "default" / "desktop"
        sibling_mobile = self.tmp_path / "profiles" / "default" / "mobile"
        personal_edge = Path(r"C:\Users\SomeUser\AppData\Local\Microsoft\Edge\User Data")
        target_desktop.mkdir(parents=True, exist_ok=True)
        sibling_mobile.mkdir(parents=True, exist_ok=True)

        # Exact matching checks
        self.assertTrue(
            browser_manager._matches_exact_profile_dir(
                ["msedge.exe", f"--user-data-dir={target_desktop}"], target_desktop
            )
        )
        self.assertTrue(
            browser_manager._matches_exact_profile_dir(
                f'"C:\\Program Files\\Edge\\msedge.exe" --user-data-dir="{target_desktop}" --headless=new',
                target_desktop,
            )
        )
        self.assertFalse(
            browser_manager._matches_exact_profile_dir(
                f'msedge.exe --user-data-dir="{personal_edge}"', target_desktop
            )
        )
        self.assertFalse(
            browser_manager._matches_exact_profile_dir(
                ["msedge.exe", "--user-data-dir", str(sibling_mobile)], target_desktop
            )
        )

        # Create lockfiles in target profile
        lock_names = ("SingletonLock", "SingletonCookie", "SingletonSocket", "Lockfile")
        for lname in lock_names:
            (target_desktop / lname).write_text("lock", encoding="utf-8")

        matching_proc = MagicMock()
        matching_proc.info = {
            "pid": 1001,
            "name": "msedge.exe",
            "cmdline": ["msedge.exe", f"--user-data-dir={target_desktop}"],
        }
        personal_proc = MagicMock()
        personal_proc.info = {
            "pid": 1002,
            "name": "msedge.exe",
            "cmdline": ["msedge.exe", f"--user-data-dir={personal_edge}"],
        }
        mobile_proc = MagicMock()
        mobile_proc.info = {
            "pid": 1003,
            "name": "msedge.exe",
            "cmdline": ["msedge.exe", f"--user-data-dir={sibling_mobile}"],
        }

        with (
            patch.object(browser_manager.psutil, "process_iter", return_value=[matching_proc, personal_proc, mobile_proc]),
            patch.object(browser_manager.time, "sleep") as mock_sleep,
        ):
            browser_manager._cleanup_profile_locks_and_processes(target_desktop)

        matching_proc.kill.assert_called_once()
        personal_proc.kill.assert_not_called()
        mobile_proc.kill.assert_not_called()
        mock_sleep.assert_called_with(1.5)
        for lname in lock_names:
            self.assertFalse((target_desktop / lname).exists(), f"{lname} should be deleted")

        # Verify LAUNCH_CONTEXT_TIMEOUT_MS == 90000 passed to launch_persistent_context
        self.assertEqual(browser_manager.LAUNCH_CONTEXT_TIMEOUT_MS, 90000)
        mock_pw = MagicMock()
        mock_pw.devices = {
            "Pixel 7": {
                "user_agent": "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 EdgA/131.0.0.0",
                "viewport": {"width": 412, "height": 915},
            }
        }
        with (
            patch.object(browser_manager, "apply_stealth_scripts"),
            patch.object(browser_manager, "attach_resource_blocker"),
        ):
            browser_manager.create_desktop_context(mock_pw, headless=True, profile_dir=target_desktop)
            desktop_kwargs = mock_pw.chromium.launch_persistent_context.call_args.kwargs
            self.assertEqual(desktop_kwargs["timeout"], 90000)

            mock_pw.chromium.launch_persistent_context.reset_mock()
            browser_manager.create_mobile_context(mock_pw, headless=True, profile_dir=sibling_mobile)
            mobile_kwargs = mock_pw.chromium.launch_persistent_context.call_args.kwargs
            self.assertEqual(mobile_kwargs["timeout"], 90000)

    def test_7_structured_json_pid_lockfile_reclaim_and_contend(self):
        lock_path = self.tmp_path / "data" / "locks" / "default.lock"

        # 1. Acquire writes structured JSON with current PID
        handle = browser_manager.acquire_runner_lock(lock_path, account_id="default")
        self.assertIsNotNone(handle)
        data = json.loads(lock_path.read_text(encoding="utf-8"))
        self.assertEqual(data["pid"], os.getpid())
        self.assertEqual(data["account"], "default")
        self.assertIn("started_at", data)
        browser_manager.release_runner_lock(handle)
        self.assertFalse(lock_path.exists())

        # 2. Stale / dead / non-Python PID lockfile is reclaimed automatically
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(
            json.dumps({"pid": 9999999, "account": "default", "started_at": "2020-01-01T00:00:00"}),
            encoding="utf-8",
        )
        fake_non_python = MagicMock()
        fake_non_python.is_running.return_value = True
        fake_non_python.status.return_value = "running"
        fake_non_python.name.return_value = "notepad.exe"
        with (
            patch.object(browser_manager.psutil, "pid_exists", return_value=True),
            patch.object(browser_manager.psutil, "Process", return_value=fake_non_python),
        ):
            reclaimed = browser_manager.acquire_runner_lock(lock_path, account_id="default")

        self.assertIsNotNone(reclaimed)
        reclaimed_data = json.loads(lock_path.read_text(encoding="utf-8"))
        self.assertEqual(reclaimed_data["pid"], os.getpid())
        browser_manager.release_runner_lock(reclaimed)

        # 3. Live Python bot PID blocks second acquire_runner_lock without deleting owner's lockfile
        owner_payload = {"pid": 54321, "account": "default", "started_at": "2026-11-24T00:00:00"}
        lock_path.write_text(json.dumps(owner_payload), encoding="utf-8")
        with patch.object(browser_manager, "_is_python_bot_pid_alive", return_value=True):
            second = browser_manager.acquire_runner_lock(lock_path, account_id="default")

        self.assertIsNone(second)
        self.assertTrue(lock_path.exists())
        still_owner = json.loads(lock_path.read_text(encoding="utf-8"))
        self.assertEqual(still_owner["pid"], 54321)


# ==============================================================================
# 3. Dashboard, Points Sanity, Cooldown & Bing Star Tests
# ==============================================================================
class TestDashboardAndCooldownFixes(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmpdir.name).resolve()
        self._orig_oauth_file = mobile_app_api.OAUTH_FILE
        mobile_app_api.set_oauth_file(self.tmp_path / "data" / "mobile_oauth.json")
        mobile_app_api.clear_oauth_memory_cache()
        mobile_app_api.reset_oauth_circuit_breaker()

    def tearDown(self):
        mobile_app_api.clear_oauth_memory_cache()
        mobile_app_api.reset_oauth_circuit_breaker()
        mobile_app_api.OAUTH_FILE = self._orig_oauth_file
        self._tmpdir.cleanup()

    def test_8_dashboard_circuit_breaker_false_zero_and_session_expired(self):
        store = StateStore(
            account_id="default",
            data_dir=self.tmp_path / "data",
            profiles_dir=self.tmp_path / "profiles",
        )
        store.update_points_ledger(4914, 0)
        self.assertEqual(store.get_last_known_points(), 4914)

        d_prof = self.tmp_path / "profiles" / "default" / "desktop"
        m_prof = self.tmp_path / "profiles" / "default" / "mobile"
        d_prof.mkdir(parents=True, exist_ok=True)
        m_prof.mkdir(parents=True, exist_ok=True)

        class RunArgs:
            debug = True
            desktop_target = 30
            mobile_target = 20
            batch_size = 3
            account = None

        # Case A: Dashboard returns 0 pts and found_pc=False, found_mobile=False
        ctx_a = MagicMock()
        page_a = MagicMock()
        page_a.is_closed.return_value = False
        page_a.url = "https://rewards.bing.com/earn"
        ctx_a.pages = [page_a]

        bad_progress = {
            "found": False,
            "found_pc": False,
            "found_mobile": False,
            "pc_cur": 0,
            "pc_max": 90,
            "mobile_cur": 0,
            "mobile_max": 60,
            "balance": 0,
            "scrape_error": False,
        }

        mock_notifier_a = MagicMock()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(runner, "DESKTOP_PROFILE", d_prof))
            stack.enter_context(patch.object(runner, "MOBILE_PROFILE", m_prof))
            stack.enter_context(patch.object(runner, "_get_state_store", return_value=store))
            stack.enter_context(patch.object(runner, "Notifier", return_value=mock_notifier_a))
            stack.enter_context(patch.object(runner, "wait_for_internet", return_value=True))
            stack.enter_context(patch.object(runner, "get_sync_playwright"))
            stack.enter_context(patch.object(runner, "get_unique_search_queries", return_value=["q"] * 60))
            stack.enter_context(patch.object(runner, "ensure_desktop_page", return_value=(ctx_a, page_a)))
            stack.enter_context(patch.object(runner, "perform_warmup_visit"))
            stack.enter_context(patch.object(runner, "dismiss_popups"))
            stack.enter_context(patch.object(runner, "extract_current_points", return_value=0))
            stack.enter_context(patch.object(runner, "get_daily_search_progress", return_value=bad_progress))
            mock_init = stack.enter_context(patch.object(runner, "init_session"))
            mock_search = stack.enter_context(patch.object(runner, "execute_single_search"))
            stack.enter_context(patch.object(runner, "unload_model_from_vram"))
            stack.enter_context(patch.object(runner, "cleanup_zombie_browsers"))
            stack.enter_context(patch.object(runner, "purge_profile_caches"))

            ok_a = runner.run_full_pipeline(RunArgs())

        self.assertFalse(ok_a)
        reload_waits = [c for c in page_a.wait_for_timeout.call_args_list if c.args == (5000,)]
        self.assertEqual(len(reload_waits), 2)
        mock_search.assert_not_called()
        mock_init.assert_not_called()
        self.assertEqual(store.get_last_known_points(), 4914)
        ledger_disk = json.loads(store.points_ledger_file.read_text(encoding="utf-8"))
        self.assertEqual(ledger_disk["total_points"], 4914)

        # Case B: Dashboard redirects to login.live.com -> SESSION_EXPIRED + notify_auth_expired + False
        ctx_b = MagicMock()
        page_b = MagicMock()
        page_b.is_closed.return_value = False
        page_b.url = "https://login.live.com/oauth20_authorize.srf?client_id=test"
        ctx_b.pages = [page_b]

        mock_notifier_b = MagicMock()
        errors_b: list[str] = []
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(runner, "DESKTOP_PROFILE", d_prof))
            stack.enter_context(patch.object(runner, "MOBILE_PROFILE", m_prof))
            stack.enter_context(patch.object(runner, "_get_state_store", return_value=store))
            stack.enter_context(patch.object(runner, "Notifier", return_value=mock_notifier_b))
            stack.enter_context(patch.object(runner, "wait_for_internet", return_value=True))
            stack.enter_context(patch.object(runner, "get_sync_playwright"))
            stack.enter_context(patch.object(runner, "get_unique_search_queries", return_value=["q"] * 60))
            stack.enter_context(patch.object(runner, "ensure_desktop_page", return_value=(ctx_b, page_b)))
            stack.enter_context(patch.object(runner, "perform_warmup_visit"))
            stack.enter_context(patch.object(runner, "dismiss_popups"))
            stack.enter_context(patch.object(runner, "extract_current_points", return_value=0))
            stack.enter_context(patch.object(runner, "get_daily_search_progress", return_value=bad_progress))
            stack.enter_context(patch.object(runner, "_log_error", side_effect=errors_b.append))
            mock_init_b = stack.enter_context(patch.object(runner, "init_session"))
            mock_search_b = stack.enter_context(patch.object(runner, "execute_single_search"))
            stack.enter_context(patch.object(runner, "unload_model_from_vram"))
            stack.enter_context(patch.object(runner, "cleanup_zombie_browsers"))
            stack.enter_context(patch.object(runner, "purge_profile_caches"))

            ok_b = runner.run_full_pipeline(RunArgs())

        self.assertFalse(ok_b)
        self.assertTrue(any("SESSION_EXPIRED" in msg for msg in errors_b))
        mock_notifier_b.notify_auth_expired.assert_called_once()
        self.assertIn("SESSION_EXPIRED", mock_notifier_b.notify_auth_expired.call_args[0][0])
        mock_init_b.assert_not_called()
        mock_search_b.assert_not_called()

    def test_9_mobile_point_sanity_filter_and_reader(self):
        self.assertIsNone(tracker.sanitize_scraped_points(0, 4000))
        self.assertIsNone(tracker.sanitize_scraped_points(10, 4000))
        self.assertIsNone(tracker.sanitize_scraped_points(14, 4000))
        self.assertEqual(tracker.sanitize_scraped_points(4010, 4000), 4010)

        # extract_current_points ignores bare "24°C" header weather numbers and reads #flyout_id_rc ("4,560")
        page = MagicMock()

        def make_loc(text: str, count: int = 1, visible: bool = True):
            loc = MagicMock()
            loc.first = loc
            loc.count.return_value = count
            loc.is_visible.return_value = visible
            loc.inner_text.return_value = text
            loc.text_content.return_value = text
            loc.get_attribute.return_value = ""
            return loc

        empty_loc = make_loc("", count=0, visible=False)
        header_weather_loc = make_loc("24°C Hanoi", count=1, visible=True)
        flyout_loc = make_loc("4,560", count=1, visible=True)

        def locator_side_effect(sel):
            if sel == "#flyout_id_rc":
                return flyout_loc
            if sel == "header, #b_header, [role='banner']":
                return header_weather_loc
            return empty_loc

        page.locator.side_effect = locator_side_effect
        self.assertEqual(tracker.extract_current_points(page, last_known_points=4000), 4560)

        # If #flyout_id_rc is absent and only bare "24°C" is in header, returns None
        flyout_loc.count.return_value = 0
        flyout_loc.is_visible.return_value = False
        self.assertIsNone(tracker.extract_current_points(page, last_known_points=4000))

    def test_10_adaptive_cooldown_counter_reset_and_none_immunity(self):
        ctrl = CooldownController()
        ctrl.reset_burst(4000)

        # Passing None or 14 leaves zero_delta_checks / no_gain_streak / searches_since_check untouched
        for bad in (None, 14, None, 14, None, 14):
            self.assertFalse(ctrl.record_search(bad))
            self.assertEqual(ctrl.zero_delta_checks, 0)
            self.assertEqual(ctrl.no_gain_streak, 0)
            self.assertEqual(ctrl.searches_since_check, 0)
            self.assertEqual(ctrl.last_check_points, 4000)
            self.assertEqual(ctrl.backoff_level, 0)

        # Trigger cooldown after 4 searches at 4000
        self.assertFalse(ctrl.record_search(4000))
        self.assertFalse(ctrl.record_search(4000))
        self.assertFalse(ctrl.record_search(4000))
        self.assertTrue(ctrl.record_search(4000))
        self.assertEqual(ctrl.backoff_level, 1)
        self.assertEqual(ctrl.zero_delta_checks, 2)
        self.assertEqual(ctrl.no_gain_streak, 2)

        # next_cooldown_seconds() resets no_gain_streak & zero_delta_checks
        cd = ctrl.next_cooldown_seconds(jitter_fn=lambda a, b: 0)
        self.assertEqual(cd, 20 * 60)
        self.assertEqual(ctrl.no_gain_streak, 0)
        self.assertEqual(ctrl.zero_delta_checks, 0)
        self.assertEqual(ctrl.searches_since_check, 0)

        # on_cooldown_exit() resets counters so a single 2-search post-cooldown batch never fires prematurely
        ctrl.zero_delta_checks = 1
        ctrl.no_gain_streak = 1
        ctrl.on_cooldown_exit(4000)
        self.assertEqual(ctrl.no_gain_streak, 0)
        self.assertEqual(ctrl.zero_delta_checks, 0)
        self.assertFalse(ctrl.record_search(4000))
        self.assertFalse(ctrl.record_search(4000))
        self.assertEqual(ctrl.zero_delta_checks, 1)
        self.assertFalse(ctrl.cooldown_triggered)

    def test_11_unified_bing_star_parser_and_progress_integration(self):
        # Verify _format_bing_star_result normalizes to "3 / 2,100"
        formatted = activities._format_bing_star_result({"current": 3, "max": 2100, "found": True})
        self.assertEqual(formatted, {"current": 3, "max": 2100, "text": "3 / 2,100", "found": True})

        def _simulate_scan_js(js_src: str, *, card_text: str = "", dialog_bar: tuple[int, int] | None = None):
            # Verify the production JS contains dialog progressbar & dot/comma number parsing
            self.assertIn('[role="dialog"] [role="progressbar"]', js_src)
            self.assertIn(r"replace(/[,\.]/g, '')", js_src)

            if dialog_bar is not None:
                return {"current": dialog_bar[0], "max": dialog_bar[1], "found": True}

            m = re.search(
                r"(?:Bing\s*Star\s*bonus|Star\s*bonus|Bing\s*Star)[\s\S]{0,200}?(\d[\d,\.]*)\s*(?:/|of|trên)\s*(\d[\d,\.]*)",
                card_text,
                re.IGNORECASE,
            )
            if m:
                cur = int(re.sub(r"[,\.]", "", m.group(1)))
                mx = int(re.sub(r"[,\.]", "", m.group(2))) or 2100
                return {"current": cur, "max": mx, "found": True}
            return {"current": 0, "max": 2100, "found": False}

        # 1. Dot separator "3 / 2.100"
        page_dot = MagicMock()
        page_dot.evaluate.side_effect = lambda js: _simulate_scan_js(js, card_text="Bing Star bonus 3 / 2.100")
        res_dot = activities.extract_bing_star_bonus(page_dot)
        self.assertEqual(res_dot, {"current": 3, "max": 2100, "text": "3 / 2,100", "found": True})

        # 2. Comma separator "3 / 2,100"
        page_comma = MagicMock()
        page_comma.evaluate.side_effect = lambda js: _simulate_scan_js(js, card_text="Bing Star bonus 3 / 2,100")
        res_comma = activities.extract_bing_star_bonus(page_comma)
        self.assertEqual(res_comma, {"current": 3, "max": 2100, "text": "3 / 2,100", "found": True})

        # 3. Dialog [role="progressbar"] opened after clicking Star card
        page_dialog = MagicMock()
        dialog_open = {"open": False}
        star_loc = MagicMock()
        star_loc.first = star_loc
        star_loc.count.return_value = 1
        star_loc.is_visible.return_value = True
        star_loc.click.side_effect = lambda timeout=None: dialog_open.__setitem__("open", True)
        page_dialog.locator.return_value = star_loc
        page_dialog.evaluate.side_effect = lambda js: _simulate_scan_js(
            js, dialog_bar=(3, 2100) if dialog_open["open"] else None
        )
        res_dialog = activities.extract_bing_star_bonus(page_dialog)
        self.assertEqual(res_dialog, {"current": 3, "max": 2100, "text": "3 / 2,100", "found": True})
        star_loc.click.assert_called_once()

        # 4. get_daily_search_progress populates bing_star_bonus consistently
        page_prog = MagicMock()
        page_prog.url = "https://rewards.bing.com/earn"
        page_prog.inner_text.return_value = "Desktop Bing search 90 / 90\nMobile Bing search 60 / 60"

        def prog_eval(js):
            if "found_count" in js:
                return {
                    "activities": {
                        "Bing Search": {"cur": 1, "max": 1, "done": True},
                        "Daily Set": {"cur": 3, "max": 3, "done": True},
                        "Edge Browsing": {"cur": 30, "max": 30, "done": True},
                        "Mobile App": {"cur": 1, "max": 1, "done": True},
                        "Visual Search": {"cur": 1, "max": 1, "done": True},
                    },
                    "found_count": 5,
                }
            if "Bing\\s*Star" in js:
                return _simulate_scan_js(js, card_text="Bing Star bonus 3 / 2.100")
            return False

        page_prog.evaluate.side_effect = prog_eval
        with patch.object(activities, "dismiss_popups"):
            prog = activities.get_daily_search_progress(page_prog, use_dapi=False)

        self.assertEqual(
            prog["bing_star_bonus"],
            {"current": 3, "max": 2100, "text": "3 / 2,100", "found": True},
        )


# ==============================================================================
# 4. Quest, Keep Earning, Visual Search & Edge Browsing Tests
# ==============================================================================
class TestQuestsKeepEarningVisualSearchAndEdgeBrowsing(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmpdir.name).resolve()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_12a_canonical_quest_url_dedup_and_per_day_task_cap(self):
        store = StateStore(
            account_id="quest_acc",
            data_dir=self.tmp_path / "data",
            profiles_dir=self.tmp_path / "profiles",
        )
        self.assertEqual(
            activities.canonicalize_quest_url(
                "https://rewards.bing.com/earn/quest/DiscordNitro3M_punchcard?form=ML2YFE&OCID=ML2YFE"
            ),
            "https://rewards.bing.com/earn/quest/DiscordNitro3M_punchcard",
        )

        anchor_1 = MagicMock()
        anchor_1.get_attribute.return_value = "/earn/quest/DiscordNitro3M_punchcard"
        anchor_1.inner_text.return_value = "Discord Nitro 3/4 tasks"

        anchor_2 = MagicMock()
        anchor_2.get_attribute.return_value = "/earn/quest/DiscordNitro3M_punchcard?form=ML2YFE&OCID=ML2YFE"
        anchor_2.inner_text.return_value = "Discord Nitro 3/4 tasks"

        claim_btn = MagicMock()
        claim_btn.first = claim_btn
        claim_btn.is_visible.return_value = True
        claim_btn.inner_text.return_value = "Claim your reward"

        body_loc = MagicMock()
        body_loc.inner_text.return_value = "Activities\nStatus: 3/4 tasks"

        anchors_loc = MagicMock()
        anchors_loc.all.return_value = [anchor_1, anchor_2]

        page = MagicMock()
        page.url = "https://rewards.bing.com/earn"
        page.context.pages = [page]
        visited_urls: list[str] = []

        def fake_goto(url, wait_until=None, timeout=None):
            visited_urls.append(url)
            page.url = url

        page.goto.side_effect = fake_goto
        page.evaluate.return_value = 1

        def page_locator(sel):
            if sel == 'a[href*="/earn/quest/"]':
                return anchors_loc
            if sel == "body":
                return body_loc
            if sel == '[data-bot-quest-target="0"]':
                return claim_btn
            m = MagicMock()
            m.first = m
            m.all.return_value = []
            m.is_visible.return_value = False
            return m

        page.locator.side_effect = page_locator

        with patch.object(activities, "dismiss_popups"):
            activities.solve_all_earn_quests(page, state_store=store)

        quest_visits = [u for u in visited_urls if "/earn/quest/" in u]
        self.assertEqual(quest_visits, ["https://rewards.bing.com/earn/quest/DiscordNitro3M_punchcard"])
        claim_btn.click.assert_called_once()

        expected_key = "https://rewards.bing.com/earn/quest/DiscordNitro3M_punchcard::claim your reward"
        self.assertIn(expected_key, store.get_attempted_quest_tasks_today())

        # Second same-day run skips the stuck task button completely
        claim_btn.click.reset_mock()
        with patch.object(activities, "dismiss_popups"):
            activities.solve_all_earn_quests(page, state_store=store)
        claim_btn.click.assert_not_called()

    def test_12b_keep_earning_multi_card_continuation_search_offer_and_skip_list(self):
        store = StateStore(
            account_id="ke_acc",
            data_dir=self.tmp_path / "data",
            profiles_dir=self.tmp_path / "profiles",
        )

        cards = [
            {
                "title": "Deck the walls (+20 pts)",
                "pts": 20,
                "href": "https://www.bing.com/search?q=deck+the+walls&form=ML2BFU",
                "completes": False,
                "is_search_offer": False,
            },
            {
                "title": "Search Offer Card (+10 pts)",
                "pts": 10,
                "href": "https://www.bing.com/?form=ML2BF0",
                "completes": True,
                "is_search_offer": True,
            },
            {
                "title": "Url Offer Card (+10 pts)",
                "pts": 10,
                "href": "https://www.bing.com/spotlight?form=ML2BF1",
                "completes": True,
                "is_search_offer": False,
            },
        ]
        completed_hrefs: set[str] = set()
        current_points = {"cur": 0, "max": 60}
        active_card: dict = {"card": None}
        seen_skip_norms_per_eval: list[list[str]] = []

        page = MagicMock()
        page.url = "https://rewards.bing.com/earn"
        context = MagicMock()
        page.context = context

        # Main /earn locators
        sec_loc = MagicMock()
        sec_loc.first = sec_loc
        sec_loc.is_visible.return_value = True

        card_loc = MagicMock()
        card_loc.first = card_loc

        def main_locator(sel):
            if "moreactivities" in sel or "Keep earning" in sel:
                return sec_loc
            if sel == '[data-bot-keep-earning-target="active"]':
                return card_loc
            m = MagicMock()
            m.first = m
            m.is_visible.return_value = False
            return m

        page.locator.side_effect = main_locator

        def make_sub_tab(card: dict) -> MagicMock:
            tab = MagicMock()
            tab.url = card["href"]
            tab.is_closed.return_value = False

            search_input = MagicMock()
            search_input.count.return_value = 1 if card["is_search_offer"] else 0
            search_input.is_visible.return_value = card["is_search_offer"]

            hidden = MagicMock()
            hidden.count.return_value = 0
            hidden.is_visible.return_value = False

            def tab_locator(sel):
                loc = MagicMock()
                if sel in ("#sb_form_q", "textarea[name='q']", "input[name='q']"):
                    loc.first = search_input if sel == "#sb_form_q" else hidden
                else:
                    loc.first = hidden
                loc.all.return_value = []
                return loc

            tab.locator.side_effect = tab_locator
            tab.evaluate.return_value = "search on bing" if card["is_search_offer"] else "article content"
            tab._search_input = search_input
            return tab

        sub_tabs_by_href: dict[str, MagicMock] = {}

        def expect_page_cm(timeout=None):
            cm = MagicMock()
            card = active_card["card"]
            tab = make_sub_tab(card)
            sub_tabs_by_href[card["href"]] = tab
            cm.__enter__.return_value.value = tab
            if card["completes"]:
                completed_hrefs.add(card["href"])
                current_points["cur"] += card["pts"]
            return cm

        context.expect_page.side_effect = expect_page_cm

        def main_evaluate(script, arg=None):
            if "const m = (sec.innerText || '')" in script:
                return [current_points["cur"], current_points["max"]]
            if "data-expanded" in script:
                return False
            if "data-bot-keep-earning-target" in script and isinstance(arg, list) and len(arg) == 2:
                tried_hrefs = set(arg[0] or [])
                skip_norms = set(arg[1] or [])
                seen_skip_norms_per_eval.append(sorted(skip_norms))
                for c in cards:
                    norm = activities.normalize_offer_title(c["title"])
                    if c["href"] in completed_hrefs or c["href"] in tried_hrefs or norm in skip_norms:
                        continue
                    active_card["card"] = c
                    return {"title": c["title"], "pts": c["pts"], "href": c["href"]}
                return None
            if "targetHref" in script and isinstance(arg, list) and len(arg) == 2:
                return arg[0] in completed_hrefs
            return None

        page.evaluate.side_effect = main_evaluate

        with (
            patch.object(activities, "dismiss_popups"),
            patch.object(activities, "claim_all_available_rewards"),
            patch("time.sleep"),
        ):
            processed_1 = activities.solve_keep_earning_activities(page, context=context, state_store=store)

        # Card #1 failed to credit, yet cards #2 and #3 still ran -> all 3 processed!
        self.assertEqual(processed_1, 3)

        # Card #2 (Search Offer) performed search box interaction inside solve_interactive_tab
        search_tab = sub_tabs_by_href["https://www.bing.com/?form=ML2BF0"]
        search_tab._search_input.fill.assert_called_once_with("search offer card")
        search_tab.keyboard.press.assert_any_call("Enter")

        # Stuck Card #1 persisted in StateStore skip list
        self.assertTrue(store.is_offer_attempted_today("Deck the walls (+20 pts)"))
        self.assertIn("deck the walls", store.get_attempted_offers_today())

        # Same-day re-run skips stuck Card #1 and processes remaining uncompleted cards
        completed_hrefs.discard("https://www.bing.com/?form=ML2BF0")
        seen_skip_norms_per_eval.clear()
        sub_tabs_by_href.clear()

        with (
            patch.object(activities, "dismiss_popups"),
            patch.object(activities, "claim_all_available_rewards"),
            patch("time.sleep"),
        ):
            processed_2 = activities.solve_keep_earning_activities(page, context=context, state_store=store)

        self.assertEqual(processed_2, 1)
        self.assertNotIn("https://www.bing.com/search?q=deck+the+walls&form=ML2BFU", sub_tabs_by_href)
        self.assertIn("https://www.bing.com/?form=ML2BF0", sub_tabs_by_href)
        self.assertTrue(any("deck the walls" in s for s in seen_skip_norms_per_eval))

    def test_12c_visual_search_telemetry_12s_plus_8s_and_found_false_abort(self):
        fake_jpeg = self.tmp_path / "fake_upload.jpg"
        fake_jpeg.write_bytes(b"\xff\xd8\xff\xe0FAKEJPEG")

        def _setup_vs_mocks(progress_seq: list[dict]):
            main_page = MagicMock()
            main_page.url = "https://rewards.bing.com/earn"
            main_page.is_closed.return_value = False
            vs_tab = MagicMock()
            vs_tab.url = "https://www.bing.com/images/search?view=detailv2&iss=sbiupload&bcid=vs123"
            vs_tab.is_closed.return_value = False
            hidden = MagicMock()
            hidden.first = hidden
            hidden.count.return_value = 0
            hidden.is_visible.return_value = False
            vs_tab.locator.return_value = hidden
            return main_page, vs_tab, progress_seq

        # Case 1: 12000ms wait -> found=True & 0/1 -> 8000ms recheck -> 1/1 done (single attempt only)
        main_1, tab_1, seq_1 = _setup_vs_mocks([
            {
                "found": True, "found_pc": True, "found_mobile": True,
                "activities": {"Visual Search": {"cur": 0, "max": 1, "done": False}},
            },
            {
                "found": True, "found_pc": True, "found_mobile": True,
                "activities": {"Visual Search": {"cur": 1, "max": 1, "done": True}},
            },
        ])

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(activities, "dismiss_popups"))
            stack.enter_context(patch.object(activities, "claim_all_available_rewards"))
            stack.enter_context(patch.object(activities, "_prepare_visual_search_jpeg", return_value=fake_jpeg))
            mock_open_1 = stack.enter_context(
                patch.object(activities, "_open_visual_search_surface", return_value=(tab_1, [tab_1]))
            )
            stack.enter_context(patch.object(activities, "_trigger_file_chooser_upload", return_value=True))
            stack.enter_context(patch.object(activities, "_wait_for_vs_transition", return_value=True))
            stack.enter_context(
                patch.object(
                    activities,
                    "read_visual_search_streak_status",
                    side_effect=[
                        {"cur": 0, "max": 1, "done": False},
                        {"cur": 0, "max": 1, "done": False},
                        {"cur": 1, "max": 1, "done": True},
                    ],
                )
            )
            stack.enter_context(patch.object(activities, "get_daily_search_progress", side_effect=seq_1))
            ok_1 = activities.handle_visual_search_streak(main_1)

        self.assertTrue(ok_1)
        self.assertEqual(mock_open_1.call_count, 1)
        wait_args_1 = [c.args[0] for c in main_1.wait_for_timeout.call_args_list if c.args]
        self.assertIn(12000, wait_args_1)
        self.assertIn(8000, wait_args_1)

        # Case 2: get_daily_search_progress returns found=False / scrape_error=True -> immediate abort (1 attempt only)
        main_2, tab_2, seq_2 = _setup_vs_mocks([
            {"found": False, "found_pc": False, "found_mobile": False, "scrape_error": True, "activities": {}}
        ])

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(activities, "dismiss_popups"))
            stack.enter_context(patch.object(activities, "claim_all_available_rewards"))
            stack.enter_context(patch.object(activities, "_prepare_visual_search_jpeg", return_value=fake_jpeg))
            mock_open_2 = stack.enter_context(
                patch.object(activities, "_open_visual_search_surface", return_value=(tab_2, [tab_2]))
            )
            stack.enter_context(patch.object(activities, "_trigger_file_chooser_upload", return_value=True))
            stack.enter_context(patch.object(activities, "_wait_for_vs_transition", return_value=True))
            stack.enter_context(
                patch.object(
                    activities,
                    "read_visual_search_streak_status",
                    return_value={"cur": 0, "max": 1, "done": False},
                )
            )
            stack.enter_context(patch.object(activities, "get_daily_search_progress", side_effect=seq_2))
            ok_2 = activities.handle_visual_search_streak(main_2)

        self.assertFalse(ok_2)
        self.assertEqual(mock_open_2.call_count, 1)
        wait_args_2 = [c.args[0] for c in main_2.wait_for_timeout.call_args_list if c.args]
        self.assertIn(12000, wait_args_2)
        self.assertNotIn(8000, wait_args_2)

    def test_12d_standalone_edge_browsing_fallback_and_active_telemetry(self):
        store = StateStore(
            account_id="edge_acc",
            data_dir=self.tmp_path / "data",
            profiles_dir=self.tmp_path / "profiles",
        )
        today = datetime.date.today().isoformat()
        store.update_edge_browsing_state(1700, done=False, today_str=today)

        # Part A: active_edge_browsing_cooldown performs heartbeat + multi-URL navigation
        edge_page = MagicMock()
        edge_page.is_closed.return_value = False
        edge_ctx = MagicMock()
        edge_ctx.pages = [edge_page]
        edge_page.context = edge_ctx

        clock = {"t": time.time()}

        def fake_time():
            return clock["t"]

        def fake_sleep(secs):
            clock["t"] += float(secs)

        with (
            patch.object(browser_manager.time, "time", side_effect=fake_time),
            patch.object(browser_manager.time, "sleep", side_effect=fake_sleep),
            patch.object(browser_manager.random, "randint", side_effect=lambda a, b: a),
            patch.object(browser_manager.random, "choice", side_effect=lambda seq: seq[0]),
        ):
            _, elapsed = browser_manager.active_edge_browsing_cooldown(
                edge_page,
                total_seconds=160,
                initial_seconds=1700,
                state_store=store,
            )

        self.assertEqual(elapsed, 160)
        visited_edge_urls = [c.args[0] for c in edge_page.goto.call_args_list]
        self.assertGreaterEqual(len(visited_edge_urls), 2)
        self.assertEqual(visited_edge_urls[0], browser_manager.EDGE_BROWSING_URLS[0])
        self.assertEqual(visited_edge_urls[1], browser_manager.EDGE_BROWSING_URLS[1])
        edge_page.mouse.move.assert_called()
        edge_page.mouse.wheel.assert_called()
        edge_page.evaluate.assert_called()
        self.assertEqual(store.get_edge_browsing_state(today)["seconds"], EDGE_BROWSING_CAP_SECONDS)
        self.assertTrue(store.get_edge_browsing_state(today)["done"])

        # Part B: run_full_pipeline skips Step [2/3] when all_maxed=True but invokes run_standalone_edge_browsing
        store_pipe = StateStore(
            account_id="edge_pipe",
            data_dir=self.tmp_path / "data_pipe",
            profiles_dir=self.tmp_path / "profiles_pipe",
        )
        store_pipe.update_edge_browsing_state(1800, done=False, today_str=today)

        d_prof = self.tmp_path / "profiles_pipe" / "edge_pipe" / "desktop"
        m_prof = self.tmp_path / "profiles_pipe" / "edge_pipe" / "mobile"
        d_prof.mkdir(parents=True, exist_ok=True)
        m_prof.mkdir(parents=True, exist_ok=True)

        d_ctx = MagicMock()
        d_page = MagicMock()
        d_page.is_closed.return_value = False
        d_page.url = "https://rewards.bing.com/earn"
        d_ctx.pages = [d_page]
        d_page.context = d_ctx

        progress_maxed_edge_pending = {
            "pc_cur": 90, "pc_max": 90,
            "mobile_cur": 60, "mobile_max": 60,
            "found": True, "found_pc": True, "found_mobile": True,
            "scrape_error": False, "all_maxed": True,
            "all_activities_done": False, "primary_four_done": False,
            "activities": {
                "Bing Search": {"cur": 1, "max": 1, "done": True},
                "Daily Set": {"cur": 3, "max": 3, "done": True},
                "Edge Browsing": {"cur": 0, "max": 30, "done": False},
                "Visual Search": {"cur": 1, "max": 1, "done": True},
            },
            "bing_star_bonus": {"text": "0 / 2,100", "found": False},
        }

        class PipeArgs:
            debug = True
            desktop_target = 30
            mobile_target = 20
            batch_size = 3
            account = None
            edge_headed = False
            edge_browsing_max_seconds = EDGE_BROWSING_CAP_SECONDS

        clock_pipe = {"t": time.time()}
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(runner, "DESKTOP_PROFILE", d_prof))
            stack.enter_context(patch.object(runner, "MOBILE_PROFILE", m_prof))
            stack.enter_context(patch.object(runner, "_get_state_store", return_value=store_pipe))
            stack.enter_context(patch.object(runner, "Notifier", return_value=MagicMock()))
            stack.enter_context(patch.object(runner, "wait_for_internet", return_value=True))
            stack.enter_context(patch.object(runner, "get_sync_playwright"))
            stack.enter_context(patch.object(runner, "get_unique_search_queries", return_value=["q"] * 60))
            stack.enter_context(patch.object(runner, "ensure_desktop_page", return_value=(d_ctx, d_page)))
            stack.enter_context(patch.object(runner, "perform_warmup_visit"))
            stack.enter_context(patch.object(runner, "dismiss_popups"))
            stack.enter_context(patch.object(runner, "claim_dashboard_pending_points", return_value=False))
            stack.enter_context(patch.object(runner, "extract_current_points", return_value=4500))
            stack.enter_context(patch.object(runner, "detect_unusual_activity_warning", return_value=False))
            stack.enter_context(patch.object(runner, "init_session"))
            stack.enter_context(patch.object(runner, "update_session"))
            stack.enter_context(patch.object(runner, "finish_session"))
            stack.enter_context(patch.object(runner, "log_session_result"))
            stack.enter_context(patch.object(runner, "update_points_ledger"))
            stack.enter_context(patch.object(runner, "get_daily_search_progress", return_value=progress_maxed_edge_pending))
            stack.enter_context(patch.object(runner, "ensure_streak_protection"))
            stack.enter_context(patch.object(runner, "run_all_dashboard_activities"))
            stack.enter_context(patch.object(runner, "solve_visual_search_streak"))
            stack.enter_context(patch.object(runner, "solve_mobile_app_daily_checkin", return_value=0))
            stack.enter_context(patch.object(runner, "solve_mobile_app_promotions"))
            stack.enter_context(patch.object(runner, "solve_mobile_app_read_to_earn"))
            stack.enter_context(patch.object(runner, "solve_punch_cards"))
            stack.enter_context(patch.object(runner, "solve_all_earn_quests"))
            stack.enter_context(patch.object(runner, "solve_keep_earning_activities"))
            stack.enter_context(patch.object(runner, "claim_msn_daily_gaming_tile"))
            stack.enter_context(patch.object(runner, "solve_wallpaper_trivia"))
            stack.enter_context(patch.object(runner, "interact_with_copilot"))
            stack.enter_context(patch.object(runner, "solve_promotional_links"))
            stack.enter_context(patch.object(runner, "claim_all_available_rewards"))
            stack.enter_context(patch.object(runner, "extract_bing_star_bonus", return_value={"found": False}))
            stack.enter_context(patch.object(runner, "unload_model_from_vram"))
            stack.enter_context(patch.object(runner, "cleanup_zombie_browsers"))
            stack.enter_context(patch.object(runner, "purge_profile_caches"))
            stack.enter_context(patch.object(runner, "_close_quietly"))
            mock_search_pipe = stack.enter_context(patch.object(runner, "execute_single_search"))
            spy_edge = stack.enter_context(
                patch.object(runner, "run_standalone_edge_browsing", wraps=runner.run_standalone_edge_browsing)
            )
            stack.enter_context(patch.object(browser_manager.time, "time", side_effect=lambda: clock_pipe["t"]))
            stack.enter_context(
                patch.object(browser_manager.time, "sleep", side_effect=lambda s: clock_pipe.__setitem__("t", clock_pipe["t"] + float(s)))
            )
            stack.enter_context(patch.object(browser_manager.random, "randint", side_effect=lambda a, b: a))

            runner.run_full_pipeline(PipeArgs())

        mock_search_pipe.assert_not_called()
        spy_edge.assert_called_once()
        final_edge = store_pipe.get_edge_browsing_state(today)
        self.assertEqual(final_edge["seconds"], EDGE_BROWSING_CAP_SECONDS)
        self.assertTrue(final_edge["done"])


class TestStabilityFixesNov24(unittest.TestCase):
    """
    Unit tests cho 5 bản vá ổn định (24/11):
    1. Visual Search Renderer Crash (GPU flags removal in non-headless + TargetClosedError recovery per attempt).
    2. Cold-Start Timeout (90s / 90000ms).
    3. Bonus Tiles Timeout Cascade (wait_until='commit' + consecutive_timeouts skip guard).
    4. Bing Star Counter Regression (StateStore helpers + monotonic max in get_daily_search_progress).
    5. Edge Browsing Anti-Hang Guard (active_edge_browsing_cooldown & standalone recovery on hang/crash).
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_fix1_headless_false_gpu_flags_stripped(self):
        """Fix 1A: headless=False phải loại bỏ hoàn toàn --disable-gpu và --disable-software-rasterizer."""
        args_headless_false = browser_manager.build_launch_args(headless=False)
        self.assertNotIn("--disable-gpu", args_headless_false)
        self.assertNotIn("--disable-software-rasterizer", args_headless_false)

        args_headless_true = browser_manager.build_launch_args(headless=True)
        self.assertIn("--disable-gpu", args_headless_true)
        self.assertIn("--disable-software-rasterizer", args_headless_true)

        # Kiểm tra create_desktop_context truyền args đã lọc khi headless=False
        mock_playwright = MagicMock()
        mock_ctx = MagicMock()
        mock_playwright.chromium.launch_persistent_context.return_value = mock_ctx
        with (
            patch.object(browser_manager, "apply_stealth_scripts"),
            patch.object(browser_manager, "attach_resource_blocker"),
        ):
            browser_manager.create_desktop_context(
                mock_playwright,
                headless=False,
                account_id="test_acc",
                profile_dir=self.tmp_path / "profiles" / "desktop",
            )
        call_kwargs = mock_playwright.chromium.launch_persistent_context.call_args[1]
        passed_args = call_kwargs.get("args", [])
        self.assertNotIn("--disable-gpu", passed_args)
        self.assertNotIn("--disable-software-rasterizer", passed_args)

    def test_fix1_visual_search_tab_crash_recovery_in_attempt(self):
        """Fix 1B: Khi tab bị TargetClosedError (crash) ở attempt 1, đóng tab cũ an toàn, mở tab mới và tiếp tục attempt 2."""
        store = StateStore(
            account_id="vs_crash_acc",
            data_dir=self.tmp_path / "data",
            profiles_dir=self.tmp_path / "profiles",
        )
        page1 = MagicMock()
        page1.url = ""
        page1.is_closed.return_value = False

        page2 = MagicMock()
        page2.url = ""
        page2.is_closed.return_value = False

        context = MagicMock()
        context.new_page.return_value = page2
        page1.context = context
        page2.context = context

        attempt_counter = [0]

        class TargetClosedError(Exception):
            pass

        def fake_goto(url, **kwargs):
            attempt_counter[0] += 1
            if attempt_counter[0] == 1:
                raise TargetClosedError("Target closed / Page crashed")
            return None

        page1.goto.side_effect = fake_goto
        page2.goto.side_effect = fake_goto

        def fake_status(pg):
            if attempt_counter[0] >= 2:
                return {"done": True, "cur": 1, "max": 1}
            return {"done": False, "cur": 0, "max": 1}

        with (
            patch.object(activities, "read_visual_search_streak_status", side_effect=fake_status),
            patch.object(activities, "dismiss_popups"),
            patch.object(activities, "time"),
        ):
            ok = activities.handle_visual_search_streak(page1, state_store=store)

        self.assertTrue(ok)
        self.assertEqual(attempt_counter[0], 2)
        page1.close.assert_called_once()
        context.new_page.assert_called_once()

    def test_fix2_cold_start_timeout_90s(self):
        """Fix 2: LAUNCH_CONTEXT_TIMEOUT_MS phải là 90000ms (90s)."""
        self.assertEqual(browser_manager.LAUNCH_CONTEXT_TIMEOUT_MS, 90000)

        mock_playwright = MagicMock()
        mock_ctx = MagicMock()
        mock_playwright.chromium.launch_persistent_context.return_value = mock_ctx
        with (
            patch.object(browser_manager, "apply_stealth_scripts"),
            patch.object(browser_manager, "attach_resource_blocker"),
        ):
            browser_manager.create_desktop_context(
                mock_playwright,
                headless=True,
                account_id="test_acc_90s",
                profile_dir=self.tmp_path / "profiles" / "desktop",
            )
        call_kwargs = mock_playwright.chromium.launch_persistent_context.call_args[1]
        self.assertEqual(call_kwargs.get("timeout"), 90000)

    def test_fix3_bonus_tiles_commit_and_consecutive_timeouts_skip(self):
        """Fix 3: 3 thẻ phụ đổi sang wait_until='commit' và ngắt sớm nếu 2 thẻ liên tiếp bị TimeoutError."""
        mock_page = MagicMock()
        mock_page.wait_for_timeout = MagicMock()

        activities.reset_consecutive_timeouts()
        self.assertEqual(activities.consecutive_timeouts, 0)

        # 3A: Kiểm tra wait_until='commit' khi chạy bình thường
        activities.claim_msn_daily_gaming_tile(mock_page)
        gaming_goto = mock_page.goto.call_args_list[-1]
        self.assertEqual(gaming_goto[1].get("wait_until"), "commit")
        self.assertEqual(activities.consecutive_timeouts, 0)

        activities.solve_wallpaper_trivia(mock_page)
        trivia_goto = mock_page.goto.call_args_list[-1]
        self.assertEqual(trivia_goto[1].get("wait_until"), "commit")
        self.assertEqual(activities.consecutive_timeouts, 0)

        activities.interact_with_copilot(mock_page)
        copilot_goto = mock_page.goto.call_args_list[-1]
        self.assertEqual(copilot_goto[1].get("wait_until"), "commit")
        self.assertEqual(activities.consecutive_timeouts, 0)

        # 3B: Mô phỏng 2 thẻ phụ liên tiếp dính TimeoutError
        class MockTimeoutError(Exception):
            pass

        def fail_timeout(url, **kwargs):
            raise MockTimeoutError(f"Navigation timeout exceeded for {url}")

        mock_page.goto.side_effect = fail_timeout

        activities.reset_consecutive_timeouts()
        activities.claim_msn_daily_gaming_tile(mock_page)
        self.assertEqual(activities.consecutive_timeouts, 1)

        activities.solve_wallpaper_trivia(mock_page)
        self.assertEqual(activities.consecutive_timeouts, 2)

        # Thẻ thứ 3 (Copilot) phải bị skip hoàn toàn trước khi gọi page.goto
        goto_count_before = mock_page.goto.call_count
        activities.interact_with_copilot(mock_page)
        goto_count_after = mock_page.goto.call_count
        self.assertEqual(goto_count_before, goto_count_after)

        # Reset helper
        activities.reset_consecutive_timeouts()
        self.assertEqual(activities.consecutive_timeouts, 0)

    def test_fix4_bing_star_state_store_helper_and_monotonic_progress(self):
        """Fix 4: StateStore lưu/đọc bing_star_points theo ngày và get_daily_search_progress duy trì max đơn điệu."""
        store = StateStore(
            account_id="star_acc",
            data_dir=self.tmp_path / "data",
            profiles_dir=self.tmp_path / "profiles",
        )
        today = datetime.date.today().isoformat()

        # Helper StateStore
        self.assertEqual(store.get_bing_star_points(today), 0)
        store.set_bing_star_points(3, today)
        self.assertEqual(store.get_bing_star_points(today), 3)

        # Khi get_daily_search_progress đọc DOM mà kết quả chỉ ra 0/2100 (scraped=0)
        # Giá trị đơn điệu max(scraped=0, api=0, stored=3) phải giữ nguyên 3/2100 thay vì tụt về 0
        mock_page = MagicMock()
        mock_page.url = "https://rewards.bing.com/earn"
        mock_page.inner_text.return_value = "Desktop Bing search 90 / 90\nMobile Bing search 60 / 60"
        mock_page.evaluate.return_value = None
        mock_loc = MagicMock()
        mock_loc.first = mock_loc
        mock_loc.count.return_value = 0
        mock_loc.is_visible.return_value = False
        mock_page.locator.return_value = mock_loc

        with (
            patch.object(activities, "extract_bing_star_bonus", return_value={"current": 0, "max": 2100, "text": "0 / 2,100", "found": False}),
            patch.object(activities, "dismiss_popups"),
        ):
            prog = activities.get_daily_search_progress(mock_page, use_dapi=False, state_store=store)

        star_result = prog.get("bing_star_bonus", {})
        self.assertEqual(star_result.get("current"), 3)
        self.assertEqual(star_result.get("text"), "3 / 2,100")
        self.assertTrue(star_result.get("found"))
        self.assertEqual(store.get_bing_star_points(today), 3)

        # Nếu lần quét tiếp theo tìm thấy 5 pts (scraped=5), cập nhật lên 5
        with (
            patch.object(activities, "extract_bing_star_bonus", return_value={"current": 5, "max": 2100, "text": "5 / 2,100", "found": True}),
            patch.object(activities, "dismiss_popups"),
        ):
            prog2 = activities.get_daily_search_progress(mock_page, use_dapi=False, state_store=store)

        star_result2 = prog2.get("bing_star_bonus", {})
        self.assertEqual(star_result2.get("current"), 5)
        self.assertEqual(star_result2.get("text"), "5 / 2,100")
        self.assertEqual(store.get_bing_star_points(today), 5)

    def test_fix5_edge_browsing_anti_hang_guard(self):
        """Fix 5: active_edge_browsing_cooldown bọc URL rotation (commit, timeout=15000) và JS evaluate, tự mở tab mới khi crash/hang."""
        store = StateStore(
            account_id="eb_guard_acc",
            data_dir=self.tmp_path / "data",
            profiles_dir=self.tmp_path / "profiles",
        )
        today = datetime.date.today().isoformat()
        store.update_edge_browsing_state(1800, done=False, today_str=today)

        page1 = MagicMock()
        page1.is_closed.return_value = False
        page2 = MagicMock()
        page2.is_closed.return_value = False

        context = MagicMock()
        context.pages = [page1]
        context.new_page.return_value = page2
        page1.context = context
        page2.context = context

        # Ban đầu page1.goto gặp TimeoutError
        class MockTimeout(Exception):
            pass

        goto_calls = [0]
        def fake_goto(url, **kwargs):
            goto_calls[0] += 1
            if goto_calls[0] == 1:
                raise MockTimeout("Timeout 15000ms")
            return None

        page1.goto.side_effect = fake_goto
        page2.goto.side_effect = fake_goto

        clock = {"t": time.time()}
        def fake_time():
            return clock["t"]
        def fake_sleep(secs):
            clock["t"] += float(secs)

        with (
            patch.object(browser_manager.time, "time", side_effect=fake_time),
            patch.object(browser_manager.time, "sleep", side_effect=fake_sleep),
            patch.object(browser_manager.random, "randint", side_effect=lambda a, b: a),
            patch.object(browser_manager.random, "choice", side_effect=lambda seq: seq[0]),
        ):
            returned_page, elapsed = browser_manager.active_edge_browsing_cooldown(
                page1,
                total_seconds=60,
                initial_seconds=1800,
                state_store=store,
            )

        self.assertEqual(elapsed, 60)
        # Đã phục hồi tab mới an toàn
        context.new_page.assert_called()
        page1.close.assert_called()

        # Kiểm tra run_standalone_edge_browsing hoạt động bình thường với guard
        with (
            patch.object(browser_manager, "create_desktop_context", return_value=context),
            patch.object(browser_manager.time, "time", side_effect=fake_time),
            patch.object(browser_manager.time, "sleep", side_effect=fake_sleep),
        ):
            browsed = browser_manager.run_standalone_edge_browsing(
                playwright_instance=MagicMock(),
                headless=True,
                store=store,
            )
        self.assertGreaterEqual(browsed, 0)


class TestStatelessBurstOptimizations(unittest.TestCase):
    """
    Unit tests cho 5 nhiệm vụ tối ưu thời gian chạy (~4-6 phút/phiên, Stateless Burst):
    1. Exit-on-Cooldown Burst Mode (`runner.py`, `core/cooldown_controller.py`).
    2. Context Reuse & Compact Startup (`runner.py`, `core/searcher.py`).
    3. Daily Activities Skip-in-0s Gate (`core/state_store.py`, `runner.py`).
    4. Safe-Speed Read-to-Earn Cap (`core/mobile_app_api.py`).
    5. Batch-Only Point Check & Balanced Search Pacing (`core/searcher.py`, `core/activities.py`, `runner.py`).
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmpdir.name).resolve()
        self._orig_oauth_file = mobile_app_api.OAUTH_FILE
        mobile_app_api.set_oauth_file(self.tmp_path / "data" / "mobile_oauth.json")
        mobile_app_api.clear_oauth_memory_cache()
        mobile_app_api.reset_oauth_circuit_breaker()

    def tearDown(self):
        mobile_app_api.clear_oauth_memory_cache()
        mobile_app_api.reset_oauth_circuit_breaker()
        mobile_app_api.OAUTH_FILE = self._orig_oauth_file
        self._tmpdir.cleanup()

    def _make_profiles_and_store(self, account_id: str = "burst_acc"):
        store = StateStore(
            account_id=account_id,
            data_dir=self.tmp_path / "data",
            profiles_dir=self.tmp_path / "profiles",
        )
        d_prof = self.tmp_path / "profiles" / account_id / "desktop"
        m_prof = self.tmp_path / "profiles" / account_id / "mobile"
        d_prof.mkdir(parents=True, exist_ok=True)
        m_prof.mkdir(parents=True, exist_ok=True)
        return store, d_prof, m_prof

    def test_opt1_exit_on_cooldown_burst_mode_and_cli_flag(self):
        # 1. CooldownController default & record_batch
        ctrl_default = CooldownController()
        self.assertTrue(ctrl_default.exit_on_cooldown)
        ctrl_keep = CooldownController(exit_on_cooldown=False)
        self.assertFalse(ctrl_keep.exit_on_cooldown)

        ctrl_default.reset_burst(2000)
        self.assertFalse(ctrl_default.record_batch(2012, searches_in_batch=4))
        self.assertEqual(ctrl_default.last_batch_delta, 12)
        self.assertTrue(ctrl_default.record_batch(2012, searches_in_batch=4))
        self.assertEqual(ctrl_default.last_batch_delta, 0)
        self.assertTrue(ctrl_default.cooldown_triggered)

        # 2. CLI --no-exit-on-cooldown flag sets exit_on_cooldown=False
        seen_exit_flags: list[bool] = []
        with (
            patch.object(runner, "acquire_runner_lock", return_value=MagicMock()),
            patch.object(runner, "release_runner_lock"),
            patch.object(runner, "cleanup_zombie_browsers"),
            patch.object(runner, "is_within_circadian_window", return_value=True),
            patch.object(runner, "run_full_pipeline", side_effect=lambda a: seen_exit_flags.append(a.exit_on_cooldown) or False),
            patch.object(runner, "put_pc_to_sleep"),
            patch.object(sys, "argv", ["runner.py", "--no-sleep", "--no-exit-on-cooldown"]),
        ):
            with self.assertRaises(SystemExit) as cm:
                runner.main()
            self.assertEqual(cm.exception.code, 0)
        self.assertEqual(seen_exit_flags, [False])

        # 3. Pipeline exits immediately on cooldown when Edge Browsing is 30/30 (no 15-18m sleep)
        store, d_prof, m_prof = self._make_profiles_and_store("opt1_done_edge")
        today = datetime.date.today().isoformat()
        store.update_edge_browsing_state(EDGE_BROWSING_CAP_SECONDS, done=True, today_str=today)
        store.mark_daily_activities_completed(True, today_str=today)

        d_ctx = MagicMock()
        d_page = MagicMock()
        d_page.is_closed.return_value = False
        d_page.url = "https://www.bing.com/search?q=test"
        d_ctx.pages = [d_page]

        prog_active = {
            "pc_cur": 0, "pc_max": 90,
            "mobile_cur": 0, "mobile_max": 60,
            "found": True, "found_pc": True, "found_mobile": True,
            "scrape_error": False, "all_maxed": False,
            "all_activities_done": True, "primary_four_done": True,
            "balance": 2000,
            "activities": {
                "Daily Set": {"cur": 3, "max": 3, "done": True},
                "Edge Browsing": {"cur": 30, "max": 30, "done": True},
                "Visual Search": {"cur": 1, "max": 1, "done": True},
            },
            "bing_star_bonus": {"text": "0 / 2,100", "found": False},
        }

        sleep_durations: list[float] = []

        class BurstArgs:
            debug = True
            desktop_target = 30
            mobile_target = 20
            batch_size = 3
            account = None
            exit_on_cooldown = True

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(runner, "DESKTOP_PROFILE", d_prof))
            stack.enter_context(patch.object(runner, "MOBILE_PROFILE", m_prof))
            stack.enter_context(patch.object(runner, "_get_state_store", return_value=store))
            stack.enter_context(patch.object(runner, "Notifier", return_value=MagicMock()))
            stack.enter_context(patch.object(runner, "wait_for_internet", return_value=True))
            stack.enter_context(patch.object(runner, "get_sync_playwright"))
            stack.enter_context(patch.object(runner, "get_unique_search_queries", return_value=[f"q{i}" for i in range(60)]))
            stack.enter_context(patch.object(runner, "get_daily_search_progress", return_value=prog_active))
            stack.enter_context(patch.object(runner, "ensure_desktop_page", return_value=(d_ctx, d_page)))
            stack.enter_context(patch.object(runner, "get_variable_batch_size", return_value=4))
            stack.enter_context(patch.object(runner, "execute_single_search", return_value=True))
            stack.enter_context(patch.object(runner, "extract_current_points", return_value=2000))
            stack.enter_context(patch.object(runner, "detect_unusual_activity_warning", return_value=False))
            stack.enter_context(patch.object(runner, "save_searched_keyword"))
            stack.enter_context(patch.object(runner, "init_session"))
            stack.enter_context(patch.object(runner, "update_session"))
            stack.enter_context(patch.object(runner, "finish_session"))
            stack.enter_context(patch.object(runner, "log_session_result"))
            stack.enter_context(patch.object(runner, "unload_model_from_vram"))
            stack.enter_context(patch.object(runner, "cleanup_zombie_browsers"))
            stack.enter_context(patch.object(runner, "purge_profile_caches"))
            mock_standalone = stack.enter_context(patch.object(runner, "run_standalone_edge_browsing", return_value=0))
            stack.enter_context(patch("time.sleep", side_effect=lambda s: sleep_durations.append(float(s))))

            res = runner.run_full_pipeline(BurstArgs())

        self.assertFalse(res)
        self.assertFalse(any(s >= 60 for s in sleep_durations), f"Unexpected long cooldown sleep: {sleep_durations}")
        mock_standalone.assert_not_called()
        d_ctx.close.assert_called()
        self.assertEqual(store.get_last_known_points(), 2000)

        # 4. If Edge Browsing < 30/30 when cooldown hits, runs 1 standalone Edge session then exits
        store2, d_prof2, m_prof2 = self._make_profiles_and_store("opt1_pending_edge")
        store2.update_edge_browsing_state(300, done=False, today_str=today)
        store2.mark_daily_activities_completed(True, today_str=today)
        prog_pending_edge = dict(prog_active)
        prog_pending_edge["activities"] = {
            "Daily Set": {"cur": 3, "max": 3, "done": True},
            "Edge Browsing": {"cur": 5, "max": 30, "done": False},
            "Visual Search": {"cur": 1, "max": 1, "done": True},
        }
        sleep_durations2: list[float] = []
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(runner, "DESKTOP_PROFILE", d_prof2))
            stack.enter_context(patch.object(runner, "MOBILE_PROFILE", m_prof2))
            stack.enter_context(patch.object(runner, "_get_state_store", return_value=store2))
            stack.enter_context(patch.object(runner, "Notifier", return_value=MagicMock()))
            stack.enter_context(patch.object(runner, "wait_for_internet", return_value=True))
            stack.enter_context(patch.object(runner, "get_sync_playwright"))
            stack.enter_context(patch.object(runner, "get_unique_search_queries", return_value=[f"q{i}" for i in range(60)]))
            stack.enter_context(patch.object(runner, "get_daily_search_progress", return_value=prog_pending_edge))
            stack.enter_context(patch.object(runner, "ensure_desktop_page", return_value=(d_ctx, d_page)))
            stack.enter_context(patch.object(runner, "get_variable_batch_size", return_value=4))
            stack.enter_context(patch.object(runner, "execute_single_search", return_value=True))
            stack.enter_context(patch.object(runner, "extract_current_points", return_value=2000))
            stack.enter_context(patch.object(runner, "detect_unusual_activity_warning", return_value=False))
            stack.enter_context(patch.object(runner, "save_searched_keyword"))
            stack.enter_context(patch.object(runner, "init_session"))
            stack.enter_context(patch.object(runner, "update_session"))
            stack.enter_context(patch.object(runner, "finish_session"))
            stack.enter_context(patch.object(runner, "log_session_result"))
            stack.enter_context(patch.object(runner, "unload_model_from_vram"))
            stack.enter_context(patch.object(runner, "cleanup_zombie_browsers"))
            stack.enter_context(patch.object(runner, "purge_profile_caches"))
            mock_standalone2 = stack.enter_context(patch.object(runner, "run_standalone_edge_browsing", return_value=120))
            stack.enter_context(patch("time.sleep", side_effect=lambda s: sleep_durations2.append(float(s))))

            runner.run_full_pipeline(BurstArgs())

        mock_standalone2.assert_called_once()
        self.assertFalse(any(s >= 60 for s in sleep_durations2))

    def test_opt2_compact_startup_no_warmup_and_context_reuse(self):
        store, d_prof, m_prof = self._make_profiles_and_store("opt2_reuse")
        today = datetime.date.today().isoformat()
        store.update_edge_browsing_state(EDGE_BROWSING_CAP_SECONDS, done=True, today_str=today)

        d_ctx = MagicMock()
        d_page = MagicMock()
        d_page.is_closed.return_value = False
        d_page.url = "https://rewards.bing.com/earn"
        d_ctx.pages = [d_page]

        prog_1 = {
            "pc_cur": 84, "pc_max": 90,
            "mobile_cur": 60, "mobile_max": 60,
            "found": True, "found_pc": True, "found_mobile": True,
            "scrape_error": False, "all_maxed": False,
            "all_activities_done": True, "primary_four_done": True,
            "balance": 3000,
            "activities": {
                "Bing Search": {"cur": 1, "max": 1, "done": True},
                "Daily Set": {"cur": 3, "max": 3, "done": True},
                "Edge Browsing": {"cur": 30, "max": 30, "done": True},
                "Visual Search": {"cur": 1, "max": 1, "done": True},
            },
            "bing_star_bonus": {"text": "0 / 2,100", "found": False},
        }
        prog_done = dict(prog_1, pc_cur=90, all_maxed=True)

        randint_calls: list[tuple[int, int]] = []
        orig_randint = runner.random.randint

        def spy_randint(a, b):
            randint_calls.append((a, b))
            return orig_randint(a, b)

        close_calls: list[object] = []
        ensure_calls: list[object] = []

        def fake_ensure(ctx, pw, headless):
            ensure_calls.append(ctx)
            return d_ctx, d_page

        class NonDebugArgs:
            debug = False
            desktop_target = 30
            mobile_target = 20
            batch_size = 3
            account = None
            exit_on_cooldown = True

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(runner, "DESKTOP_PROFILE", d_prof))
            stack.enter_context(patch.object(runner, "MOBILE_PROFILE", m_prof))
            stack.enter_context(patch.object(runner, "_get_state_store", return_value=store))
            stack.enter_context(patch.object(runner, "Notifier", return_value=MagicMock()))
            stack.enter_context(patch.object(runner, "wait_for_internet", return_value=True))
            stack.enter_context(patch.object(runner, "get_sync_playwright"))
            stack.enter_context(patch.object(runner, "get_unique_search_queries", return_value=[f"q{i}" for i in range(60)]))
            stack.enter_context(patch.object(runner, "ensure_desktop_page", side_effect=fake_ensure))
            mock_warmup = stack.enter_context(patch.object(runner, "perform_warmup_visit"))
            stack.enter_context(patch.object(runner, "dismiss_popups"))
            stack.enter_context(patch.object(runner, "claim_dashboard_pending_points", return_value=False))
            stack.enter_context(patch.object(runner, "extract_current_points", return_value=3006))
            stack.enter_context(patch.object(runner, "detect_unusual_activity_warning", return_value=False))
            stack.enter_context(patch.object(runner, "init_session"))
            stack.enter_context(patch.object(runner, "update_session"))
            stack.enter_context(patch.object(runner, "finish_session"))
            stack.enter_context(patch.object(runner, "log_session_result"))
            stack.enter_context(patch.object(runner, "update_points_ledger"))
            stack.enter_context(patch.object(runner, "get_daily_search_progress", side_effect=[prog_1, prog_1, prog_done]))
            stack.enter_context(patch.object(runner, "ensure_streak_protection"))
            stack.enter_context(patch.object(runner, "run_all_dashboard_activities"))
            stack.enter_context(patch.object(runner, "solve_visual_search_streak"))
            stack.enter_context(patch.object(runner, "solve_mobile_app_daily_checkin", return_value=0))
            stack.enter_context(patch.object(runner, "solve_mobile_app_promotions"))
            stack.enter_context(patch.object(runner, "solve_mobile_app_read_to_earn"))
            stack.enter_context(patch.object(runner, "solve_punch_cards"))
            stack.enter_context(patch.object(runner, "solve_all_earn_quests"))
            stack.enter_context(patch.object(runner, "solve_keep_earning_activities", return_value=0))
            stack.enter_context(patch.object(runner, "claim_msn_daily_gaming_tile"))
            stack.enter_context(patch.object(runner, "solve_wallpaper_trivia"))
            stack.enter_context(patch.object(runner, "interact_with_copilot"))
            stack.enter_context(patch.object(runner, "solve_promotional_links"))
            stack.enter_context(patch.object(runner, "claim_all_available_rewards"))
            stack.enter_context(patch.object(runner, "extract_bing_star_bonus", return_value={"found": False}))
            stack.enter_context(patch.object(runner, "unload_model_from_vram"))
            stack.enter_context(patch.object(runner, "cleanup_zombie_browsers"))
            stack.enter_context(patch.object(runner, "purge_profile_caches"))
            stack.enter_context(patch.object(runner, "save_searched_keyword"))
            stack.enter_context(patch.object(runner, "execute_single_search", return_value=True))
            stack.enter_context(patch.object(runner, "_close_quietly", side_effect=lambda c: close_calls.append(c)))
            stack.enter_context(patch.object(runner.random, "randint", side_effect=spy_randint))
            stack.enter_context(patch("time.sleep"))

            ok = runner.run_full_pipeline(NonDebugArgs())

        self.assertTrue(ok)
        # Startup jitter is (15, 30), never (30, 90)
        self.assertIn((15, 30), randint_calls)
        self.assertNotIn((30, 90), randint_calls)
        # No warmup visit; navigates straight to rewards.bing.com/earn
        mock_warmup.assert_not_called()
        d_page.goto.assert_any_call("https://rewards.bing.com/earn", wait_until="domcontentloaded")
        # Context opened in Step [1/3] is reused (passed non-None) into Step [2/3] searches
        self.assertIsNone(ensure_calls[0])
        self.assertIs(ensure_calls[1], d_ctx)

    def test_opt3_daily_activities_skip_in_0s_gate(self):
        store, d_prof, m_prof = self._make_profiles_and_store("opt3_gate")
        today = datetime.date.today().isoformat()
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()

        self.assertFalse(store.is_daily_activities_completed(today))
        store.mark_daily_activities_completed(today)
        self.assertTrue(store.is_daily_activities_completed(today))
        self.assertFalse(store.is_daily_activities_completed(tomorrow))

        # Verify run_full_pipeline skips ALL Step [1/3] activities in 0s when gate is True
        store.update_edge_browsing_state(EDGE_BROWSING_CAP_SECONDS, done=True, today_str=today)
        d_ctx = MagicMock()
        d_page = MagicMock()
        d_page.is_closed.return_value = False
        d_page.url = "https://www.bing.com/search?q=ok"
        d_ctx.pages = [d_page]

        prog_need_search = {
            "pc_cur": 84, "pc_max": 90,
            "mobile_cur": 60, "mobile_max": 60,
            "found": True, "found_pc": True, "found_mobile": True,
            "scrape_error": False, "all_maxed": False,
            "all_activities_done": True, "primary_four_done": True,
            "balance": 3500,
            "activities": {
                "Daily Set": {"cur": 3, "max": 3, "done": True},
                "Edge Browsing": {"cur": 30, "max": 30, "done": True},
                "Visual Search": {"cur": 1, "max": 1, "done": True},
            },
            "bing_star_bonus": {"text": "0 / 2,100", "found": False},
        }
        prog_done = dict(prog_need_search, pc_cur=90, all_maxed=True)

        class GateArgs:
            debug = True
            desktop_target = 30
            mobile_target = 20
            batch_size = 3
            account = None
            exit_on_cooldown = True

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(runner, "DESKTOP_PROFILE", d_prof))
            stack.enter_context(patch.object(runner, "MOBILE_PROFILE", m_prof))
            stack.enter_context(patch.object(runner, "_get_state_store", return_value=store))
            stack.enter_context(patch.object(runner, "Notifier", return_value=MagicMock()))
            stack.enter_context(patch.object(runner, "wait_for_internet", return_value=True))
            stack.enter_context(patch.object(runner, "get_sync_playwright"))
            stack.enter_context(patch.object(runner, "get_unique_search_queries", return_value=[f"q{i}" for i in range(60)]))
            stack.enter_context(patch.object(runner, "get_daily_search_progress", side_effect=[prog_need_search, prog_done]))
            stack.enter_context(patch.object(runner, "ensure_desktop_page", return_value=(d_ctx, d_page)))
            stack.enter_context(patch.object(runner, "dismiss_popups"))
            stack.enter_context(patch.object(runner, "claim_dashboard_pending_points", return_value=False))
            stack.enter_context(patch.object(runner, "extract_current_points", return_value=3506))
            stack.enter_context(patch.object(runner, "detect_unusual_activity_warning", return_value=False))
            stack.enter_context(patch.object(runner, "init_session"))
            stack.enter_context(patch.object(runner, "update_session"))
            stack.enter_context(patch.object(runner, "finish_session"))
            stack.enter_context(patch.object(runner, "log_session_result"))
            stack.enter_context(patch.object(runner, "update_points_ledger"))
            stack.enter_context(patch.object(runner, "extract_bing_star_bonus", return_value={"found": False}))
            stack.enter_context(patch.object(runner, "unload_model_from_vram"))
            stack.enter_context(patch.object(runner, "cleanup_zombie_browsers"))
            stack.enter_context(patch.object(runner, "purge_profile_caches"))
            stack.enter_context(patch.object(runner, "save_searched_keyword"))
            mock_dash = stack.enter_context(patch.object(runner, "run_all_dashboard_activities"))
            mock_vs = stack.enter_context(patch.object(runner, "solve_visual_search_streak"))
            mock_ci = stack.enter_context(patch.object(runner, "solve_mobile_app_daily_checkin"))
            mock_rte = stack.enter_context(patch.object(runner, "solve_mobile_app_read_to_earn"))
            mock_ke = stack.enter_context(patch.object(runner, "solve_keep_earning_activities"))
            mock_search = stack.enter_context(patch.object(runner, "execute_single_search", return_value=True))
            stack.enter_context(patch("time.sleep"))

            ok = runner.run_full_pipeline(GateArgs())

        self.assertTrue(ok)
        mock_dash.assert_not_called()
        mock_vs.assert_not_called()
        mock_ci.assert_not_called()
        mock_rte.assert_not_called()
        mock_ke.assert_not_called()
        self.assertEqual(mock_search.call_count, 2)

    def test_opt4_safe_speed_read_to_earn_cap_and_delay(self):
        store, _, _ = self._make_profiles_and_store("opt4_rte")
        page = MagicMock()

        # 5 articles x 6 pts = 30 pts -> exits at 5th article before sleeping or posting 6..10
        resp_6 = MagicMock()
        resp_6.status_code = 200
        resp_6.json.return_value = {"response": {"balance": 2030, "activity": {"p": 6}}}

        uniform_ranges: list[tuple[float, float]] = []
        sleep_vals: list[float] = []

        def fake_uniform(a, b):
            uniform_ranges.append((a, b))
            return (a + b) / 2.0

        with (
            patch.object(mobile_app_api, "get_mobile_access_token", return_value="tok_rte"),
            patch.object(mobile_app_api.requests, "post", return_value=resp_6) as mock_post,
            patch.object(mobile_app_api.random, "uniform", side_effect=fake_uniform),
            patch.object(mobile_app_api.time, "sleep", side_effect=lambda s: sleep_vals.append(s)),
        ):
            earned = mobile_app_api.solve_mobile_app_read_to_earn(page, state_store=store)

        self.assertEqual(earned, 30)
        self.assertEqual(mock_post.call_count, 5)
        self.assertEqual(len(sleep_vals), 4)
        self.assertTrue(all(r == (6.0, 9.5) for r in uniform_ranges))
        self.assertTrue(store.is_read_to_earn_done())

    def test_opt5_sequential_batch_only_point_check_and_pacing(self):
        from core import searcher

        # 1. Stealth CTR 25% & 3.0-5.0s dwell time in core/searcher.py
        page_serp = MagicMock()
        page_serp.url = "https://www.bing.com/search?q=test"
        with (
            patch.object(searcher, "smooth_scroll"),
            patch.object(searcher.random, "random", return_value=0.26),
            patch.object(searcher, "simulate_organic_click") as mock_ctr_skip,
        ):
            searcher._perform_post_search_dwell_and_ctr(page_serp, is_mobile=False)
        mock_ctr_skip.assert_not_called()

        with (
            patch.object(searcher, "smooth_scroll"),
            patch.object(searcher.random, "random", return_value=0.24),
            patch.object(searcher, "simulate_organic_click") as mock_ctr_hit,
        ):
            searcher._perform_post_search_dwell_and_ctr(page_serp, is_mobile=False)
        mock_ctr_hit.assert_called_once()

        # Verify simulate_organic_click dwell uses (3.0, 5.0)
        ctr_page = MagicMock()
        ctr_page.url = "https://www.bing.com/search?q=test"
        target_el = MagicMock()
        target_el.is_visible.return_value = True
        target_el.bounding_box.return_value = None
        results_loc = MagicMock()
        results_loc.count.return_value = 1
        results_loc.nth.return_value = target_el
        ctr_page.locator.return_value = results_loc
        ctr_page.context.pages = [ctr_page]
        ctr_cm = MagicMock()
        ctr_cm.__exit__.side_effect = TimeoutError("same tab")
        ctr_page.context.expect_page.return_value = ctr_cm

        dwell_args: list[float] = []
        with (
            patch.object(searcher, "_organic_read_scroll", side_effect=lambda p, d, is_mobile=False: dwell_args.append(d)),
            patch.object(searcher.time, "sleep"),
        ):
            searcher.simulate_organic_click(ctr_page, is_mobile=True)
        self.assertEqual(len(dwell_args), 1)
        self.assertGreaterEqual(dwell_args[0], 3.0)
        self.assertLessEqual(dwell_args[0], 5.0)

        # 2. UrlOffer dwell time in core/activities.py uses (3.0, 4.5)
        offer_page = MagicMock()
        offer_page.url = "https://www.msn.com/article"
        hidden_loc = MagicMock()
        hidden_loc.first = hidden_loc
        hidden_loc.count.return_value = 0
        hidden_loc.is_visible.return_value = False
        offer_page.locator.return_value = hidden_loc
        offer_page.evaluate.return_value = "article content"
        offer_sleeps: list[float] = []
        with patch.object(activities.time, "sleep", side_effect=lambda s: offer_sleeps.append(float(s))):
            activities.solve_interactive_tab(offer_page, card_title="Sample Offer")
        self.assertEqual(len(offer_sleeps), 1)
        self.assertGreaterEqual(offer_sleeps[0], 3.0)
        self.assertLessEqual(offer_sleeps[0], 4.5)

        # 3. Runner runs Desktop then Mobile sequentially, checks points ONCE per batch, sleeps (4.0, 6.5)
        store, d_prof, m_prof = self._make_profiles_and_store("opt5_batch")
        today = datetime.date.today().isoformat()
        store.update_edge_browsing_state(EDGE_BROWSING_CAP_SECONDS, done=True, today_str=today)
        store.mark_daily_activities_completed(True, today_str=today)

        d_ctx, m_ctx = MagicMock(), MagicMock()
        d_page, m_page = MagicMock(), MagicMock()
        d_page.is_closed.return_value = False
        m_page.is_closed.return_value = False
        d_page.url = "https://www.bing.com/search?q=pc"
        m_page.url = "https://www.bing.com/search?q=mob"
        d_ctx.pages = [d_page]
        m_ctx.pages = [m_page]

        prog_both = {
            "pc_cur": 78, "pc_max": 90,
            "mobile_cur": 51, "mobile_max": 60,
            "found": True, "found_pc": True, "found_mobile": True,
            "scrape_error": False, "all_maxed": False,
            "all_activities_done": True, "primary_four_done": True,
            "balance": 4000,
            "activities": {
                "Daily Set": {"cur": 3, "max": 3, "done": True},
                "Edge Browsing": {"cur": 30, "max": 30, "done": True},
                "Visual Search": {"cur": 1, "max": 1, "done": True},
            },
            "bing_star_bonus": {"text": "0 / 2,100", "found": False},
        }
        prog_done = dict(prog_both, pc_cur=90, mobile_cur=60, all_maxed=True)

        order_log: list[str] = []
        point_checks: list[str] = []
        inter_search_uniforms: list[tuple[float, float]] = []

        def fake_search(page, query, device_type="desktop"):
            kind = "mobile" if device_type == "mobile" else "desktop"
            order_log.append(kind)
            return True

        def fake_extract(page):
            kind = "mobile" if page is m_page else "desktop"
            point_checks.append(kind)
            return 4012 if page is d_page else 4021

        orig_runner_uniform = runner.random.uniform

        def spy_runner_uniform(a, b):
            inter_search_uniforms.append((a, b))
            return orig_runner_uniform(a, b)

        class SeqArgs:
            debug = True
            desktop_target = 30
            mobile_target = 20
            batch_size = 3
            account = None
            exit_on_cooldown = True

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(runner, "DESKTOP_PROFILE", d_prof))
            stack.enter_context(patch.object(runner, "MOBILE_PROFILE", m_prof))
            stack.enter_context(patch.object(runner, "_get_state_store", return_value=store))
            stack.enter_context(patch.object(runner, "Notifier", return_value=MagicMock()))
            stack.enter_context(patch.object(runner, "wait_for_internet", return_value=True))
            stack.enter_context(patch.object(runner, "get_sync_playwright"))
            stack.enter_context(patch.object(runner, "get_unique_search_queries", return_value=[f"q{i}" for i in range(60)]))
            stack.enter_context(patch.object(runner, "get_daily_search_progress", side_effect=[prog_both, prog_done]))
            stack.enter_context(patch.object(runner, "ensure_desktop_page", return_value=(d_ctx, d_page)))
            stack.enter_context(patch.object(runner, "ensure_mobile_page", return_value=(m_ctx, m_page)))
            stack.enter_context(patch.object(runner, "get_variable_batch_size", side_effect=lambda b, device_type="desktop": 4 if device_type == "desktop" else 3))
            stack.enter_context(patch.object(runner, "execute_single_search", side_effect=fake_search))
            stack.enter_context(patch.object(runner, "extract_current_points", side_effect=fake_extract))
            stack.enter_context(patch.object(runner, "detect_unusual_activity_warning", return_value=False))
            stack.enter_context(patch.object(runner, "claim_dashboard_pending_points", return_value=False))
            stack.enter_context(patch.object(runner, "extract_bing_star_bonus", return_value={"found": False}))
            stack.enter_context(patch.object(runner, "save_searched_keyword"))
            stack.enter_context(patch.object(runner, "init_session"))
            stack.enter_context(patch.object(runner, "update_session"))
            stack.enter_context(patch.object(runner, "finish_session"))
            stack.enter_context(patch.object(runner, "log_session_result"))
            stack.enter_context(patch.object(runner, "update_points_ledger"))
            stack.enter_context(patch.object(runner, "unload_model_from_vram"))
            stack.enter_context(patch.object(runner, "cleanup_zombie_browsers"))
            stack.enter_context(patch.object(runner, "purge_profile_caches"))
            stack.enter_context(patch.object(runner.random, "uniform", side_effect=spy_runner_uniform))
            stack.enter_context(patch("time.sleep"))

            ok = runner.run_full_pipeline(SeqArgs())

        self.assertTrue(ok)
        # Sequential: 4 desktop searches strictly before 3 mobile searches
        self.assertEqual(order_log, ["desktop"] * 4 + ["mobile"] * 3)
        # Batch-only point check: 1 desktop batch check + 1 mobile batch check + 1 final summary check
        self.assertEqual(point_checks, ["desktop", "mobile", "desktop"])
        self.assertIn((4.0, 6.5), inter_search_uniforms)


if __name__ == "__main__":
    unittest.main(verbosity=2)
