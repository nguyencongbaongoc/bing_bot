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
            is_valid_visual_search_result("https://www.bing.com/images?FORM=SBIVSP")
        )
        self.assertFalse(
            is_valid_visual_search_result("https://www.bing.com/images?FORM=SBIRDI")
        )
        self.assertFalse(
            is_valid_visual_search_result("https://www.bing.com/images/search?view=detailv2&iss=sbi&FORM=SBIRDI")
        )
        self.assertFalse(
            is_valid_visual_search_result("https://www.bing.com/images/search?view=detailv2&iss=sbi")
        )
        self.assertFalse(
            is_valid_visual_search_result("https://www.bing.com/visualsearch?FORM=VSMAGC")
        )
        self.assertTrue(
            is_valid_visual_search_result(
                "https://www.bing.com/images/search?view=detailv2&iss=sbiupload&bcid=abc123"
            )
        )
        self.assertTrue(
            is_valid_visual_search_result("https://www.bing.com/images/search?bcid=xyz999")
        )
        self.assertFalse(
            is_valid_visual_search_result(
                "https://www.bing.com/images/search?view=detailV2&ccid=1ocLq3vE&FORM=IMVNXT&idpview=singleimage"
            )
        )
        self.assertFalse(
            is_valid_visual_search_result(
                "https://www.bing.com/images/search?view=detailv2&FORM=SBIHMP"
            )
        )
        vs_insights_page = MagicMock()
        vs_insights_loc = MagicMock()
        vs_insights_loc.count.return_value = 1
        vs_insights_loc.first.is_visible.return_value = True
        vs_insights_page.locator.return_value = vs_insights_loc
        self.assertTrue(
            is_valid_visual_search_result("https://www.bing.com/visualsearch?FORM=VSMAGC", target_page=vs_insights_page)
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

    def test_7_visual_search_local_screenshot_vsstreak_and_hash_bypass(self):
        import hashlib
        from PIL import Image

        activities_src = (ROOT_DIR / "core" / "activities.py").read_text(encoding="utf-8")
        self.assertNotIn("wikimedia.org", activities_src.lower())
        self.assertNotIn("picsum.photos", activities_src.lower())
        self.assertIn("features=vsstreak,vstooltip&form=ML2XES", activities_src)
        self.assertIn("sbisrc=ImgPicker", activities_src)

        with tempfile.TemporaryDirectory() as tmpdir:
            sample_a = Path(tmpdir) / "Screenshot_2026_a.png"
            sample_b = Path(tmpdir) / "Screenshot_2026_b.png"
            Image.new("RGBA", (300, 200), (10, 120, 220, 255)).save(sample_a)
            Image.new("RGBA", (800, 600), (200, 80, 40, 255)).save(sample_b)

            # Verify _prepare_visual_search_jpeg creates RGB JPEG (520..640px) with unique hashes
            with patch.object(activities, "_pick_local_screenshot", side_effect=[sample_a, sample_b]):
                out1 = activities._prepare_visual_search_jpeg()
                h1 = hashlib.sha256(out1.read_bytes()).hexdigest()
                with Image.open(out1) as im1:
                    self.assertEqual(im1.mode, "RGB")
                    self.assertEqual(im1.format, "JPEG")
                    self.assertGreaterEqual(max(im1.size), 520)
                    self.assertLessEqual(max(im1.size), 640)
                out2 = activities._prepare_visual_search_jpeg()
                h2 = hashlib.sha256(out2.read_bytes()).hexdigest()
                self.assertNotEqual(h1, h2)

            main_page = MagicMock()
            main_page.url = "https://rewards.bing.com/earn"
            main_page.is_closed.return_value = False

            vs_tab = MagicMock()
            vs_tab.url = "about:blank"
            vs_tab.is_closed.return_value = False
            main_page.context.pages = [main_page]
            main_page.context.new_page.return_value = vs_tab

            def vs_goto(url, wait_until=None, timeout=None):
                vs_tab.url = url

            vs_tab.goto.side_effect = vs_goto

            # Main page has no Visual Search card -> falls back to tracked vsstreak URL
            no_loc = MagicMock()
            no_loc.first = MagicMock()
            no_loc.first.count.return_value = 0
            no_loc.first.is_visible.return_value = False
            main_page.locator.return_value = no_loc

            dropzone_mounted = {"ready": False}
            sb_sbip_elem = MagicMock()
            sb_sbip_elem.count.return_value = 1
            sb_sbip_elem.is_visible.return_value = True

            def click_sb_sbip(timeout=None, force=False):
                dropzone_mounted["ready"] = True

            sb_sbip_elem.click.side_effect = click_sb_sbip

            browse_elem = MagicMock()
            browse_elem.count.return_value = 1
            browse_elem.is_visible.return_value = True

            dropzone_elem = MagicMock()
            dropzone_elem.count.return_value = 1
            dropzone_elem.is_visible.side_effect = lambda timeout=None: dropzone_mounted["ready"]

            hidden_elem = MagicMock()
            hidden_elem.count.return_value = 0
            hidden_elem.is_visible.return_value = False

            def vs_locator(sel):
                loc = MagicMock()
                if sel == "#sb_sbip":
                    loc.first = sb_sbip_elem
                elif sel == '#sbi_b, #sb_sbipane, #sb_brtext, label[for="sb_fileinput"], #sb_brs':
                    loc.first = dropzone_elem
                elif sel == "#sb_brs":
                    loc.first = browse_elem
                else:
                    loc.first = hidden_elem
                return loc

            vs_tab.locator.side_effect = vs_locator

            mock_fc = MagicMock()

            def fc_set_files(path):
                vs_tab.url = "https://www.bing.com/images/search?view=detailv2&iss=sbiupload&bcid=hotfix123"

            mock_fc.set_files.side_effect = fc_set_files
            vs_tab.expect_file_chooser.return_value.__enter__.return_value.value = mock_fc

            progress_done = {
                "activities": {
                    "Visual Search": {"cur": 1, "max": 1, "done": True},
                }
            }

            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(activities, "_pick_local_screenshot", return_value=sample_a))
                stack.enter_context(patch.object(activities, "dismiss_popups"))
                stack.enter_context(patch.object(activities, "claim_all_available_rewards"))
                stack.enter_context(
                    patch.object(
                        activities,
                        "read_visual_search_streak_status",
                        side_effect=[{"cur": 0, "max": 1, "done": False}, {"cur": 1, "max": 1, "done": True}],
                    )
                )
                stack.enter_context(patch.object(activities, "get_daily_search_progress", return_value=progress_done))

                ok = handle_visual_search_streak(main_page)

            self.assertTrue(ok)
            vs_tab.goto.assert_called_once_with(
                "https://www.bing.com/?features=vsstreak,vstooltip&form=ML2XES",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            sb_sbip_elem.click.assert_called_once_with(timeout=5000)
            vs_tab.expect_file_chooser.assert_called_once_with(timeout=4500)
            mock_fc.set_files.assert_called_once()
            uploaded_path = Path(mock_fc.set_files.call_args[0][0])
            self.assertEqual(uploaded_path.name, "temp_vs_upload.jpg")
            self.assertTrue(uploaded_path.exists())
            vs_tab.close.assert_called_once()

    def test_8_mid_loop_earn_check_returns_to_bing_and_test_logs_isolated(self):
        import logging
        from core import logger as core_logger

        # 1. Production log FileHandler must be suppressed during unittest
        self.assertTrue(core_logger.is_testing_mode())
        file_handlers = [h for h in core_logger._logger.handlers if isinstance(h, logging.FileHandler)]
        self.assertEqual(file_handlers, [])

        # 2. Mid-loop get_daily_search_progress() returning incomplete desktop count navigates d_page back to bing.com
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            store = StateStore(account_id="acc_mid", data_dir=tmp_path / "data", profiles_dir=tmp_path / "profiles")
            today = datetime.date.today().isoformat()
            store.update_edge_browsing_state(1860, done=True, today_str=today)

            d_prof = tmp_path / "profiles" / "acc_mid" / "desktop"
            m_prof = tmp_path / "profiles" / "acc_mid" / "mobile"
            d_prof.mkdir(parents=True)
            m_prof.mkdir(parents=True)

            ctx = MagicMock()
            d_page = MagicMock()
            d_page.is_closed.return_value = False
            d_page.url = "https://www.bing.com/"
            ctx.pages = [d_page]

            scan1 = {
                "pc_cur": 84, "pc_max": 90,
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
            # Mid-loop double-check leaves d_page on /earn and reports 87/90 (1 more desktop search needed)
            recheck_partial = dict(scan1, pc_cur=87)
            recheck_done = dict(scan1, pc_cur=90, all_maxed=True)

            def fake_progress(pg):
                if pg is d_page and fake_progress.calls == 2:
                    pg.url = "https://rewards.bing.com/earn"
                    fake_progress.calls += 1
                    return recheck_partial
                fake_progress.calls += 1
                if fake_progress.calls <= 2:
                    return scan1
                return recheck_done

            fake_progress.calls = 0

            class NormalArgs:
                debug = True
                desktop_target = 30
                mobile_target = 20
                batch_size = 3
                account = None

            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(runner, "DESKTOP_PROFILE", d_prof))
                stack.enter_context(patch.object(runner, "MOBILE_PROFILE", m_prof))
                stack.enter_context(patch.object(runner, "_get_state_store", return_value=store))
                stack.enter_context(patch.object(runner, "wait_for_internet", return_value=True))
                stack.enter_context(patch.object(runner, "get_sync_playwright"))
                stack.enter_context(patch.object(runner, "ensure_desktop_page", return_value=(ctx, d_page)))
                stack.enter_context(patch.object(runner, "perform_warmup_visit"))
                stack.enter_context(patch.object(runner, "dismiss_popups"))
                stack.enter_context(patch.object(runner, "extract_current_points", return_value=1500))
                stack.enter_context(patch.object(runner, "detect_unusual_activity_warning", return_value=False))
                stack.enter_context(patch.object(runner, "init_session"))
                stack.enter_context(patch.object(runner, "update_session"))
                stack.enter_context(patch.object(runner, "finish_session"))
                stack.enter_context(patch.object(runner, "log_session_result"))
                stack.enter_context(patch.object(runner, "update_points_ledger"))
                stack.enter_context(patch.object(runner, "get_daily_search_progress", side_effect=fake_progress))
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
                stack.enter_context(patch.object(runner, "execute_single_search", return_value=True))
                stack.enter_context(patch.object(runner, "_close_quietly"))
                stack.enter_context(patch("time.sleep"))
                stack.enter_context(patch("random.random", return_value=0.99))

                ok = runner.run_full_pipeline(NormalArgs())

            self.assertTrue(ok)
            d_page.goto.assert_any_call("https://www.bing.com/", wait_until="domcontentloaded")

    def test_9_claim_dashboard_pending_points_drawer_flow(self):
        from core.activities import claim_dashboard_pending_points

        # Case 1: Standard successful claim flow (Ready to claim card clicked -> drawer opens -> claim button clicked)
        page = MagicMock()
        mock_card = MagicMock()
        mock_card.first = mock_card
        mock_card.is_visible.return_value = True
        mock_card.count.return_value = 1

        mock_claim_btn = MagicMock()
        mock_claim_btn.first = mock_claim_btn
        mock_claim_btn.is_visible.return_value = True
        mock_claim_btn.is_enabled.return_value = True
        mock_claim_btn.get_attribute.return_value = "false"
        mock_claim_btn.count.return_value = 1

        def locator_side_effect(selector):
            loc = MagicMock()
            loc.first = loc
            if "Ready to claim" in selector or "Sẵn sàng nhận" in selector:
                return mock_card
            elif "No points" in selector or "Không có điểm" in selector:
                loc.is_visible.return_value = False
                loc.count.return_value = 0
                return loc
            elif "Claim points" in selector or "Nhận điểm" in selector or selector == 'button:has-text("Claim")':
                if mock_card.click.called:
                    return mock_claim_btn
                else:
                    loc.is_visible.return_value = False
                    loc.count.return_value = 0
                    return loc
            loc.is_visible.return_value = False
            loc.count.return_value = 0
            return loc

        page.locator.side_effect = locator_side_effect
        res = claim_dashboard_pending_points(page)

        page.goto.assert_called_with("https://rewards.bing.com/dashboard", wait_until="domcontentloaded")
        mock_card.click.assert_called_once()
        mock_claim_btn.click.assert_called_once()
        self.assertTrue(res)

        # Case 2: Drawer already open (skips clicking card, clicks claim button)
        page2 = MagicMock()
        mock_card2 = MagicMock()
        mock_card2.first = mock_card2
        mock_claim_btn2 = MagicMock()
        mock_claim_btn2.first = mock_claim_btn2
        mock_claim_btn2.is_visible.return_value = True
        mock_claim_btn2.is_enabled.return_value = True
        mock_claim_btn2.get_attribute.return_value = "false"
        mock_claim_btn2.count.return_value = 1

        def locator_open_side_effect(selector):
            loc = MagicMock()
            loc.first = loc
            if "Ready to claim" in selector:
                return mock_card2
            elif "Claim points" in selector or "Nhận điểm" in selector:
                return mock_claim_btn2
            elif "No points" in selector:
                loc.is_visible.return_value = False
                return loc
            loc.is_visible.return_value = False
            return loc

        page2.locator.side_effect = locator_open_side_effect
        res2 = claim_dashboard_pending_points(page2)
        mock_card2.click.assert_not_called()
        mock_claim_btn2.click.assert_called_once()
        self.assertTrue(res2)

        # Case 3: Claim button disabled (0 pts to claim)
        page3 = MagicMock()
        mock_card3 = MagicMock()
        mock_card3.first = mock_card3
        mock_card3.is_visible.return_value = True
        mock_card3.count.return_value = 1

        mock_disabled_btn = MagicMock()
        mock_disabled_btn.first = mock_disabled_btn
        mock_disabled_btn.is_visible.return_value = True
        mock_disabled_btn.is_enabled.return_value = False
        mock_disabled_btn.get_attribute.return_value = "true"
        mock_disabled_btn.count.return_value = 1

        def locator_disabled_side_effect(selector):
            loc = MagicMock()
            loc.first = loc
            if "Ready to claim" in selector:
                return mock_card3
            elif "Claim points" in selector:
                if mock_card3.click.called:
                    return mock_disabled_btn
                else:
                    loc.is_visible.return_value = False
                    return loc
            elif "No points" in selector:
                loc.is_visible.return_value = False
                return loc
            loc.is_visible.return_value = False
            return loc

        page3.locator.side_effect = locator_disabled_side_effect
        res3 = claim_dashboard_pending_points(page3)
        mock_card3.click.assert_called_once()
        mock_disabled_btn.click.assert_not_called()
        self.assertFalse(res3)

        # Case 4: Blocked by "No points to claim right now" text
        page4 = MagicMock()
        mock_card4 = MagicMock()
        mock_card4.first = mock_card4
        mock_card4.is_visible.return_value = True
        mock_card4.count.return_value = 1

        mock_btn4 = MagicMock()
        mock_btn4.first = mock_btn4
        mock_btn4.is_visible.return_value = True
        mock_btn4.is_enabled.return_value = True
        mock_btn4.get_attribute.return_value = "false"
        mock_btn4.count.return_value = 1

        mock_no_points = MagicMock()
        mock_no_points.first = mock_no_points
        mock_no_points.is_visible.return_value = True
        mock_no_points.count.return_value = 1

        def locator_blocked_side_effect(selector):
            loc = MagicMock()
            loc.first = loc
            if "Ready to claim" in selector:
                return mock_card4
            elif "No points" in selector or "Không có điểm" in selector:
                return mock_no_points
            elif "Claim points" in selector:
                if mock_card4.click.called:
                    return mock_btn4
                else:
                    loc.is_visible.return_value = False
                    return loc
            loc.is_visible.return_value = False
            return loc

        page4.locator.side_effect = locator_blocked_side_effect
        res4 = claim_dashboard_pending_points(page4)
        mock_card4.click.assert_called_once()
        mock_btn4.click.assert_not_called()
        self.assertFalse(res4)

    def test_10_runner_calls_claim_dashboard_pending_points_at_stage_3(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            store = StateStore(account_id="acc_claim", data_dir=tmp_path / "data", profiles_dir=tmp_path / "profiles")
            today = datetime.date.today().isoformat()
            store.update_edge_browsing_state(1860, done=True, today_str=today)

            d_prof = tmp_path / "profiles" / "acc_claim" / "desktop"
            m_prof = tmp_path / "profiles" / "acc_claim" / "mobile"
            d_prof.mkdir(parents=True)
            m_prof.mkdir(parents=True)

            ctx = MagicMock()
            d_page = MagicMock()
            d_page.is_closed.return_value = False
            ctx.pages = [d_page]

            scan_progress = {
                "pc_cur": 90, "pc_max": 90,
                "mobile_cur": 60, "mobile_max": 60,
                "found": True, "found_pc": True, "found_mobile": True,
                "all_maxed": True, "all_activities_done": True,
                "status": "empty",
            }

            call_sequence = []

            def fake_claim(page):
                call_sequence.append(("claim_dashboard_pending_points", page))
                return True

            def fake_extract(page):
                call_sequence.append(("extract_current_points", page))
                return 1850

            class Args:
                debug = True
                desktop_target = 0
                mobile_target = 0
                batch_size = 3
                account = None

            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(runner, "DESKTOP_PROFILE", d_prof))
                stack.enter_context(patch.object(runner, "MOBILE_PROFILE", m_prof))
                stack.enter_context(patch.object(runner, "_get_state_store", return_value=store))
                stack.enter_context(patch.object(runner, "wait_for_internet", return_value=True))
                stack.enter_context(patch.object(runner, "get_sync_playwright"))
                stack.enter_context(patch.object(runner, "ensure_desktop_page", return_value=(ctx, d_page)))
                stack.enter_context(patch.object(runner, "perform_warmup_visit"))
                stack.enter_context(patch.object(runner, "dismiss_popups"))
                stack.enter_context(patch.object(runner, "claim_dashboard_pending_points", side_effect=fake_claim))
                stack.enter_context(patch.object(runner, "extract_current_points", side_effect=fake_extract))
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
                stack.enter_context(patch.object(runner, "execute_single_search", return_value=True))
                stack.enter_context(patch.object(runner, "_close_quietly"))
                stack.enter_context(patch("time.sleep"))

                ok = runner.run_full_pipeline(Args())

            self.assertTrue(ok)
            # Verify claim_dashboard_pending_points was called on d_page
            claim_calls = [c for c in call_sequence if c[0] == "claim_dashboard_pending_points"]
            extract_calls = [c for c in call_sequence if c[0] == "extract_current_points"]

            self.assertGreaterEqual(len(claim_calls), 1)
            self.assertEqual(claim_calls[-1][1], d_page)
            self.assertGreaterEqual(len(extract_calls), 1)

            # Stage [3/3] claim occurs strictly before the final extract_current_points call
            last_claim_idx = max(i for i, c in enumerate(call_sequence) if c[0] == "claim_dashboard_pending_points")
            last_extract_idx = max(i for i, c in enumerate(call_sequence) if c[0] == "extract_current_points")
            self.assertLess(last_claim_idx, last_extract_idx)

    def test_11_runner_visual_search_headless_false_micro_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            store = StateStore(account_id="acc_vs_micro", data_dir=tmp_path / "data", profiles_dir=tmp_path / "profiles")
            today = datetime.date.today().isoformat()
            store.update_edge_browsing_state(1860, done=True, today_str=today)

            d_prof = tmp_path / "profiles" / "acc_vs_micro" / "desktop"
            m_prof = tmp_path / "profiles" / "acc_vs_micro" / "mobile"
            d_prof.mkdir(parents=True)
            m_prof.mkdir(parents=True)

            ctx_headless_1 = MagicMock()
            page_headless_1 = MagicMock()
            page_headless_1.is_closed.return_value = False
            ctx_headless_1.pages = [page_headless_1]

            ctx_vs_visible = MagicMock()
            page_vs_visible = MagicMock()
            page_vs_visible.is_closed.return_value = False
            ctx_vs_visible.pages = [page_vs_visible]

            ctx_headless_2 = MagicMock()
            page_headless_2 = MagicMock()
            page_headless_2.is_closed.return_value = False
            ctx_headless_2.pages = [page_headless_2]

            scan_incomplete_vs = {
                "pc_cur": 90, "pc_max": 90,
                "mobile_cur": 60, "mobile_max": 60,
                "found": True, "found_pc": True, "found_mobile": True,
                "all_maxed": True, "all_activities_done": False,
                "activities": {
                    "Bing Search": {"cur": 1, "max": 1, "done": True},
                    "Daily Set": {"cur": 3, "max": 3, "done": True},
                    "Edge Browsing": {"cur": 30, "max": 30, "done": True},
                    "Visual Search": {"cur": 0, "max": 1, "done": False},
                },
                "status": "ok",
            }

            scan_complete_vs = {
                "pc_cur": 90, "pc_max": 90,
                "mobile_cur": 60, "mobile_max": 60,
                "found": True, "found_pc": True, "found_mobile": True,
                "all_maxed": True, "all_activities_done": True,
                "activities": {
                    "Bing Search": {"cur": 1, "max": 1, "done": True},
                    "Daily Set": {"cur": 3, "max": 3, "done": True},
                    "Edge Browsing": {"cur": 30, "max": 30, "done": True},
                    "Visual Search": {"cur": 1, "max": 1, "done": True},
                },
                "status": "empty",
            }

            events = []

            def fake_ensure_desktop(ctx, pw, headless):
                events.append(("ensure_desktop_page", ctx is None, headless))
                if ctx is None and len([e for e in events if e[0] == "ensure_desktop_page"]) == 1:
                    return (ctx_headless_1, page_headless_1)
                return (ctx_headless_2, page_headless_2)

            def fake_create_desktop(pw, headless, account_id=None, profile_dir=None):
                events.append(("create_desktop_context", headless))
                return ctx_vs_visible

            def fake_close(ctx):
                if ctx is ctx_headless_1:
                    events.append(("close", "ctx_headless_1"))
                elif ctx is ctx_vs_visible:
                    events.append(("close", "ctx_vs_visible"))
                elif ctx is ctx_headless_2:
                    events.append(("close", "ctx_headless_2"))

            def fake_vs(pg, state_store=None):
                events.append(("solve_visual_search_streak", pg is page_vs_visible))
                return True

            class HeadlessArgs:
                debug = False
                desktop_target = 0
                mobile_target = 0
                batch_size = 3
                account = None

            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(runner, "DESKTOP_PROFILE", d_prof))
                stack.enter_context(patch.object(runner, "MOBILE_PROFILE", m_prof))
                stack.enter_context(patch.object(runner, "_get_state_store", return_value=store))
                stack.enter_context(patch.object(runner, "wait_for_internet", return_value=True))
                stack.enter_context(patch.object(runner, "get_sync_playwright"))
                stack.enter_context(patch.object(runner, "ensure_desktop_page", side_effect=fake_ensure_desktop))
                stack.enter_context(patch.object(runner, "create_desktop_context", side_effect=fake_create_desktop))
                stack.enter_context(patch.object(runner, "perform_warmup_visit"))
                stack.enter_context(patch.object(runner, "dismiss_popups"))
                stack.enter_context(patch.object(runner, "claim_dashboard_pending_points", return_value=False))
                stack.enter_context(patch.object(runner, "extract_current_points", return_value=1850))
                stack.enter_context(patch.object(runner, "detect_unusual_activity_warning", return_value=False))
                stack.enter_context(patch.object(runner, "init_session"))
                stack.enter_context(patch.object(runner, "update_session"))
                stack.enter_context(patch.object(runner, "finish_session"))
                stack.enter_context(patch.object(runner, "log_session_result"))
                stack.enter_context(patch.object(runner, "update_points_ledger"))
                stack.enter_context(
                    patch.object(
                        runner,
                        "get_daily_search_progress",
                        side_effect=[scan_incomplete_vs, scan_complete_vs, scan_complete_vs],
                    )
                )
                stack.enter_context(patch.object(runner, "ensure_streak_protection"))
                stack.enter_context(patch.object(runner, "run_all_dashboard_activities"))
                stack.enter_context(patch.object(runner, "solve_visual_search_streak", side_effect=fake_vs))
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
                stack.enter_context(patch.object(runner, "_close_quietly", side_effect=fake_close))
                stack.enter_context(patch("time.sleep"))

                ok = runner.run_full_pipeline(HeadlessArgs())

            self.assertTrue(ok)
            self.assertIn(("close", "ctx_headless_1"), events)
            self.assertIn(("create_desktop_context", False), events)
            self.assertIn(("solve_visual_search_streak", True), events)
            self.assertIn(("close", "ctx_vs_visible"), events)
            self.assertIn(("ensure_desktop_page", True, True), events)

            idx_close_1 = events.index(("close", "ctx_headless_1"))
            idx_create_vis = events.index(("create_desktop_context", False))
            idx_solve_vs = events.index(("solve_visual_search_streak", True))
            idx_close_vis = events.index(("close", "ctx_vs_visible"))
            idx_reopen_hl = len(events) - 1 - events[::-1].index(("ensure_desktop_page", True, True))
            self.assertLess(idx_close_1, idx_create_vis)
            self.assertLess(idx_create_vis, idx_solve_vs)
            self.assertLess(idx_solve_vs, idx_close_vis)
            self.assertLess(idx_close_vis, idx_reopen_hl)


if __name__ == "__main__":
    unittest.main(verbosity=2)
