import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import runner
from core.activities import get_daily_search_progress
from core.cooldown_controller import CooldownController
from core.notifier import Notifier
from core.state_store import StateStore, atomic_write_json, get_account_paths, safe_read_json
from core.tracker import extract_current_points


class MockRewardsState:
    def __init__(self):
        self.lock = threading.Lock()
        self.balance = 1490
        self.pc_cur = 60
        self.pc_max = 90
        self.mob_cur = 30
        self.mob_max = 60
        self.cooldown_mode = False
        self.dapi_error_mode = False
        self.dapi_empty_mode = False
        self.search_queries: list[str] = []


def make_handler(state: MockRewardsState):
    class MockHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path.startswith("/dapi/me"):
                with state.lock:
                    if state.dapi_error_mode:
                        self.send_response(500)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(b"{corrupted-json")
                        return
                    if state.dapi_empty_mode:
                        payload = {
                            "response": {
                                "balance": state.balance,
                                "counters": {
                                    "pcSearch": {"pointProgress": 90, "pointProgressMax": 90},
                                    "mobileSearch": {"pointProgress": 60, "pointProgressMax": 60},
                                },
                                "activities": {
                                    "Bing Search": {"cur": 1, "max": 1, "done": True},
                                    "Daily Set": {"cur": 3, "max": 3, "done": True},
                                    "Edge Browsing": {"cur": 30, "max": 30, "done": True},
                                    "Visual Search": {"cur": 1, "max": 1, "done": True},
                                },
                                "promotions": [],
                            }
                        }
                    else:
                        payload = {
                            "response": {
                                "balance": state.balance,
                                "counters": {
                                    "pcSearch": {"pointProgress": state.pc_cur, "pointProgressMax": state.pc_max},
                                    "mobileSearch": {"pointProgress": state.mob_cur, "pointProgressMax": state.mob_max},
                                },
                                "activities": {
                                    "Bing Search": {"cur": 1, "max": 1, "done": True},
                                    "Daily Set": {"cur": 2, "max": 3, "done": False},
                                    "Edge Browsing": {"cur": 15, "max": 30, "done": False},
                                    "Visual Search": {"cur": 1, "max": 1, "done": True},
                                },
                                "promotions": [{"id": "promo_1", "title": "Daily Quiz", "pointValue": 10, "complete": False}],
                            }
                        }
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if parsed.path.startswith("/earn"):
                with state.lock:
                    bal = state.balance
                    pc_cur, pc_max = state.pc_cur, state.pc_max
                    mob_cur, mob_max = state.mob_cur, state.mob_max
                html = f"""<!doctype html>
<html><head><title>Microsoft Rewards</title></head>
<body>
  <span id="id_rc">{bal}</span>
  <div role="progressbar" aria-label="Bing Search" aria-valuenow="1" aria-valuemax="1"></div>
  <div role="progressbar" aria-label="Daily Set" aria-valuenow="3" aria-valuemax="3"></div>
  <div role="progressbar" aria-label="Edge Browsing" aria-valuenow="30" aria-valuemax="30"></div>
  <div role="progressbar" aria-label="Mobile App" aria-valuenow="1" aria-valuemax="1"></div>
  <div role="progressbar" aria-label="Visual Search" aria-valuenow="1" aria-valuemax="1"></div>
  <div>Points breakdown</div>
  <div>Desktop Bing search {pc_cur} / {pc_max}</div>
  <div>Mobile Bing search {mob_cur} / {mob_max}</div>
  <div>Bing Star bonus 120 / 2,100</div>
</body></html>""".encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html)
                return

            if parsed.path.startswith("/search"):
                qs = parse_qs(parsed.query)
                query = (qs.get("q") or [""])[0]
                with state.lock:
                    if query:
                        state.search_queries.append(query)
                        if not state.cooldown_mode:
                            state.balance += 3
                            state.pc_cur = min(state.pc_max, state.pc_cur + 3)
                    bal = state.balance
                html = f"""<!doctype html>
<html><head><title>{query} - Bing</title></head>
<body>
  <header>
    <input id="sb_form_q" name="q" value="{query}" />
    <span id="id_rc">{bal}</span>
  </header>
  <main id="b_results">
    <li class="b_algo"><h2><a href="#res1">Result for {query}</a></h2></li>
  </main>
</body></html>""".encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html)
                return

            self.send_response(404)
            self.end_headers()

    return MockHandler


class TestPhase85E2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mock_state = MockRewardsState()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.mock_state))
        cls.port = cls.server.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_1_atomic_io_and_bak_recovery(self):
        """Mid-write JSON corruption recovers transparently from .bak without losing counters."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "daily_state_acc.json"
            atomic_write_json(state_path, {"date": "2026-11-15", "edge_browsing_seconds": 900, "status": "partial"})
            atomic_write_json(state_path, {"date": "2026-11-15", "edge_browsing_seconds": 1200, "status": "partial"})
            # Simulate truncated crash write to primary file
            state_path.write_text('{"date": "2026-11-15", "edge_brows', encoding="utf-8")

            recovered = safe_read_json(state_path, default={})
            self.assertEqual(recovered.get("edge_browsing_seconds"), 900)
            self.assertEqual(recovered.get("status"), "partial")

    def test_2_adaptive_zero_point_cooldown(self):
        """4 consecutive zero-point searches abort burst early and apply exponential backoff (20m->30m->45m)."""
        ctrl = CooldownController()
        ctrl.reset_burst(baseline_points=1000)

        self.assertFalse(ctrl.record_search_points(1000))  # 1
        self.assertFalse(ctrl.record_search_points(1000))  # 2 -> 1st zero check
        self.assertFalse(ctrl.record_search_points(1000))  # 3
        self.assertTrue(ctrl.record_search_points(1000))   # 4 -> 2nd zero check -> abort!

        cd1 = ctrl.next_cooldown_seconds(jitter_fn=lambda a, b: 0)
        self.assertEqual(cd1, 20 * 60)

        # Second trigger escalates to 30m
        ctrl.reset_burst(baseline_points=1000)
        ctrl.record_search_points(1000)
        ctrl.record_search_points(1000)
        ctrl.record_search_points(1000)
        self.assertTrue(ctrl.record_search_points(1000))
        self.assertEqual(ctrl.next_cooldown_seconds(jitter_fn=lambda a, b: 0), 30 * 60)

        # Point increment resets back to normal 16-24m log-normal range
        ctrl.reset_burst(baseline_points=1000)
        ctrl.record_search_points(1003)
        self.assertFalse(ctrl.record_search_points(1006))
        self.assertEqual(ctrl.backoff_level, 0)
        normal_cd = ctrl.next_cooldown_seconds()
        self.assertGreaterEqual(normal_cd, 960)
        self.assertLessEqual(normal_cd, 1440)

    def test_3_dapi_first_resolution_and_error_separation(self):
        """Valid dapi/me populates progress; HTTP 500/malformed returns status='error' instead of 100%."""
        with self.mock_state.lock:
            self.mock_state.dapi_error_mode = False
            self.mock_state.dapi_empty_mode = False
            self.mock_state.pc_cur = 60
            self.mock_state.mob_cur = 30

        res_active = get_daily_search_progress(
            page=None,
            dapi_url=f"{self.base_url}/dapi/me",
            access_token="mock_token",
            use_dapi=True,
        )
        self.assertEqual(res_active["source"], "dapi")
        self.assertEqual(res_active["status"], "active")
        self.assertEqual(res_active["pc_cur"], 60)
        self.assertEqual(res_active["mobile_cur"], 30)
        self.assertFalse(res_active["all_maxed"])

        with self.mock_state.lock:
            self.mock_state.dapi_empty_mode = True
        res_empty = get_daily_search_progress(
            page=None,
            dapi_url=f"{self.base_url}/dapi/me",
            access_token="mock_token",
            use_dapi=True,
        )
        self.assertEqual(res_empty["status"], "empty")
        self.assertTrue(res_empty["all_maxed"])

        with self.mock_state.lock:
            self.mock_state.dapi_empty_mode = False
            self.mock_state.dapi_error_mode = True
        res_error = get_daily_search_progress(
            page=None,
            dapi_url=f"{self.base_url}/dapi/me",
            access_token="mock_token",
            use_dapi=True,
        )
        self.assertEqual(res_error["status"], "error")
        self.assertTrue(res_error["scrape_error"])
        self.assertFalse(res_error["all_maxed"])

    def test_4_notifier_deduplication(self):
        """Actionable alerts dispatch once per day and suppress duplicates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(account_id="acc_alert", data_dir=Path(tmpdir), profiles_dir=Path(tmpdir))
            sent_messages = []
            notifier = Notifier(
                account_id="acc_alert",
                state_store=store,
                telegram_sender=lambda text, tok, cid, timeout: sent_messages.append(text) or True,
                toast_sender=lambda title, msg: sent_messages.append(f"{title}: {msg}") or True,
            )
            with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"}):
                self.assertTrue(notifier.notify_auth_expired("login.live.com redirect"))
                self.assertFalse(notifier.notify_auth_expired("login.live.com redirect"))
                self.assertEqual(len(sent_messages), 1)
                self.assertEqual(notifier.check_and_notify_milestones(1550), [1500])
                self.assertEqual(notifier.check_and_notify_milestones(1600), [])
                self.assertEqual(len(sent_messages), 2)

    def test_5_per_account_isolation_and_live_patchright_serp(self):
        """Headless Patchright runs against mock SERP, increments points, and isolates acc_test_a vs acc_test_b."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            prof_dir = Path(tmpdir) / "profiles"
            paths_a = get_account_paths("acc_test_a", data_dir=data_dir, profiles_dir=prof_dir)
            paths_b = get_account_paths("acc_test_b", data_dir=data_dir, profiles_dir=prof_dir)

            self.assertNotEqual(paths_a["lock_file"], paths_b["lock_file"])
            self.assertNotEqual(paths_a["daily_state_file"], paths_b["daily_state_file"])
            self.assertNotEqual(paths_a["desktop_profile"], paths_b["desktop_profile"])

            lock_a = runner.acquire_runner_lock(paths_a["lock_file"])
            lock_b = runner.acquire_runner_lock(paths_b["lock_file"])
            self.assertIsNotNone(lock_a)
            self.assertIsNotNone(lock_b)
            # Second lock on acc_test_a must fail while lock_a is held
            self.assertIsNone(runner.acquire_runner_lock(paths_a["lock_file"]))
            runner.release_runner_lock(lock_a)
            runner.release_runner_lock(lock_b)

            with self.mock_state.lock:
                self.mock_state.balance = 1490
                self.mock_state.cooldown_mode = False
                self.mock_state.search_queries.clear()

            with runner.get_sync_playwright()() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.goto(f"{self.base_url}/earn", wait_until="domcontentloaded")
                    start_pts = extract_current_points(page)
                    self.assertEqual(start_pts, 1490)

                    ctrl = CooldownController()
                    ctrl.reset_burst(start_pts)
                    for kw in ("thoi tiet ha noi", "gia vang hom nay"):
                        page.goto(f"{self.base_url}/search?q={kw}", wait_until="domcontentloaded")
                        pts = extract_current_points(page)
                        aborted = ctrl.record_search_points(pts)
                        self.assertFalse(aborted)

                    end_pts = extract_current_points(page)
                    self.assertEqual(end_pts, 1496)

                    # Now toggle cooldown_mode (0 pts for 4 searches) and verify abort
                    with self.mock_state.lock:
                        self.mock_state.cooldown_mode = True
                    ctrl.reset_burst(end_pts)
                    aborted = False
                    for idx in range(4):
                        page.goto(f"{self.base_url}/search?q=cooldown_{idx}", wait_until="domcontentloaded")
                        pts = extract_current_points(page)
                        if ctrl.record_search_points(pts):
                            aborted = True
                            break
                    self.assertTrue(aborted)
                finally:
                    browser.close()


if __name__ == "__main__":
    unittest.main()
