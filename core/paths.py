"""
Centralized path resolver for the MS Rewards Bot.
Single source of truth for all filesystem locations.
Works cross-platform (Windows, macOS, Linux) with zero hardcoded paths.
"""

from pathlib import Path
import os
import sys


# Always resolves to the repository root regardless of current working directory (CWD)
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = PROJECT_ROOT / "data"
PROFILES_DIR: Path = PROJECT_ROOT / "profiles"
LOGS_DIR: Path = PROJECT_ROOT / "logs"
WEB_DIR: Path = PROJECT_ROOT / "web"
DB_PATH: Path = DATA_DIR / "rewards_hub.db"

# Profile subdirectories
DESKTOP_PROFILE: Path = PROFILES_DIR / "desktop"
MOBILE_PROFILE: Path = PROFILES_DIR / "mobile"

# State files
DAILY_STATE_FILE: Path = DATA_DIR / "daily_state.json"
POINTS_LEDGER_FILE: Path = DATA_DIR / "points_ledger.json"
SEARCH_HISTORY_FILE: Path = DATA_DIR / "searched_history.json"
LEGACY_HISTORY_FILE: Path = DATA_DIR / "used_keywords.json"
MOBILE_OAUTH_FILE: Path = DATA_DIR / "mobile_oauth.json"
DEBUG_DAPI_FILE: Path = DATA_DIR / "debug_dapi_me.json"
PROMO_LINKS_FILE: Path = DATA_DIR / "promo_links.json"

# Log files
BOT_LOG_FILE: Path = LOGS_DIR / "bot.log"
HISTORY_JSON: Path = LOGS_DIR / "history.json"


def ensure_runtime_dirs() -> None:
    """Create required runtime directories if missing on a fresh machine."""
    for d in (DATA_DIR, PROFILES_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def normalize_profile_dir(raw_path: str | None, account_id: str) -> Path:
    """
    Convert any stored profile path (relative or legacy machine-specific absolute path)
    into a valid Path inside the current machine's PROFILES_DIR.
    """
    if not raw_path:
        return (PROFILES_DIR / account_id).resolve()

    p = Path(raw_path)
    # If it is already a clean relative path like 'profiles/acc_primary'
    if not p.is_absolute() and not raw_path.startswith(("C:", "c:", "D:", "d:", "/", "\\")):
        return (PROJECT_ROOT / p).resolve()

    # If it's an absolute path that actually exists on THIS machine, use it
    if p.exists():
        return p.resolve()

    # Otherwise it came from another machine -> remap folder name into local PROFILES_DIR
    folder_name = Path(raw_path.replace("\\", "/")).name or account_id
    return (PROFILES_DIR / folder_name).resolve()


def to_portable_rel_path(path: Path | str) -> str:
    """Store paths in SQLite/config as portable POSIX relative strings (e.g. 'profiles/acc_primary')."""
    p = Path(path).resolve()
    try:
        return p.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def get_edge_executable_path() -> str | None:
    """
    Dynamic cross-platform Microsoft Edge discovery.
    Returns path to Edge executable or None if not found (Patchright will use bundled Chromium).
    """
    import shutil
    import platform
    
    system = platform.system().lower()
    
    if system == "windows":
        # 1. Check PATH first (msedge)
        edge_path = shutil.which("msedge")
        if edge_path:
            return edge_path
        
        # 2. Check common installation directories
        program_files = [
            os.environ.get("PROGRAMFILES", ""),
            os.environ.get("PROGRAMFILES(X86)", ""),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        
        for base in program_files:
            if not base:
                continue
            candidates = [
                Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                Path(base) / "Microsoft" / "Edge Beta" / "Application" / "msedge.exe",
                Path(base) / "Microsoft" / "Edge Dev" / "Application" / "msedge.exe",
                Path(base) / "Microsoft" / "Edge SxS" / "Application" / "msedge.exe",
            ]
            for candidate in candidates:
                if candidate.exists():
                    return str(candidate)
    
    elif system == "darwin":  # macOS
        candidates = [
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            Path.home() / "Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        # Check PATH
        edge_path = shutil.which("microsoft-edge")
        if edge_path:
            return edge_path
    
    else:  # Linux
        for cmd in ["microsoft-edge", "microsoft-edge-stable", "microsoft-edge-beta", "microsoft-edge-dev"]:
            edge_path = shutil.which(cmd)
            if edge_path:
                return edge_path
    
    return None


def get_python_executable() -> str:
    """
    Get the current Python executable path.
    Always use sys.executable for subprocess calls.
    """
    return sys.executable