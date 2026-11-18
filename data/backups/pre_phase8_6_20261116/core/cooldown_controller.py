"""
Adaptive Zero-Point Delta Cooldown Controller & Variable Burst Sizing.
Phase 8.5 - MS Rewards Bot.
"""

from __future__ import annotations

import math
import random
from typing import Optional

from core.tracker import _log_info, _log_warning

BACKOFF_STEPS_SECONDS: tuple[int, ...] = (20 * 60, 30 * 60, 45 * 60)
BACKOFF_JITTER_SECONDS = 120
CHECK_INTERVAL_SEARCHES = 2
CONSECUTIVE_ZERO_CHECKS_THRESHOLD = 2  # 2 checks * 2 searches = 4 searches


def get_variable_batch_size(configured_batch_size: int = 3) -> int:
    """Trả về kích thước batch ngẫu nhiên (2..5) khi dùng cấu hình mặc định."""
    if configured_batch_size == 3:
        return random.randint(2, 5)
    return max(1, int(configured_batch_size))


def get_natural_cooldown_seconds() -> int:
    """
    Trả về khoảng nghỉ cooldown phân phối log-normal tự nhiên trong khoảng 16..24 phút (960..1440s).
    """
    raw = random.lognormvariate(math.log(1170), 0.12)
    return int(max(960, min(1440, round(raw))))


class CooldownController:
    """
    Theo dõi delta điểm số sau mỗi 2 lượt tìm kiếm trong một đợt (burst).
    Nếu delta == 0 qua 2 lần kiểm tra liên tiếp (4 lượt tìm kiếm không tăng điểm
    khi chưa chạm trần ngày), kích hoạt Adaptive Cooldown:
      - Hủy các từ khóa còn lại trong đợt hiện tại để tránh lãng phí từ khóa.
      - Tăng dần thời gian nghỉ: 20m -> 30m -> 45m (kèm jitter ±120s).
      - Tự động reset về dải log-normal 16–24m ngay khi điểm tăng trở lại.
    """

    def __init__(self, check_interval: int = CHECK_INTERVAL_SEARCHES):
        self.check_interval = max(1, int(check_interval))
        self.last_check_points: Optional[int] = None
        self.searches_since_check: int = 0
        self.zero_delta_checks: int = 0
        self.backoff_level: int = 0
        self.cooldown_triggered: bool = False

    def reset_burst(self, baseline_points: Optional[int] = None) -> None:
        """Khởi tạo lại bộ đếm trước mỗi đợt tìm kiếm mới."""
        if baseline_points is not None:
            self.last_check_points = int(baseline_points)
        self.searches_since_check = 0
        self.zero_delta_checks = 0
        self.cooldown_triggered = False

    def record_search_points(
        self,
        current_points: Optional[int],
        daily_cap_reached: bool = False,
    ) -> bool:
        """
        Ghi nhận điểm sau một lượt tìm kiếm.
        Trả về True nếu phát hiện Cooldown ẩn và cần dừng ngay đợt tìm kiếm hiện tại.
        """
        if current_points is None or current_points < 0:
            return False

        pts = int(current_points)
        if self.last_check_points is None:
            self.last_check_points = pts

        self.searches_since_check += 1
        if self.searches_since_check < self.check_interval:
            return False

        # Đủ 2 lượt tìm kiếm -> tính delta
        self.searches_since_check = 0
        delta = pts - self.last_check_points
        self.last_check_points = pts

        if delta > 0:
            if self.backoff_level > 0 or self.zero_delta_checks > 0:
                _log_info(
                    f"[Adaptive Cooldown] Điểm đã tăng trở lại (+{delta} pts). "
                    "Reset chu kỳ nghỉ về mức bình thường (16-24m)."
                )
            self.zero_delta_checks = 0
            self.backoff_level = 0
            self.cooldown_triggered = False
            return False

        if daily_cap_reached:
            return False

        self.zero_delta_checks += 1
        if self.zero_delta_checks >= CONSECUTIVE_ZERO_CHECKS_THRESHOLD:
            self.backoff_level = min(self.backoff_level + 1, len(BACKOFF_STEPS_SECONDS))
            self.cooldown_triggered = True
            _log_warning(
                "[Adaptive Cooldown] Phát hiện Cooldown ẩn (0 pts sau 4 lượt tìm kiếm). "
                "Dừng đợt hiện tại và tăng thời gian nghỉ."
            )
            return True

        return False

    def next_cooldown_seconds(self, jitter_fn=None) -> int:
        """
        Trả về số giây cooldown tiếp theo:
        - Nếu đang trong backoff: 20m -> 30m -> 45m (±120s jitter).
        - Nếu bình thường: 16–24m log-normal.
        """
        if self.backoff_level > 0:
            base = BACKOFF_STEPS_SECONDS[self.backoff_level - 1]
            jitter = (
                jitter_fn(-BACKOFF_JITTER_SECONDS, BACKOFF_JITTER_SECONDS)
                if jitter_fn is not None
                else random.randint(-BACKOFF_JITTER_SECONDS, BACKOFF_JITTER_SECONDS)
            )
            return max(60, int(base + jitter))
        return get_natural_cooldown_seconds()
