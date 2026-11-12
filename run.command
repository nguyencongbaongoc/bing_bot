#!/usr/bin/env bash
# MS Rewards Bot - Launcher Script (macOS/Linux)
# Auto-bootstraps .venv if user runs this directly before running setup.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

write_step() {
    echo -e "[*] $1"
}

write_success() {
    echo -e "[OK] $1" | sed "s/^/${GREEN}/" | sed "s/$/${NC}/"
}

write_error() {
    echo -e "[X] $1" | sed "s/^/${RED}/" | sed "s/$/${NC}/"
}

write_warning() {
    echo -e "[!] $1" | sed "s/^/${YELLOW}/" | sed "s/$/${NC}/"
}

main() {
    cd "$PROJECT_ROOT"

    VENV_PATH="$PROJECT_ROOT/.venv"
    VENV_PYTHON="$VENV_PATH/bin/python"
    RUNNER_PY="$PROJECT_ROOT/runner.py"

    # ==============================================================
    # 1. SELF-HEALING VENV CHECK
    # ==============================================================
    if [ -f "$VENV_PYTHON" ]; then
        # Try a silent Python command to check if venv is healthy
        if "$VENV_PYTHON" -c "exit(0)" >/dev/null 2>&1; then
            exec "$VENV_PYTHON" "$RUNNER_PY" "$@"
        fi
    fi

    # ==============================================================
    # 2. BOOTSTRAP VENV (only if missing or broken)
    # ==============================================================
    write_step "Auto-bootstrapping .venv for current machine..."

    if ! command -v python3 &> /dev/null; then
        write_error "Python 3 not found in system PATH!"
        echo "    Please install Python 3.9+ via Homebrew (brew install python) or python.org"
        exit 1
    fi

    write_step "[1/3] Creating virtual environment .venv..."
    python3 -m venv "$VENV_PATH"

    write_step "[2/3] Installing dependencies..."
    "$VENV_PATH/bin/pip" install --upgrade pip >/dev/null 2>&1
    if [ -f "$PROJECT_ROOT/requirements.txt" ]; then
        "$VENV_PATH/bin/pip" install -r "$PROJECT_ROOT/requirements.txt"
    else
        "$VENV_PATH/bin/pip" install patchright requests psutil
    fi

    write_step "[3/3] Installing Patchright Chromium..."
    "$VENV_PYTHON" -m patchright install chromium

    write_success "Environment ready! Starting bot..."
    echo ""

    # ==============================================================
    # 3. RUN BOT
    # ==============================================================
    exec "$VENV_PYTHON" "$RUNNER_PY" "$@"
}

main "$@"