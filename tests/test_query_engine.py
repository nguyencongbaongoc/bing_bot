"""Unit tests for Phase 1: Query Engine (Jaccard + Topic Rotation), StateDB WAL & Monotonic Bing Star."""

from __future__ import annotations

import datetime
from pathlib import Path

from core.query_engine import (
    extract_core_tokens,
    is_too_similar,
    jaccard_similarity,
    normalize_tokens,
    select_diverse_batch,
)
from core.state_db import StateDB


def test_query_engine_blocks_20_ga_nuong_muoi_ot_variants():
    variants = [
        ("recipe", "cách làm gà nướng muối ớt"),
        ("recipe", "công thức nấu gà nướng muối ớt"),
        ("recipe", "hướng dẫn làm gà nướng muối ớt"),
        ("recipe", "bí quyết làm món gà nướng muối ớt"),
        ("recipe", "mẹo nấu gà nướng muối ớt ngon tại nhà"),
        ("recipe", "gà nướng muối ớt chuẩn vị miền nam"),
        ("recipe", "cách ướp gà nướng muối ớt giòn da"),
        ("recipe", "gà nướng muối ớt bằng nồi chiên không dầu"),
        ("recipe", "hướng dẫn làm món gà nướng muối ớt thơm lừng"),
        ("recipe", "bí quyết nướng gà muối ớt không bị khô"),
        ("recipe", "cách làm gà nướng muối ớt cay nồng"),
        ("recipe", "công thức gà nướng muối ớt xanh"),
        ("recipe", "món gà nướng muối ớt đãi tiệc"),
        ("recipe", "gà nướng muối ớt nguyên con đơn giản"),
        ("recipe", "cách làm gà nướng muối ớt chanh"),
        ("recipe", "hướng dẫn nấu gà nướng muối ớt sả"),
        ("recipe", "bí quyết ướp gà nướng muối ớt đậm đà"),
        ("recipe", "mẹo làm gà nướng muối ớt siêu nhanh"),
        ("recipe", "gà nướng muối ớt kiểu tây bắc"),
        ("recipe", "công thức làm gà nướng muối ớt mật ong"),
    ]
    other_topics = [
        ("weather", "thời tiết hôm nay tại Hà Nội"),
        ("tech", "đánh giá chi tiết MacBook Air M3"),
        ("travel", "kinh nghiệm du lịch tự túc Hội An"),
        ("finance", "giá vàng SJC hôm nay"),
    ]
    candidates = variants + other_topics

    batch = select_diverse_batch(count=3, recent_queries=[], candidates=candidates, jaccard_threshold=0.35)
    assert len(batch) == 3

    ga_count = sum(1 for _, q in batch if "gà" in q.lower() and "muối ớt" in q.lower())
    assert ga_count <= 1, f"Expected at most 1 variant, got {batch}"

    topics = {t for t, _ in batch}
    assert len(topics) == 3, f"Expected 3 distinct topics, got {batch}"

    for i in range(len(batch)):
        for j in range(i + 1, len(batch)):
            q1, q2 = batch[i][1], batch[j][1]
            assert jaccard_similarity(set(normalize_tokens(q1)), set(normalize_tokens(q2))) < 0.35
            assert jaccard_similarity(extract_core_tokens(q1), extract_core_tokens(q2)) < 0.35


def test_query_engine_blocks_against_15_recent_history():
    recent_15 = [f"câu tìm kiếm mẫu số {i}" for i in range(14)] + ["cách làm gà nướng muối ớt"]
    candidates = [
        ("recipe", "bí quyết làm món gà nướng muối ớt"),
        ("recipe", "hướng dẫn làm gà nướng muối ớt"),
        ("weather", "dự báo thời tiết Đà Nẵng"),
        ("tech", "so sánh cấu hình Samsung Galaxy S24 Ultra"),
        ("sports", "lịch thi đấu Ngoại Hạng Anh tuần này"),
    ]
    assert is_too_similar("bí quyết làm món gà nướng muối ớt", recent_15, threshold=0.35)
    batch = select_diverse_batch(count=3, recent_queries=recent_15, candidates=candidates, jaccard_threshold=0.35)
    assert len(batch) == 3
    assert all("gà nướng muối ớt" not in q.lower() for _, q in batch)


def test_state_db_wal_and_monotonic_bing_star(tmp_path: Path):
    db = StateDB(tmp_path / "rewards.db")
    assert db.get_journal_mode() == "wal"

    date_key = datetime.date.today().isoformat()
    db.ensure_daily_row(date_key, "default")

    assert db.update_bing_star_monotonic(date_key, "default", 15) == 15
    assert db.update_bing_star_monotonic(date_key, "default", 3) == 15
    assert db.update_bing_star_monotonic(date_key, "default", 0) == 15
    assert db.update_bing_star_monotonic(date_key, "default", 24) == 24

    assert db.increment_visual_search_attempts(date_key, "default", 1) == 1
    assert db.increment_visual_search_attempts(date_key, "default", 1) == 2
    assert db.increment_visual_search_attempts(date_key, "default", 5) == 3


def test_state_db_monotonic_pc_mobile_ignores_negative(tmp_path: Path):
    db = StateDB(tmp_path / "rewards.db")
    date_key = datetime.date.today().isoformat()
    db.ensure_daily_row(date_key, "default")

    prog = db.update_daily_fields(date_key, "default", pc_cur=45, mobile_cur=30)
    assert prog["pc_cur"] == 45
    assert prog["mobile_cur"] == 30

    prog = db.update_daily_fields(date_key, "default", pc_cur=0, mobile_cur=0)
    assert prog["pc_cur"] == 45
    assert prog["mobile_cur"] == 30

    prog = db.update_daily_fields(date_key, "default", pc_cur=-1, mobile_cur=-1)
    assert prog["pc_cur"] == 45
    assert prog["mobile_cur"] == 30

    prog = db.update_daily_fields(date_key, "default", pc_cur=60, mobile_cur=42)
    assert prog["pc_cur"] == 60
    assert prog["mobile_cur"] == 42


def test_ingest_worker_result_estimates_when_negative_one(tmp_path: Path):
    from core.planner import ingest_worker_result
    from core.state_db import atomic_write_json

    db = StateDB(tmp_path / "rewards.db")
    date_key = datetime.date.today().isoformat()
    db.update_daily_fields(date_key, "default", pc_cur=45, mobile_cur=30, last_points=5000)

    atomic_write_json(
        tmp_path / "worker_result.json",
        {
            "schema_version": "1.0",
            "account": "default",
            "status": "ok",
            "pc_completed": 3,
            "mobile_completed": 3,
            "pc_queries_used": ["q1", "q2", "q3"],
            "mobile_queries_used": ["m1", "m2", "m3"],
            "pc_cur": -1,
            "pc_max": 90,
            "mobile_cur": -1,
            "mobile_max": 60,
            "points_before": 5000,
            "points_after": 5018,
            "bing_star_points": 0,
        },
    )

    rc = ingest_worker_result(account="default", exit_on_cooldown=False, data_dir=tmp_path)
    assert rc == 0
    prog = db.get_daily_progress(date_key, "default")
    assert prog["pc_cur"] == 54
    assert prog["mobile_cur"] == 39

    # Cap at 90 (PC) and 60 (Mobile) when old + completed*3 exceeds max
    db.update_daily_fields(date_key, "default", pc_cur=84, mobile_cur=54)
    atomic_write_json(
        tmp_path / "worker_result.json",
        {
            "schema_version": "1.0",
            "account": "default",
            "status": "ok",
            "pc_completed": 3,
            "mobile_completed": 3,
            "pc_cur": -1,
            "mobile_cur": -1,
            "points_before": 5018,
            "points_after": 5030,
        },
    )
    assert ingest_worker_result(account="default", exit_on_cooldown=False, data_dir=tmp_path) == 0
    prog = db.get_daily_progress(date_key, "default")
    assert prog["pc_cur"] == 90
    assert prog["mobile_cur"] == 60

    # Zero completed queries with -1 keeps old_value unchanged
    atomic_write_json(
        tmp_path / "worker_result.json",
        {
            "schema_version": "1.0",
            "account": "default",
            "status": "ok",
            "pc_completed": 0,
            "mobile_completed": 0,
            "pc_cur": -1,
            "mobile_cur": -1,
            "points_before": 5030,
            "points_after": 5030,
        },
    )
    assert ingest_worker_result(account="default", exit_on_cooldown=False, data_dir=tmp_path) == 0
    prog = db.get_daily_progress(date_key, "default")
    assert prog["pc_cur"] == 90
    assert prog["mobile_cur"] == 60


def test_core_has_no_browser_imports():
    import ast

    core_dir = Path(__file__).resolve().parent.parent / "core"
    for py_file in core_dir.glob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=py_file.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "playwright" not in alias.name.lower()
                    assert "patchright" not in alias.name.lower()
            elif isinstance(node, ast.ImportFrom):
                mod = (node.module or "").lower()
                assert "playwright" not in mod
                assert "patchright" not in mod
        assert "playwright" not in text.lower(), f"Forbidden playwright reference in {py_file.name}"
        assert "patchright" not in text.lower(), f"Forbidden patchright reference in {py_file.name}"
