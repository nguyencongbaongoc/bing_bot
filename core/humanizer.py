"""
Module giả lập hành vi con người: độ trễ Gaussian, gõ phím, cooldown.
Giúp bot tránh bị phát hiện qua telemetry hành vi.
"""

import sys
import io
import time
import random
import math
from typing import Optional

# Force UTF-8 encoding for stdout/stderr on Windows (robust version)
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', write_through=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', write_through=True)
    except Exception:
        pass


def gaussian_delay(mean: float = 9.0, std_dev: float = 2.5, min_val: float = 5.0) -> float:
    """
    Tạm dừng ngẫu nhiên theo phân phối Gaussian (chuông) để tránh chu kỳ tuần hoàn cố định.
    
    Args:
        mean: Thời gian trung bình (giây)
        std_dev: Độ lệch chuẩn
        min_val: Thời gian tối thiểu (giây)
        
    Returns:
        Thời gian thực tế đã ngủ (giây)
    """
    # Box-Muller transform để sinh số Gaussian
    u1 = random.random()
    u2 = random.random()
    z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
    
    delay = max(min_val, mean + z * std_dev)
    
    # Cap tối đa để không ngủ quá lâu (mean + 3*std_dev)
    max_delay = mean + 3 * std_dev
    delay = min(delay, max_delay)
    
    time.sleep(delay)
    return delay


def typing_delay() -> int:
    """
    Trả về độ trễ gõ phím ngẫu nhiên (milliseconds) để giả lập tốc độ gõ tự nhiên.
    
    Returns:
        Độ trễ giữa các ký tự (ms), trong khoảng 50-140ms
    """
    # Phân bố lệch về phía nhanh (con người gõ nhanh hơn lúc đầu)
    base = random.randint(50, 100)
    variation = random.randint(-20, 40)
    return max(30, min(180, base + variation))


def cooldown_sleep(minutes: int = 15) -> None:
    """
    Tạm dừng cooldown ~15 phút + random(30, 90) giây, in tiến trình đếm ngược.
    
    Args:
        minutes: Số phút cooldown cơ bản
    """
    extra_seconds = random.randint(30, 90)
    total_seconds = minutes * 60 + extra_seconds
    
    print(f"\n[Cooldown] Nghỉ {minutes} phút {extra_seconds} giây ({total_seconds}s)...")
    
    # Chia nhỏ để in tiến trình mỗi 30 giây
    interval = 30
    elapsed = 0
    
    while elapsed < total_seconds:
        remaining = total_seconds - elapsed
        mins = remaining // 60
        secs = remaining % 60
        print(f"  ⏳ Còn lại: {mins:02d}:{secs:02d}", end="\r")
        
        sleep_time = min(interval, remaining)
        time.sleep(sleep_time)
        elapsed += sleep_time
    
    print(f"  ✅ Cooldown hoàn tất!                          ")


def human_scroll_delay() -> float:
    """
    Độ trễ cuộn trang mượt mà (giây).
    
    Returns:
        Thời gian cuộn (0.5 - 1.5 giây)
    """
    return random.uniform(0.5, 1.5)


def human_read_delay() -> float:
    """
    Độ trễ 'đọc trang' giả lập (giây).
    
    Returns:
        Thời gian dừng đọc (2 - 6 giây)
    """
    return random.uniform(2.0, 6.0)


def maybe_click_result(probability: float = 0.2) -> bool:
    """
    Xác suất ngẫu nhiên click vào kết quả tìm kiếm (mặc định 20%).
    
    Args:
        probability: Xác suất click (0.0 - 1.0)
        
    Returns:
        True nếu nên click, False nếu bỏ qua
    """
    return random.random() < probability


def random_viewport_offset() -> tuple[int, int]:
    """
    Tạo offset ngẫu nhiên cho viewport để tránh fingerprint cố định.
    
    Returns:
        Tuple (x, y) offset nhỏ
    """
    return (random.randint(-5, 5), random.randint(-5, 5))


# Alias for backward compatibility and clearer naming
human_delay = gaussian_delay


if __name__ == "__main__":
    # Test nhanh
    print("Testing gaussian_delay (3 samples):")
    for _ in range(3):
        d = gaussian_delay(2.0, 0.5, 1.0)
        print(f"  Slept {d:.2f}s")
    
    print("\nTesting typing_delay (10 samples):")
    for _ in range(10):
        print(f"  {typing_delay()}ms", end=" ")
    print()
    
    print("\nTesting cooldown_sleep (10s demo):")
    cooldown_sleep(0)  # 0 phút + random seconds