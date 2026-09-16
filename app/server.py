"""
Run the Mowazaf server.

Usage:
    uv run python -m app.server
    uv run python -m app.server --reload
"""
from __future__ import annotations

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )