"""
FastAPI Web Dashboard for MS Rewards Bot - Phase 7.4
Real-time dashboard with WebSocket log streaming and multi-account control.
"""

import asyncio
import json
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, date
from pathlib import Path
from typing import Optional, Dict, List, Any
import sys

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Add project root to path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from core.database import (
    init_db, get_db, get_db_path, list_accounts, get_account,
    get_daily_progress, get_latest_ledger, get_ledger_history,
    get_oauth_tokens, get_recent_search_queries,
    upsert_account, update_account_status,
    insert_run_log
)
from core.onboarding import launch_mobile_oauth_onboarding

# Import runner function lazily to avoid circular imports
def get_run_full_pipeline():
    from runner import run_full_pipeline
    return run_full_pipeline

def get_run_multi_account_queue():
    from core.orchestrator import run_multi_account_queue
    return run_multi_account_queue


# =============================================================================
# PYDANTIC MODELS
# =============================================================================

class AccountOnboardRequest(BaseModel):
    account_id: str
    display_name: str = ""
    proxy_url: Optional[str] = None
    preferred_hour: Optional[int] = None
    target_reward: Optional[str] = None
    target_points: int = 0


class AccountToggleRequest(BaseModel):
    enabled: bool


class RunRequest(BaseModel):
    force_now: bool = False


# =============================================================================
# GLOBAL STATE
# =============================================================================

# Active runner lock to prevent overlapping runs
_runner_lock = asyncio.Lock()
_active_runner_task: Optional[asyncio.Task] = None

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        async with self._lock:
            for connection in self.active_connections[:]:
                try:
                    await connection.send_json(message)
                except Exception:
                    self.active_connections.remove(connection)


manager = ConnectionManager()


# =============================================================================
# DATABASE HELPERS
# =============================================================================

def get_overview_data() -> dict:
    """Get comprehensive overview data for dashboard."""
    init_db()
    
    today_str = date.today().isoformat()
    accounts = list_accounts(enabled_only=False)
    
    result_accounts = []
    total_points = 0
    total_earned_today = 0
    ip_count: Dict[str, int] = {}
    
    for acc in accounts:
        account_id = acc["account_id"]
        
        # Get daily progress
        daily = get_daily_progress(account_id, today_str) or {}
        
        # Get latest ledger
        ledger = get_latest_ledger(account_id) or {}
        
        # Get OAuth status
        oauth = get_oauth_tokens(account_id)
        
        # Get ledger history for chart
        history = get_ledger_history(account_id, limit=30)
        
        # Track IP correlation
        proxy = acc.get("proxy_url")
        if proxy:
            ip_count[proxy] = ip_count.get(proxy, 0) + 1
        
        current_points = ledger.get("end_points", 0)
        target_points = acc.get("target_points", 0)
        
        total_points += current_points
        total_earned_today += ledger.get("earned_points", 0) or 0
        
        result_accounts.append({
            "account_id": account_id,
            "display_name": acc.get("display_name") or account_id,
            "enabled": bool(acc.get("enabled", 1)),
            "proxy_url": proxy,
            "timezone": acc.get("timezone", "Asia/Ho_Chi_Minh"),
            "preferred_hour": acc.get("preferred_hour"),
            "target_reward": acc.get("target_reward"),
            "target_points": target_points,
            "status": acc.get("status", "idle"),
            "current_points": current_points,
            "earned_today": ledger.get("earned_points", 0) or 0,
            "bing_star_bonus": ledger.get("bing_star_bonus", "0 / 2,100"),
            "redeem_ready": current_points >= target_points if target_points > 0 else False,
            "daily_progress": {
                "pc_searches_done": daily.get("pc_searches_done", 0),
                "pc_searches_target": daily.get("pc_searches_target", 90),
                "mobile_searches_done": daily.get("mobile_searches_done", 0),
                "mobile_searches_target": daily.get("mobile_searches_target", 60),
                "activities_done": daily.get("activities_done", 0),
                "checkin_awarded": daily.get("checkin_awarded", 0),
                "status": daily.get("status", "pending"),
            },
            "ledger_history": [
                {
                    "date": h.get("date_str"),
                    "total_points": h.get("end_points", 0),
                    "earned_today": h.get("earned_points", 0),
                    "desktop_searches": h.get("desktop_searches", 0),
                    "mobile_searches": h.get("mobile_searches", 0),
                }
                for h in history
            ],
            "oauth_connected": oauth is not None,
        })
    
    # Check IP correlation warning
    ip_warnings = []
    for ip, count in ip_count.items():
        if count > 2:
            ip_warnings.append(f"⚠️ {count} accounts sharing egress IP: {ip}")
    
    return {
        "accounts": result_accounts,
        "summary": {
            "total_accounts": len(accounts),
            "enabled_accounts": sum(1 for a in accounts if a.get("enabled", 1)),
            "total_points": total_points,
            "total_earned_today": total_earned_today,
            "active_runner": _active_runner_task is not None and not _active_runner_task.done() if _active_runner_task else False,
        },
        "ip_warnings": ip_warnings,
        "timestamp": datetime.now().isoformat(),
    }


def check_ip_correlation() -> List[str]:
    """Check for IP correlation warnings."""
    init_db()
    accounts = list_accounts(enabled_only=True)
    ip_count: Dict[str, int] = {}
    for acc in accounts:
        proxy = acc.get("proxy_url")
        if proxy:
            ip_count[proxy] = ip_count.get(proxy, 0) + 1
    warnings = []
    for ip, count in ip_count.items():
        if count > 2:
            warnings.append(f"{count} accounts sharing egress IP: {ip}")
    return warnings


# =============================================================================
# BACKGROUND TASKS
# =============================================================================

async def run_single_account_pipeline(account_id: str, force_now: bool = False):
    """Run full pipeline for a single account in background."""
    global _active_runner_task
    
    async with _runner_lock:
        if _active_runner_task and not _active_runner_task.done():
            raise RuntimeError("Another pipeline is already running")
        
        def _run():
            # Run in thread pool since orchestrator is synchronous
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                # Update account status
                with get_db() as conn:
                    conn.execute(
                        "UPDATE accounts SET status = 'running', updated_at = datetime('now') WHERE account_id = ?",
                        (account_id,)
                    )
                
                get_run_full_pipeline()(
                    account_id=account_id,
                    suppress_auto_sleep=True
                )
                
                with get_db() as conn:
                    conn.execute(
                        "UPDATE accounts SET status = 'completed', updated_at = datetime('now') WHERE account_id = ?",
                        (account_id,)
                    )
            except Exception as e:
                with get_db() as conn:
                    conn.execute(
                        "UPDATE accounts SET status = 'failed', updated_at = datetime('now') WHERE account_id = ?",
                        (account_id,)
                    )
                insert_run_log(account_id, "ERROR", "orchestrator", str(e))
            finally:
                loop.close()
        
        _active_runner_task = asyncio.get_event_loop().run_in_executor(None, _run)
        try:
            await _active_runner_task
        finally:
            _active_runner_task = None


async def run_multi_account_queue_bg():
    """Run sequential FIFO queue for all enabled accounts."""
    global _active_runner_task
    
    async with _runner_lock:
        if _active_runner_task and not _active_runner_task.done():
            raise RuntimeError("Another pipeline is already running")
        
        accounts = list_accounts(enabled_only=True)
        
        def _run_queue():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                for acc in accounts:
                    account_id = acc["account_id"]
                    with get_db() as conn:
                        conn.execute(
                            "UPDATE accounts SET status = 'running', updated_at = datetime('now') WHERE account_id = ?",
                            (account_id,)
                        )
                    
                    get_run_full_pipeline()(
                        account_id=account_id,
                        suppress_auto_sleep=True
                    )
                    
                    with get_db() as conn:
                        conn.execute(
                            "UPDATE accounts SET status = 'completed', updated_at = datetime('now') WHERE account_id = ?",
                            (account_id,)
                        )
                    
                    # Circadian jitter between accounts
                    import random
                    time.sleep(random.randint(900, 2400))  # 15-40 min
            except Exception as e:
                insert_run_log("system", "ERROR", "orchestrator", f"Queue error: {e}")
            finally:
                loop.close()
        
        _active_runner_task = asyncio.get_event_loop().run_in_executor(None, _run_queue)
        try:
            await _active_runner_task
        finally:
            _active_runner_task = None


async def onboard_account_task(request: AccountOnboardRequest):
    """Background task for account onboarding."""
    try:
        with get_db() as conn:
            conn.execute(
                "UPDATE accounts SET status = 'onboarding', updated_at = datetime('now') WHERE account_id = ?",
                (request.account_id,)
            )
        
        # Launch visible browser for OAuth
        launch_mobile_oauth_onboarding(
            account_id=request.account_id,
            display_name=request.display_name or request.account_id,
            proxy_url=request.proxy_url,
            device_profile="Pixel 7",
        )
        
        upsert_account(
            account_id=request.account_id,
            display_name=request.display_name or request.account_id,
            proxy_url=request.proxy_url,
            preferred_hour=request.preferred_hour,
            target_reward=request.target_reward,
            target_points=request.target_points,
        )
        
        with get_db() as conn:
            conn.execute(
                "UPDATE accounts SET status = 'idle', updated_at = datetime('now') WHERE account_id = ?",
                (request.account_id,)
            )
    except Exception as e:
        with get_db() as conn:
            conn.execute(
                "UPDATE accounts SET status = 'failed', updated_at = datetime('now') WHERE account_id = ?",
                (request.account_id,)
            )
        insert_run_log(request.account_id, "ERROR", "onboarding", str(e))


async def open_redeem_browser(account_id: str):
    """Open visible browser at redeem page for manual redemption."""
    from patchright.sync_api import sync_playwright
    from core.fingerprint import inject_stealth_fingerprint
    
    account = get_account(account_id)
    if not account:
        raise ValueError(f"Account {account_id} not found")
    
    profile_dir = ROOT_DIR / account.get("profile_dir", f"profiles/{account_id}")
    desktop_profile = profile_dir / "desktop"
    
    if not desktop_profile.exists():
        raise ValueError(f"Desktop profile not found: {desktop_profile}")
    
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(desktop_profile),
            channel="msedge",
            headless=False,
            args=["--start-maximized"],
            locale="vi-VN",
            timezone_id=account.get("timezone", "Asia/Ho_Chi_Minh"),
        )
        
        # Apply fingerprint
        inject_stealth_fingerprint(context, account_id)
        
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://rewards.bing.com/redeem", wait_until="domcontentloaded")
        
        # Keep browser open - user will close manually
        while True:
            try:
                if page.is_closed():
                    break
                time.sleep(1)
            except Exception:
                break


# =============================================================================
# LOG STREAMING
# =============================================================================

async def log_streamer():
    """Periodically check for new logs and broadcast via WebSocket."""
    last_log_id = 0
    with get_db() as conn:
        row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM run_logs").fetchone()
        last_log_id = row[0] if row else 0
    
    while True:
        await asyncio.sleep(1)
        try:
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT * FROM run_logs WHERE id > ? ORDER BY id ASC LIMIT 50",
                    (last_log_id,)
                ).fetchall()
                
                for row in rows:
                    last_log_id = row["id"]
                    await manager.broadcast({
                        "type": "log",
                        "data": dict(row)
                    })
        except Exception as e:
            print(f"[Log Streamer] Error: {e}")


# =============================================================================
# FASTAPI APP
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Start log streamer
    streamer_task = asyncio.create_task(log_streamer())
    yield
    streamer_task.cancel()


app = FastAPI(
    title="MS Rewards Bot Dashboard",
    description="Real-time multi-account control center",
    version="7.4.0",
    lifespan=lifespan,
)

# Static files
app.mount("/static", StaticFiles(directory=ROOT_DIR / "web" / "static"), name="static")


# =============================================================================
# API ROUTES
# =============================================================================

@app.get("/")
async def root():
    return FileResponse(ROOT_DIR / "web" / "static" / "index.html")


@app.get("/api/overview")
async def api_overview():
    return get_overview_data()


@app.get("/api/ip-correlation")
async def api_ip_correlation():
    return {"warnings": check_ip_correlation()}


@app.post("/api/accounts/onboard")
async def api_onboard(request: AccountOnboardRequest, background_tasks: BackgroundTasks):
    # Check if account already exists
    existing = get_account(request.account_id)
    if existing:
        raise HTTPException(status_code=400, detail=f"Account {request.account_id} already exists")
    
    background_tasks.add_task(onboard_account_task, request)
    return {"status": "started", "account_id": request.account_id}


@app.post("/api/accounts/{account_id}/toggle")
async def api_toggle(account_id: str, request: AccountToggleRequest):
    update_account_status(account_id, "idle" if request.enabled else "paused")
    with get_db() as conn:
        conn.execute(
            "UPDATE accounts SET enabled = ? WHERE account_id = ?",
            (1 if request.enabled else 0, account_id)
        )
    return {"status": "ok", "enabled": request.enabled}


@app.post("/api/accounts/{account_id}/run")
async def api_run_single(account_id: str, request: RunRequest, background_tasks: BackgroundTasks):
    if not get_account(account_id):
        raise HTTPException(status_code=404, detail="Account not found")
    
    background_tasks.add_task(run_single_account_pipeline, account_id, request.force_now)
    return {"status": "started", "account_id": account_id}


@app.post("/api/queue/run-all")
async def api_run_all(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_multi_account_queue_bg)
    return {"status": "started"}


@app.post("/api/accounts/{account_id}/open-redeem")
async def api_open_redeem(account_id: str, background_tasks: BackgroundTasks):
    if not get_account(account_id):
        raise HTTPException(status_code=404, detail="Account not found")
    
    background_tasks.add_task(open_redeem_browser, account_id)
    return {"status": "started", "account_id": account_id}


@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Send initial connection message
        await websocket.send_json({"type": "connected", "message": "WebSocket connected"})
        
        # Keep connection alive
        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception:
                break
    finally:
        manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765)