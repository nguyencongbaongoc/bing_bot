#!/usr/bin/env bash
# MS Rewards Bot - Automated Setup Script (macOS/Linux)
# This script performs a complete one-time setup for the MS Rewards Bot:
# 1. Verifies Python installation
# 2. Creates/updates .venv virtual environment
# 3. Installs dependencies from requirements.txt
# 4. Installs Patchright Chromium browser
# 5. Creates required directories (logs, profiles)
# 6. Guides user through one-time login (setup_login.py)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

write_header() {
    echo -e "\n==================================================================="
    echo "  $1"
    echo "==================================================================="
}

write_step() {
    echo -e "\n[*] $1"
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
    write_header "MS REWARDS BOT - AUTOMATED SETUP (macOS/Linux)"

    # 1. Check Python
    write_step "Checking Python installation..."
    if ! command -v python3 &> /dev/null; then
        write_error "Python 3 not found in system PATH!"
        echo "    Please install Python 3.9+ via Homebrew (brew install python) or python.org"
        exit 1
    fi
    PYTHON_VERSION=$(python3 --version)
    write_success "Found: $PYTHON_VERSION"

    # 2. Create/Update Virtual Environment
    VENV_PATH="$PROJECT_ROOT/.venv"
    if [ ! -d "$VENV_PATH" ]; then
        write_step "Creating virtual environment (.venv)..."
        python3 -m venv "$VENV_PATH"
        write_success "Virtual environment created"
    else
        write_success "Virtual environment already exists"
    fi

    # 3. Resolve venv python paths
    VENV_PYTHON="$VENV_PATH/bin/python"
    VENV_PIP="$VENV_PATH/bin/pip"
    RUNNER_PY="$PROJECT_ROOT/runner.py"
    SETUP_LOGIN_PY="$PROJECT_ROOT/setup_login.py"

    if [ ! -f "$VENV_PYTHON" ]; then
        write_error "Virtual environment python not found at: $VENV_PYTHON"
        exit 1
    fi

    # 4. Upgrade pip and install requirements
    write_step "Upgrading pip..."
    "$VENV_PYTHON" -m pip install --upgrade pip
    write_success "pip upgraded"

    write_step "Installing dependencies from requirements.txt..."
    "$VENV_PIP" install -r "$PROJECT_ROOT/requirements.txt"
    write_success "Dependencies installed"

    # 5. Install Patchright Chromium driver
    write_step "Installing Patchright Chromium driver..."
    "$VENV_PYTHON" -m patchright install chromium
    write_success "Patchright Chromium ready"

    # 6. Create directories
    write_step "Creating required directories..."
    for dir in "logs" "profiles/desktop" "profiles/mobile"; do
        DIR_PATH="$PROJECT_ROOT/$dir"
        if [ ! -d "$DIR_PATH" ]; then
            mkdir -p "$DIR_PATH"
            write_success "Created: $dir"
        fi
    done

    # 7. One-Time Login
    write_header "ONE-TIME LOGIN REQUIRED"
    echo "Browser will launch for you to sign in to Microsoft Rewards."
    echo "- Sign in to your Microsoft account"
    echo "- IMPORTANT: Check 'Keep me signed in' / 'Giữ đăng nhập'"
    echo "- Close browser window (X button) when done"
    echo "This will run for both Desktop and Mobile profiles."
    echo ""
    read -p "Press Enter to continue..."

    write_step "Running setup_login.py..."
    export PYTHONUTF8=1
    "$VENV_PYTHON" "$SETUP_LOGIN_PY"
    write_success "Login setup complete"

    write_header "SETUP COMPLETE!"
    write_success "System is ready. Usage:"
    echo "  - Run bot:         ./run.command"
    echo "  - Debug with UI:   .venv/bin/python runner.py --debug"
    echo "  - View points:     cat logs/history.json"
    echo ""
}

main "$@"