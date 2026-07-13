"""Single Brain backend — FastAPI + SQLite (real persistence).

Serves the dashboard and a JSON API the frontend talks to (replacing localStorage).
Auth: Magic Link (factor 1) + TOTP authenticator (factor 2), enforced by a gate
middleware in front of the dashboard and every /api route. HTTPS via Traefik.
"""
import os
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List

# Quincy operates in Hawaii; report buckets ("today", "this week") are computed
# against Hawaii-Aleutian Standard Time, which has no daylight saving.
HST = timezone(timedelta(hours=-10))
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel, Field

from . import db, seed, auth, repo, config, catalog, google_int

app = FastAPI(title="Single Brain API")
app.include_router(auth.router)

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "index.html"


# ---------------- RBAC / access control ----------------
# Configured super admins are always elevated regardless of the stored role.
SUPER_ADMINS = {
    e.strip().lower()
    for e in os.environ.get("SB_SUPER_ADMINS", "quincy@cleverwolfdigital.com").split(",")
    if e.strip()
}
# How many additional people (beyond the configured owner) may be Super Admin.
MAX_PROMOTED_SUPER_ADMINS = int(os.environ.get("SB_MAX_PROMOTED_ADMINS", "2") or "2")


def _ensure_app_user(email, name=None):
    """Register a logged-in email as an app user (defaults to 'staff'; configured
    super admins are elevated). Idempotent — safe to call on every request."""
    if not email:
        return None
    email = email.strip().lower()
    rows = db.query("SELECT * FROM app_users WHERE email=?", (email,))
    if not rows:
        role = "super_admin" if email in SUPER_ADMINS else "staff"
        db.execute("INSERT OR IGNORE INTO app_users(email,name,role) VALUES(?,?,?)", (email, name, role))
    elif email in SUPER_ADMINS and rows[0].get("role") != "super_admin":
        db.execute("UPDATE app_users SET role='super_admin' WHERE email=?", (email,))
    return db.query("SELECT * FROM app_users WHERE email=?", (email,))[0]


def _role(email):
    email = (email or "").strip().lower()
    if email in SUPER_ADMINS:
        return "super_admin"
    rows = db.query("SELECT role FROM app_users WHERE email=?", (email,))
    return (rows[0]["role"] if rows else "staff") or "staff"


def _is_admin(email):
    return _role(email) == "super_admin"


def _access_lists(email):
    """(businesses, projects) a user may view. (None, None) for admins = everything."""
    if _is_admin(email):
        return None, None
    biz = [r["business"] for r in db.query("SELECT business FROM user_business_access WHERE email=?", (email,))]
    proj = [r["project"] for r in db.query("SELECT project FROM user_project_access WHERE email=?", (email,))]
    return biz, proj


def _require_admin(request):
    email = auth.current_user(request)
    if not _is_admin(email):
        raise HTTPException(403, "Super Admin access required.")
    return email


def _visible_tasks(email, tasks):
    """Filter a task list to what a viewer may see: their assigned tasks plus any
    task tied to a business they've been granted. Admins see everything."""
    if _is_admin(email):
        return tasks
    biz, _ = _access_lists(email)
    allowed = set(biz or [])
    me = (email or "").strip().lower()
    return [t for t in tasks
            if (t.get("assignee") or "").strip().lower() == me or (t.get("business") or "") in allowed]


@app.middleware("http")
async def _auth_gate(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or any(path == p or path.startswith(p) for p in auth.PUBLIC_PREFIXES):
        return await call_next(request)
    if not auth.current_user(request):
        if path.startswith("/api/"):
            return JSONResponse({"detail": "authentication required"}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    return await call_next(request)


@app.on_event("startup")
def _startup():
    db.init_db()
    seed.seed()
    catalog.seed_catalog()
    catalog.generate_recurring()
    auth.init_auth_db()
    google_int.init_db()


@app.get("/", response_class=HTMLResponse)
def index():
    if FRONTEND.exists():
        return HTMLResponse(FRONTEND.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Single Brain</h1><p>Frontend not deployed yet.</p>")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    email = auth.current_user(request)
    _ensure_app_user(email)
    role = _role(email)
    biz, proj = _access_lists(email)
    return {
        "email": email,
        "role": role,
        "is_super_admin": role == "super_admin",
        "businesses": biz,   # null => all (admin); list => the only ones visible
        "projects": proj,
    }


def _apply_timing(t):
    """Add derived timer fields: effective actual seconds (including any live,
    still-running interval) and a running flag. Non-destructive to stored data."""
    actual = t.get("actual_sec") or 0
    running = bool(t.get("started_at"))
    if running:
        actual += max(0, int(time.time()) - int(t["started_at"]))
    t["actual_sec_effective"] = actual
    t["running"] = running
    return t


def _tasks_with_deps(viewer=None):
    tasks = db.query("SELECT * FROM tasks ORDER BY id DESC")
    deps = db.query("SELECT task_id, depends_on FROM task_dependencies")
    by_task = {}
    for d in deps:
        by_task.setdefault(d["task_id"], []).append(d["depends_on"])
    for t in tasks:
        t["dependencies"] = by_task.get(t["id"], [])
        _apply_timing(t)
    if viewer is not None:
        tasks = _visible_tasks(viewer, tasks)
    return tasks


@app.get("/api/state")
def state(request: Request):
    viewer = auth.current_user(request)
    return {
        "businesses": db.query("SELECT * FROM businesses ORDER BY name"),
        "projects": db.query("SELECT * FROM projects ORDER BY id"),
        "staff": db.query("SELECT * FROM staff ORDER BY id"),
        "blockers": db.query("SELECT * FROM blockers ORDER BY id"),
        "recommendations": db.query("SELECT * FROM recommendations ORDER BY id"),
        "tasks": _tasks_with_deps(viewer),
        "journal": db.query("SELECT * FROM daily_journal ORDER BY id DESC LIMIT 30"),
    }


def _sync_tasks_to_repo():
    """Write all tasks to a markdown file in the brain repo, commit, and push,
    so tasks become the shared source of truth for every agent. Non-fatal on failure."""
    try:
        rows = db.query(
            "SELECT * FROM tasks ORDER BY "
            "CASE priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END, id DESC"
        )
        def cell(v):
            return str(v if v is not None else "").replace("|", "/").replace("\n", " ").strip()

        def hm(sec):
            sec = int(sec or 0)
            if sec <= 0:
                return "—"
            h, m = divmod(sec // 60, 60)
            return f"{h}h {m:02d}m" if h else f"{m}m"

        lines = [
            "# Tasks",
            "",
            "_Source of truth for dashboard tasks. Auto-generated from the Single Brain dashboard "
            "Quick Add — do not hand-edit; changes may be overwritten._",
            "",
            "| Task | Business | Category | Priority | Due | Status | Est | Tracked |",
            "|------|----------|----------|----------|-----|--------|-----|---------|",
        ]
        for r in rows:
            est = f"{r.get('estimate_min')}m" if r.get("estimate_min") else "—"
            lines.append(
                f"| {cell(r.get('name'))} | {cell(r.get('business'))} | {cell(r.get('category'))} "
                f"| {cell(r.get('priority'))} | {cell(r.get('due'))} | {cell(r.get('status') or 'open')} "
                f"| {est} | {hm(r.get('actual_sec'))} |"
            )
        path = config.BRAIN_REPO / "Personal" / "Notes" / "Tasks.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        repo.pull()
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        res = repo.commit_and_push("tasks: update from Single Brain dashboard", [path])
        return bool(res.get("pushed"))
    except Exception:
        return False


# ---------- Tasks (Quick Add) ----------
class TaskIn(BaseModel):
    name: str
    business: Optional[str] = None
    category: Optional[str] = None
    priority: str = "Medium"
    due: Optional[str] = None
    notes: Optional[str] = None
    estimate_min: Optional[int] = None
    assignee: Optional[str] = None
    client: Optional[str] = None
    dependencies: List[int] = Field(default_factory=list)


@app.get("/api/tasks")
def get_tasks(request: Request):
    return _tasks_with_deps(auth.current_user(request))


@app.post("/api/tasks")
def create_task(t: TaskIn, request: Request):
    name = (t.name or "").strip()
    if len(name) < 5:
        raise HTTPException(422, "Task name must be at least 5 characters.")
    if t.priority not in ("High", "Medium", "Low"):
        raise HTTPException(422, "Priority must be High, Medium, or Low.")
    est = t.estimate_min if (t.estimate_min and t.estimate_min > 0) else None
    # Admins may assign to anyone; staff-created tasks belong to the creator so
    # they remain visible to them under access filtering.
    current = auth.current_user(request)
    if _is_admin(current):
        assignee = (t.assignee or "").strip().lower() or None
    else:
        assignee = (current or "").strip().lower() or None
    tid = db.execute(
        "INSERT INTO tasks(business,name,category,priority,due,notes,status,estimate_min,actual_sec,assignee,client) "
        "VALUES(?,?,?,?,?,?,'open',?,0,?,?)",
        (t.business, name, t.category, t.priority, t.due, t.notes, est, assignee, (t.client or "").strip() or None),
    )
    for dep in t.dependencies:
        db.execute("INSERT OR IGNORE INTO task_dependencies(task_id,depends_on) VALUES(?,?)", (tid, dep))
    pushed = _sync_tasks_to_repo()
    return {"id": tid, "ok": True, "pushed": pushed}


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int):
    db.execute("DELETE FROM task_dependencies WHERE task_id=? OR depends_on=?", (task_id, task_id))
    db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    _sync_tasks_to_repo()
    return {"ok": True}


# ---------- Time tracking (start / pause / complete / reopen) ----------
def _get_task(task_id: int):
    rows = db.query("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not rows:
        raise HTTPException(404, "Task not found.")
    return rows[0]


@app.post("/api/tasks/{task_id}/start")
def start_task(task_id: int):
    t = _get_task(task_id)
    if not t.get("started_at"):
        db.execute(
            "UPDATE tasks SET started_at=?, completed_at=NULL, status='active' WHERE id=?",
            (int(time.time()), task_id),
        )
    return {"ok": True, "task": _apply_timing(_get_task(task_id))}


@app.post("/api/tasks/{task_id}/pause")
def pause_task(task_id: int):
    t = _get_task(task_id)
    if t.get("started_at"):
        add = max(0, int(time.time()) - int(t["started_at"]))
        db.execute(
            "UPDATE tasks SET actual_sec=COALESCE(actual_sec,0)+?, started_at=NULL, status='open' WHERE id=?",
            (add, task_id),
        )
    return {"ok": True, "task": _apply_timing(_get_task(task_id))}


@app.post("/api/tasks/{task_id}/complete")
def complete_task(task_id: int):
    t = _get_task(task_id)
    add = max(0, int(time.time()) - int(t["started_at"])) if t.get("started_at") else 0
    db.execute(
        "UPDATE tasks SET actual_sec=COALESCE(actual_sec,0)+?, started_at=NULL, "
        "completed_at=?, status='done' WHERE id=?",
        (add, int(time.time()), task_id),
    )
    pushed = _sync_tasks_to_repo()
    return {"ok": True, "pushed": pushed, "task": _apply_timing(_get_task(task_id))}


@app.post("/api/tasks/{task_id}/reopen")
def reopen_task(task_id: int):
    db.execute("UPDATE tasks SET completed_at=NULL, started_at=NULL, status='open' WHERE id=?", (task_id,))
    _sync_tasks_to_repo()
    return {"ok": True, "task": _apply_timing(_get_task(task_id))}


# ---------- Productivity reports ----------
def _hst_midnight_epoch(d):
    """Epoch seconds for local Hawaii midnight of date `d`."""
    return int(datetime(d.year, d.month, d.day, tzinfo=HST).timestamp())


def _add_months(y, m, delta):
    idx = (y * 12 + (m - 1)) + delta
    return idx // 12, idx % 12 + 1


def _period_bounds(period, anchor):
    """Return (start_epoch, end_epoch, label) for the period containing `anchor`
    (a date in Hawaii local time)."""
    from datetime import date as _date
    y, m, d = anchor.year, anchor.month, anchor.day
    if period == "day":
        start = _date(y, m, d)
        end = start + timedelta(days=1)
        label = start.strftime("%a, %b %d, %Y")
    elif period == "week":
        start = _date(y, m, d) - timedelta(days=_date(y, m, d).weekday())  # Monday
        end = start + timedelta(days=7)
        label = f"Week of {start.strftime('%b %d')} – {(end - timedelta(days=1)).strftime('%b %d, %Y')}"
    elif period == "month":
        start = _date(y, m, 1)
        ny, nm = _add_months(y, m, 1)
        end = _date(ny, nm, 1)
        label = start.strftime("%B %Y")
    elif period == "quarter":
        q = (m - 1) // 3
        start = _date(y, q * 3 + 1, 1)
        ny, nm = _add_months(y, q * 3 + 1, 3)
        end = _date(ny, nm, 1)
        label = f"Q{q + 1} {y}"
    elif period == "year":
        start = _date(y, 1, 1)
        end = _date(y + 1, 1, 1)
        label = str(y)
    else:
        raise HTTPException(422, "period must be day, week, month, quarter, or year.")
    return _hst_midnight_epoch(start), _hst_midnight_epoch(end), label


def _shift_anchor(period, anchor, direction):
    from datetime import date as _date
    if period == "day":
        return anchor + timedelta(days=direction)
    if period == "week":
        return anchor + timedelta(days=7 * direction)
    if period == "month":
        ny, nm = _add_months(anchor.year, anchor.month, direction)
        return _date(ny, nm, min(anchor.day, 28))
    if period == "quarter":
        ny, nm = _add_months(anchor.year, anchor.month, 3 * direction)
        return _date(ny, nm, min(anchor.day, 28))
    if period == "year":
        return _date(anchor.year + direction, anchor.month, min(anchor.day, 28))
    return anchor


@app.get("/api/reports")
def reports(request: Request, period: str = "week", date: Optional[str] = None, offset: int = 0):
    from datetime import date as _date
    viewer = auth.current_user(request)
    if date:
        try:
            anchor = _date.fromisoformat(date)
        except ValueError:
            raise HTTPException(422, "date must be YYYY-MM-DD.")
    else:
        now_hst = datetime.now(HST)
        anchor = _date(now_hst.year, now_hst.month, now_hst.day)
    for _ in range(abs(offset)):
        anchor = _shift_anchor(period, anchor, 1 if offset > 0 else -1)

    start, end, label = _period_bounds(period, anchor)
    done = db.query(
        "SELECT * FROM tasks WHERE status='done' AND completed_at>=? AND completed_at<? "
        "ORDER BY completed_at DESC",
        (start, end),
    )
    done = _visible_tasks(viewer, done)

    def bucket(key):
        agg = {}
        for t in done:
            k = (t.get(key) or "—").strip() or "—"
            row = agg.setdefault(k, {"name": k, "count": 0, "actual_sec": 0, "estimate_min": 0})
            row["count"] += 1
            row["actual_sec"] += t.get("actual_sec") or 0
            row["estimate_min"] += t.get("estimate_min") or 0
        return sorted(agg.values(), key=lambda r: (-r["actual_sec"], -r["count"]))

    total_actual = sum(t.get("actual_sec") or 0 for t in done)
    total_est = sum(t.get("estimate_min") or 0 for t in done)
    tracked = [t for t in done if (t.get("actual_sec") or 0) > 0]

    # Portfolio-wide snapshot (not period-bound) for context, scoped to the viewer.
    open_all = _visible_tasks(viewer, db.query("SELECT * FROM tasks WHERE status!='done'"))
    active_count = sum(1 for r in open_all if r.get("started_at"))

    return {
        "period": period,
        "label": label,
        "start": start,
        "end": end,
        "completed": len(done),
        "actual_sec": total_actual,
        "estimate_min": total_est,
        "tracked_count": len(tracked),
        "open_count": len(open_all),
        "active_count": active_count,
        "by_business": bucket("business"),
        "by_category": bucket("category"),
        "recent": [
            {
                "id": t["id"],
                "name": t.get("name"),
                "business": t.get("business"),
                "category": t.get("category"),
                "priority": t.get("priority"),
                "actual_sec": t.get("actual_sec") or 0,
                "estimate_min": t.get("estimate_min") or 0,
                "completed_at": t.get("completed_at"),
            }
            for t in done[:50]
        ],
    }


# ---------- Daily journal ----------
class JournalIn(BaseModel):
    kind: str            # morning | eod
    date: str
    energy_level: Optional[int] = None
    top_priorities: Optional[str] = None
    new_context: Optional[str] = None
    awareness: Optional[str] = None
    what_got_done: Optional[str] = None
    what_didnt: Optional[str] = None
    new_decisions: Optional[str] = None
    new_blockers: Optional[str] = None
    wins: Optional[str] = None
    tomorrow_focus: Optional[str] = None


@app.get("/api/journal")
def get_journal():
    return db.query("SELECT * FROM daily_journal ORDER BY id DESC LIMIT 60")


@app.post("/api/journal")
def add_journal(j: JournalIn):
    if j.kind not in ("morning", "eod"):
        raise HTTPException(422, "kind must be 'morning' or 'eod'.")
    db.execute(
        """INSERT INTO daily_journal
           (date,type,energy_level,top_priorities,new_context,awareness,
            what_got_done,what_didnt,new_decisions,new_blockers,wins,tomorrow_focus)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (j.date, j.kind, j.energy_level, j.top_priorities, j.new_context, j.awareness,
         j.what_got_done, j.what_didnt, j.new_decisions, j.new_blockers, j.wins, j.tomorrow_focus),
    )
    return {"ok": True}


# ---------- CWD Brain Chat (Grok / xAI) ----------
XAI_API_KEY = os.environ.get("XAI_API_KEY", "")
XAI_BASE_URL = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1")
XAI_MODEL = os.environ.get("XAI_MODEL", "grok-4")


class ChatIn(BaseModel):
    message: str
    history: List[dict] = Field(default_factory=list)


def _state_context(viewer=None):
    """Build Grok's context from the SOURCE OF TRUTH (Master_Dashboard.md + project files)
    plus the live task list — not the stale DB seed. Tasks are scoped to the viewer."""
    parts = []
    for label, rel in [
        ("Master_Dashboard.md (SINGLE SOURCE OF TRUTH — portfolio by tier, staff+emails, projects, rotation, backlog)",
         "Personal/Notes/Master_Dashboard.md"),
        ("Kimchee_88.md (Kimchee #88 full project + task list)",
         "Personal/Notes/Projects/Kimchee_88.md"),
    ]:
        try:
            text = (config.BRAIN_REPO / rel).read_text(encoding="utf-8")
            parts.append(f"===== {label} =====\n{text[:14000]}")
        except Exception:
            pass
    try:
        tasks = "; ".join(
            f'{t["name"]} [{t.get("priority") or "?"}, {t.get("business") or "-"}, {t.get("status") or "open"}, due {t.get("due") or "-"}]'
            for t in _tasks_with_deps(viewer)[:60]
        ) or "none"
        parts.append(f"===== Live tasks (dashboard DB, synced to Tasks.md) =====\n{tasks}")
    except Exception:
        pass
    return "\n\n".join(parts) if parts else "No portfolio data available."


@app.post("/api/chat")
def chat(c: ChatIn, request: Request):
    if not XAI_API_KEY:
        raise HTTPException(503, "Grok is not configured (XAI_API_KEY missing).")
    system = (
        "You are Grok, powering the CWD Brain Chat inside Quincy Solano's Single Brain command "
        "center. Be concise, direct, and practical — a sharp operator, not a chatbot. "
        "Below is Quincy's FULL portfolio, read live from Master_Dashboard.md (the single source of "
        "truth) plus the live task list. It lists ALL businesses by tier (there are ~29 across "
        "Tier 1–4), staff + emails, projects, Kimchee #88, daily rotation, and automation backlog. "
        "Base every answer on this — never claim a business is missing if it appears below. "
        "When asked to add or change a business, staff member, project, or task, state exactly what "
        "you'd change and ask the user to confirm (live write-actions are rolling out; for now you "
        "advise, summarize, and draft).\n\n" + _state_context(auth.current_user(request))
    )
    msgs = [{"role": "system", "content": system}]
    for h in (c.history or [])[-8:]:
        role = h.get("role")
        content = h.get("content")
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": str(content)[:2000]})
    msgs.append({"role": "user", "content": (c.message or "")[:4000]})

    payload = json.dumps({"model": XAI_MODEL, "messages": msgs, "temperature": 0.4}).encode("utf-8")
    req = urllib.request.Request(
        XAI_BASE_URL.rstrip("/") + "/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        reply = data["choices"][0]["message"]["content"].strip()
        return {"reply": reply}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:300]
        raise HTTPException(502, f"Grok API error ({e.code}): {detail}")
    except Exception as e:
        raise HTTPException(502, f"Grok request failed: {e}")


# ---------- Manual sync (pull latest + push tasks to GitHub) ----------
@app.post("/api/sync")
def sync_now(request: Request):
    """Pull the latest from GitHub and push the current task state back, so the
    dashboard, the repo, and every agent (Claude Code, Obsidian, Jermes) converge."""
    pushed = _sync_tasks_to_repo()
    return {"ok": True, "pushed": bool(pushed)}


# ---------- Task assignment ----------
class AssignIn(BaseModel):
    assignee: Optional[str] = None


@app.post("/api/tasks/{task_id}/assign")
def assign_task(task_id: int, body: AssignIn, request: Request):
    _require_admin(request)
    _get_task(task_id)
    a = (body.assignee or "").strip().lower() or None
    db.execute("UPDATE tasks SET assignee=? WHERE id=?", (a, task_id))
    return {"ok": True, "assignee": a}


# ---------- Super Admin: users + access ----------
class AppUserIn(BaseModel):
    email: str
    name: Optional[str] = None
    role: Optional[str] = "staff"


class AccessIn(BaseModel):
    businesses: List[str] = Field(default_factory=list)
    projects: List[str] = Field(default_factory=list)


def _user_summary(u):
    email = u["email"]
    biz = [r["business"] for r in db.query("SELECT business FROM user_business_access WHERE email=?", (email,))]
    proj = [r["project"] for r in db.query("SELECT project FROM user_project_access WHERE email=?", (email,))]
    cnt = db.query(
        "SELECT COUNT(*) c FROM tasks WHERE lower(assignee)=? AND status!='done'", (email.lower(),)
    )[0]["c"]
    return {
        "email": email,
        "name": u.get("name"),
        "role": u.get("role") or "staff",
        "is_configured_admin": email.lower() in SUPER_ADMINS,
        "businesses": biz,
        "projects": proj,
        "open_tasks": cnt,
    }


@app.get("/api/admin/users")
def admin_users(request: Request):
    _require_admin(request)
    return [_user_summary(u) for u in db.query("SELECT * FROM app_users ORDER BY role DESC, email")]


@app.post("/api/admin/users")
def admin_add_user(body: AppUserIn, request: Request):
    _require_admin(request)
    email = (body.email or "").strip().lower()
    if not auth._valid_email(email):
        raise HTTPException(422, "Enter a valid email address.")
    is_domain = email.endswith("@" + auth.ALLOWED_DOMAIN)
    req_role = (body.role or "staff").strip()
    if email in SUPER_ADMINS:
        role = "super_admin"
    elif req_role == "guest" or not is_domain:
        role = "guest"          # external emails can only be guests
    elif req_role == "super_admin":
        role = "super_admin"
    else:
        role = "staff"
    # Cap the number of *promoted* Super Admins (the configured owner is exempt).
    if role == "super_admin" and email not in SUPER_ADMINS:
        others = [r["email"] for r in db.query("SELECT email FROM app_users WHERE role='super_admin'")
                  if r["email"] not in SUPER_ADMINS and r["email"] != email]
        if len(others) >= MAX_PROMOTED_SUPER_ADMINS:
            raise HTTPException(
                409,
                f"You can have at most {MAX_PROMOTED_SUPER_ADMINS} Super Admins beyond the owner. "
                "Demote one first.",
            )
    name = (body.name or "").strip() or None
    if db.query("SELECT email FROM app_users WHERE email=?", (email,)):
        db.execute("UPDATE app_users SET name=?, role=? WHERE email=?", (name, role, email))
    else:
        db.execute("INSERT INTO app_users(email,name,role) VALUES(?,?,?)", (email, name, role))
    return {"ok": True, "email": email}


@app.delete("/api/admin/users/{email}")
def admin_delete_user(email: str, request: Request):
    _require_admin(request)
    email = email.strip().lower()
    if email in SUPER_ADMINS:
        raise HTTPException(400, "Cannot remove a configured super admin.")
    db.execute("DELETE FROM app_users WHERE email=?", (email,))
    db.execute("DELETE FROM user_business_access WHERE email=?", (email,))
    db.execute("DELETE FROM user_project_access WHERE email=?", (email,))
    return {"ok": True}


@app.put("/api/admin/users/{email}/access")
def admin_set_access(email: str, body: AccessIn, request: Request):
    _require_admin(request)
    email = email.strip().lower()
    if not db.query("SELECT email FROM app_users WHERE email=?", (email,)):
        raise HTTPException(404, "User not found.")
    db.execute("DELETE FROM user_business_access WHERE email=?", (email,))
    db.execute("DELETE FROM user_project_access WHERE email=?", (email,))
    for b in dict.fromkeys(x for x in body.businesses if x):
        db.execute("INSERT OR IGNORE INTO user_business_access(email,business) VALUES(?,?)", (email, b))
    for p in dict.fromkeys(x for x in body.projects if x):
        db.execute("INSERT OR IGNORE INTO user_project_access(email,project) VALUES(?,?)", (email, p))
    return {"ok": True}


# ================= Catalog (businesses, projects, campaigns, staff, clients) =================
@app.get("/api/catalog")
def catalog_all(request: Request):
    biz = db.query("SELECT * FROM businesses ORDER BY COALESCE(tier, 9), name")
    names = {b["id"]: b["name"] for b in biz}
    for b in biz:
        b["parent_name"] = names.get(b.get("parent_id"))
    return {
        "businesses": biz,
        "projects": db.query("SELECT * FROM projects ORDER BY id"),
        "staff": db.query("SELECT * FROM staff ORDER BY id"),
        "clients": db.query("SELECT * FROM clients ORDER BY name"),
        "recurring": db.query("SELECT * FROM recurring_tasks ORDER BY id"),
        "contacts": db.query("SELECT * FROM client_contacts ORDER BY id"),
        "client_notes": db.query("SELECT * FROM client_notes ORDER BY id DESC"),
    }


class ClientNoteIn(BaseModel):
    client_id: int
    body: str


@app.post("/api/client-notes")
def add_client_note(n: ClientNoteIn, request: Request):
    _require_admin(request)
    body = (n.body or "").strip()
    if not body:
        raise HTTPException(422, "Note can't be empty.")
    if not db.query("SELECT 1 FROM clients WHERE id=?", (n.client_id,)):
        raise HTTPException(422, "A valid client is required.")
    author = auth.current_user(request) or "unknown"
    nid = db.execute(
        "INSERT INTO client_notes(client_id,author,body) VALUES(?,?,?)",
        (n.client_id, author, body),
    )
    return {"ok": True, "id": nid}


@app.delete("/api/client-notes/{nid}")
def delete_client_note(nid: int, request: Request):
    _require_admin(request)
    db.execute("DELETE FROM client_notes WHERE id=?", (nid,))
    return {"ok": True}


# ================= Google Drive + Calendar (per-user OAuth) =================
@app.get("/auth/google/start")
def google_start(request: Request):
    email = auth.current_user(request)
    if not email:
        return RedirectResponse("/login", status_code=302)
    if not google_int.configured():
        raise HTTPException(503, "Google integration is not configured yet.")
    state = auth._session.dumps({"email": email, "g": 1})
    return RedirectResponse(google_int.auth_url(state), status_code=302)


@app.get("/auth/google/callback")
def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        return RedirectResponse("/?google=denied", status_code=302)
    try:
        data = auth._session.loads(state, max_age=600)
        email = data.get("email")
    except Exception:
        return RedirectResponse("/?google=error", status_code=302)
    if not email or not code:
        return RedirectResponse("/?google=error", status_code=302)
    try:
        tok = google_int.exchange_code(code)
        google_int.save_tokens(email, tok)
    except Exception:
        return RedirectResponse("/?google=error", status_code=302)
    return RedirectResponse("/?google=connected", status_code=302)


@app.get("/api/google/status")
def google_status(request: Request):
    return google_int.status(auth.current_user(request))


@app.get("/api/google/calendar")
def google_calendar(request: Request):
    try:
        ev = google_int.calendar_events(auth.current_user(request))
    except Exception as e:
        raise HTTPException(502, f"Google Calendar error: {e}")
    if ev is None:
        raise HTTPException(400, "Google is not connected.")
    return ev


@app.get("/api/google/drive")
def google_drive(request: Request):
    try:
        files = google_int.drive_files(auth.current_user(request))
    except Exception as e:
        raise HTTPException(502, f"Google Drive error: {e}")
    if files is None:
        raise HTTPException(400, "Google is not connected.")
    return files


@app.post("/api/google/disconnect")
def google_disconnect(request: Request):
    google_int.disconnect(auth.current_user(request))
    return {"ok": True}


class ContactIn(BaseModel):
    client_id: Optional[int] = None
    name: Optional[str] = None
    email: Optional[str] = None
    title: Optional[str] = None
    phone: Optional[str] = None


@app.post("/api/contacts")
def create_contact(c: ContactIn, request: Request):
    _require_admin(request)
    if not c.client_id or not db.query("SELECT 1 FROM clients WHERE id=?", (c.client_id,)):
        raise HTTPException(422, "A valid client is required.")
    if not ((c.name or "").strip() or (c.email or "").strip()):
        raise HTTPException(422, "Give the contact a name or an email.")
    cid = db.execute(
        "INSERT INTO client_contacts(client_id,name,email,title,phone) VALUES(?,?,?,?,?)",
        (c.client_id, (c.name or "").strip() or None, (c.email or "").strip() or None,
         (c.title or "").strip() or None, (c.phone or "").strip() or None),
    )
    return {"ok": True, "id": cid}


@app.put("/api/contacts/{cid}")
def update_contact(cid: int, c: ContactIn, request: Request):
    _require_admin(request)
    if not db.query("SELECT 1 FROM client_contacts WHERE id=?", (cid,)):
        raise HTTPException(404, "Contact not found.")
    db.execute(
        "UPDATE client_contacts SET name=?,email=?,title=?,phone=? WHERE id=?",
        ((c.name or "").strip() or None, (c.email or "").strip() or None,
         (c.title or "").strip() or None, (c.phone or "").strip() or None, cid),
    )
    return {"ok": True}


@app.delete("/api/contacts/{cid}")
def delete_contact(cid: int, request: Request):
    _require_admin(request)
    db.execute("DELETE FROM client_contacts WHERE id=?", (cid,))
    return {"ok": True}


def _clamp_tier(t):
    """Businesses live in Tier 1–4 only; anything out of range is clamped so a
    business can never fall into a non-existent tier and vanish from the grid."""
    if t is None:
        return None
    try:
        return min(4, max(1, int(t)))
    except (TypeError, ValueError):
        return None


class BusinessIn(BaseModel):
    name: str
    initials: Optional[str] = None
    tier: Optional[int] = None
    owner: Optional[str] = None
    state: Optional[str] = None
    status: Optional[str] = "active"
    kind: Optional[str] = "business"
    parent_id: Optional[int] = None
    notes: Optional[str] = None


@app.post("/api/businesses")
def create_business(b: BusinessIn, request: Request):
    _require_admin(request)
    name = (b.name or "").strip()
    if not name:
        raise HTTPException(422, "Name is required.")
    if db.query("SELECT 1 FROM businesses WHERE name=?", (name,)):
        raise HTTPException(409, "A business with that name already exists.")
    initials = (b.initials or "").strip() or catalog._initials(name)
    bid = db.execute(
        "INSERT INTO businesses(name,initials,tier,owner,state,status,kind,parent_id,notes) VALUES(?,?,?,?,?,?,?,?,?)",
        (name, initials, _clamp_tier(b.tier), b.owner, b.state, b.status or "active", b.kind or "business", b.parent_id, b.notes),
    )
    return {"ok": True, "id": bid}


@app.put("/api/businesses/{bid}")
def update_business(bid: int, b: BusinessIn, request: Request):
    _require_admin(request)
    if not db.query("SELECT 1 FROM businesses WHERE id=?", (bid,)):
        raise HTTPException(404, "Business not found.")
    name = (b.name or "").strip()
    initials = (b.initials or "").strip() or catalog._initials(name)
    parent = b.parent_id if b.parent_id != bid else None  # no self-parenting
    db.execute(
        "UPDATE businesses SET name=?,initials=?,tier=?,owner=?,state=?,status=?,kind=?,parent_id=?,notes=? WHERE id=?",
        (name, initials, _clamp_tier(b.tier), b.owner, b.state, b.status, b.kind, parent, b.notes, bid),
    )
    return {"ok": True}


@app.delete("/api/businesses/{bid}")
def delete_business(bid: int, request: Request):
    _require_admin(request)
    db.execute("UPDATE businesses SET parent_id=NULL WHERE parent_id=?", (bid,))
    db.execute("DELETE FROM businesses WHERE id=?", (bid,))
    return {"ok": True}


class ProjectIn(BaseModel):
    name: str
    business: Optional[str] = None
    owner: Optional[str] = None
    status: Optional[str] = "active"
    state: Optional[str] = None
    badge: Optional[str] = None
    kind: Optional[str] = "project"     # 'project' | 'campaign'
    priority: Optional[str] = None
    next_action: Optional[str] = None
    due: Optional[str] = None


@app.post("/api/projects")
def create_project(p: ProjectIn, request: Request):
    _require_admin(request)
    if not (p.name or "").strip():
        raise HTTPException(422, "Name is required.")
    pid = db.execute(
        "INSERT INTO projects(name,business,owner,status,state,badge,kind,priority,next_action,due) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (p.name.strip(), p.business, p.owner, p.status or "active", p.state, p.badge,
         p.kind or "project", p.priority, p.next_action, p.due),
    )
    return {"ok": True, "id": pid}


@app.put("/api/projects/{pid}")
def update_project(pid: int, p: ProjectIn, request: Request):
    _require_admin(request)
    if not db.query("SELECT 1 FROM projects WHERE id=?", (pid,)):
        raise HTTPException(404, "Project not found.")
    db.execute(
        "UPDATE projects SET name=?,business=?,owner=?,status=?,state=?,badge=?,kind=?,priority=?,next_action=?,due=? WHERE id=?",
        (p.name.strip(), p.business, p.owner, p.status, p.state, p.badge, p.kind, p.priority, p.next_action, p.due, pid),
    )
    return {"ok": True}


@app.delete("/api/projects/{pid}")
def delete_project(pid: int, request: Request):
    _require_admin(request)
    db.execute("DELETE FROM projects WHERE id=?", (pid,))
    return {"ok": True}


class StaffIn(BaseModel):
    name: str
    role: Optional[str] = None
    focus: Optional[str] = None
    status: Optional[str] = "active"
    email: Optional[str] = None
    notes: Optional[str] = None


@app.post("/api/staff")
def create_staff(s: StaffIn, request: Request):
    _require_admin(request)
    if not (s.name or "").strip():
        raise HTTPException(422, "Name is required.")
    sid = db.execute(
        "INSERT INTO staff(name,role,focus,status,email,notes) VALUES(?,?,?,?,?,?)",
        (s.name.strip(), s.role, s.focus, s.status or "active", (s.email or "").strip().lower() or None, s.notes),
    )
    return {"ok": True, "id": sid}


@app.put("/api/staff/{sid}")
def update_staff(sid: int, s: StaffIn, request: Request):
    _require_admin(request)
    if not db.query("SELECT 1 FROM staff WHERE id=?", (sid,)):
        raise HTTPException(404, "Staff member not found.")
    db.execute(
        "UPDATE staff SET name=?,role=?,focus=?,status=?,email=?,notes=? WHERE id=?",
        (s.name.strip(), s.role, s.focus, s.status, (s.email or "").strip().lower() or None, s.notes, sid),
    )
    return {"ok": True}


@app.delete("/api/staff/{sid}")
def delete_staff(sid: int, request: Request):
    _require_admin(request)
    db.execute("DELETE FROM staff WHERE id=?", (sid,))
    return {"ok": True}


class ClientIn(BaseModel):
    name: str
    business: Optional[str] = None
    retainer_amount: Optional[float] = None
    cadence: Optional[str] = "monthly"
    status: Optional[str] = "active"
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    assignee: Optional[str] = None
    notes: Optional[str] = None


@app.post("/api/clients")
def create_client(c: ClientIn, request: Request):
    _require_admin(request)
    if not (c.name or "").strip():
        raise HTTPException(422, "Name is required.")
    cid = db.execute(
        "INSERT INTO clients(name,business,retainer_amount,cadence,status,contact_name,contact_email,assignee,notes) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (c.name.strip(), c.business, c.retainer_amount, c.cadence or "monthly", c.status or "active",
         c.contact_name, c.contact_email, (c.assignee or "").strip().lower() or None, c.notes),
    )
    return {"ok": True, "id": cid}


@app.put("/api/clients/{cid}")
def update_client(cid: int, c: ClientIn, request: Request):
    _require_admin(request)
    if not db.query("SELECT 1 FROM clients WHERE id=?", (cid,)):
        raise HTTPException(404, "Client not found.")
    db.execute(
        "UPDATE clients SET name=?,business=?,retainer_amount=?,cadence=?,status=?,contact_name=?,contact_email=?,assignee=?,notes=? WHERE id=?",
        (c.name.strip(), c.business, c.retainer_amount, c.cadence, c.status, c.contact_name, c.contact_email,
         (c.assignee or "").strip().lower() or None, c.notes, cid),
    )
    return {"ok": True}


@app.delete("/api/clients/{cid}")
def delete_client(cid: int, request: Request):
    _require_admin(request)
    db.execute("DELETE FROM clients WHERE id=?", (cid,))
    return {"ok": True}


class RecurringIn(BaseModel):
    name: str
    business: Optional[str] = None
    client_id: Optional[int] = None
    client_name: Optional[str] = None
    category: Optional[str] = None
    priority: Optional[str] = "Medium"
    estimate_min: Optional[int] = None
    assignee: Optional[str] = None
    day_of_month: Optional[int] = 1
    active: Optional[int] = 1


@app.post("/api/recurring")
def create_recurring(r: RecurringIn, request: Request):
    _require_admin(request)
    if not (r.name or "").strip():
        raise HTTPException(422, "Name is required.")
    rid = db.execute(
        "INSERT INTO recurring_tasks(name,business,client_id,client_name,category,priority,estimate_min,assignee,day_of_month,active) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (r.name.strip(), r.business, r.client_id, r.client_name, r.category, r.priority or "Medium",
         r.estimate_min, (r.assignee or "").strip().lower() or None, r.day_of_month or 1, 1 if r.active else 0),
    )
    return {"ok": True, "id": rid}


@app.put("/api/recurring/{rid}")
def update_recurring(rid: int, r: RecurringIn, request: Request):
    _require_admin(request)
    if not db.query("SELECT 1 FROM recurring_tasks WHERE id=?", (rid,)):
        raise HTTPException(404, "Recurring task not found.")
    db.execute(
        "UPDATE recurring_tasks SET name=?,business=?,client_id=?,client_name=?,category=?,priority=?,estimate_min=?,assignee=?,day_of_month=?,active=? WHERE id=?",
        (r.name.strip(), r.business, r.client_id, r.client_name, r.category, r.priority, r.estimate_min,
         (r.assignee or "").strip().lower() or None, r.day_of_month or 1, 1 if r.active else 0, rid),
    )
    return {"ok": True}


@app.delete("/api/recurring/{rid}")
def delete_recurring(rid: int, request: Request):
    _require_admin(request)
    db.execute("DELETE FROM recurring_tasks WHERE id=?", (rid,))
    return {"ok": True}


@app.post("/api/recurring/generate")
def recurring_generate(request: Request):
    _require_admin(request)
    created = catalog.generate_recurring()
    if created:
        _sync_tasks_to_repo()
    return {"ok": True, "created": created}


# ================= Sidebar pins (per-user, max 5) =================
MAX_PINS = 5


class PinIn(BaseModel):
    kind: str    # business | project | client | campaign
    ref: str     # the item's name


@app.get("/api/pins")
def get_pins(request: Request):
    email = (auth.current_user(request) or "").strip().lower()
    return db.query("SELECT kind, ref FROM user_pins WHERE email=? ORDER BY created_at", (email,))


@app.post("/api/pins")
def toggle_pin(body: PinIn, request: Request):
    email = (auth.current_user(request) or "").strip().lower()
    kind = (body.kind or "").strip().lower()
    ref = (body.ref or "").strip()
    if kind not in ("business", "project", "client", "campaign") or not ref:
        raise HTTPException(422, "kind must be business/project/client/campaign and ref is required.")
    existing = db.query("SELECT 1 FROM user_pins WHERE email=? AND kind=? AND ref=?", (email, kind, ref))
    if existing:
        db.execute("DELETE FROM user_pins WHERE email=? AND kind=? AND ref=?", (email, kind, ref))
        return {"ok": True, "pinned": False}
    count = db.query("SELECT COUNT(*) c FROM user_pins WHERE email=?", (email,))[0]["c"]
    if count >= MAX_PINS:
        raise HTTPException(409, f"You can pin up to {MAX_PINS} items. Unpin one first.")
    db.execute("INSERT OR IGNORE INTO user_pins(email,kind,ref) VALUES(?,?,?)", (email, kind, ref))
    return {"ok": True, "pinned": True}


# ================= Feedback (bugs / suggestions) via email =================
FEEDBACK_TO = os.environ.get("SB_FEEDBACK_TO", "").strip() or (sorted(SUPER_ADMINS)[0] if SUPER_ADMINS else "")


FEEDBACK_STATUSES = ("open", "in_progress", "resolved", "wont_fix")
FEEDBACK_LABELS = {"open": "Open", "in_progress": "In Progress", "resolved": "Resolved", "wont_fix": "Won't Fix"}


class FeedbackIn(BaseModel):
    kind: str = "bug"     # bug | suggestion
    message: str
    page: Optional[str] = None


class FeedbackUpdate(BaseModel):
    status: Optional[str] = None
    admin_note: Optional[str] = None
    notify: Optional[bool] = False


@app.post("/api/feedback")
def submit_feedback(body: FeedbackIn, request: Request):
    email = auth.current_user(request) or "unknown"
    msg = (body.message or "").strip()
    if len(msg) < 3:
        raise HTTPException(422, "Please add a bit more detail.")
    kind = "suggestion" if (body.kind or "").lower().startswith("sugg") else "bug"
    fid = db.execute(
        "INSERT INTO feedback(email,kind,message,page,status) VALUES(?,?,?,?,'open')",
        (email, kind, msg, body.page),
    )
    sent = False
    if FEEDBACK_TO:
        try:
            sent = auth.send_email(
                FEEDBACK_TO, f"[Single Brain] {kind} from {email} (#{fid})",
                f"{kind} report\nFrom: {email}\nPage: {body.page or '-'}\n\n{msg}",
            )
        except Exception:
            pass
    return {"ok": True, "id": fid, "sent": bool(sent)}


@app.get("/api/feedback")
def list_feedback(request: Request):
    email = (auth.current_user(request) or "").strip().lower()
    if _is_admin(email):
        return db.query(
            "SELECT * FROM feedback ORDER BY "
            "CASE status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END, id DESC"
        )
    return db.query("SELECT * FROM feedback WHERE lower(email)=? ORDER BY id DESC", (email,))


@app.put("/api/feedback/{fid}")
def update_feedback(fid: int, body: FeedbackUpdate, request: Request):
    _require_admin(request)
    rows = db.query("SELECT * FROM feedback WHERE id=?", (fid,))
    if not rows:
        raise HTTPException(404, "Ticket not found.")
    t = rows[0]
    status = body.status if body.status in FEEDBACK_STATUSES else (t.get("status") or "open")
    note = body.admin_note if body.admin_note is not None else t.get("admin_note")
    db.execute(
        "UPDATE feedback SET status=?, admin_note=?, updated_at=datetime('now') WHERE id=?",
        (status, note, fid),
    )
    notified = False
    if body.notify and t.get("email") and "@" in (t.get("email") or ""):
        subject = f"[Single Brain] Update on your feedback (#{fid})"
        text = (
            f"Your {t.get('kind') or 'feedback'} report has been updated.\n\n"
            f"Status: {FEEDBACK_LABELS.get(status, status)}\n"
            + (f"\nNote from the team:\n{note}\n" if note else "")
            + f"\n— Original message —\n{t.get('message')}"
        )
        try:
            notified = auth.send_email(t["email"], subject, text)
        except Exception:
            pass
    return {"ok": True, "notified": bool(notified)}
