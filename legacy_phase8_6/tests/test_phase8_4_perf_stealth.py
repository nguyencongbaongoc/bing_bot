import datetime
import io
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import runner
from core import searcher, trends


class TestPhase84PerfStealth(unittest.TestCase):
    def test_1_route_filter_blocks_ads_and_media_allows_microsoft_bing(self):
        """Third-party ad/media route filter blocks doubleclick.net and media while allowing Bing/Microsoft."""
        self.assertTrue(runner.should_abort_route("https://ad.doubleclick.net/ddm/activity", "script"))
        self.assertTrue(runner.should_abort_route("https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js", "script"))
        self.assertTrue(runner.should_abort_route("https://cdn.taboola.com/libtrc/impl.js", "script"))
        self.assertTrue(runner.should_abort_route("https://widgets.outbrain.com/outbrain.js", "script"))
        self.assertTrue(runner.should_abort_route("https://cdn.example-news.com/video/intro.mp4", "media"))

        # Must allow 100% of requests to bing.com, rewards.bing.com, microsoft.com, live.com, msn.com
        self.assertFalse(runner.should_abort_route("https://www.bing.com/search?q=test", "document"))
        self.assertFalse(runner.should_abort_route("https://rewards.bing.com/earn", "xhr"))
        self.assertFalse(runner.should_abort_route("https://www.bing.com/rp/video.mp4", "media"))
        self.assertFalse(runner.should_abort_route("https://www.microsoft.com/en-us/rewards", "document"))
        self.assertFalse(runner.should_abort_route("https://login.live.com/oauth20_authorize.srf", "document"))
        self.assertFalse(runner.should_abort_route("https://www.msn.com/vi-vn/feed", "fetch"))

    def test_2_biometric_typing_produces_exact_target_with_qwerty_typo(self):
        """Biometric typing produces exact target string even when QWERTY typo simulation triggers."""
        typed_buffer = []
        delays_seen = []
        pauses_seen = []

        class FakeKeyboard:
            def type(self, ch, delay=0):
                delays_seen.append(delay)
                typed_buffer.append(ch)

            def press(self, key):
                if key == "Backspace" and typed_buffer:
                    typed_buffer.pop()

        class FakePage:
            keyboard = FakeKeyboard()

        target = "thoi tiet ha noi hom nay"
        page = FakePage()
        with patch.object(searcher, "_sleep_ms", side_effect=lambda ms, page=None: pauses_seen.append(ms)):
            searcher.biometric_type_text(page, target, typo_prob=1.0)

        self.assertEqual("".join(typed_buffer), target)
        # Ensure at least one Backspace correction happened (buffer had extra char typed then removed)
        self.assertGreater(len(delays_seen), len(target))
        # Ensure intra-word delays are in 35..75ms
        self.assertTrue(all(35 <= d <= 75 for d in delays_seen))
        # Ensure word-boundary pauses (140..260ms) or typo pauses (180..320ms) occurred
        self.assertTrue(any(180 <= p <= 320 for p in pauses_seen))
        self.assertTrue(any(140 <= p <= 260 for p in pauses_seen))

    def test_3_circadian_guard_allows_0800_blocks_0200_unless_force_hours(self):
        """Circadian active window guard allows 08:00 and blocks 02:00 unless --force-hours is passed."""
        self.assertTrue(runner.is_within_circadian_window(datetime.datetime(2026, 11, 15, 8, 0)))
        self.assertTrue(runner.is_within_circadian_window(datetime.datetime(2026, 11, 15, 7, 0)))
        self.assertTrue(runner.is_within_circadian_window(datetime.datetime(2026, 11, 15, 22, 30)))
        self.assertFalse(runner.is_within_circadian_window(datetime.datetime(2026, 11, 15, 2, 0)))
        self.assertFalse(runner.is_within_circadian_window(datetime.datetime(2026, 11, 15, 23, 18)))

        # Verify main() exits with code 0 when outside window and --force-hours is NOT passed
        with patch.object(runner, "acquire_runner_lock", return_value=MagicMock()), \
             patch.object(runner, "release_runner_lock"), \
             patch.object(runner, "cleanup_zombie_browsers"), \
             patch.object(runner, "is_within_circadian_window", return_value=False), \
             patch.object(runner, "run_full_pipeline") as mock_pipeline, \
             patch.object(sys, "argv", ["runner.py"]):
            with self.assertRaises(SystemExit) as cm:
                runner.main()
            self.assertEqual(cm.exception.code, 0)
            mock_pipeline.assert_not_called()

        # Verify main() runs pipeline when --force-hours IS passed even outside circadian window
        with patch.object(runner, "acquire_runner_lock", return_value=MagicMock()), \
             patch.object(runner, "release_runner_lock"), \
             patch.object(runner, "cleanup_zombie_browsers"), \
             patch.object(runner, "is_within_circadian_window", return_value=False), \
             patch.object(runner, "run_full_pipeline", return_value=True) as mock_pipeline, \
             patch.object(runner, "put_pc_to_sleep"), \
             patch.object(sys, "argv", ["runner.py", "--force-hours", "--no-sleep"]):
            with self.assertRaises(SystemExit) as cm:
                runner.main()
            self.assertEqual(cm.exception.code, 0)
            mock_pipeline.assert_called_once()

    def test_4_topic_clustered_queries_group_related_topics(self):
        """Topic-clustered query generator groups related queries together in 2-5 query bursts."""
        queries = trends.generate_topic_clustered_queries(needed=12, cluster_size_range=(2, 5))
        self.assertEqual(len(queries), 12)
        self.assertEqual(len(set(q.lower() for q in queries)), 12)

        # Check that consecutive queries share topic entities (adjacent pairs share a city/device/dish)
        entities = trends.CITIES + trends.TRAVEL_DESTINATIONS + trends.TECH_ITEMS + trends.RECIPE_ITEMS
        adjacent_shared = 0
        for i in range(len(queries) - 1):
            q1, q2 = queries[i].lower(), queries[i + 1].lower()
            if any(ent.lower() in q1 and ent.lower() in q2 for ent in entities):
                adjacent_shared += 1
        # With 12 queries in clusters of 2-5, there must be multiple adjacent pairs sharing the same entity
        self.assertGreaterEqual(adjacent_shared, 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
