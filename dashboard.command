#!/bin/bash
# dashboard.command - macOS launcher for MS Rewards Bot Dashboard
# Usage: chmod +x dashboard.command && ./dashboard.command

cd "$(dirname "$0")"

# Activate virtual environment
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Start the dashboard
echo "Starting MS Rewards Bot Dashboard on http://127.0.0.1:8765"
echo "Press Ctrl+C to stop"
echo ""

# Open browser after a short delay
(sleep 2 && open "http://127.0.0.1:8765") &

# Run uvicorn
python -m uvicorn web.server:app --host 127.0.0.1 --port 8765