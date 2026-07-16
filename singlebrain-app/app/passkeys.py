"""Passkeys (WebAuthn) — biometric sign-in as an alternative to the authenticator app.

A passkey is ALREADY multi-factor: it takes possession of the device plus a
biometric/PIN to unlock. So a verified passkey assertion stands in for BOTH the
magic link and the TOTP code — one touch, no inbox round-trip. The authenticator
app stays as the fallback for anyone who hasn't enrolled a passkey (or is on a
device that can't hold one), so nobody is ever locked out.

Why this is STRONGER than the code it replaces, not a convenience trade-off:
  • Phishing-resistant — the credential is bound to the RP ID, so a passkey minted
    for brain.cleverwolfdigital.com will not sign for a look-alike domain. A TOTP
    code can be typed into a fake page; a passkey cannot.
  • No shared secret — the server stores only a PUBLIC key. A DB leak yields
    nothing an attacker can sign with (a leaked TOTP seed is game over).
  • Replay-resistant — per-assertion signature counter + server-issued challenge.

Credentials are DISCOVERABLE (resident keys), so sign-in is usernameless: the OS
offers the matching account and we identify the user from the assertion itself.

Challenges are held in a short-lived SIGNED COOKIE rather than server state, which
matches how the rest of this module already does magic/pending/session tokens.
"""
import os
import secrets
from urllib.parse import urlparse

from webauthn import (
    generate_registration_options,
    verify_registration_response,
    generate_authentication_options,
    verify_authentication_response,
    options_to_json,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from . import db

# The relying party is this exact host. Derived from the same BASE_URL the magic
# link uses, so dev (localhost) and prod stay consistent without a second setting.
BASE_URL = os.environ.get("SB_BASE_URL", "https://brain.cleverwolfdigital.com").rstrip("/")
RP_ID = os.environ.get("SB_RP_ID", "") or (urlparse(BASE_URL).hostname or "localhost")
RP_NAME = "Single Brain"
ORIGIN = BASE_URL

CHALLENGE_MAX_AGE = 300  # 5 minutes to complete a ceremony


def init_passkey_db():
    """Create the credential store. Additive + idempotent, like the rest of init."""
    with db.get_conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS webauthn_credentials(
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 email TEXT NOT NULL,
                 credential_id TEXT NOT NULL UNIQUE,   -- base64url
                 public_key TEXT NOT NULL,             -- base64url (PUBLIC key only)
                 sign_count INTEGER NOT NULL DEFAULT 0,
                 label TEXT,
                 created_at TEXT DEFAULT (datetime('now')),
                 last_used_at TEXT
               )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_webauthn_email ON webauthn_credentials(email)")
        # Stable, opaque per-user handle. Deliberately NOT the email: the handle is
        # stored on the authenticator itself, so it shouldn't carry PII and must
        # survive an address change.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(auth_users)")}
        if cols and "webauthn_handle" not in cols:
            conn.execute("ALTER TABLE auth_users ADD COLUMN webauthn_handle TEXT")
        conn.commit()


def _handle_for(email):
    """Fetch (or mint) this user's opaque WebAuthn handle."""
    rows = db.query("SELECT webauthn_handle FROM auth_users WHERE email=?", (email,))
    if rows and rows[0].get("webauthn_handle"):
        return base64url_to_bytes(rows[0]["webauthn_handle"])
    handle = secrets.token_bytes(32)
    db.execute("UPDATE auth_users SET webauthn_handle=? WHERE email=?", (bytes_to_base64url(handle), email))
    return handle


def _email_for_handle(handle_b64):
    rows = db.query("SELECT email FROM auth_users WHERE webauthn_handle=?", (handle_b64,))
    return rows[0]["email"] if rows else None


def has_passkey(email):
    return bool(db.query("SELECT 1 FROM webauthn_credentials WHERE email=? LIMIT 1", ((email or "").strip().lower(),)))


def list_for(email):
    return db.query(
        "SELECT id, label, created_at, last_used_at FROM webauthn_credentials WHERE email=? ORDER BY id DESC",
        ((email or "").strip().lower(),),
    )


def delete_for(email, cred_row_id):
    """Remove one of MY passkeys. Scoped by email so nobody can delete another's."""
    db.execute("DELETE FROM webauthn_credentials WHERE id=? AND email=?", (cred_row_id, (email or "").strip().lower()))


# ---------------- registration (enrolling a new passkey, while signed in) --------
def registration_options(email, display_name=None):
    """Returns (options_json, challenge_b64). Existing credentials are excluded so a
    device can't silently enroll twice."""
    email = (email or "").strip().lower()
    existing = db.query("SELECT credential_id FROM webauthn_credentials WHERE email=?", (email,))
    opts = generate_registration_options(
        rp_id=RP_ID,
        rp_name=RP_NAME,
        user_id=_handle_for(email),
        user_name=email,
        user_display_name=display_name or email,
        authenticator_selection=AuthenticatorSelectionCriteria(
            # Resident key => usernameless sign-in. User verification REQUIRED is what
            # forces the biometric/PIN, which is what makes one touch a full factor.
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["credential_id"])) for c in existing
        ],
    )
    return options_to_json(opts), bytes_to_base64url(opts.challenge)


def registration_verify(email, credential, challenge_b64, label=None):
    """Verify the attestation and store the PUBLIC key. Raises on any mismatch."""
    email = (email or "").strip().lower()
    v = verify_registration_response(
        credential=credential,
        expected_challenge=base64url_to_bytes(challenge_b64),
        expected_rp_id=RP_ID,
        expected_origin=ORIGIN,
        require_user_verification=True,
    )
    db.execute(
        "INSERT OR REPLACE INTO webauthn_credentials(email, credential_id, public_key, sign_count, label) "
        "VALUES(?,?,?,?,?)",
        (
            email,
            bytes_to_base64url(v.credential_id),
            bytes_to_base64url(v.credential_public_key),
            v.sign_count,
            (label or "").strip()[:60] or "Passkey",
        ),
    )
    return True


# ---------------- authentication (signing in with a passkey) --------------------
def authentication_options():
    """Usernameless: no allow_credentials, so the OS offers whichever account has a
    passkey for this site. Returns (options_json, challenge_b64)."""
    opts = generate_authentication_options(
        rp_id=RP_ID,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return options_to_json(opts), bytes_to_base64url(opts.challenge)


class PasskeyError(Exception):
    pass


def authentication_verify(credential, challenge_b64):
    """Verify an assertion and return the owning email. Raises PasskeyError if the
    credential is unknown or verification fails."""
    raw_id = credential.get("id") if isinstance(credential, dict) else None
    if not raw_id:
        raise PasskeyError("Malformed passkey response.")
    rows = db.query("SELECT * FROM webauthn_credentials WHERE credential_id=?", (raw_id,))
    if not rows:
        raise PasskeyError("That passkey isn't registered here.")
    cred = rows[0]

    v = verify_authentication_response(
        credential=credential,
        expected_challenge=base64url_to_bytes(challenge_b64),
        expected_rp_id=RP_ID,
        expected_origin=ORIGIN,
        credential_public_key=base64url_to_bytes(cred["public_key"]),
        credential_current_sign_count=int(cred["sign_count"] or 0),
        require_user_verification=True,
    )
    # A counter that fails to advance can indicate a cloned authenticator; py_webauthn
    # already rejects that case, so reaching here means the assertion is sound.
    db.execute(
        "UPDATE webauthn_credentials SET sign_count=?, last_used_at=datetime('now') WHERE id=?",
        (v.new_sign_count, cred["id"]),
    )
    return cred["email"]
