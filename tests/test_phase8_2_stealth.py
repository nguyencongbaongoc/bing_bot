import os
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
from core.fingerprint import inject_stealth_fingerprint
from core.searcher import _organic_read_scroll


class TestPhase82Stealth(unittest.TestCase):
    def test_no_synthetic_flags_in_launch_args(self):
        forbidden_exact = {
            "--no-sandbox",
            "--use-mock-keychain",
            "--password-store=basic",
            "--disable-application-cache",
        }
        forbidden_prefixes = ("--disk-cache-size", "--media-cache-size")

        for headless in (True, False):
            for is_mobile in (False, True):
                args = runner.build_launch_args(headless=headless, is_mobile=is_mobile)
                for flag in args:
                    self.assertNotIn(flag, forbidden_exact)
                    for prefix in forbidden_prefixes:
                        self.assertFalse(
                            flag.startswith(prefix),
                            f"Unexpected synthetic flag {flag} in build_launch_args",
                        )
                if headless:
                    self.assertIn("--headless=new", args)
                    self.assertNotIn("--headless", args)

    def test_pixel_7_mobile_context_screen_and_headers(self):
        mock_pw = MagicMock()
        mock_pw.devices = {
            "Pixel 7": {
                "user_agent": "Mozilla/5.0 Chrome/131.0.0.0 Mobile",
                "viewport": {"width": 412, "height": 839},
                "device_scale_factor": 2.625,
                "is_mobile": True,
                "has_touch": True,
                "default_browser_type": "chromium",
            }
        }
        mock_ctx = MagicMock()
        mock_pw.chromium.launch_persistent_context.return_value = mock_ctx

        with patch.object(runner, "apply_stealth_scripts"):
            runner.create_mobile_context(mock_pw, headless=True)

        call_kwargs = mock_pw.chromium.launch_persistent_context.call_args.kwargs
        self.assertEqual(call_kwargs["screen"], {"width": 412, "height": 915})
        self.assertEqual(call_kwargs["viewport"], {"width": 412, "height": 915})
        self.assertEqual(call_kwargs["device_scale_factor"], 2.625)
        self.assertTrue(call_kwargs["is_mobile"])
        self.assertTrue(call_kwargs["has_touch"])
        self.assertEqual(call_kwargs["locale"], "vi-VN")
        self.assertEqual(call_kwargs["timezone_id"], "Asia/Ho_Chi_Minh")
        self.assertEqual(call_kwargs["extra_http_headers"]["Sec-CH-UA-Platform"], '"Android"')
        self.assertEqual(call_kwargs["extra_http_headers"]["Sec-CH-UA-Mobile"], "?1")
        self.assertEqual(call_kwargs["extra_http_headers"]["Sec-CH-UA-Model"], '"Pixel 7"')

        # Verify mobile fingerprint injection aligns to 8 cores / 8 GB without GPU/WebGL override
        mock_fp_ctx = MagicMock()
        inject_stealth_fingerprint(mock_fp_ctx, account_id="acc_test", is_mobile=True)
        injected_js = mock_fp_ctx.add_init_script.call_args[0][0]
        self.assertIn("() => 8", injected_js)
        self.assertNotIn("WEBGL_debug_renderer_info", injected_js)

    def test_variable_batch_size_and_lognormal_cooldown(self):
        desktop_batches = {runner.get_variable_batch_size(3, device_type="desktop") for _ in range(100)}
        self.assertTrue(desktop_batches.issubset({4, 5, 6}))
        self.assertEqual(desktop_batches, {4, 5, 6})
        mobile_batches = {runner.get_variable_batch_size(3, device_type="mobile") for _ in range(100)}
        self.assertTrue(mobile_batches.issubset({3, 4, 5}))
        self.assertEqual(mobile_batches, {3, 4, 5})
        self.assertEqual(runner.get_variable_batch_size(7), 7)

        cooldowns = [runner.get_natural_cooldown_seconds() for _ in range(200)]
        for cd in cooldowns:
            self.assertGreaterEqual(cd, 900)
            self.assertLessEqual(cd, 1080)
        self.assertGreater(len(set(cooldowns)), 10)

    def test_ssd_guard_retention_by_age_and_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fresh_cache = root / "desktop" / "Default" / "Cache"
            old_cache = root / "mobile" / "Default" / "Code Cache"
            fresh_cache.mkdir(parents=True)
            old_cache.mkdir(parents=True)

            fresh_file = fresh_cache / "asset.bin"
            old_file = old_cache / "old.bin"
            fresh_file.write_bytes(b"a" * 1024)
            old_file.write_bytes(b"b" * 1024)

            # Set old_cache mtime to 10 days ago
            ten_days_ago = time.time() - (10 * 86400)
            os.utime(old_cache, (ten_days_ago, ten_days_ago))

            runner.purge_profile_caches(str(root), max_age_days=7, max_total_mb=500)

            self.assertTrue(fresh_cache.exists(), "Warm cache < 7 days and < 500 MB must be kept")
            self.assertFalse(old_cache.exists(), "Cache > 7 days must be pruned")

            # Now test size-based pruning (max_total_mb=1)
            big_old = root / "desktop" / "Default" / "GPUCache"
            big_new = root / "desktop" / "Default" / "DawnCache"
            big_old.mkdir(parents=True)
            big_new.mkdir(parents=True)
            (big_old / "data1.bin").write_bytes(b"x" * (800 * 1024))
            (big_new / "data2.bin").write_bytes(b"y" * (800 * 1024))

            two_days_ago = time.time() - (2 * 86400)
            os.utime(big_old, (two_days_ago, two_days_ago))

            runner.purge_profile_caches(str(root), max_age_days=7, max_total_mb=1)
            self.assertFalse(big_old.exists(), "Oldest cache should be pruned when exceeding size limit")
            self.assertTrue(big_new.exists(), "Newest cache should remain once total size <= limit")

    def test_organic_read_scroll_and_warmup_visit(self):
        mock_page = MagicMock()
        scroll_deltas = []
        with (
            patch("core.searcher.time.sleep"),
            patch("core.searcher.smooth_scroll", side_effect=lambda p, y, is_mobile=False: scroll_deltas.append(y)),
        ):
            _organic_read_scroll(mock_page, dwell=8.0, is_mobile=False)

        self.assertGreaterEqual(len(scroll_deltas), 2)
        self.assertLessEqual(len(scroll_deltas), 3)

        warmup_page = MagicMock()
        with patch("runner.time.sleep"):
            ok = runner.perform_warmup_visit(warmup_page, duration_range=(5.0, 8.0))
        self.assertTrue(ok)
        warmup_page.goto.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
