"""Google Drive + Calendar integration via OAuth 2.0.

Per-user tokens; stdlib urllib only (no google client library). Each user connects
their own Google account; we store access + refresh tokens, read their upcoming
calendar events, and read/upload/share their Drive files.

Calendar is read-only; Drive is full-access (`drive` scope) so the dashboard can
upload files and grant share permissions. Changing the scope means already-connected
users must reconnect once to re-consent to the wider permission.
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
          "https://www.googleapis.com/auth/calendar "
          "https://www.googleapis.com/auth/drive")

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


def _api_post(url, token, data, content_type):
    req = urllib.request.Request(
        url, data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
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
        "id": f.get("id"),
        "name": f.get("name"), "link": f.get("webViewLink"),
        "modified": f.get("modifiedTime"), "mime": f.get("mimeType"),
    } for f in data.get("files", [])]


def upload_file(email, filename, content, mimetype=None):
    """Upload raw bytes to the user's Drive via a single multipart/related request.
    Returns the created file (id, name, link, mime, modified), or None if the user
    isn't connected. Suitable for modest files; large uploads should use resumable."""
    token = get_access_token(email)
    if not token:
        return None
    mimetype = (mimetype or "application/octet-stream").split(";")[0].strip() or "application/octet-stream"
    boundary = "singlebrain" + str(int(time.time() * 1000))
    bb = boundary.encode("utf-8")
    meta = json.dumps({"name": (filename or "upload").strip() or "upload"}).encode("utf-8")
    body = b"".join([
        b"--", bb, b"\r\n",
        b"Content-Type: application/json; charset=UTF-8\r\n\r\n", meta, b"\r\n",
        b"--", bb, b"\r\n",
        b"Content-Type: ", mimetype.encode("utf-8"), b"\r\n\r\n", content, b"\r\n",
        b"--", bb, b"--\r\n",
    ])
    url = ("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&"
           "fields=id,name,mimeType,modifiedTime,webViewLink")
    data = _api_post(url, token, body, f"multipart/related; boundary={boundary}")
    return {
        "id": data.get("id"), "name": data.get("name"), "link": data.get("webViewLink"),
        "modified": data.get("modifiedTime"), "mime": data.get("mimeType"),
    }


def share_file(email, file_id, share_type="anyone", email_address=None, role="reader"):
    """Grant access to a Drive file and return its shareable link.
    share_type 'anyone' => anyone-with-the-link; 'user' => a specific email address.
    role 'reader' (view) or 'writer' (edit). Returns None if the user isn't connected."""
    token = get_access_token(email)
    if not token:
        return None
    role = "writer" if role == "writer" else "reader"
    fid = urllib.parse.quote(file_id, safe="")
    if share_type == "user":
        addr = (email_address or "").strip()
        if not addr:
            raise ValueError("An email address is required to share with a specific person.")
        perm = {"role": role, "type": "user", "emailAddress": addr}
        send_notify = "true"      # let the person know they've been given access
    else:
        perm = {"role": role, "type": "anyone"}
        send_notify = "false"     # link sharing needs no notification
    url = (f"https://www.googleapis.com/drive/v3/files/{fid}/permissions?"
           + urllib.parse.urlencode({"sendNotificationEmail": send_notify, "fields": "id"}))
    _api_post(url, token, json.dumps(perm).encode("utf-8"), "application/json")
    info = _api_get(
        f"https://www.googleapis.com/drive/v3/files/{fid}?fields=id,name,webViewLink", token
    )
    return {"id": info.get("id"), "name": info.get("name"), "link": info.get("webViewLink")}


def delete_drive_file(email, file_id):
    """Move a Drive file to the user's Drive trash (recoverable ~30 days), rather than
    hard-deleting. Returns True on success (or if the file is already gone), or None if
    the user isn't connected. Raises on other API errors."""
    token = get_access_token(email)
    if not token:
        return None
    fid = urllib.parse.quote(file_id, safe="")
    url = f"https://www.googleapis.com/drive/v3/files/{fid}?fields=id"
    body = json.dumps({"trashed": True}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="PATCH",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            json.loads(r.read().decode("utf-8"))
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return True   # already deleted in Drive; treat as success
        raise


def status(email):
    email = (email or "").strip().lower()
    rows = db.query("SELECT email, connected_at, scope FROM google_tokens WHERE email=?", (email,))
    scopes = ((rows[0]["scope"] if rows else "") or "").split()
    return {
        "configured": configured(),
        "connected": bool(rows),
        "connected_at": rows[0]["connected_at"] if rows else None,
        # Full calendar scope (read teammates' calendars + create events). Users who
        # connected before this scope shipped will show False until they reconnect.
        "calendar": "https://www.googleapis.com/auth/calendar" in scopes,
        "drive": "https://www.googleapis.com/auth/drive" in scopes,
    }


def list_calendar_events(viewer_email, calendar_id, time_min, time_max, max_results=100):
    """Read events from a specific calendar (a teammate's email) using the viewer's
    token. Returns a list of events, or None if the viewer isn't connected. Raises
    urllib.error.HTTPError (403/404) when the viewer lacks access to that calendar."""
    token = get_access_token(viewer_email)
    if not token:
        return None
    cid = urllib.parse.quote(calendar_id, safe="@.")
    url = "https://www.googleapis.com/calendar/v3/calendars/" + cid + "/events?" + urllib.parse.urlencode({
        "timeMin": time_min, "timeMax": time_max, "singleEvents": "true",
        "orderBy": "startTime", "maxResults": max_results,
    })
    data = _api_get(url, token)
    out = []
    for e in data.get("items", []):
        if e.get("status") == "cancelled":
            continue
        start = e.get("start", {})
        end = e.get("end", {})
        out.append({
            "summary": e.get("summary") or "(busy)",
            "start": start.get("dateTime") or start.get("date"),
            "end": end.get("dateTime") or end.get("date"),
            "all_day": "date" in start and "dateTime" not in start,
            "location": e.get("location"),
            "link": e.get("htmlLink"),
            "organizer": (e.get("organizer") or {}).get("email"),
        })
    return out


def create_event(viewer_email, summary, start_local, end_local, tz,
                 attendees=None, description=None, location=None, send_updates=True):
    """Create an event on the viewer's primary calendar and invite attendees.
    start_local/end_local are naive local datetimes (YYYY-MM-DDTHH:MM:SS) interpreted
    in `tz`. Returns the created event (id, link, start), or None if not connected."""
    token = get_access_token(viewer_email)
    if not token:
        return None
    tz = tz or "Pacific/Honolulu"
    body = {
        "summary": summary or "Meeting",
        "start": {"dateTime": start_local, "timeZone": tz},
        "end": {"dateTime": end_local, "timeZone": tz},
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [{"email": a} for a in attendees if a]
    url = "https://www.googleapis.com/calendar/v3/calendars/primary/events?" + urllib.parse.urlencode({
        "sendUpdates": "all" if send_updates else "none",
    })
    data = _api_post(url, token, json.dumps(body).encode("utf-8"), "application/json")
    return {
        "id": data.get("id"),
        "link": data.get("htmlLink"),
        "summary": data.get("summary"),
        "start": (data.get("start") or {}).get("dateTime"),
    }


def disconnect(email):
    db.execute("DELETE FROM google_tokens WHERE email=?", ((email or "").strip().lower(),))
