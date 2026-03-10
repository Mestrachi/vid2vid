#!/usr/bin/env python3
"""
GlowUp AI — Dating Profile Web App
A simple Python HTTP server using Jinja2 templates and direct API calls.

Usage:
    cp .env.example .env
    # Edit .env and add your API keys
    python server.py
"""

import json
import mimetypes
import os
import re
import sys
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Load .env file manually if it exists (no dotenv dependency needed)
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

from jinja2 import Environment, FileSystemLoader, select_autoescape

import ai_client
import auth
from database import db, init_db

# ─────────────────────────────────────────
# Setup
# ─────────────────────────────────────────
BASE_DIR = Path(__file__).parent
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = BASE_DIR / "uploads"

UPLOAD_DIR.mkdir(exist_ok=True)

jinja = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
)

PORT = int(os.getenv("PORT", "8000"))


# ─────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────
def render(template_name: str, **ctx) -> bytes:
    tmpl = jinja.get_template(template_name)
    return tmpl.render(**ctx).encode("utf-8")


def json_response(handler, data: dict, status: int = 200):
    body = json.dumps(data).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def html_response(handler, body: bytes, status: int = 200, extra_headers: list = None):
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    if extra_headers:
        for k, v in extra_headers:
            handler.send_header(k, v)
    handler.end_headers()
    handler.wfile.write(body)


def redirect(handler, location: str, set_cookie: str = None):
    handler.send_response(302)
    handler.send_header("Location", location)
    if set_cookie:
        handler.send_header("Set-Cookie", set_cookie)
    handler.end_headers()


def get_cookie(handler, name: str) -> str | None:
    cookie_header = handler.headers.get("Cookie", "")
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith(f"{name}="):
            return part[len(name) + 1:]
    return None


def get_current_user(handler) -> dict | None:
    token = get_cookie(handler, "session")
    if not token:
        return None
    return auth.get_user_from_token(token)


def require_auth(handler) -> dict | None:
    user = get_current_user(handler)
    if not user:
        redirect(handler, "/login")
        return None
    return user


def read_json_body(handler) -> dict:
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return {}
    return json.loads(handler.rfile.read(length))


def read_form_body(handler) -> dict:
    length = int(handler.headers.get("Content-Length", 0))
    raw = handler.rfile.read(length).decode("utf-8")
    parsed = parse_qs(raw)
    return {k: v[0] for k, v in parsed.items()}


# ─── Multipart form parser (replaces deprecated cgi module) ──────────
class MultipartFile:
    def __init__(self, name: str, filename: str, content_type: str, data: bytes):
        self.name = name
        self.filename = filename
        self.type = content_type
        self._data = data

    def read(self) -> bytes:
        return self._data

    @property
    def size(self) -> int:
        return len(self._data)


def parse_multipart(handler) -> dict[str, list]:
    """
    Parse multipart/form-data body.
    Returns dict mapping field_name -> list of values (str or MultipartFile).
    """
    content_type = handler.headers.get("Content-Type", "")
    length = int(handler.headers.get("Content-Length", 0))
    raw = handler.rfile.read(length)

    # Extract boundary
    boundary = None
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            boundary = part[9:].strip().encode()
            break

    if not boundary:
        return {}

    fields: dict[str, list] = {}
    delimiter = b"--" + boundary
    parts = raw.split(delimiter)

    for part in parts[1:]:  # skip preamble
        if part in (b"--\r\n", b"--", b"--\r\n--"):
            continue
        if part.startswith(b"--"):
            continue

        # Split headers from body
        if b"\r\n\r\n" in part:
            header_block, body = part.split(b"\r\n\r\n", 1)
        else:
            continue

        # Remove trailing CRLF from body
        if body.endswith(b"\r\n"):
            body = body[:-2]

        # Parse headers
        headers = {}
        for line in header_block.split(b"\r\n"):
            line = line.strip()
            if b":" in line:
                k, v = line.split(b":", 1)
                headers[k.strip().lower().decode()] = v.strip().decode()

        # Parse Content-Disposition
        disposition = headers.get("content-disposition", "")
        field_name = None
        filename = None
        for token in disposition.split(";"):
            token = token.strip()
            if token.startswith('name="'):
                field_name = token[6:-1]
            elif token.startswith('filename="'):
                filename = token[10:-1]

        if not field_name:
            continue

        content_type_field = headers.get("content-type", "application/octet-stream")

        if filename is not None:
            value = MultipartFile(field_name, filename, content_type_field, body)
        else:
            value = body.decode("utf-8", errors="replace")

        fields.setdefault(field_name, []).append(value)

    return fields


# ─────────────────────────────────────────
# Request Handler
# ─────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        print(f"  {self.address_string()} - {format % args}")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        try:
            if path.startswith("/static/"):
                self._serve_static(path)
            elif path == "/":
                self._landing()
            elif path == "/login":
                self._login_page()
            elif path == "/register":
                self._register_page()
            elif path == "/logout":
                self._logout()
            elif path == "/dashboard":
                self._dashboard()
            elif path == "/dashboard/profile":
                self._profile_page()
            elif path == "/dashboard/ranking":
                self._ranking_page()
            elif path == "/dashboard/messaging":
                self._messaging_page()
            elif path == "/dashboard/generate":
                self._generate_page()
            elif re.match(r"^/api/generate/status/[^/]+$", path):
                session_id = path.split("/")[-1]
                self._api_generate_status(session_id)
            else:
                self._not_found()
        except Exception as e:
            print(f"ERROR in GET {path}: {e}")
            traceback.print_exc()
            self._server_error(str(e))

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        try:
            if path == "/login":
                self._login_post()
            elif path == "/register":
                self._register_post()
            elif path == "/api/profile/analyze":
                self._api_analyze_bio()
            elif path == "/api/ranking":
                self._api_ranking()
            elif path == "/api/messaging":
                self._api_messaging()
            elif path == "/api/messaging/first":
                self._api_first_message()
            elif path == "/api/generate/train":
                self._api_generate_train()
            else:
                self._not_found()
        except Exception as e:
            print(f"ERROR in POST {path}: {e}")
            traceback.print_exc()
            json_response(self, {"error": str(e)}, 500)

    # ─── Static files ───────────────────────────
    def _serve_static(self, path: str):
        file_path = STATIC_DIR / path[len("/static/"):]
        if not file_path.exists() or not file_path.is_file():
            self._not_found()
            return
        mime, _ = mimetypes.guess_type(str(file_path))
        content = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    # ─── Pages ──────────────────────────────────
    def _landing(self):
        user = get_current_user(self)
        html_response(self, render("landing.html", user=user))

    def _login_page(self):
        user = get_current_user(self)
        if user:
            redirect(self, "/dashboard")
            return
        html_response(self, render("login.html", error=None))

    def _register_page(self):
        user = get_current_user(self)
        if user:
            redirect(self, "/dashboard")
            return
        html_response(self, render("register.html", error=None))

    def _logout(self):
        redirect(self, "/", set_cookie="session=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax")

    def _dashboard(self):
        user = require_auth(self)
        if not user:
            return
        html_response(self, render("dashboard.html", user=user, page="home"))

    def _profile_page(self):
        user = require_auth(self)
        if not user:
            return
        html_response(self, render("profile.html", user=user, page="profile"))

    def _ranking_page(self):
        user = require_auth(self)
        if not user:
            return
        html_response(self, render("ranking.html", user=user, page="ranking"))

    def _messaging_page(self):
        user = require_auth(self)
        if not user:
            return
        html_response(self, render("messaging.html", user=user, page="messaging"))

    def _generate_page(self):
        user = require_auth(self)
        if not user:
            return
        html_response(self, render("generate.html", user=user, page="generate"))

    # ─── Auth POSTs ─────────────────────────────
    def _login_post(self):
        form = read_form_body(self)
        email = form.get("email", "")
        password = form.get("password", "")

        user = auth.login_user(email, password)
        if not user:
            html_response(self, render("login.html", error="Invalid email or password"), 200)
            return

        token = auth.create_session_token(user["id"])
        redirect(
            self,
            "/dashboard",
            set_cookie=f"session={token}; Max-Age={30 * 86400}; Path=/; HttpOnly; SameSite=Lax",
        )

    def _register_post(self):
        form = read_form_body(self)
        name = form.get("name", "").strip()
        email = form.get("email", "").strip()
        password = form.get("password", "")

        try:
            user = auth.register_user(name, email, password)
        except ValueError as e:
            html_response(self, render("register.html", error=str(e)), 200)
            return

        token = auth.create_session_token(user["id"])
        redirect(
            self,
            "/dashboard",
            set_cookie=f"session={token}; Max-Age={30 * 86400}; Path=/; HttpOnly; SameSite=Lax",
        )

    # ─── API Endpoints ───────────────────────────
    def _api_analyze_bio(self):
        user = get_current_user(self)
        if not user:
            json_response(self, {"error": "Unauthorized"}, 401)
            return

        data = read_json_body(self)
        bio = data.get("bio", "").strip()

        if not bio:
            json_response(self, {"error": "Bio is required"}, 400)
            return
        if len(bio) < 20:
            json_response(self, {"error": "Bio is too short"}, 400)
            return
        if len(bio) > 2000:
            json_response(self, {"error": "Bio is too long (max 2000 chars)"}, 400)
            return

        analysis = ai_client.analyze_bio(bio)

        with db() as conn:
            conn.execute(
                "INSERT INTO bio_analyses (id, user_id, original_bio, analysis) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), user["id"], bio, json.dumps(analysis)),
            )

        json_response(self, {"analysis": analysis})

    def _api_ranking(self):
        user = get_current_user(self)
        if not user:
            json_response(self, {"error": "Unauthorized"}, 401)
            return

        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            json_response(self, {"error": "Expected multipart/form-data"}, 400)
            return

        form = parse_multipart(self)
        photos = form.get("photos", [])
        if len(photos) < 2:
            json_response(self, {"error": "Upload at least 2 photos"}, 400)
            return

        descriptions = []
        for i, photo in enumerate(photos):
            if isinstance(photo, MultipartFile):
                size_kb = photo.size // 1024
                descriptions.append(f'"{photo.filename}", {size_kb}KB')
            else:
                descriptions.append(f'"photo_{i+1}", unknown size')

        result = ai_client.rank_photos(descriptions)

        with db() as conn:
            conn.execute(
                "INSERT INTO ranking_results (id, user_id, photo_count, result) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), user["id"], len(photos), json.dumps(result)),
            )

        json_response(self, {"result": result})

    def _api_messaging(self):
        user = get_current_user(self)
        if not user:
            json_response(self, {"error": "Unauthorized"}, 401)
            return

        data = read_json_body(self)
        conversation = data.get("conversation", "").strip()
        context = data.get("context", "")

        if not conversation:
            json_response(self, {"error": "Conversation is required"}, 400)
            return

        result = ai_client.generate_replies(conversation, context)

        with db() as conn:
            conn.execute(
                "INSERT INTO messaging_sessions (id, user_id, mode, conversation, result) VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), user["id"], "reply", conversation, json.dumps(result)),
            )

        json_response(self, {"result": result})

    def _api_first_message(self):
        user = get_current_user(self)
        if not user:
            json_response(self, {"error": "Unauthorized"}, 401)
            return

        data = read_json_body(self)
        profile = data.get("conversation", "").strip()

        if not profile:
            json_response(self, {"error": "Profile description is required"}, 400)
            return

        result = ai_client.generate_first_message(profile)
        json_response(self, {"result": result})

    def _api_generate_train(self):
        user = get_current_user(self)
        if not user:
            json_response(self, {"error": "Unauthorized"}, 401)
            return

        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            json_response(self, {"error": "Expected multipart/form-data"}, 400)
            return

        form = parse_multipart(self)
        photos = [p for p in form.get("photos", []) if isinstance(p, MultipartFile)]
        styles = [s for s in form.get("styles", []) if isinstance(s, str)]

        if len(photos) < 5:
            json_response(self, {"error": "Upload at least 5 photos"}, 400)
            return

        # Save photos locally
        user_upload_dir = UPLOAD_DIR / user["id"]
        user_upload_dir.mkdir(exist_ok=True)

        # Collect (filename, bytes) for ZIP packaging
        image_files = []

        for photo in photos:
            safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", photo.filename or "photo.jpg")
            dest = user_upload_dir / f"{uuid.uuid4().hex}_{safe_name}"
            file_bytes = photo.read()
            dest.write_bytes(file_bytes)
            image_files.append((safe_name, file_bytes))

        # Create DB session
        session_id = str(uuid.uuid4())
        with db() as conn:
            conn.execute(
                """INSERT INTO photo_sessions
                   (id, user_id, status, photo_styles, uploaded_count)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, user["id"], "training", json.dumps(styles), len(photos)),
            )

        # Start FAL training (async) — uploads a ZIP of all images, then submits
        request_id = None
        if image_files and os.getenv("FAL_KEY"):
            try:
                request_id = ai_client.fal_submit_training(image_files)
                with db() as conn:
                    conn.execute(
                        "UPDATE photo_sessions SET fal_request_id = ? WHERE id = ?",
                        (request_id, session_id),
                    )
            except Exception as e:
                print(f"FAL training submit error: {e}")
                with db() as conn:
                    conn.execute(
                        "UPDATE photo_sessions SET status = 'failed', error_message = ? WHERE id = ?",
                        (str(e), session_id),
                    )

        json_response(self, {
            "sessionId": session_id,
            "requestId": request_id,
            "status": "training",
        })

    def _api_generate_status(self, session_id: str):
        user = get_current_user(self)
        if not user:
            json_response(self, {"error": "Unauthorized"}, 401)
            return

        with db() as conn:
            row = conn.execute(
                "SELECT * FROM photo_sessions WHERE id = ? AND user_id = ?",
                (session_id, user["id"]),
            ).fetchone()

        if not row:
            json_response(self, {"error": "Session not found"}, 404)
            return

        session = dict(row)

        if session["status"] == "completed":
            photos = json.loads(session["generated_photos"] or "[]")
            json_response(self, {"status": "completed", "photos": photos})
            return

        if session["status"] == "failed":
            json_response(self, {
                "status": "failed",
                "error": session["error_message"] or "Generation failed",
            })
            return

        if not session["fal_request_id"]:
            json_response(self, {"status": session["status"]})
            return

        # Check FAL status
        try:
            fal_status = ai_client.fal_check_status(session["fal_request_id"])
            status_str = fal_status.get("status", "IN_PROGRESS")

            if status_str == "COMPLETED":
                # Get result and generate photos
                result = ai_client.fal_get_result(session["fal_request_id"])
                lora_url = result.get("diffusers_lora_file", {}).get("url")

                if not lora_url:
                    with db() as conn:
                        conn.execute(
                            "UPDATE photo_sessions SET status='failed', error_message='No LoRA weights' WHERE id=?",
                            (session_id,),
                        )
                    json_response(self, {"status": "failed", "error": "Training failed"})
                    return

                with db() as conn:
                    conn.execute(
                        "UPDATE photo_sessions SET status='generating', lora_url=? WHERE id=?",
                        (lora_url, session_id),
                    )

                # Generate photos for each style
                styles = json.loads(session["photo_styles"] or '["professional","casual"]')
                generated = []

                for style in styles:
                    try:
                        images = ai_client.fal_generate_photo(lora_url, style)
                        for img in images:
                            generated.append({"url": img["url"], "style": style})
                    except Exception as e:
                        print(f"Generation error for {style}: {e}")

                with db() as conn:
                    conn.execute(
                        "UPDATE photo_sessions SET status='completed', generated_photos=? WHERE id=?",
                        (json.dumps(generated), session_id),
                    )

                json_response(self, {"status": "completed", "photos": generated})

            elif status_str == "FAILED":
                with db() as conn:
                    conn.execute(
                        "UPDATE photo_sessions SET status='failed', error_message='FAL training failed' WHERE id=?",
                        (session_id,),
                    )
                json_response(self, {"status": "failed", "error": "Training failed"})
            else:
                json_response(self, {"status": session["status"], "falStatus": status_str})

        except Exception as e:
            print(f"Status check error: {e}")
            json_response(self, {"status": session["status"]})

    def _not_found(self):
        body = b"<h1>404 Not Found</h1>"
        self.send_response(404)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _server_error(self, msg: str):
        body = f"<h1>500 Server Error</h1><pre>{msg}</pre>".encode()
        self.send_response(500)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ─────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    print(f"\n🌟 GlowUp AI starting at http://localhost:{PORT}\n")
    print("  Make sure to set ANTHROPIC_API_KEY and FAL_KEY in your environment")
    print("  Press Ctrl+C to stop\n")
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
