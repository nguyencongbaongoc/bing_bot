"""
Phase 8 Local Verification Suite (tests/test_phase8_local.py).
Verifies:
1. Exclusive OS Process Lock (data/locks/runner.lock) exits cleanly with code 0 on contention.
2. Edge Browsing 30-minute (1860s) daily cap persistence and cooldown skip logic.
3. Dashboard Timeout Resilience in Scan Phase 2 and Double-Check (preserves 84/90, never marks 0/90 maxed).
4. Visual Search Vietnamese "Duyệt" locator usage without JS SyntaxError.
"""

import contextlib
import datetime
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import runner
from core import activities


class TestPhase8Local(unittest.TestCase):
    def test_1_process_lock_prevents_second_instance(self):
        """Spawning a second runner.py while the first holds runner.lock exits cleanly with code 0."""
        lock_handle = runner.acquire_runner_lock(runner.LOCK_FILE)
        self.assertIsNotNone(lock_handle, "First process should acquire runner.lock")
        try:
            proc = subprocess.run(
                [sys.executable, str(ROOT_DIR / "runner.py"), "--no-sleep"],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(proc.returncode, 0, f"Second runner.py should exit 0, got {proc.returncode}: {proc.stderr}")
            combined = (proc.stdout or "") + (proc.stderr or "")
            self.assertIn("Another bot instance", combined)
            self.assertIn("Exiting cleanly without killing active browsers", combined)
        finally:
            runner.release_runner_lock(lock_handle)

    def test_2_edge_browsing_30_min_cap(self):
        """Once edge_browsing_seconds >= 1860 or edge_browsing_done == True, Edge Browsing is skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_state_file = Path(tmpdir) / "daily_state.json"
            today_str = datetime.date.today().isoformat()

            with patch.object(runner, "DAILY_STATE_FILE", tmp_state_file), \
                 patch.object(runner, "DATA_DIR", Path(tmpdir)):
                runner.update_edge_browsing_state(1860, done=True, today_str=today_str)
                state = runner.get_edge_browsing_state(today_str=today_str)
                self.assertTrue(state["done"])
                self.assertEqual(state["seconds"], 1860)

                mock_page = MagicMock()
                mock_page.is_closed.return_value = False
                returned_page, elapsed = runner.active_edge_browsing_cooldown(
                    mock_page,
                    total_seconds=900,
                    initial_seconds=state["seconds"],
                )
                self.assertEqual(elapsed, 0)
                mock_page.goto.assert_not_called()

    def test_3_timeout_resilience_scan_phase_2_and_double_check(self):
        """TimeoutError during Scan Phase 2 and Double-Check preserves 84/90 and never sets all_maxed=True."""
        mock_page = MagicMock()
        mock_page.url = "https://www.bing.com"
        mock_page.is_closed.return_value = False
        mock_page.goto.side_effect = TimeoutError("Simulated /earn timeout")
        mock_page.reload.side_effect = TimeoutError("Simulated /earn reload timeout")

        res = activities.get_daily_search_progress(mock_page)
        self.assertFalse(res["found"])
        self.assertTrue(res["scrape_error"])
        self.assertFalse(res["all_maxed"])
        self.assertFalse(res["found_pc"])
        self.assertFalse(res["found_mobile"])

        # Verify run_full_pipeline preserves Scan Phase 1 (84/90) and never sets all_maxed=True on timeout
        scan1_progress = {
            "pc_cur": 84, "pc_current": 84, "pc_max": 90,
            "mobile_cur": 60, "mobile_current": 60, "mobile_max": 60,
            "found": True, "found_pc": True, "found_mobile": True,
            "scrape_error": False, "all_maxed": False,
            "all_activities_done": True, "primary_four_done": True,
            "activities": {
                "Bing Search": {"cur": 1, "max": 1, "done": True},
                "Daily Set": {"cur": 3, "max": 3, "done": True},
                "Edge Browsing": {"cur": 30, "max": 30, "done": True},
                "Visual Search": {"cur": 1, "max": 1, "done": True},
                "Mobile App": {"cur": 1, "max": 1, "done": True},
            },
            "bing_star_bonus": {"text": "0 / 2,100"},
        }
        timeout_progress = {
            "pc_cur": 0, "pc_current": 0, "pc_max": 90,
            "mobile_cur": 0, "mobile_current": 0, "mobile_max": 60,
            "found": False, "found_pc": False, "found_mobile": False,
            "scrape_error": True, "all_maxed": False,
            "all_activities_done": False, "activities": {},
        }

        mock_d_page = MagicMock()
        mock_d_page.is_closed.return_value = False
        mock_d_ctx = MagicMock()
        mock_d_ctx.pages = [mock_d_page]

        class DummyArgs:
            debug = True
            desktop_target = 30
            mobile_target = 20
            batch_size = 3

        with tempfile.TemporaryDirectory() as tmpdir, contextlib.ExitStack() as stack:
            tmp_state = Path(tmpdir) / "daily_state.json"
            patches = [
                patch.object(runner, "DAILY_STATE_FILE", tmp_state),
                patch.object(runner, "DATA_DIR", Path(tmpdir)),
                patch.object(runner, "wait_for_internet", return_value=True),
                patch.object(runner, "ensure_desktop_page", return_value=(mock_d_ctx, mock_d_page)),
                patch.object(runner, "get_daily_search_progress", side_effect=[scan1_progress, timeout_progress, timeout_progress]),
                patch.object(runner, "extract_current_points", return_value=1000),
                patch.object(runner, "dismiss_popups"),
                patch.object(runner, "detect_unusual_activity_warning", return_value=False),
                patch.object(runner, "init_session"),
                patch.object(runner, "update_session"),
                patch.object(runner, "log_session_result"),
                patch.object(runner, "update_points_ledger"),
                patch.object(runner, "ensure_streak_protection"),
                patch.object(runner, "run_all_dashboard_activities"),
                patch.object(runner, "solve_visual_search_streak"),
                patch.object(runner, "solve_mobile_app_daily_checkin", return_value=0),
                patch.object(runner, "solve_mobile_app_promotions"),
                patch.object(runner, "solve_mobile_app_read_to_earn"),
                patch.object(runner, "solve_punch_cards"),
                patch.object(runner, "solve_all_earn_quests"),
                patch.object(runner, "solve_keep_earning_activities"),
                patch.object(runner, "claim_msn_daily_gaming_tile"),
                patch.object(runner, "solve_wallpaper_trivia"),
                patch.object(runner, "interact_with_copilot"),
                patch.object(runner, "solve_promotional_links"),
                patch.object(runner, "claim_all_available_rewards"),
                patch.object(runner, "unload_model_from_vram"),
                patch.object(runner, "save_searched_keyword"),
                patch.object(runner, "tap_corner_rewards_coin"),
                patch("time.sleep"),
            ]
            for p_obj in patches:
                stack.enter_context(p_obj)
            mock_pw = stack.enter_context(patch.object(runner, "get_sync_playwright"))
            mock_finish = stack.enter_context(patch.object(runner, "finish_session"))
            mock_search = stack.enter_context(patch.object(runner, "execute_single_search", return_value=True))

            mock_pw.return_value.return_value.__enter__.return_value = MagicMock()
            completed = runner.run_full_pipeline(DummyArgs())
            self.assertFalse(completed, "Should not mark completed on Double-Check timeout")
            # Scan Phase 1 had 84/90 -> 6 pts remaining -> 2 desktop searches needed (not 30!)
            self.assertLessEqual(mock_search.call_count, 4)
            self.assertGreaterEqual(mock_search.call_count, 2)
            mock_finish.assert_called_with(1000, status="partial")

    def test_4_visual_search_vietnamese_locator_no_syntax_error(self):
        """Evaluating the Vietnamese 'Duyệt' selector uses Phase 8.1 upload_visual_search_image with 0 SyntaxError."""
        src = (ROOT_DIR / "core" / "activities.py").read_text(encoding="utf-8")
        self.assertNotIn('document.querySelector("{browse_sel}")', src)
        self.assertIn("def upload_visual_search_image(", src)
        self.assertIn("direct_inp.set_input_files(str(image_path))", src)
        self.assertIn("expect_file_chooser(timeout=5000)", src)
        self.assertIn("explore.microsoft.com", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
