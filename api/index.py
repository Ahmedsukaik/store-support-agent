"""Vercel entry point for the live demo (the app itself is support/web.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from support.web import create_app  # noqa: E402

app = create_app(serve_static=False)  # Vercel serves public/ itself
