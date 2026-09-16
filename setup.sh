#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# Mowazaf setup script — creates a .venv and installs dependencies with uv.
#
# Usage:
#   chmod +x setup.sh
#   ./setup.sh
#
# Works on macOS / Linux / Git-Bash(Windows).  On Windows the venv interpreter
# lives at .venv/Scripts/python.exe; adjust uv pip commands accordingly.
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

PYTHON_VERSION="${MOWAZAF_PYTHON:-3.11}"
VENV_DIR="$PROJECT_DIR/.venv"

echo "=== Mowazaf Setup ==="
echo "Project: $PROJECT_DIR"
echo "Python:  $PYTHON_VERSION"

# ── Check uv ─────────────────────────────────────────────────────────────
if ! command -v uv &>/dev/null; then
    echo "ERROR: uv is not installed. Install it first:"
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

echo "uv:      $(uv --version)"

# ── Create venv ──────────────────────────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    echo ">>> Creating virtual environment at $VENV_DIR ..."
    uv venv "$VENV_DIR" --python "$PYTHON_VERSION"
else
    echo ">>> Virtual environment already exists at $VENV_DIR"
fi

# ── Determine venv python path ────────────────────────────────────────────
if [ -f "$VENV_DIR/Scripts/python.exe" ]; then
    VENV_PYTHON="$VENV_DIR/Scripts/python.exe"   # Windows
else
    VENV_PYTHON="$VENV_DIR/bin/python"             # macOS / Linux
fi

# ── Install dependencies ─────────────────────────────────────────────────
echo ">>> Installing dependencies ..."
uv pip install --python "$VENV_PYTHON" \
    "fastapi[standard]" \
    "uvicorn[standard]" \
    "sqlalchemy>=2.0" \
    "asyncpg" \
    "psycopg2-binary" \
    "python-jose[cryptography]" \
    "python-multipart" \
    "python-dotenv" \
    "pydantic-settings" \
    "httpx"

# ── Create .env from .env.example if missing ─────────────────────────────
if [ ! -f "$PROJECT_DIR/.env" ]; then
    if [ -f "$PROJECT_DIR/.env.example" ]; then
        echo ">>> Creating .env from .env.example ..."
        cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
        echo ">>> Please edit .env and set MOWAZAF_SECRET before starting."
    fi
fi

echo ""
echo "=== Setup complete ==="
echo "Activate:  source $VENV_DIR/bin/activate  (or $VENV_DIR/Scripts/Activate.ps1 on Windows)"
echo "Run:       uv run --python $VENV_PYTHON -m uvicorn main:app --reload --port 8000"
echo ""
echo "Remember to start PostgreSQL + Redis:"
echo "  docker compose up -d"
