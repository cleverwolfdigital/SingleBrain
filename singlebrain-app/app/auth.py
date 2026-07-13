"""Single Brain auth — Magic Link (factor 1) + TOTP authenticator (factor 2).

Server-side gate in front of the dashboard + API. Provider-agnostic SMTP for the
magic-link email (Mailjet / Resend / Gmail — any SMTP works). If SMTP is not
configured, the magic link is logged to stdout so the flow stays testable.
"""
import io
import os
import re
import time
import smtplib
from email.message import EmailMessage

import pyotp
import qrcode
import qrcode.image.svg
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from . import db

# ---------------- config (env) ----------------
SECRET_KEY = os.environ.get("SB_SECRET_KEY", "dev-insecure-change-me")
ALLOWED_DOMAIN = os.environ.get("SB_ALLOWED_DOMAIN", "cleverwolfdigital.com").strip().lower()
BASE_URL = os.environ.get("SB_BASE_URL", "https://brain.cleverwolfdigital.com").rstrip("/")
ISSUER = "Single Brain"

SMTP_HOST = os.environ.get("SB_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SB_SMTP_PORT", "587") or "587")
SMTP_USER = os.environ.get("SB_SMTP_USER", "")
SMTP_PASS = os.environ.get("SB_SMTP_PASS", "")
MAIL_FROM = os.environ.get("SB_MAIL_FROM", "Single Brain <noreply@cleverwolfdigital.com>")

MAGIC_MAX_AGE = 900                     # 15 minutes
SESSION_MAX_AGE = 60 * 60 * 12          # 12 hours (default session)
REMEMBER_MAX_AGE = 60 * 60 * 24 * 90    # 90 days ("remember this device")
PENDING_MAX_AGE = 600                   # 10 minutes to complete 2FA
COOKIE_SESSION = "sb_session"
COOKIE_PENDING = "sb_pending"
SECURE_COOKIES = BASE_URL.startswith("https")

_magic = URLSafeTimedSerializer(SECRET_KEY, salt="sb-magic")
_pending = URLSafeTimedSerializer(SECRET_KEY, salt="sb-pending")
_session = URLSafeTimedSerializer(SECRET_KEY, salt="sb-session")

router = APIRouter()

# App-store links for authenticator apps, shown on first-time 2FA enrollment.
# Plain string (not an f-string) so the inline <script> braces are safe. A tiny
# script hides the non-matching store on iOS/Android; desktop shows both.
AUTHENTICATOR_APPS_HTML = """
<div style="margin:14px 0 8px">
  <p class="muted" style="margin:0 0 8px">Need an app? Get one free on your phone:</p>
  <div id="authApps" style="display:grid;gap:8px">
    <div style="display:flex;align-items:center;justify-content:space-between;background:#0a1622;border:1px solid #26414f;border-radius:9px;padding:9px 12px">
      <span style="font-size:13px">Google Authenticator</span>
      <span style="display:flex;gap:10px">
        <a data-store="ios" href="https://apps.apple.com/app/google-authenticator/id388497605" style="color:#d0a853;font-size:12px;text-decoration:none">iOS</a>
        <a data-store="android" href="https://play.google.com/store/apps/details?id=com.google.android.apps.authenticator2" style="color:#d0a853;font-size:12px;text-decoration:none">Android</a>
      </span>
    </div>
    <div style="display:flex;align-items:center;justify-content:space-between;background:#0a1622;border:1px solid #26414f;border-radius:9px;padding:9px 12px">
      <span style="font-size:13px">Microsoft Authenticator</span>
      <span style="display:flex;gap:10px">
        <a data-store="ios" href="https://apps.apple.com/app/microsoft-authenticator/id983156458" style="color:#d0a853;font-size:12px;text-decoration:none">iOS</a>
        <a data-store="android" href="https://play.google.com/store/apps/details?id=com.azure.authenticator" style="color:#d0a853;font-size:12px;text-decoration:none">Android</a>
      </span>
    </div>
  </div>
</div>
<script>
(function(){
  var ua = navigator.userAgent || "";
  var ios = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  var android = /Android/.test(ua);
  if (ios || android) {
    var keep = ios ? "ios" : "android";
    document.querySelectorAll("#authApps a[data-store]").forEach(function(a){
      if (a.getAttribute("data-store") !== keep) a.style.display = "none";
    });
  }
})();
</script>
"""

# public paths that skip the auth gate
PUBLIC_PREFIXES = ("/login", "/auth/", "/logout", "/static", "/api/health", "/favicon")


def init_auth_db():
    with db.get_conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS auth_users(
                 email TEXT PRIMARY KEY,
                 totp_secret TEXT NOT NULL,
                 enrolled INTEGER NOT NULL DEFAULT 0,
                 created_at TEXT DEFAULT (datetime('now'))
               )"""
        )
        conn.commit()


def _norm_email(email):
    return (email or "").strip().lower()


def _valid_email(email):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email or ""))


def allowed(email):
    """Domain accounts are always allowed. Guests (any domain) are allowed only if a
    super admin has explicitly provisioned them in app_users."""
    email = (email or "").strip().lower()
    if not _valid_email(email):
        return False
    if email.endswith("@" + ALLOWED_DOMAIN):
        return True
    try:
        return bool(db.query("SELECT 1 FROM app_users WHERE lower(email)=? LIMIT 1", (email,)))
    except Exception:
        return False


def _get_user(email):
    rows = db.query("SELECT * FROM auth_users WHERE email=?", (email,))
    return rows[0] if rows else None


def _ensure_user(email):
    u = _get_user(email)
    if not u:
        db.execute(
            "INSERT OR IGNORE INTO auth_users(email, totp_secret, enrolled) VALUES(?,?,0)",
            (email, pyotp.random_base32()),
        )
        u = _get_user(email)
    return u


def current_user(request):
    tok = request.cookies.get(COOKIE_SESSION)
    if not tok:
        return None
    try:
        data, ts = _session.loads(tok, max_age=REMEMBER_MAX_AGE, return_timestamp=True)
    except (BadSignature, SignatureExpired):
        return None
    ttl = int(data.get("ttl") or SESSION_MAX_AGE)
    if time.time() - ts.timestamp() > ttl:
        return None
    return data.get("email")


def _set_cookie(resp, name, value, max_age):
    resp.set_cookie(name, value, max_age=max_age, httponly=True,
                    secure=SECURE_COOKIES, samesite="lax", path="/")


def send_email(to, subject, text, html=None):
    """Generic transactional send over the same SMTP transport as magic links
    (Resend/Mailjet/Gmail). Returns True if sent, False if SMTP isn't configured."""
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS):
        print(f"[mail] SMTP not configured -- would send to {to}: {subject}", flush=True)
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = MAIL_FROM
    msg["To"] = to
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
        s.ehlo()
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)
    return True


def send_magic_email(email, link):
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS):
        print(f"[auth] SMTP not fully configured -- magic link for {email}: {link}", flush=True)
        return
    msg = EmailMessage()
    msg["Subject"] = "Your Single Brain sign-in link"
    msg["From"] = MAIL_FROM
    msg["To"] = email
    msg.set_content(
        f"Sign in to Single Brain:\n\n{link}\n\n"
        "This link expires in 15 minutes. If you didn't request it, ignore this email."
    )
    msg.add_alternative(
        f"""<div style="font-family:system-ui,sans-serif;max-width:480px">
  <h2 style="color:#0b1622">Single Brain sign-in</h2>
  <p>Click to sign in, then enter your authenticator code:</p>
  <p><a href="{link}" style="background:#d0a853;color:#07121d;padding:12px 22px;border-radius:8px;text-decoration:none;font-weight:600">Sign in</a></p>
  <p style="color:#667;font-size:13px">This link expires in 15 minutes. If you didn't request it, ignore this email.</p>
</div>""",
        subtype="html",
    )
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
        s.ehlo()
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)


# ---------------- HTML shell ----------------
def _page(title, body):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} - Single Brain</title>
<style>
 :root{{color-scheme:dark}}
 *{{box-sizing:border-box}}
 body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#07121d;
   color:#e8eef5;font-family:system-ui,-apple-system,Segoe UI,sans-serif}}
 .card{{width:min(92vw,420px);background:#0e1c2b;border:1px solid #1c313e;border-radius:16px;padding:34px 30px}}
 .brand{{font-weight:800;letter-spacing:.2px;font-size:20px;margin-bottom:2px}}
 .eyebrow{{color:#d0a853;font-size:12px;letter-spacing:.14em;text-transform:uppercase;margin:18px 0 6px}}
 h1{{font-size:22px;margin:0 0 6px}}
 p.lead{{color:#9fb2c2;font-size:14px;line-height:1.5;margin:0 0 18px}}
 label{{display:block;font-size:13px;color:#9fb2c2;margin:14px 0 6px}}
 input{{width:100%;padding:12px 14px;border-radius:9px;border:1px solid #26414f;
   background:#0a1622;color:#e8eef5;font-size:15px}}
 input:focus{{outline:none;border-color:#d0a853}}
 .btn{{display:block;width:100%;margin-top:18px;padding:12px;border:0;border-radius:9px;
   background:#d0a853;color:#07121d;font-weight:700;font-size:15px;cursor:pointer;text-align:center;text-decoration:none}}
 .muted{{color:#6f8496;font-size:12px;margin-top:16px}}
 .err{{background:#3a1720;border:1px solid #6b2334;color:#ffb4b4;padding:10px 12px;border-radius:8px;font-size:13px;margin-top:12px}}
 .ok{{background:#123024;border:1px solid #1f6b45;color:#9ff0c0;padding:10px 12px;border-radius:8px;font-size:13px;margin-top:12px}}
 .qr{{background:#fff;padding:12px;border-radius:10px;width:196px;margin:10px auto;display:grid;place-items:center}}
 .qr svg{{width:170px;height:170px}}
 code.key{{display:block;background:#0a1622;border:1px solid #26414f;border-radius:8px;
   padding:9px;font-size:12px;word-break:break-all;color:#d0a853;margin-top:6px}}
</style></head><body><div class="card">
<div class="brand">Single Brain</div>{body}</div></body></html>"""


def _login_page(msg_html=""):
    return _page("Sign in", f"""
<div class="eyebrow">Secure access</div>
<h1>Sign in</h1>
<p class="lead">Enter your work email. We'll send a one-time magic link, then you'll confirm with your authenticator code.</p>
<form method="post" action="/auth/request">
  <label>Work email</label>
  <input name="email" type="email" placeholder="you@{ALLOWED_DOMAIN}" required autofocus>
  <button class="btn" type="submit">Send magic link</button>
</form>{msg_html}
<div class="muted">Access limited to @{ALLOWED_DOMAIN} accounts.</div>""")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if current_user(request):
        return RedirectResponse("/", status_code=302)
    return HTMLResponse(_login_page())


@router.post("/auth/request", response_class=HTMLResponse)
def auth_request(request: Request, email: str = Form(...)):
    email = _norm_email(email)
    if not allowed(email):
        return HTMLResponse(_login_page(
            f'<div class="err">Only @{ALLOWED_DOMAIN} email addresses are authorized.</div>'), status_code=403)
    _ensure_user(email)
    token = _magic.dumps({"email": email})
    link = f"{BASE_URL}/auth/verify?token={token}"
    try:
        send_magic_email(email, link)
    except Exception as e:
        print(f"[auth] send failed for {email}: {e}", flush=True)
        return HTMLResponse(_login_page(
            '<div class="err">Could not send the email right now. Please try again shortly.</div>'), status_code=500)
    return HTMLResponse(_page("Check your email", f"""
<div class="eyebrow">Magic link sent</div>
<h1>Check your inbox</h1>
<p class="lead">We sent a sign-in link to <strong>{email}</strong>. It expires in 15 minutes. Open it on this device, then enter your authenticator code.</p>
<div class="ok">You can close this tab after clicking the link in your email.</div>
<div class="muted"><a href="/login" style="color:#9fb2c2">Use a different email</a></div>"""))


@router.get("/auth/verify", response_class=HTMLResponse)
def auth_verify(request: Request, token: str = ""):
    try:
        data = _magic.loads(token, max_age=MAGIC_MAX_AGE)
        email = _norm_email(data.get("email"))
    except SignatureExpired:
        return HTMLResponse(_login_page('<div class="err">That link expired. Request a new one.</div>'), status_code=400)
    except (BadSignature, Exception):
        return HTMLResponse(_login_page('<div class="err">That link is invalid. Request a new one.</div>'), status_code=400)
    if not allowed(email):
        return HTMLResponse(_login_page('<div class="err">This account is not authorized.</div>'), status_code=403)
    _ensure_user(email)
    resp = RedirectResponse("/auth/2fa", status_code=302)
    _set_cookie(resp, COOKIE_PENDING, _pending.dumps({"email": email}), PENDING_MAX_AGE)
    return resp


def _pending_email(request):
    tok = request.cookies.get(COOKIE_PENDING)
    if not tok:
        return None
    try:
        return _norm_email(_pending.loads(tok, max_age=PENDING_MAX_AGE).get("email"))
    except (BadSignature, SignatureExpired):
        return None


def _twofa_page(email, enrolling, secret=None, err=""):
    err_html = f'<div class="err">{err}</div>' if err else ""
    if enrolling:
        uri = pyotp.totp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)
        img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage)
        buf = io.BytesIO(); img.save(buf); svg = buf.getvalue().decode()
        intro = f"""<p class="lead">First time here. Get an authenticator app below, scan this code, then enter the 6-digit code.</p>
{AUTHENTICATOR_APPS_HTML}
<div class="qr">{svg}</div>
<p class="muted">Can't scan? Enter this key manually:</p><code class="key">{secret}</code>"""
    else:
        intro = '<p class="lead">Enter the 6-digit code from your authenticator app.</p>'
    return _page("Two-factor", f"""
<div class="eyebrow">Second factor</div>
<h1>Authenticator code</h1>{intro}
<form method="post" action="/auth/2fa">
  <label>6-digit code</label>
  <input name="code" inputmode="numeric" pattern="[0-9]*" maxlength="6" placeholder="123456" required autofocus autocomplete="one-time-code">
  <label style="display:flex;align-items:center;gap:9px;margin-top:14px;font-size:13px;color:#c9d6e2;cursor:pointer"><input type="checkbox" name="remember" value="1" checked style="width:auto;margin:0;accent-color:#d0a853"> Remember this device for 90 days</label>
  <button class="btn" type="submit">Verify &amp; enter</button>
</form>{err_html}
<div class="muted">Signed in as {email}. <a href="/logout" style="color:#9fb2c2">Cancel</a></div>""")


@router.get("/auth/2fa", response_class=HTMLResponse)
def twofa_page(request: Request):
    email = _pending_email(request)
    if not email:
        return RedirectResponse("/login", status_code=302)
    u = _ensure_user(email)
    enrolling = not int(u["enrolled"])
    return HTMLResponse(_twofa_page(email, enrolling, secret=u["totp_secret"] if enrolling else None))


@router.post("/auth/2fa", response_class=HTMLResponse)
def twofa_verify(request: Request, code: str = Form(...), remember: str = Form("")):
    email = _pending_email(request)
    if not email:
        return RedirectResponse("/login", status_code=302)
    u = _ensure_user(email)
    enrolling = not int(u["enrolled"])
    totp = pyotp.TOTP(u["totp_secret"])
    if not totp.verify((code or "").strip(), valid_window=1):
        return HTMLResponse(_twofa_page(email, enrolling,
                            secret=u["totp_secret"] if enrolling else None,
                            err="Incorrect code. Try the current code from your app."), status_code=401)
    if enrolling:
        db.execute("UPDATE auth_users SET enrolled=1 WHERE email=?", (email,))
    ttl = REMEMBER_MAX_AGE if remember else SESSION_MAX_AGE
    resp = RedirectResponse("/", status_code=302)
    _set_cookie(resp, COOKIE_SESSION, _session.dumps({"email": email, "ttl": ttl}), ttl)
    resp.delete_cookie(COOKIE_PENDING, path="/")
    return resp


@router.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(COOKIE_SESSION, path="/")
    resp.delete_cookie(COOKIE_PENDING, path="/")
    return resp
