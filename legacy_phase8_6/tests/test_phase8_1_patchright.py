import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import runner
from core.activities import is_valid_visual_search_result, upload_visual_search_image
from core.searcher import simulate_organic_click
from core.tracker import extract_current_points


class TargetClosedError(Exception):
    pass


class TestPhase81Patchright(unittest.TestCase):
    def test_deduplicated_launch_args_and_mobile_params(self):
        headless_args = runner.build_launch_args(headless=True, is_mobile=False)
        self.assertIn("--headless=new", headless_args)
        self.assertNotIn("--headless", headless_args)
        self.assertIn("--disable-gpu", headless_args)
        self.assertIn("--disable-software-rasterizer", headless_args)
        self.assertEqual(len(headless_args), len(set(headless_args)))

        for dup_flag in (
            "--disable-blink-features=AutomationControlled",
            "--disable-search-engine-choice-screen",
            "--disable-background-timer-throttling",
        ):
            self.assertNotIn(dup_flag, headless_args)

        headed_args = runner.build_launch_args(headless=False, is_mobile=False)
        self.assertNotIn("--headless=new", headed_args)
        self.assertNotIn("--headless", headed_args)
        self.assertIn("--start-maximized", headed_args)

        # Verify create_mobile_context enforces Pixel 7 412x915 parameters
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
        self.assertEqual(call_kwargs["viewport"], {"width": 412, "height": 915})
        self.assertEqual(call_kwargs["device_scale_factor"], 2.625)
        self.assertTrue(call_kwargs["is_mobile"])
        self.assertTrue(call_kwargs["has_touch"])
        self.assertEqual(call_kwargs["ignore_default_args"], ["--headless"])
        self.assertIn("--headless=new", call_kwargs["args"])
        self.assertNotIn("--headless", call_kwargs["args"])

    def test_ensure_desktop_page_retry_recovery_on_target_closed(self):
        mock_page = MagicMock()
        mock_page.is_closed.return_value = False
        mock_ctx = MagicMock()
        mock_ctx.pages = [mock_page]

        # Attempt 1 & 2 raise TargetClosedError; Attempt 3 succeeds
        side_effects = [
            TargetClosedError("TargetClosedError: browser closed (exitCode=21)"),
            TargetClosedError("TargetClosedError: browser closed (exitCode=21)"),
            mock_ctx,
        ]

        sleep_calls = []
        cleanup_calls = []

        with (
            patch.object(runner, "create_desktop_context", side_effect=side_effects) as mock_create,
            patch.object(runner.time, "sleep", side_effect=lambda s: sleep_calls.append(s)),
            patch.object(runner, "_cleanup_profile_locks_and_processes", side_effect=lambda p: cleanup_calls.append(p)),
        ):
            ctx, page = runner.ensure_desktop_page(None, MagicMock(), headless=True)

        self.assertIs(ctx, mock_ctx)
        self.assertIs(page, mock_page)
        self.assertEqual(mock_create.call_count, 3)
        self.assertEqual(sleep_calls, [2, 5])
        self.assertEqual(cleanup_calls, [runner.DESKTOP_PROFILE, runner.DESKTOP_PROFILE])

    def test_visual_search_set_input_files_and_url_matching(self):
        # 1. Direct set_input_files prioritized when file input is attached
        mock_page = MagicMock()
        mock_file_inp = MagicMock()
        mock_file_inp.count.return_value = 1
        mock_locator = MagicMock()
        mock_locator.first = mock_file_inp
        mock_page.locator.return_value = mock_locator

        ok = upload_visual_search_image(mock_page, "data/temp_vs_upload.jpg")
        self.assertTrue(ok)
        mock_file_inp.set_input_files.assert_called_once_with("data/temp_vs_upload.jpg")
        mock_page.expect_file_chooser.assert_not_called()

        # 2. Fallback to expect_file_chooser when file input is not attached
        mock_page_fb = MagicMock()
        mock_no_inp = MagicMock()
        mock_no_inp.count.return_value = 0
        mock_browse_btn = MagicMock()
        mock_browse_btn.count.return_value = 1
        mock_browse_btn.is_visible.return_value = True

        def locator_side_effect(sel):
            loc = MagicMock()
            loc.first = mock_no_inp if "input[type=\"file\"]" in sel else mock_browse_btn
            return loc

        mock_page_fb.locator.side_effect = locator_side_effect
        mock_fc = MagicMock()
        mock_page_fb.expect_file_chooser.return_value.__enter__.return_value.value = mock_fc

        ok_fb = upload_visual_search_image(mock_page_fb, "data/temp_vs_upload.jpg")
        self.assertTrue(ok_fb)
        mock_page_fb.expect_file_chooser.assert_called_once_with(timeout=5000)
        mock_fc.set_files.assert_called_once_with("data/temp_vs_upload.jpg")

        # 3. URL and visual-match card verification (Phase 8.6 strict iss=sbiupload / no explore.microsoft.com)
        self.assertTrue(is_valid_visual_search_result("https://www.bing.com/images/search?view=detailv2&iss=sbiupload&bcid=test123"))
        self.assertTrue(is_valid_visual_search_result("https://www.bing.com/images/search?FORM=VSMAGC"))
        self.assertTrue(is_valid_visual_search_result("https://www.bing.com/visualsearch?imgurl=https://a.com/x.jpg"))
        self.assertFalse(is_valid_visual_search_result("https://www.bing.com/images/search?view=detailv2&iss=sbi"))
        self.assertFalse(is_valid_visual_search_result("https://explore.microsoft.com/en-us/bing/visual-search/demo"))
        self.assertFalse(is_valid_visual_search_result("https://www.bing.com/visualsearch"))

        card_page = MagicMock()
        card_loc = MagicMock()
        card_loc.count.return_value = 1
        card_loc.first.is_visible.return_value = True
        card_page.locator.return_value = card_loc
        self.assertTrue(is_valid_visual_search_result("https://www.bing.com/visualsearch", target_page=card_page))

    def test_mobile_stealth_ctr_direct_serp_return_and_extract_points(self):
        # 1. Mobile Stealth CTR navigates directly back to serp_url instead of go_back()
        serp_url = "https://www.bing.com/search?q=patchright+test"
        mock_page = MagicMock()
        mock_page.url = serp_url

        mock_target = MagicMock()
        mock_target.is_visible.return_value = True
        mock_results = MagicMock()
        mock_results.count.return_value = 1
        mock_results.nth.return_value = mock_target

        mock_page.locator.return_value = mock_results
        mock_page.context.pages = [mock_page]
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = MagicMock()
        mock_cm.__exit__.side_effect = TimeoutError("no new tab")
        mock_page.context.expect_page.return_value = mock_cm

        def fake_click(**kwargs):
            mock_page.url = "https://example.com/article"

        mock_target.click.side_effect = fake_click

        with (
            patch("core.searcher.time.sleep"),
            patch("core.searcher.smooth_scroll"),
        ):
            simulate_organic_click(mock_page, is_mobile=True)

        mock_page.go_back.assert_not_called()
        mock_page.goto.assert_called_once_with(serp_url, wait_until="domcontentloaded", timeout=12000)

        # 2. extract_current_points() across Desktop (#id_rc) and Mobile (#flyout_id_rc)
        desktop_page = MagicMock()
        d_elem = MagicMock()
        d_elem.count.return_value = 1
        d_elem.is_visible.return_value = True
        d_elem.inner_text.return_value = "12,345"
        desktop_page.locator.return_value.first = d_elem
        self.assertEqual(extract_current_points(desktop_page), 12345)

        mobile_page = MagicMock()
        empty_elem = MagicMock()
        empty_elem.count.return_value = 0
        m_elem = MagicMock()
        m_elem.count.return_value = 1
        m_elem.is_visible.return_value = False
        m_elem.text_content.return_value = ""
        m_elem.get_attribute.return_value = "Rewards 4,560 points"

        def mob_locator(sel):
            loc = MagicMock()
            loc.first = m_elem if sel == "#flyout_id_rc" else empty_elem
            return loc

        mobile_page.locator.side_effect = mob_locator
        self.assertEqual(extract_current_points(mobile_page), 4560)


if __name__ == "__main__":
    unittest.main(verbosity=2)
