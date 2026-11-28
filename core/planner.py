"""
Planner & Result Ingester for MS Rewards Bot (Phase 9.0 Polyglot).
Produces data/batch_plan.json (Contract 1) and consumes data/worker_result.json (Contract 2).
"""

from __future__ import annotations

import datetime
import hashlib
import math
import random
from pathlib import Path
from typing import Any, Optional

from PIL import Image

from core.api_tasks import run_all_api_tasks
from core.query_engine import normalize_tokens, select_diverse_batch
from core.state_db import (
    DEFAULT_DATA_DIR,
    MAX_VISUAL_SEARCH_ATTEMPTS_PER_DAY,
    ROOT_DIR,
    StateDB,
    atomic_write_json,
    safe_read_json,
)


def prepare_visual_search_image(root_dir: Path = ROOT_DIR, data_dir: Path = DEFAULT_DATA_DIR) -> Optional[str]:
    """Pick a local screenshot and save a randomized JPEG copy at data/temp_vs_upload.jpg."""
    candidate_dirs = [
        root_dir / "assets" / "visual_search_images",
        root_dir / "assets",
        root_dir / "Screenshots",
        root_dir / "screenshots",
    ]
    valid_exts = {".png", ".jpg", ".jpeg"}
    images: list[Path] = []
    for d in candidate_dirs:
        if d.exists() and d.is_dir():
            for f in d.iterdir():
                if f.is_file() and f.suffix.lower() in valid_exts and f.stat().st_size > 0:
                    images.append(f)

    if not images:
        return None

    picked = random.choice(images)
    out_path = Path(data_dir) / "temp_vs_upload.jpg"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(picked) as im:
            if im.mode != "RGB":
                im = im.convert("RGB")
            w, h = im.size
            l, t, r, b = (random.randint(1, 5) for _ in range(4))
            if w - (l + r) >= 64 and h - (t + b) >= 64:
                im = im.crop((l, t, w - r, h - b))
            w, h = im.size
            max_edge = max(w, h)
            target_edge = random.randint(520, 640)
            if max_edge > 0:
                scale = target_edge / float(max_edge)
                im = im.resize((max(1, int(round(w * scale))), max(1, int(round(h * scale)))), Image.LANCZOS)
            im.save(out_path, "JPEG", quality=random.randint(84, 94), optimize=True)
        sha = hashlib.sha256(out_path.read_bytes()).hexdigest()[:10]
        print(f"[Planner] Chuẩn hóa ảnh Visual Search: {picked.name} -> {out_path.name} (sha={sha})", flush=True)
        return str(out_path.resolve())
    except Exception as exc:
        print(f"[Planner] Lỗi chuẩn hóa ảnh Visual Search ({exc}), tạo ảnh JPEG dự phòng...", flush=True)
        Image.new("RGB", (640, 480), (42, 118, 196)).save(out_path, "JPEG", quality=88)
        return str(out_path.resolve())


async def build_batch_plan(
    account: str = "default",
    exit_on_cooldown: bool = True,
    headless: bool = True,
    root_dir: Path = ROOT_DIR,
    data_dir: Path = DEFAULT_DATA_DIR,
) -> dict[str, Any]:
    """Run Tier-2 API tasks, read SQLite WAL state, and write data/batch_plan.json atomically."""
    clean_acc = (account or "default").strip() or "default"
    date_key = datetime.date.today().isoformat()
    data_root = Path(data_dir)
    db = StateDB(data_root / "rewards.db")
    db.seed_from_legacy_json_if_empty(date_key, clean_acc, data_dir=data_root)

    api_summary = await run_all_api_tasks(db, date_key, clean_acc, data_dir=data_root, country="vn")
    prog = db.get_daily_progress(date_key, clean_acc)

    pc_cur = int(api_summary.get("pc_cur") if api_summary.get("found_pc") else prog["pc_cur"])
    pc_max = int(api_summary.get("pc_max") if api_summary.get("found_pc") else prog["pc_max"]) or 90
    mob_cur = int(api_summary.get("mobile_cur") if api_summary.get("found_mobile") else prog["mobile_cur"])
    mob_max = int(api_summary.get("mobile_max") if api_summary.get("found_mobile") else prog["mobile_max"]) or 60

    pc_needed = math.ceil(max(0, pc_max - pc_cur) / 3.0)
    mob_needed = math.ceil(max(0, mob_max - mob_cur) / 3.0)

    run_activities = not bool(prog["daily_activities_done"])
    run_visual_search = (
        not bool(prog["visual_search_done"])
        and int(prog["visual_search_attempts"]) < MAX_VISUAL_SEARCH_ATTEMPTS_PER_DAY
    )
    vs_image_path = prepare_visual_search_image(root_dir=root_dir, data_dir=data_root) if run_visual_search else None
    if run_visual_search and not vs_image_path:
        run_visual_search = False

    # Stateless Burst (ExitOnCooldown=True default): max 3 PC + 3 Mobile queries per invocation.
    # On the morning setup pass (when activities or visual_search runs), cap at <=2 PC and 0 Mobile
    # so Stage 2 completes safely under the watchdog ceiling and subsequent PT40M runs handle search.
    if run_activities or run_visual_search:
        pc_count = min(2, pc_needed)
        mob_count = 0
    else:
        pc_count = min(3, pc_needed)
        mob_count = min(3, mob_needed)

    recent_queries = db.get_recent_queries(clean_acc, limit=15)
    pc_pairs = select_diverse_batch(pc_count, recent_queries=recent_queries, jaccard_threshold=0.35)
    pc_queries = [q for _, q in pc_pairs]

    mob_pairs = select_diverse_batch(
        mob_count,
        recent_queries=recent_queries,
        jaccard_threshold=0.35,
        extra_excluded=pc_queries,
    )
    mobile_queries = [q for _, q in mob_pairs]

    has_browser_tasks = bool(run_activities or run_visual_search or pc_queries or mobile_queries)

    known_points = int(api_summary.get("balance") or prog["last_points"] or db.get_last_known_points(clean_acc) or 0)
    known_star = int(prog["bing_star_points"] or 0)

    profile_root = Path(root_dir) / "profiles" / clean_acc
    plan: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": datetime.datetime.now().isoformat(),
        "date_key": date_key,
        "account": clean_acc,
        "profile_dir": str(profile_root),
        "desktop_profile_dir": str(profile_root / "desktop"),
        "mobile_profile_dir": str(profile_root / "mobile"),
        "headless": bool(headless),
        "exit_on_cooldown": bool(exit_on_cooldown),
        "has_browser_tasks": has_browser_tasks,
        "run_activities": run_activities,
        "run_visual_search": run_visual_search,
        "visual_search_image_path": vs_image_path,
        "pc_queries": pc_queries,
        "mobile_queries": mobile_queries,
        "ctr_rate": 0.25,
        "dwell_ms_range": [3000, 5000],
        "known_points_before": known_points,
        "known_bing_star_before": known_star,
        "attempted_offers": list(prog.get("attempted_offers") or []),
        "attempted_quest_tasks": list(prog.get("attempted_quest_tasks") or []),
        "api_summary": api_summary,
    }

    plan_path = data_root / "batch_plan.json"
    atomic_write_json(plan_path, plan)

    # Clear stale worker_result.json before worker runs
    result_path = data_root / "worker_result.json"
    if has_browser_tasks and result_path.exists():
        try:
            result_path.unlink()
        except Exception:
            pass

    db.log_session(
        account=clean_acc,
        date_key=date_key,
        phase="api-and-plan",
        status="ok",
        points_before=known_points,
        points_after=known_points,
        bing_star_points=known_star,
        details=f"has_browser_tasks={has_browser_tasks}, pc={len(pc_queries)}, mob={len(mobile_queries)}, vs={run_visual_search}",
    )
    print(
        f"[Planner] Plan ready: has_browser_tasks={has_browser_tasks} | "
        f"activities={run_activities} | visual_search={run_visual_search} | "
        f"PC={len(pc_queries)} ({pc_cur}/{pc_max}) | Mobile={len(mobile_queries)} ({mob_cur}/{mob_max})",
        flush=True,
    )
    return plan


def ingest_worker_result(
    account: str = "default",
    exit_on_cooldown: bool = True,
    data_dir: Path = DEFAULT_DATA_DIR,
) -> int:
    """
    Ingest data/worker_result.json into SQLite WAL and return Contract 3 exit code:
      0: ok / completed
      20: cooldown detected under Stateless Burst
      30: worker timeout
      1: system/worker error
    """
    clean_acc = (account or "default").strip() or "default"
    date_key = datetime.date.today().isoformat()
    data_root = Path(data_dir)
    db = StateDB(data_root / "rewards.db")
    plan = safe_read_json(data_root / "batch_plan.json", default={}) or {}
    result = safe_read_json(data_root / "worker_result.json", default=None)

    if not isinstance(result, dict):
        db.log_session(
            account=clean_acc,
            date_key=date_key,
            phase="ingest-result",
            status="error",
            details="Missing or unreadable data/worker_result.json",
        )
        print("[Ingester] Lỗi: Không tìm thấy data/worker_result.json.", flush=True)
        return 1

    status = str(result.get("status") or "error").lower()
    pc_used = [str(q).strip() for q in (result.get("pc_queries_used") or []) if str(q).strip()]
    mob_used = [str(q).strip() for q in (result.get("mobile_queries_used") or []) if str(q).strip()]
    for q in pc_used:
        db.add_query_history(clean_acc, q, normalize_tokens(q), topic="pc_search")
    for q in mob_used:
        db.add_query_history(clean_acc, q, normalize_tokens(q), topic="mobile_search")

    if result.get("visual_search_attempted"):
        db.increment_visual_search_attempts(date_key, clean_acc, 1)

    prog_before = db.get_daily_progress(date_key, clean_acc)
    pts_before = int(result.get("points_before") or plan.get("known_points_before") or prog_before["last_points"] or 0)
    pts_after = int(result.get("points_after") or 0)
    if pts_after <= 0:
        pts_after = pts_before

    star_after = db.update_bing_star_monotonic(date_key, clean_acc, int(result.get("bing_star_points") or 0))

    pc_completed = int(result.get("pc_completed") or len(pc_used))
    mob_completed = int(result.get("mobile_completed") or len(mob_used))

    old_pc = int(prog_before["pc_cur"] or 0)
    old_mob = int(prog_before["mobile_cur"] or 0)
    pc_max = int(result.get("pc_max") or prog_before["pc_max"] or 90)
    if pc_max <= 0:
        pc_max = 90
    mob_max = int(result.get("mobile_max") or prog_before["mobile_max"] or 60)
    if mob_max <= 0:
        mob_max = 60

    raw_pc = int(result["pc_cur"]) if result.get("pc_cur") is not None else -1
    raw_mob = int(result["mobile_cur"]) if result.get("mobile_cur") is not None else -1

    if raw_pc >= 0:
        pc_cur = max(old_pc, raw_pc)
        db_pc_cur = pc_cur
    else:
        pc_cur = max(old_pc, min(90, old_pc + pc_completed * 3))
        db_pc_cur = pc_cur if pc_completed > 0 else -1

    if raw_mob >= 0:
        mob_cur = max(old_mob, raw_mob)
        db_mob_cur = mob_cur
    else:
        mob_cur = max(old_mob, min(60, old_mob + mob_completed * 3))
        db_mob_cur = mob_cur if mob_completed > 0 else -1

    vs_done = bool(prog_before["visual_search_done"] or result.get("visual_search_done"))
    edge_done = bool(prog_before["edge_browsing_done"] or result.get("edge_browsing_done"))
    acts_done = bool(
        prog_before["daily_activities_done"]
        or (
            bool(result.get("activities_done"))
            and bool(prog_before["checkin_done"])
            and bool(prog_before["read_to_earn_done"])
            and vs_done
        )
    )

    db.update_daily_fields(
        date_key,
        clean_acc,
        daily_activities_done=acts_done,
        visual_search_done=vs_done,
        edge_browsing_done=edge_done,
        pc_cur=db_pc_cur,
        pc_max=pc_max,
        mobile_cur=db_mob_cur,
        mobile_max=mob_max,
        last_points=pts_after,
    )
    db.append_attempted_items(
        date_key,
        clean_acc,
        offers=list(result.get("attempted_offers_added") or []),
        quest_tasks=list(result.get("attempted_quest_tasks_added") or []),
    )

    errors = list(result.get("errors") or [])

    db.log_session(
        account=clean_acc,
        date_key=date_key,
        phase="ingest-result",
        status=status,
        points_before=pts_before,
        points_after=pts_after,
        pc_completed=pc_completed,
        mobile_completed=mob_completed,
        bing_star_points=star_after,
        details="; ".join(str(e) for e in errors[:5]),
    )

    print(
        f"[Ingester] status={status} | Points: {pts_before} -> {pts_after} | "
        f"PC={pc_completed} ({pc_cur}/{pc_max}) | Mobile={mob_completed} ({mob_cur}/{mob_max}) | "
        f"BingStar={star_after} | VS_done={vs_done}",
        flush=True,
    )

    if status == "timeout":
        return 30

    searches_ran = (pc_completed + mob_completed) > 0
    not_maxed = (pc_cur < pc_max) or (mob_cur < mob_max)
    cooldown_hit = bool(result.get("cooldown_detected")) or (
        searches_ran and not_maxed and pts_after <= pts_before
    )
    if exit_on_cooldown and cooldown_hit:
        print("[Ingester] [Stateless Burst] Chạm Cooldown -> thoát sạch mã 20.", flush=True)
        return 20

    if status == "error":
        return 1
    return 0
