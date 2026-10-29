"""
Module cào dữ liệu trending queries từ Google Trends RSS và quản lý lịch sử tìm kiếm.
Hỗ trợ chống trùng lặp từ khóa tuyệt đối (searched_history.json) và sinh truy vấn phong phú.
"""

import sys
import io
import json
import urllib.request
import xml.etree.ElementTree as ET
import html
import random
from pathlib import Path
from typing import List, Set, Optional

# Force UTF-8 encoding for stdout/stderr on Windows (Python 3.7+ reconfigure)
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Thư mục lưu lịch sử tìm kiếm
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
HISTORY_FILE = DATA_DIR / "searched_history.json"
LEGACY_HISTORY_FILE = DATA_DIR / "used_keywords.json"

# Danh sách từ khóa fallback cơ bản
FALLBACK_KEYWORDS: List[str] = [
    "trí tuệ nhân tạo AI", "điện thoại mới 2024", "laptop gaming giá rẻ",
    "công nghệ 5G Việt Nam", "xe điện VinFast", "thời tiết hôm nay",
    "tỷ giá đô la hôm nay", "kết quả xổ số", "bóng đá Việt Nam",
    "du lịch đà nẵng", "nhà hàng ngon hà nội", "cách làm bánh mì",
    "công việc online", "học lập trình python", "chứng chỉ google",
    "marketing số 2024", "đầu tư chứng khoán", "bitcoin giá hôm nay",
    "bất động sản hà nội", "xu hướng thời trang", "skincare routine",
    "gym tại nhà", "nấu ăn healthy", "học tiếng anh giao tiếp",
    "du lịch bỉm sập", "review sản phẩm", "unboxing tech",
    "công nghệ blockchain", "metaverse là gì", "chatgpt việt nam"
]

# Các chủ đề kết hợp đa dạng đời sống/ẩm thực/du lịch/công nghệ/thời tiết tiếng Việt
RECIPE_PREFIXES = ["cách làm", "công thức nấu", "hướng dẫn làm", "mẹo nấu", "cách nấu", "bí quyết làm món"]
RECIPE_ITEMS = [
    "phở bò truyền thống", "bún bò huế chuẩn vị", "nem rán giòn rụm", "cơm tấm sườn bì chả",
    "canh chua cá lóc", "thịt kho tàu miền tây", "bò sốt vang thơm mềm", "gỏi ngó sen tôm thịt",
    "chè bưởi an giang", "cà phê muối huế", "trà mãng cầu xiêm", "bánh mì chảo xúc xích",
    "lẩu thái chua cay", "gà nướng muối ớt", "bánh xèo giòn rụm miền tây", "sườn xào chua ngọt",
    "bánh bông lan trứng muối", "chả ram tôm đất", "kho quẹt chấm rau luộc", "chả cá lã vọng"
]

WEATHER_PREFIXES = ["thời tiết hôm nay tại", "dự báo thời tiết 3 ngày tới ở", "nhiệt độ hiện tại tại", "cuối tuần có mưa không tại"]
CITIES = ["Hà Nội", "TP Hồ Chí Minh", "Đà Nẵng", "Đà Lạt", "Nha Trang", "Sa Pa", "Hải Phòng", "Cần Thơ", "Vũng Tàu", "Huế", "Quy Nhơn", "Phú Quốc", "Hạ Long", "Ninh Bình"]

TECH_PREFIXES = ["đánh giá chi tiết", "so sánh cấu hình", "top laptop mỏng nhẹ", "mở hộp trải nghiệm", "giá bán chính hãng", "có nên mua"]
TECH_ITEMS = [
    "iPhone 16 Pro Max", "Samsung Galaxy S24 Ultra", "MacBook Air M3", "Xiaomi 14 Pro",
    "iPad Gen 10", "tai nghe chống ồn Sony", "đồng hồ thông minh Garmin", "máy lọc không khí phòng ngủ",
    "bàn phím cơ không dây", "chuột công thái học logitech", "robot hút bụi ecovacs"
]

TRAVEL_PREFIXES = ["kinh nghiệm du lịch tự túc", "địa điểm check in đẹp nhất", "quán ăn ngon giá rẻ tại", "khách sạn view đẹp ở", "lịch trình khám phá 2 ngày 1 đêm"]
TRAVEL_DESTINATIONS = ["Đà Lạt", "Sa Pa", "Ninh Bình", "Hà Giang", "Hội An", "Phú Quốc", "Mộc Châu", "Tam Đảo", "Cát Bà", "Phan Thiết", "Bảo Lộc", "Côn Đảo"]

GENERAL_QUERIES = [
    "giá vàng SJC hôm nay", "tỷ giá USD vietcombank hôm nay", "giá xăng dầu mới nhất",
    "lịch thi đấu ngoại hạng anh hôm nay", "bảng xếp hạng champions league", "bài tập yoga giảm mỡ bụng tại nhà",
    "thực đơn eat clean giảm cân 7 ngày", "lộ trình tự học IELTS 7.0 cho người mới", "cách làm hồ sơ xin việc online",
    "cách tra cứu phạt nguội giao thông", "thủ tục làm hộ chiếu trực tuyến nhanh", "chăm sóc da mùa đông hanh khô",
    "cây phong thủy để bàn làm việc", "mẹo tiết kiệm chi tiêu gia đình", "cách ngủ ngon không mơ"
]


def load_searched_history() -> List[str]:
    """Đọc lịch sử từ khóa đã tìm kiếm từ searched_history.json (hoặc used_keywords.json)."""
    target_file = HISTORY_FILE if HISTORY_FILE.exists() else (LEGACY_HISTORY_FILE if LEGACY_HISTORY_FILE.exists() else None)
    if not target_file or not target_file.exists():
        return []

    try:
        with open(target_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return [str(item).strip() for item in data if str(item).strip()]
            elif isinstance(data, dict) and "keywords" in data:
                return [str(item).strip() for item in data["keywords"] if str(item).strip()]
    except Exception as e:
        print(f"[Trends] Không thể đọc file lịch sử: {e}")
    return []


def save_searched_keyword(keyword: str, max_history: int = 200) -> None:
    """Lưu từ khóa vừa tìm kiếm vào data/searched_history.json, giữ tối đa max_history từ."""
    clean_kw = keyword.strip()
    if not clean_kw:
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    history = load_searched_history()

    # Kiểm tra xem từ đã có trong danh sách chưa (case-insensitive)
    existing_lower = {k.lower() for k in history}
    if clean_kw.lower() not in existing_lower:
        history.append(clean_kw)

    # Giữ tối đa max_history từ gần nhất
    if len(history) > max_history:
        history = history[-max_history:]

    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Trends] Lỗi khi lưu searched_history.json: {e}")


def generate_diverse_queries(needed: int, exclude: Optional[Set[str]] = None) -> List[str]:
    """Sinh các truy vấn đời sống/thời tiết/công nghệ/ẩm thực đa dạng không trùng lặp."""
    exclude_set = {k.lower().strip() for k in exclude} if exclude else set()
    candidates = []

    # 1. Ẩm thực
    for p in RECIPE_PREFIXES:
        for it in RECIPE_ITEMS:
            candidates.append(f"{p} {it}")

    # 2. Thời tiết
    for p in WEATHER_PREFIXES:
        for c in CITIES:
            candidates.append(f"{p} {c}")

    # 3. Công nghệ
    for p in TECH_PREFIXES:
        for t in TECH_ITEMS:
            candidates.append(f"{p} {t}")

    # 4. Du lịch
    for p in TRAVEL_PREFIXES:
        for d in TRAVEL_DESTINATIONS:
            candidates.append(f"{p} {d}")

    # 5. Đời sống tổng hợp
    candidates.extend(GENERAL_QUERIES)

    # Xáo trộn ngẫu nhiên
    random.shuffle(candidates)

    selected = []
    for q in candidates:
        q_clean = q.strip()
        if q_clean.lower() not in exclude_set:
            selected.append(q_clean)
            exclude_set.add(q_clean.lower())
            if len(selected) >= needed:
                break

    return selected


def fetch_trending_queries(geo: str = "VN", limit: int = 50) -> List[str]:
    """Lấy danh sách truy vấn đang hot từ Google Trends RSS."""
    url = f"https://trends.google.com/trending/rss?geo={geo}"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            xml_content = response.read().decode("utf-8")

        root = ET.fromstring(xml_content)
        queries = []
        for item in root.findall(".//item"):
            title_elem = item.find("title")
            if title_elem is not None and title_elem.text:
                clean_title = html.unescape(title_elem.text.strip())
                if clean_title:
                    queries.append(clean_title)

        return queries[:limit] if queries else FALLBACK_KEYWORDS[:limit]

    except Exception as e:
        print(f"[Trends] Lỗi khi fetch Google Trends RSS: {e}. Sử dụng fallback.")
        return FALLBACK_KEYWORDS[:limit]


def get_unique_search_queries(target_count: int, geo: str = "VN", max_history: int = 200) -> List[str]:
    """
    Lấy danh sách từ khóa tìm kiếm độc nhất tuyệt đối:
    1. Cào Google Trends mới nhất.
    2. Lọc bỏ các từ khóa đã xuất hiện trong 200 từ gần nhất của data/searched_history.json.
    3. Nếu không đủ số lượng, sinh thêm từ khóa từ bộ tổ hợp đời sống phong phú.
    """
    history = load_searched_history()
    history_set = {k.lower().strip() for k in history if k}

    # Lấy Google Trends (yêu cầu nhiều hơn để lọc trùng)
    raw_trends = fetch_trending_queries(geo=geo, limit=max(50, target_count + 20))
    
    unique_queries = []
    for q in raw_trends:
        q_clean = q.strip()
        if q_clean and q_clean.lower() not in history_set and q_clean.lower() not in {x.lower() for x in unique_queries}:
            unique_queries.append(q_clean)

    # Nếu thiếu từ khóa do Trends ít hoặc trùng lặp quá nhiều trong ngày
    if len(unique_queries) < target_count:
        needed = target_count - len(unique_queries)
        current_used = history_set.union({x.lower() for x in unique_queries})
        synthesized = generate_diverse_queries(needed=needed, exclude=current_used)
        unique_queries.extend(synthesized)

    # Xáo trộn thứ tự tự nhiên
    random.shuffle(unique_queries)
    return unique_queries[:target_count]


if __name__ == "__main__":
    print(f"Lịch sử hiện tại: {len(load_searched_history())} từ khóa")
    sample_queries = get_unique_search_queries(10)
    print(f"Mẫu 10 từ khóa độc nhất:")
    for i, q in enumerate(sample_queries, 1):
        print(f"  {i}. {q}")
