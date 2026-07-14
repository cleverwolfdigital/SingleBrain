"""Google Drive + Calendar (read-only) integration via OAuth 2.0.

Per-user tokens; stdlib urllib only (no google client library). Each user connects
their own Google account; we store access + refresh tokens and read their upcoming
calendar events and recent Drive files.
"""
import os
import json
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone

from . import db

REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "https://brain.cleverwolfdigital.com/auth/google/callback")
SCOPES = ("openid email "
          "https://www.googleapis.com/auth/calendar.readonly "
          "https://www.googleapis.com/auth/drive.readonly")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def _cfg(env_key, meta_key):
    """Read a setting from the environment first, then the stored app_meta value
    (which admins can set from the dashboard so no SSH/.env editing is needed)."""
    v = os.environ.get(env_key, "").strip()
    if v:
        return v
    try:
        rows = db.query("SELECT value FROM app_meta WHERE key=?", (meta_key,))
        return (rows[0]["value"] or "").strip() if rows else ""
    except Exception:
        return ""


def client_id():
    return _cfg("GOOGLE_CLIENT_ID", "google_client_id")


def client_secret():
    return _cfg("GOOGLE_CLIENT_SECRET", "google_client_secret")


def set_config(client_id_val=None, client_secret_val=None):
    if client_id_val:
        db.execute("INSERT INTO app_meta(key,value) VALUES('google_client_id',?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (client_id_val.strip(),))
    if client_secret_val:
        db.execute("INSERT INTO app_meta(key,value) VALUES('google_client_secret',?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (client_secret_val.strip(),))


def configured():
    return bool(client_id() and client_secret())


def init_db():
    with db.get_conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS google_tokens(
                 email TEXT PRIMARY KEY,
                 access_token TEXT,
                 refresh_token TEXT,
                 expiry INTEGER,
                 scope TEXT,
                 connected_at TEXT DEFAULT (datetime('now')))"""
        )
        conn.commit()


def auth_url(state):
    params = {
        "client_id": client_id(),
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",          # get a refresh token
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def _post_token(data):
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URL, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def exchange_code(code):
    return _post_token({
        "code": code,
        "client_id": client_id(),
        "client_secret": client_secret(),
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    })


def save_tokens(email, tok):
    email = (email or "").strip().lower()
    expiry = int(time.time()) + int(tok.get("expires_in", 3600)) - 60
    db.execute(
        "INSERT INTO google_tokens(email,access_token,refresh_token,expiry,scope) VALUES(?,?,?,?,?) "
        "ON CONFLICT(email) DO UPDATE SET access_token=excluded.access_token, "
        "refresh_token=COALESCE(excluded.refresh_token, google_tokens.refresh_token), "
        "expiry=excluded.expiry, scope=excluded.scope",
        (email, tok.get("access_token"), tok.get("refresh_token"), expiry, tok.get("scope")),
    )


def get_access_token(email):
    email = (email or "").strip().lower()
    rows = db.query("SELECT * FROM google_tokens WHERE email=?", (email,))
    if not rows:
        return None
    t = rows[0]
    if int(t.get("expiry") or 0) > int(time.time()):
        return t["access_token"]
    if not t.get("refresh_token"):
        return None
    tok = _post_token({
        "client_id": client_id(),
        "client_secret": client_secret(),
        "refresh_token": t["refresh_token"],
        "grant_type": "refresh_token",
    })
    save_tokens(email, tok)
    return tok.get("access_token")


def _api_get(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def calendar_events(email, max_results=8):
    token = get_access_token(email)
    if not token:
        return None
    now = datetime.now(timezone.utc).isoformat()
    url = "https://www.googleapis.com/calendar/v3/calendars/primary/events?" + urllib.parse.urlencode({
        "timeMin": now, "maxResults": max_results, "singleEvents": "true", "orderBy": "startTime",
    })
    data = _api_get(url, token)
    out = []
    for e in data.get("items", []):
        start = e.get("start", {})
        out.append({
            "summary": e.get("summary", "(no title)"),
            "start": start.get("dateTime") or start.get("date"),
            "all_day": "date" in start and "dateTime" not in start,
            "link": e.get("htmlLink"),
        })
    return out


def drive_files(email, max_results=10):
    token = get_access_token(email)
    if not token:
        return None
    url = "https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode({
        "pageSize": max_results, "orderBy": "modifiedTime desc",
        "fields": "files(id,name,mimeType,modifiedTime,webViewLink)",
        "q": "trashed=false",
    })
    data = _api_get(url, token)
    return [{
        "name": f.get("name"), "link": f.get("webViewLink"),
        "modified": f.get("modifiedTime"), "mime": f.get("mimeType"),
    } for f in data.get("files", [])]


def status(email):
    email = (email or "").strip().lower()
    rows = db.query("SELECT email, connected_at FROM google_tokens WHERE email=?", (email,))
    return {
        "configured": configured(),
        "connected": bool(rows),
        "connected_at": rows[0]["connected_at"] if rows else None,
    }


def disconnect(email):
    db.execute("DELETE FROM google_tokens WHERE email=?", ((email or "").strip().lower(),))
