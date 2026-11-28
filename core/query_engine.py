"""
Diverse Query Engine with Jaccard similarity guard and strict topic rotation.
Prevents root-keyword spam (e.g. repeated variants of 'gà nướng muối ớt').
"""

from __future__ import annotations

import html
import random
import re
import urllib.request
import xml.etree.ElementTree as ET
from typing import Iterable, Optional

# Common Vietnamese & English query boilerplate / prefixes stripped when checking core topic overlap
_STOPWORDS = frozenset({
    "cách", "làm", "công", "thức", "nấu", "hướng", "dẫn", "mẹo", "bí", "quyết",
    "món", "ngon", "chuẩn", "vị", "tại", "nhà", "đơn", "giản", "nhanh", "nhất",
    "đánh", "giá", "chi", "tiết", "so", "sánh", "cấu", "hình", "giá", "bán",
    "chính", "hãng", "có", "nên", "mua", "mở", "hộp", "trải", "nghiệm", "top",
    "thời", "tiết", "hôm", "nay", "ở", "dự", "báo", "nhiệt", "độ", "hiện",
    "kinh", "nghiệm", "du", "lịch", "tự", "túc", "địa", "điểm", "check", "in",
    "đẹp", "quán", "ăn", "rẻ", "khách", "sạn", "view", "lịch", "trình", "khám",
    "phá", "ngày", "đêm", "cho", "người", "mới", "và", "của", "với", "từ", "về",
    "how", "to", "make", "best", "review", "guide", "tips", "today", "in", "for",
})

TOPIC_POOLS: dict[str, list[str]] = {
    "recipe": [
        "cách làm phở bò truyền thống",
        "công thức nấu bún bò huế chuẩn vị",
        "hướng dẫn làm nem rán giòn rụm",
        "cách nấu canh chua cá lóc miền tây",
        "bí quyết làm món thịt kho tàu mềm ngon",
        "mẹo nấu bò sốt vang thơm mềm",
        "cách làm gỏi ngó sen tôm thịt",
        "công thức nấu chè bưởi an giang",
        "hướng dẫn làm bánh xèo miền tây",
        "cách nấu lẩu thái chua cay hải sản",
        "bí quyết làm sườn xào chua ngọt",
        "cách làm gà nướng muối ớt",
        "hướng dẫn làm chả cá lã vọng hà nội",
        "mẹo nấu cơm tấm sườn bì chả",
    ],
    "weather": [
        "thời tiết hôm nay tại Hà Nội",
        "dự báo thời tiết 3 ngày tới ở Đà Nẵng",
        "nhiệt độ hiện tại tại TP Hồ Chí Minh",
        "cuối tuần có mưa không tại Đà Lạt",
        "thời tiết hôm nay tại Nha Trang",
        "dự báo thời tiết cuối tuần ở Sa Pa",
        "nhiệt độ hiện tại tại Cần Thơ",
        "thời tiết biển Phú Quốc tuần này",
        "dự báo không khí lạnh miền Bắc",
    ],
    "tech": [
        "đánh giá chi tiết MacBook Air M3",
        "so sánh cấu hình Samsung Galaxy S24 Ultra",
        "có nên mua iPhone 16 Pro Max",
        "mở hộp trải nghiệm tai nghe chống ồn Sony",
        "giá bán chính hãng đồng hồ thông minh Garmin",
        "top laptop mỏng nhẹ cho sinh viên IT",
        "so sánh bàn phím cơ không dây keychron",
        "đánh giá chuột công thái học logitech",
        "máy lọc không khí phòng ngủ loại nào tốt",
    ],
    "travel": [
        "kinh nghiệm du lịch tự túc Hội An",
        "lịch trình khám phá 2 ngày 1 đêm Ninh Bình",
        "địa điểm check in đẹp nhất Hà Giang",
        "quán ăn ngon giá rẻ tại Huế",
        "khách sạn view đẹp ở Vũng Tàu",
        "kinh nghiệm du lịch tự túc Cát Bà",
        "lịch trình khám phá Mộc Châu mùa hoa",
        "địa điểm du lịch Quy Nhơn mùa hè",
    ],
    "finance": [
        "giá vàng SJC hôm nay",
        "tỷ giá USD Vietcombank mới nhất",
        "giá xăng dầu trong nước hôm nay",
        "cách tính lãi suất tiết kiệm ngân hàng",
        "chỉ số VN-Index phiên giao dịch hôm nay",
        "thủ tục mở tài khoản chứng khoán online",
    ],
    "health": [
        "bài tập yoga giảm mỡ bụng tại nhà",
        "thực đơn eat clean giảm cân 7 ngày",
        "lợi ích của chạy bộ buổi sáng",
        "cách cải thiện giấc ngủ sâu tự nhiên",
        "thực phẩm giàu omega 3 tốt cho tim mạch",
        "bài tập giãn cơ cổ vai gáy văn phòng",
    ],
    "sports": [
        "lịch thi đấu Ngoại Hạng Anh tuần này",
        "bảng xếp hạng Champions League mới nhất",
        "lịch thi đấu đội tuyển bóng đá Việt Nam",
        "kết quả giải quần vợt ATP hôm nay",
        "tin chuyển nhượng bóng đá châu Âu",
    ],
    "education": [
        "lộ trình tự học IELTS 7.0 cho người mới",
        "tài liệu học lập trình Python cơ bản",
        "cách viết CV xin việc ngành phần mềm",
        "ứng dụng học từ vựng tiếng Anh hiệu quả",
        "kỹ năng quản lý thời gian Pomodoro",
    ],
    "lifestyle": [
        "cách tra cứu phạt nguội giao thông toàn quốc",
        "thủ tục làm hộ chiếu trực tuyến nhanh",
        "cây phong thủy để bàn làm việc hợp mệnh",
        "mẹo tiết kiệm điện điều hòa mùa nóng",
        "cách chăm sóc cây sen đá trong nhà",
        "gợi ý sách hay phát triển bản thân",
    ],
}


def normalize_tokens(text: str) -> list[str]:
    """Lowercase and strip punctuation while preserving Vietnamese diacritics."""
    cleaned = re.sub(r"[^\w\s]", " ", str(text or "").lower())
    return [tok for tok in cleaned.split() if tok]


def extract_core_tokens(text: str) -> set[str]:
    """Extract core subject tokens by removing common prefix/boilerplate words."""
    tokens = normalize_tokens(text)
    core = {tok for tok in tokens if tok not in _STOPWORDS and len(tok) > 1}
    return core if core else set(tokens)


def _core_bigrams(text: str) -> set[tuple[str, str]]:
    tokens = [tok for tok in normalize_tokens(text) if tok not in _STOPWORDS and len(tok) > 1]
    if len(tokens) < 2:
        return set()
    return {(tokens[i], tokens[i + 1]) for i in range(len(tokens) - 1)}


def jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Compute Jaccard similarity between two token sets."""
    if not set_a or not set_b:
        return 0.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / float(len(union))


def is_too_similar(
    candidate: str,
    existing_queries: Iterable[str],
    threshold: float = 0.35,
) -> bool:
    """
    Return True if candidate violates Jaccard >= threshold (raw or core tokens)
    or shares a core subject phrase (2+ consecutive non-stopword tokens) with any existing query.
    """
    cand_raw = set(normalize_tokens(candidate))
    cand_core = extract_core_tokens(candidate)
    cand_bi = _core_bigrams(candidate)
    if not cand_raw:
        return True

    for old in existing_queries:
        old_str = str(old or "").strip()
        if not old_str:
            continue
        if old_str.lower() == candidate.strip().lower():
            return True
        old_raw = set(normalize_tokens(old_str))
        if jaccard_similarity(cand_raw, old_raw) >= threshold:
            return True
        old_core = extract_core_tokens(old_str)
        if jaccard_similarity(cand_core, old_core) >= threshold:
            return True
        if cand_bi and (cand_bi & _core_bigrams(old_str)):
            return True
    return False


def fetch_google_trends_candidates(geo: str = "VN", limit: int = 15) -> list[tuple[str, str]]:
    """Fetch trending keywords from Google Trends RSS as ('trends', query) pairs."""
    url = f"https://trends.google.com/trending/rss?geo={geo}"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
                )
            },
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            xml_text = resp.read().decode("utf-8", errors="replace")
        root = ET.fromstring(xml_text)
        out: list[tuple[str, str]] = []
        for idx, item in enumerate(root.findall(".//item")):
            t_el = item.find("title")
            if t_el is not None and t_el.text:
                q = html.unescape(t_el.text.strip())
                if q:
                    out.append((f"trends_{idx % 3}", q))
                    if len(out) >= limit:
                        break
        return out
    except Exception:
        return []


def build_default_candidates(include_trends: bool = True, geo: str = "VN") -> list[tuple[str, str]]:
    """Build shuffled multi-topic candidate list."""
    candidates: list[tuple[str, str]] = []
    for topic, queries in TOPIC_POOLS.items():
        for q in queries:
            candidates.append((topic, q))
    if include_trends:
        candidates.extend(fetch_google_trends_candidates(geo=geo, limit=12))
    random.shuffle(candidates)
    return candidates


def select_diverse_batch(
    count: int,
    recent_queries: list[str],
    candidates: Optional[list[tuple[str, str]]] = None,
    jaccard_threshold: float = 0.35,
    extra_excluded: Optional[Iterable[str]] = None,
) -> list[tuple[str, str]]:
    """
    Select up to `count` diverse queries compared against the last 15 recent queries
    and within the batch itself:
      - Reject any candidate with Jaccard >= jaccard_threshold or shared core subject phrase.
      - Enforce at least min(count, 3) distinct topics in the selected batch.
    """
    if count <= 0:
        return []

    history_window = list(recent_queries[:15])
    if extra_excluded:
        history_window.extend(str(x) for x in extra_excluded if str(x).strip())

    pool = list(candidates) if candidates is not None else build_default_candidates(include_trends=True)
    if not pool:
        return []

    selected: list[tuple[str, str]] = []
    used_topics: set[str] = set()
    min_distinct_topics = min(count, 3)

    # Pass 1: strictly require unseen topic until min_distinct_topics is satisfied
    for topic, query in pool:
        if len(selected) >= count:
            break
        q_clean = str(query or "").strip()
        if not q_clean:
            continue
        if len(used_topics) < min_distinct_topics and topic in used_topics:
            continue
        existing = history_window + [q for _, q in selected]
        if is_too_similar(q_clean, existing, threshold=jaccard_threshold):
            continue
        selected.append((topic, q_clean))
        used_topics.add(topic)

    # Pass 2: if still short and we already satisfied topic diversity (or no new topics left), fill remaining slots
    if len(selected) < count:
        for topic, query in pool:
            if len(selected) >= count:
                break
            q_clean = str(query or "").strip()
            if not q_clean:
                continue
            if any(q.lower() == q_clean.lower() for _, q in selected):
                continue
            existing = history_window + [q for _, q in selected]
            if is_too_similar(q_clean, existing, threshold=jaccard_threshold):
                continue
            selected.append((topic, q_clean))
            used_topics.add(topic)

    return selected
