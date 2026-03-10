"""Authentication helpers using only Python stdlib (no PyJWT needed)."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from database import db

SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32))


# ─────────────────────────────────────────
# Password hashing
# ─────────────────────────────────────────

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return f"{salt}:{h.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        salt, stored_hash = password_hash.split(":", 1)
        h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
        return hmac.compare_digest(h.hex(), stored_hash)
    except Exception:
        return False


# ─────────────────────────────────────────
# Simple HMAC-based session tokens
# (lightweight JWT-like, stdlib only)
# ─────────────────────────────────────────

def _b64_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64_decode(s: str) -> bytes:
    pad = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * (pad % 4))


def create_session_token(user_id: str) -> str:
    expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    payload = json.dumps({"user_id": user_id, "exp": expires_at}).encode()
    payload_b64 = _b64_encode(payload)
    sig = hmac.new(SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def decode_token(token: str) -> dict | None:
    try:
        payload_b64, sig = token.rsplit(".", 1)
        expected_sig = hmac.new(SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        data = json.loads(_b64_decode(payload_b64))
        exp = datetime.fromisoformat(data["exp"])
        if exp < datetime.now(timezone.utc):
            return None
        return data
    except Exception:
        return None


def get_user_from_token(token: str) -> dict | None:
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None

    with db() as conn:
        user = conn.execute(
            "SELECT id, name, email FROM users WHERE id = ?",
            (payload["user_id"],),
        ).fetchone()

    return dict(user) if user else None


# ─────────────────────────────────────────
# User management
# ─────────────────────────────────────────

def register_user(name: str, email: str, password: str) -> dict:
    if not name or not email:
        raise ValueError("Name and email are required")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")

    with db() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE email = ?", (email.lower(),)
        ).fetchone()
        if existing:
            raise ValueError("An account with this email already exists")

        user_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO users (id, name, email, password_hash) VALUES (?, ?, ?, ?)",
            (user_id, name.strip(), email.lower(), hash_password(password)),
        )
        return {"id": user_id, "name": name.strip(), "email": email.lower()}


def login_user(email: str, password: str) -> dict | None:
    with db() as conn:
        user = conn.execute(
            "SELECT id, name, email, password_hash FROM users WHERE email = ?",
            (email.lower(),),
        ).fetchone()

    if not user or not verify_password(password, user["password_hash"]):
        return None

    return {"id": user["id"], "name": user["name"], "email": user["email"]}
