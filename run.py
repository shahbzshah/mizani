"""Mizani entry point. Run: .venv/bin/python run.py (serves on :5000)."""
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
