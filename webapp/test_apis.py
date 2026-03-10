#!/usr/bin/env python3
"""
GlowUp AI — API Connection Test Script

Run this script to verify your API keys work before starting the server.

Usage:
    python3 test_apis.py
"""

import os
import sys

# Load .env if it exists
def _load_dotenv():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

_load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
FAL_KEY = os.getenv("FAL_KEY", "")

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
WARN = "\033[93m⚠\033[0m"


def test_anthropic():
    """Test Anthropic Claude API connection."""
    print("\n─── Anthropic Claude API ───────────────────")
    if not ANTHROPIC_API_KEY:
        print(f"  {FAIL} ANTHROPIC_API_KEY is not set in .env")
        print("     Get your key at: https://console.anthropic.com/")
        return False

    print(f"  Key found: sk-ant-...{ANTHROPIC_API_KEY[-6:]}")

    try:
        import requests
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 50,
                "messages": [{"role": "user", "content": "Reply with exactly: API_OK"}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        text = resp.json()["content"][0]["text"].strip()
        print(f"  {PASS} API call succeeded — response: {text!r}")
        return True
    except Exception as e:
        print(f"  {FAIL} API call failed: {e}")
        return False


def test_fal():
    """Test FAL.ai API connection."""
    print("\n─── FAL.ai API ─────────────────────────────")
    if not FAL_KEY:
        print(f"  {WARN} FAL_KEY is not set in .env")
        print("     Get your key at: https://fal.ai/dashboard")
        print("     Photo generation will be disabled without this key.")
        return False

    print(f"  Key found: ...{FAL_KEY[-6:]}")

    try:
        import requests
        # Test with a lightweight model list or status check
        resp = requests.get(
            "https://fal.run/fal-ai/flux-lora",
            headers={"Authorization": f"Key {FAL_KEY}"},
            timeout=10,
        )
        # 405 Method Not Allowed means the endpoint exists and key is valid
        if resp.status_code in (200, 405, 422):
            print(f"  {PASS} FAL.ai key is valid (status {resp.status_code})")
            return True
        elif resp.status_code == 401:
            print(f"  {FAIL} FAL.ai key is invalid or expired (401 Unauthorized)")
            return False
        else:
            print(f"  {WARN} Unexpected status {resp.status_code} — key may be valid")
            return True
    except Exception as e:
        print(f"  {FAIL} Could not reach FAL.ai: {e}")
        return False


def test_server_imports():
    """Test that all server modules import correctly."""
    print("\n─── Server Module Imports ──────────────────")
    modules = [
        ("jinja2", "Jinja2 templating"),
        ("requests", "HTTP client"),
    ]
    all_ok = True
    for mod, label in modules:
        try:
            __import__(mod)
            print(f"  {PASS} {label} ({mod})")
        except ImportError:
            print(f"  {FAIL} {label} ({mod}) — run: pip install -r requirements.txt")
            all_ok = False

    # Test local modules
    sys.path.insert(0, os.path.dirname(__file__))
    for mod, label in [("database", "Database"), ("auth", "Auth"), ("ai_client", "AI Client")]:
        try:
            __import__(mod)
            print(f"  {PASS} {label} ({mod}.py)")
        except Exception as e:
            print(f"  {FAIL} {label}: {e}")
            all_ok = False

    return all_ok


def test_database():
    """Test SQLite database initialization."""
    print("\n─── Database ───────────────────────────────")
    sys.path.insert(0, os.path.dirname(__file__))
    try:
        from database import init_db, db
        init_db()
        with db() as conn:
            conn.execute("SELECT 1")
        print(f"  {PASS} SQLite database initialized successfully")
        return True
    except Exception as e:
        print(f"  {FAIL} Database error: {e}")
        return False


if __name__ == "__main__":
    print("=" * 50)
    print("  GlowUp AI — API Connection Test")
    print("=" * 50)

    results = {
        "imports": test_server_imports(),
        "database": test_database(),
        "anthropic": test_anthropic(),
        "fal": test_fal(),
    }

    print("\n─── Summary ────────────────────────────────")
    if results["imports"] and results["database"]:
        print(f"  {PASS} Server is ready to run")
    else:
        print(f"  {FAIL} Fix import/database errors before starting")

    if results["anthropic"]:
        print(f"  {PASS} Bio analysis, photo ranking, and messaging will work")
    else:
        print(f"  {WARN} Text features (bio, ranking, messaging) need ANTHROPIC_API_KEY")

    if results["fal"]:
        print(f"  {PASS} AI photo generation will work")
    else:
        print(f"  {WARN} Photo generation needs FAL_KEY")

    print()
    if results["imports"] and results["database"] and results["anthropic"]:
        print("  Start the server with:  python3 server.py")
        print("  Or using:               bash start.sh")
    else:
        print("  Fix the issues above, then run:  bash start.sh")
    print()
