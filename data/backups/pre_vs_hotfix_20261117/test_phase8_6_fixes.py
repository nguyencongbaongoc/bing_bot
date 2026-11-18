"""
Phase 8.6 Verification Suite (tests/test_phase8_6_fixes.py).
Verifies:
1. No calls to tap_corner_rewards_coin() in runner.py or handle_visual_search_streak().
2. CooldownController burst sizes (4-6 Desktop / 3-5 Mobile), 900-1080s normal cooldowns,
   ignoring unreadable points (< 100 such as 0 or 24) while triggering on real >= 100 stalls.
3. is_valid_visual_search_result() rejects explore.microsoft.com and Pexels sample URLs (FORM=IMVNXT),
   while accepting genuine iss=sbiupload / bcid= visual search results.
4. Related Chaining filter rejects "Xem tất cả hình ảnh" (/images/search) and "Bản đồ lớn hơn" (/maps),
   while accepting valid /search?q=... links.
5. StateStore persists mobile_checkin_done, searches_completed_today, visual_search_attempts_today (max 3),
   triggers should_fast_exit_today(), and skips redundant mobile check-in API calls.
6. Zero-RAM cooldown closes d_context before sleeping when edge_browsing_done is True,
   and mid-burst Page crashed / TargetClosedError auto-recovers and retries the query once.
"""

import ast
import contextlib
import datetime
import inspect
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import runner
from core import activities, mobile_app_api, searcher
from core.activities import handle_visual_search_streak, is_valid_visual_search_result
from core.cooldown_controller import (
    CooldownController,
    get_natural_cooldown_seconds,
    get_variable_batch_size,
)
from core.searcher import is_valid_related_search_link, perform_related_cluster_search
from core.state_store import StateStore


def _collect_called_names(source: str) -> list[str]:
    tree = ast.parse(textwrap.dedent(source))
    called: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called.append(node.func.attr)
    return called


class TargetClosedError(Exception):
    pass


class TestPhase86Fixes(unittest.TestCase):
    def test_1_no_corner_coin_calls_in_runner_or_visual_search(self):
        runner_src = (ROOT_DIR / "runner.py").read_text(encoding="utf-8")
        runner_calls = _collect_called_names(runner_src)
        self.assertNotIn("tap_corner_rewards_coin", runner_calls)

        vs_src = inspect.getsource(handle_visual_search_streak)
        vs_calls = _collect_called_names(vs_src)
        self.assertNotIn("tap_corner_rewards_coin", vs_calls)

        self.assertIn(
            "Microsoft Rewards Bot - Phase 8.6 High-Speed Precision Autonomy",
            runner_src,
        )

    def test_2_cooldown_burst_sizes_intervals_and_ignore_low_points(self):
        desktop_sizes = {get_variable_batch_size(3, device_type="desktop") for _ in range(100)}
        mobile_sizes = {get_variable_batch_size(3, device_type="mobile") for _ in range(100)}
        self.assertEqual(desktop_sizes, {4, 5, 6})
        self.assertEqual(mobile_sizes, {3, 4, 5})

        ctrl = CooldownController()
        self.assertIn(ctrl.get_batch_size("desktop"), {4, 5, 6})
        self.assertIn(ctrl.get_batch_size("mobile"), {3, 4, 5})

        cds = [get_natural_cooldown_seconds() for _ in range(150)]
        for cd in cds:
            self.assertGreaterEqual(cd, 900)
            self.assertLessEqual(cd, 1080)

        # Ignore 0 (hidden mobile header) and 24 (weather widget)
        ctrl.reset_burst(baseline_points=1500)
        for bad_val in (0, 24, 0, 24, 0, 24):
            self.assertFalse(ctrl.record_search(bad_val))
        self.assertEqual(ctrl.zero_delta_checks, 0)
        self.assertEqual(ctrl.backoff_level, 0)
        self.assertEqual(ctrl.last_check_points, 1500)

        # Real balance stall (>= 100) still triggers adaptive cooldown after 4 searches
        self.assertFalse(ctrl.record_search(1500))
        self.assertFalse(ctrl.record_search(1500))
        self.assertFalse(ctrl.record_search(1500))
        self.assertTrue(ctrl.record_search(1500))
        self.assertEqual(ctrl.backoff_level, 1)

    def test_3_visual_search_validation_strict_sbiupload(self):
        self.assertFalse(
            is_valid_visual_search_result("https://explore.microsoft.com/en-us/bing/visual-search")
        )
        self.assertFalse(
            is_valid_visual_search_result(
                "https://www.bing.com/images/search?view=detailv2&FORM=IMVNXT&imgurl=https://images.pexels.com/photos/975771/pexels-photo-975771.jpeg"
            )
        )
        self.assertFalse(
            is_valid_visual_search_result("https://www.bing.com/images/search?view=detailv2&iss=sbi")
        )
        self.assertTrue(
            is_valid_visual_search_result(
                "https://www.bing.com/images/search?view=detailv2&iss=sbiupload&bcid=abc123"
            )
        )
        self.assertTrue(
            is_valid_visual_search_result("https://www.bing.com/images/search?bcid=xyz999")
        )

    def test_4_related_chaining_filters_images_maps_and_ui_labels(self):
        self.assertFalse(is_valid_related_search_link("/images/search?q=meo+con", "Xem tất cả hình ảnh"))
        self.assertFalse(is_valid_related_search_link("/maps?q=ha+noi", "Bản đồ lớn hơn"))
        self.assertFalse(is_valid_related_search_link("/videos/search?q=bong+da", "Video của bóng đá"))
        self.assertFalse(is_valid_related_search_link("/search?q=thoi+tiet", "See all"))
        self.assertFalse(is_valid_related_search_link("/search?q=hoa+hong", "More images"))
        self.assertTrue(is_valid_related_search_link("/search?q=gia+vang+hom+nay", "giá vàng hôm nay"))
        self.assertTrue(
            is_valid_related_search_link(
                "https://www.bing.com/search?q=du+bao+thoi+tiet+ha+noi",
                "dự báo thời tiết hà nội",
            )
        )

        # Verify perform_related_cluster_search selects only valid /search?q= link
        mock_page = MagicMock()
        mock_page.url = "https://www.bing.com/search?q=ha+noi"

        bad_img = MagicMock()
        bad_img.is_visible.return_value = True
        bad_img.inner_text.return_value = "Xem tất cả hình ảnh"
        bad_img.get_attribute.return_value = "/images/search?q=ha+noi"

        bad_map = MagicMock()
        bad_map.is_visible.return_value = True
        bad_map.inner_text.return_value = "Bản đồ lớn hơn"
        bad_map.get_attribute.return_value = "/maps?q=ha+noi"

        good_link = MagicMock()
        good_link.is_visible.return_value = True
        good_link.inner_text.return_value = "thời tiết hà nội hôm nay"
        good_link.get_attribute.return_value = "/search?q=thoi+tiet+ha+noi+hom+nay"
        good_link.bounding_box.return_value = {"x": 100, "y": 200, "width": 120, "height": 20}

        mock_loc = MagicMock()
        mock_loc.all.return_value = [bad_img, bad_map, good_link]
        mock_page.locator.return_value = mock_loc

        with (
            patch("core.searcher.smooth_scroll"),
            patch("core.searcher.human_bezier_move"),
            patch("core.searcher.save_searched_keyword"),
            patch("core.searcher._perform_post_search_dwell_and_ctr"),
        ):
            picked = perform_related_cluster_search(mock_page, is_mobile=False)

        self.assertEqual(picked, "thời tiết hà nội hôm nay")

    def test_5_state_store_persistence_checkin_dedup_and_fast_exit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            today = datetime.date.today().isoformat()
            store = StateStore(
                account_id="acc_86",
                data_dir=Path(tmpdir) / "data",
                profiles_dir=Path(tmpdir) / "profiles",
            )

            self.assertFalse(store.is_mobile_checkin_done(today))
            store.mark_mobile_checkin_done(True, today_str=today)
            self.assertTrue(store.is_mobile_checkin_done(today))

            # Calling solve_mobile_app_daily_checkin skips OAuth/API when already marked done
            with patch.object(mobile_app_api, "get_mobile_access_token") as mock_oauth:
                pts = mobile_app_api.solve_mobile_app_daily_checkin(state_store=store)
            self.assertEqual(pts, 0)
            mock_oauth.assert_not_called()

            # Visual search attempts capped at 3
            self.assertEqual(store.increment_visual_search_attempts_today(1, today_str=today), 1)
            self.assertEqual(store.increment_visual_search_attempts_today(1, today_str=today), 2)
            self.assertEqual(store.increment_visual_search_attempts_today(5, today_str=today), 3)
            self.assertEqual(store.get_visual_search_attempts_today(today_str=today), 3)

            # Before searches completed -> do not fast-exit
            self.assertFalse(store.should_fast_exit_today(today_str=today))

            # After searches completed and max visual search attempts reached -> fast-exit True
            store.mark_searches_completed_today(True, core_tasks_done=False, today_str=today)
            self.assertTrue(store.is_searches_completed_today(today_str=today))
            self.assertTrue(store.should_fast_exit_today(today_str=today))

            # Verify run_full_pipeline fast-exits without launching Playwright
            with (
                patch.object(runner, "_get_state_store", return_value=store),
                patch.object(runner, "get_sync_playwright") as mock_pw,
            ):
                class Args:
                    debug = False
                    desktop_target = 30
                    mobile_target = 20
                    batch_size = 3
                    account = None

                res = runner.run_full_pipeline(Args())
            self.assertTrue(res)
            mock_pw.assert_not_called()

    def test_6_zero_ram_cooldown_and_mid_burst_crash_recovery(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            store = StateStore(account_id="acc_ram", data_dir=tmp_path / "data", profiles_dir=tmp_path / "profiles")
            today = datetime.date.today().isoformat()
            store.update_edge_browsing_state(1860, done=True, today_str=today)

            d_prof = tmp_path / "profiles" / "acc_ram" / "desktop"
            m_prof = tmp_path / "profiles" / "acc_ram" / "mobile"
            d_prof.mkdir(parents=True)
            m_prof.mkdir(parents=True)

            ctx_1 = MagicMock()
            page_1 = MagicMock()
            page_1.is_closed.return_value = False
            ctx_1.pages = [page_1]

            ctx_2 = MagicMock()
            page_2 = MagicMock()
            page_2.is_closed.return_value = False
            ctx_2.pages = [page_2]

            ctx_final = MagicMock()
            page_final = MagicMock()
            page_final.is_closed.return_value = False
            ctx_final.pages = [page_final]

            scan_progress = {
                "pc_cur": 0, "pc_max": 90,
                "mobile_cur": 60, "mobile_max": 60,
                "found": True, "found_pc": True, "found_mobile": True,
                "scrape_error": False, "all_maxed": False,
                "all_activities_done": True, "primary_four_done": True,
                "activities": {
                    "Bing Search": {"cur": 1, "max": 1, "done": True},
                    "Daily Set": {"cur": 3, "max": 3, "done": True},
                    "Edge Browsing": {"cur": 30, "max": 30, "done": True},
                    "Visual Search": {"cur": 1, "max": 1, "done": True},
                },
                "bing_star_bonus": {"text": "0 / 2,100"},
            }

            events: list[str] = []

            def fake_close(ctx):
                if ctx is ctx_1:
                    events.append("close_ctx_1")
                elif ctx is ctx_2:
                    events.append("close_ctx_2")

            def fake_sleep(secs):
                events.append(f"sleep:{secs}")

            # 2 desktop searches with batch_size=1 -> triggers 1 cooldown between query 1 and query 2.
            # Query 1 raises TargetClosedError("Page crashed") on first attempt, then succeeds on retry.
            search_calls = {"n": 0}

            def fake_search(page, query, device_type="desktop"):
                search_calls["n"] += 1
                if search_calls["n"] == 1:
                    raise TargetClosedError("Page crashed")
                return True

            class CustomArgs:
                debug = True
                desktop_target = 2
                mobile_target = 0
                batch_size = 1
                account = None

            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(runner, "DESKTOP_PROFILE", d_prof))
                stack.enter_context(patch.object(runner, "MOBILE_PROFILE", m_prof))
                stack.enter_context(patch.object(runner, "_get_state_store", return_value=store))
                stack.enter_context(patch.object(runner, "wait_for_internet", return_value=True))
                stack.enter_context(patch.object(runner, "get_sync_playwright"))
                stack.enter_context(
                    patch.object(
                        runner,
                        "ensure_desktop_page",
                        side_effect=[
                            (ctx_1, page_1),  # Phase 1 scan
                            (ctx_1, page_1),  # Query 1 attempt 0 (crashes)
                            (ctx_2, page_2),  # Query 1 retry after crash recovery
                            (ctx_2, page_2),  # Query 2 after Zero-RAM cooldown reopens
                            (ctx_final, page_final),  # Phase 3 summary
                        ],
                    )
                )
                stack.enter_context(patch.object(runner, "perform_warmup_visit"))
                stack.enter_context(patch.object(runner, "dismiss_popups"))
                stack.enter_context(patch.object(runner, "extract_current_points", return_value=1500))
                stack.enter_context(patch.object(runner, "detect_unusual_activity_warning", return_value=False))
                stack.enter_context(patch.object(runner, "init_session"))
                stack.enter_context(patch.object(runner, "update_session"))
                stack.enter_context(patch.object(runner, "finish_session"))
                stack.enter_context(patch.object(runner, "log_session_result"))
                stack.enter_context(patch.object(runner, "update_points_ledger"))
                stack.enter_context(patch.object(runner, "get_daily_search_progress", return_value=scan_progress))
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
                stack.enter_context(patch.object(runner, "save_searched_keyword"))
                stack.enter_context(patch.object(runner, "execute_single_search", side_effect=fake_search))
                stack.enter_context(patch.object(runner, "_close_quietly", side_effect=fake_close))
                stack.enter_context(patch("time.sleep", side_effect=fake_sleep))

                ok = runner.run_full_pipeline(CustomArgs())

            self.assertTrue(ok)
            # Total search calls = 3 (Query 1 crashed + Query 1 recovered retry + Query 2)
            self.assertEqual(search_calls["n"], 3)
            # Zero-RAM cooldown closed ctx_2 BEFORE sleeping for 900..1080s cooldown
            close2_idx = next(i for i, e in enumerate(events) if e == "close_ctx_2")
            long_sleep_idx = next(
                i for i, e in enumerate(events)
                if e.startswith("sleep:") and float(e.split(":")[1]) >= 900
            )
            self.assertLess(close2_idx, long_sleep_idx)


if __name__ == "__main__":
    unittest.main(verbosity=2)
