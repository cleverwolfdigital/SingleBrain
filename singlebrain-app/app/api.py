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
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel, Field

from . import db, seed, auth, repo, config, catalog, google_int, passkeys

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


def _can_assign(email):
    """May hand work to someone else. Admins always can; other staff need the
    can_assign flag — a deliberately narrow grant, so a lead can delegate and get
    completion notices WITHOUT inheriting user management and every business."""
    if _is_admin(email):
        return True
    rows = db.query("SELECT can_assign FROM app_users WHERE email=?", ((email or "").strip().lower(),))
    return bool(rows and rows[0].get("can_assign"))


def _require_can_assign(request):
    email = auth.current_user(request)
    if not _can_assign(email):
        raise HTTPException(403, "You're not allowed to assign tasks to other people.")
    return email


# ---------- Restricted businesses ----------
# A business can be locked so that being a Super Admin is NOT enough to see it —
# only people granted it explicitly, plus the configured owner in SB_SUPER_ADMINS
# (who must keep access or nobody could administer the lock). Everything under a
# locked business inherits the lock: its projects/campaigns, clients, tasks,
# invoices and files. This is the one place "admins see everything" does not hold.
def _is_owner(email):
    """A CONFIGURED super admin (SB_SUPER_ADMINS), not merely a promoted one."""
    return (email or "").strip().lower() in SUPER_ADMINS


def _restricted_business_names():
    return {r["name"] for r in db.query("SELECT name FROM businesses WHERE COALESCE(restricted,0)=1")}


def _granted_business_names(email):
    return {r["business"] for r in
            db.query("SELECT business FROM user_business_access WHERE email=?", ((email or "").strip().lower(),))}


def _hidden_businesses(email):
    """Restricted businesses this person may not see. Empty for the owner."""
    if _is_owner(email):
        return set()
    restricted = _restricted_business_names()
    if not restricted:
        return set()
    return restricted - _granted_business_names(email)


def _can_see_business(email, name):
    """Single source of truth for 'may this person look at this business?'."""
    if not name:
        return True
    if name in _hidden_businesses(email):
        return False
    if _is_admin(email):
        return True
    biz, _ = _access_lists(email)
    return biz is None or name in set(biz or [])


def _require_business_visible(email, name):
    if name and not _can_see_business(email, name):
        raise HTTPException(403, "You don't have access to that business.")


def _access_lists(email):
    """(businesses, projects) a user may view. (None, None) for admins = everything.

    An admin who is not the owner still loses any RESTRICTED business they weren't
    granted — in that case they get explicit lists instead of None, so every caller
    that already understood "a list means these only" keeps working unchanged."""
    hidden = _hidden_businesses(email)
    if _is_admin(email):
        if not hidden:
            return None, None
        biz = [r["name"] for r in db.query("SELECT name FROM businesses") if r["name"] not in hidden]
        proj = [r["name"] for r in db.query("SELECT name, business FROM projects")
                if (r["business"] or "") not in hidden]
        return biz, proj
    biz = [r["business"] for r in db.query("SELECT business FROM user_business_access WHERE email=?", (email,))
           if r["business"] not in hidden]
    proj = [r["project"] for r in db.query("SELECT project FROM user_project_access WHERE email=?", (email,))]
    if hidden:
        under_hidden = {r["name"] for r in db.query("SELECT name, business FROM projects")
                        if (r["business"] or "") in hidden}
        proj = [p for p in proj if p not in under_hidden]
    return biz, proj


def _require_admin(request):
    email = auth.current_user(request)
    if not _is_admin(email):
        raise HTTPException(403, "Super Admin access required.")
    return email


def _visible_tasks(email, tasks):
    """Filter a task list to what a viewer may see: their assigned tasks (they may be
    one of several assignees, or a member of a team the task went to) plus any task
    tied to a business they've been granted. Admins see everything — except work under
    a RESTRICTED business, which is hidden even if the task is assigned to them. Being
    on the task is not a way around the lock; the fix is to grant them the business."""
    hidden = _hidden_businesses(email)
    if hidden:
        tasks = [t for t in tasks if (t.get("business") or "") not in hidden]
    if _is_admin(email):
        return tasks
    biz, _ = _access_lists(email)
    allowed = set(biz or [])
    me = (email or "").strip().lower()
    mine_ids = {r["task_id"] for r in
                db.query("SELECT task_id FROM task_assignees WHERE lower(email)=?", (me,))}
    return [t for t in tasks
            if t.get("id") in mine_ids
            or (t.get("assignee") or "").strip().lower() == me
            or (t.get("business") or "") in allowed]


# ---------- Team / multi-assignee helpers ----------
def _team_member_emails(team_id):
    if not team_id:
        return []
    return [(r["email"] or "").strip().lower()
            for r in db.query("SELECT email FROM team_members WHERE team_id=?", (team_id,))
            if (r["email"] or "").strip()]


def _clean_emails(emails):
    out, seen = [], set()
    for e in emails or []:
        e = (e or "").strip().lower()
        if e and "@" in e and e not in seen:
            seen.add(e)
            out.append(e)
    return out


def _set_task_assignees(task_id, emails, team_id=None):
    """Replace a task's assignee set. If a team is given, its current members are folded
    in (snapshot). tasks.assignee holds the first person for backward-compatible display
    and legacy filters; tasks.team_id records the team. Returns the final email list."""
    people = _clean_emails(emails)
    seen = set(people)
    for e in _team_member_emails(team_id):
        if e not in seen:
            seen.add(e)
            people.append(e)
    db.execute("DELETE FROM task_assignees WHERE task_id=?", (task_id,))
    for e in people:
        db.execute("INSERT OR IGNORE INTO task_assignees(task_id,email) VALUES(?,?)", (task_id, e))
    primary = people[0] if people else None
    db.execute("UPDATE tasks SET assignee=?, team_id=? WHERE id=?",
               (primary, team_id or None, task_id))
    return people


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
    catalog.generate_invoices()
    _sweep_overdue_invoices()
    auth.init_auth_db()
    google_int.init_db()
    passkeys.init_passkey_db()   # must follow init_auth_db — it extends auth_users


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
        "can_assign": _can_assign(email),   # may hand work to other people
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
    amap = {}
    for r in db.query("SELECT task_id, email FROM task_assignees"):
        amap.setdefault(r["task_id"], []).append(r["email"])
    team_names = {r["id"]: r["name"] for r in db.query("SELECT id, name FROM teams")}
    for t in tasks:
        t["dependencies"] = by_task.get(t["id"], [])
        # Full assignee set (falls back to the legacy single assignee for old rows).
        t["assignees"] = amap.get(t["id"]) or ([t["assignee"]] if t.get("assignee") else [])
        t["team"] = team_names.get(t.get("team_id")) if t.get("team_id") else None
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
    assignee: Optional[str] = None            # legacy single assignee (still honored)
    assignees: List[str] = Field(default_factory=list)  # assign to several people at once
    team_id: Optional[int] = None             # assign to a whole team (its members are added)
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
    # Admins (and can-assign staff) may assign to anyone / any team; other staff-created
    # tasks belong to the creator so they stay visible to them under access filtering.
    current = auth.current_user(request)
    # Filing work under a locked business you can't see would create a task that
    # immediately vanishes from your own list — refuse it plainly instead.
    _require_business_visible(current, t.business)
    if _can_assign(current):
        people = _clean_emails(([t.assignee] if t.assignee else []) + list(t.assignees or []))
        team_id = t.team_id or None
    else:
        people = _clean_emails([current])
        team_id = None
    # Remember WHO assigned it — that's who hears about it when it's completed.
    tid = db.execute(
        "INSERT INTO tasks(business,name,category,priority,due,notes,status,estimate_min,actual_sec,assigned_by,client) "
        "VALUES(?,?,?,?,?,?,'open',?,0,?,?)",
        (t.business, name, t.category, t.priority, t.due, t.notes, est,
         (current or "").strip().lower() or None, (t.client or "").strip() or None),
    )
    final = _set_task_assignees(tid, people, team_id)
    for dep in t.dependencies:
        db.execute("INSERT OR IGNORE INTO task_dependencies(task_id,depends_on) VALUES(?,?)", (tid, dep))
    # Tell everyone newly put on the task (never the person creating it).
    notified = _notify_task_assigned(_get_task(tid), current, final, [])
    pushed = _sync_tasks_to_repo()
    return {"id": tid, "ok": True, "pushed": pushed, "notified": notified, "assignees": final}


def _can_edit_task(email, t):
    """Who may change a task: admins, whoever handed it out, and anyone on it.
    Editing is deliberately open to the people doing the work — they're the ones who
    notice a wrong due date — but it stays off-limits to bystanders."""
    if _is_admin(email):
        return True
    me = (email or "").strip().lower()
    if not me:
        return False
    return me == (t.get("assigned_by") or "").strip().lower() or me in _task_audience(t["id"])


def _require_task_edit(request, t):
    email = auth.current_user(request)
    if not _can_edit_task(email, t):
        raise HTTPException(403, "You can only change tasks you assigned or are on.")
    return email


@app.put("/api/tasks/{task_id}")
def update_task(task_id: int, t: TaskIn, request: Request):
    """Edit an existing task in place. Same shape as Quick Add — the dashboard reuses the
    one form for both — so this is a full replace of the editable fields, not a patch."""
    before = _get_task(task_id)
    actor = _require_task_edit(request, before)
    name = (t.name or "").strip()
    if len(name) < 5:
        raise HTTPException(422, "Task name must be at least 5 characters.")
    if t.priority not in ("High", "Medium", "Low"):
        raise HTTPException(422, "Priority must be High, Medium, or Low.")
    est = t.estimate_min if (t.estimate_min and t.estimate_min > 0) else None
    _require_business_visible(actor, before.get("business"))
    _require_business_visible(actor, t.business)
    db.execute(
        "UPDATE tasks SET name=?, business=?, category=?, priority=?, due=?, notes=?, "
        "estimate_min=?, client=? WHERE id=?",
        (name, t.business, t.category, t.priority, t.due, t.notes, est,
         (t.client or "").strip() or None, task_id),
    )
    # Dependencies are a full replace too (a task can't depend on itself).
    db.execute("DELETE FROM task_dependencies WHERE task_id=?", (task_id,))
    for dep in t.dependencies:
        if int(dep) != task_id:
            db.execute("INSERT OR IGNORE INTO task_dependencies(task_id,depends_on) VALUES(?,?)", (task_id, dep))
    # Only people allowed to delegate can change WHO is on it; for everyone else the
    # existing assignee set is left exactly as it was.
    people_before = _task_audience(task_id)
    added = []
    if _can_assign(actor):
        people = _clean_emails(([t.assignee] if t.assignee else []) + list(t.assignees or []))
        final = _set_task_assignees(task_id, people, t.team_id or None)
        added = [e for e in final if e not in people_before]
    after = _get_task(task_id)
    notified = _notify_task_assigned(after, actor, added, people_before)
    notified += _notify_task_updated(after, actor, _task_changes(before, after), set(added))
    pushed = _sync_tasks_to_repo()
    return {"ok": True, "pushed": pushed, "notified": notified,
            "task": _apply_timing(after), "assignees": sorted(_task_audience(task_id))}


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int, request: Request):
    _require_task_edit(request, _get_task(task_id))
    db.execute("DELETE FROM task_dependencies WHERE task_id=? OR depends_on=?", (task_id, task_id))
    db.execute("DELETE FROM task_assignees WHERE task_id=?", (task_id,))
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
def complete_task(task_id: int, request: Request):
    t = _get_task(task_id)
    add = max(0, int(time.time()) - int(t["started_at"])) if t.get("started_at") else 0
    db.execute(
        "UPDATE tasks SET actual_sec=COALESCE(actual_sec,0)+?, started_at=NULL, "
        "completed_at=?, status='done' WHERE id=?",
        (add, int(time.time()), task_id),
    )
    done = _get_task(task_id)
    # Tell the assigner (+ admins) it's finished — never the person who just did it.
    notified = _notify_task_done(done, auth.current_user(request))
    pushed = _sync_tasks_to_repo()
    return {"ok": True, "pushed": pushed, "notified": notified, "task": _apply_timing(done)}


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

# What the dashboard can DO, so Brain Chat can also answer "how do I ...?" questions
# about the app itself. Keep this in sync with the tutorial, guided tour, and patch notes.
DASHBOARD_FEATURES = (
    "=== What the Single Brain dashboard can do (so you can guide users on HOW, not just WHAT) ===\n"
    "- Tasks: Quick Add creates tasks (business, category, priority, due, estimate, assignees, client, "
    "dependencies). A task can be assigned to a whole TEAM, to several PEOPLE at once, or both (use the "
    "'Assign to' picker). Each task has a live timer (start/pause/complete) and syncs to Tasks.md in the "
    "repo. 'My Tasks' shows every task the current user is on (as an individual or via a team).\n"
    "- Editing a task: click the pencil (edit) button on any task row — or click a task inside a "
    "business/project detail view — to reopen the same form and change name, business, client, category, "
    "priority, due date, estimate, notes, dependencies, and (if you may assign) who's on it. Allowed for "
    "Super Admins, whoever assigned the task, and anyone on it; the same rule governs deleting a task. "
    "Editing an already-overdue task doesn't force its due date forward.\n"
    "- Teams: reusable named groups of people (e.g. Design, Dev), managed in Super Admin (New team -> name "
    "+ members). Assigning a task to a team includes all its current members, who each see the task and are "
    "emailed. Deleting a team keeps existing tasks' people, just drops the team label.\n"
    "- Businesses/Projects/Campaigns/Clients: browsed as cards; click one for a detail overlay with its "
    "tasks. Admins can add/edit/delete them and set clients' recurring monthly tasks. Sub-businesses roll "
    "up under a parent. Pin up to 5 items to the sidebar with the star.\n"
    "- Campaigns (sidebar; appears for anyone who has campaigns or may create them): advertising run FOR a "
    "client over a FLIGHT (start_on..end_on) against a BUDGET. Each card shows two bars — how much of the "
    "flight has elapsed vs how much budget is spent — plus pacing (spend share / elapsed share; ~100 is on "
    "plan, >115 ahead, <85 behind). A campaign holds LINE ITEMS (separate flights, creatives or markets, "
    "each with their own dates + budget) and DELIVERY logged per period (impressions, reach, clicks, "
    "spend). CTR, CPM and CPC are always CALCULATED from those numbers, never stored. Reach is reported as "
    "the best single period, never summed, because the same listener is reached repeatedly. 'Client report' "
    "builds a client-ready document — headline numbers, delivery by period, line items, work completed "
    "during the flight, what's next — printable to PDF or copyable as plain text for an email. Campaigns "
    "also show inside each client. You may manage campaigns for any business you've been GRANTED — Super "
    "Admin is not required.\n"
    "- Locked (restricted) businesses: a business can be set to Access = Locked when editing it. Then ONLY "
    "people explicitly granted that business can see it — and unlike every other rule in this app, being a "
    "Super Admin is NOT enough. Its clients, campaigns, tasks, invoices and files all inherit the lock, "
    "filtered server-side rather than merely hidden on screen, and a locked business's work is hidden even "
    "from someone it is assigned to (grant them the business instead). The configured account owner always "
    "keeps access so a business can never be locked away from everybody.\n"
    "- Billing (Super Admins only — it doesn't appear in the sidebar for anyone else): tracks whether "
    "each client's retainer got invoiced and paid. On the 1st of every month a DRAFT invoice is created "
    "automatically for each active client with a monthly retainer, dated due on the 15th. The Billing "
    "view shows Billed / Collected / Outstanding / Overdue for a chosen month (or all time), and a table "
    "where each row moves draft -> sent -> paid with one click; 'Mark paid' stamps today's date. "
    "'+ New invoice' adds a one-off (client, period, amount, due date, reference, notes) and the pencil "
    "edits any invoice — including the invoice number/reference from whatever tool actually issues it. "
    "Each client's detail overlay has an Invoices card with the same rows. Single Brain does NOT create "
    "or send the invoice document to the client — it tracks the status; the invoice itself still goes "
    "out from wherever it goes today. Invoices that are marked sent and past their due date show as "
    "OVERDUE, and the staffer who owns that client (plus admins) gets a reminder email — at most one "
    "per invoice per day, and never to the client.\n"
    "- Files & attachments: any business, campaign, project, or task can have files attached (reference, "
    "review, retrieval, storage). Open the item and click 'Files', or use the paperclip on a task row. "
    "The first time, click 'Connect Your Drive' — a popup authorizes Google, then closes itself. Users "
    "can upload files (or drag-drop), add a link, and share each file by link or with a specific person "
    "(view or edit). Each file can be removed from the item (kept in Drive) or deleted from Drive "
    "entirely (moved to Drive trash). A paperclip badge on each card shows the attachment count.\n"
    "- The Overview carries a Team Calendar panel: it opens on the user's OWN day, and the dropdown "
    "switches to any teammate. Day/Week toggle, a 'New meeting' button so they can book without leaving "
    "the dashboard, and a chevron that collapses it (remembered per browser). 'Full view' — or the "
    "sidebar's Team Calendar — opens the same thing full-size.\n"
    "- Team Calendar: pick a teammate from a dropdown to see their calendar (Day/Week). The teammate "
    "must first share their Google Calendar with the viewer ('See all event details'); otherwise the "
    "view explains how. 'New meeting' creates a Google Calendar event on the viewer's calendar and "
    "emails invites to the teammates/clients added as guests. Requires full Google Calendar access "
    "(one-time reconnect).\n"
    "- Productivity reports roll up completed work + tracked time by day/week/month/quarter/year.\n"
    "- Notifications: when a task is EDITED, everyone on it plus the assigner get an email listing each "
    "changed field as 'old -> new' (never the editor; anyone just added gets the fuller 'assigned to you' "
    "email instead). When a task is ASSIGNED, everyone newly put on it is emailed (never the assigner). "
    "When a task is completed, the person who ASSIGNED it (plus admins) gets an email — never the person "
    "who completed it. When a file is added to a task, EVERYONE on the task plus the assigner are emailed "
    "and the file is auto-shared with them in Drive. Files on a business/project/campaign notify nobody.\n"
    "- Assigning: Super Admins can assign work; other staff need the 'Can assign tasks' permission "
    "(Super Admin -> edit a person). Guests can never assign. A task can go to a team and/or several people; "
    "whoever assigns is who gets the completion email.\n"
    "- Feedback: a bug/suggestion reporter reached from the 'Feedback' tab on the right edge of every "
    "screen (above the chat wolf). It opens the Feedback view; 'New feedback' files a report. It takes "
    "a SCREENSHOT (paste/drop/attach) and auto-captures the page, browser, OS and screen size; each ticket "
    "has a status (open/in progress/resolved) the submitter can track, and the team is emailed with the image.\n"
    "- Daily Journal (morning focus + end-of-day log). Super Admins manage team roles, per-business "
    "access, and guest invites. Feedback tab files bugs/ideas. A Tutorial tab + guided tour explain it "
    "all, and a 'What's new' card (with prev/next arrows) shows release notes.\n"
    "- Sign-in: magic link to their email + a 6-digit authenticator (TOTP) code, with "
    "'remember this device for 90 days'. Users can also add a PASSKEY (Face ID / Touch ID / "
    "Windows Hello) from the initials/avatar menu top-right — after that, one touch on the login "
    "page replaces BOTH the magic link and the code. The authenticator app remains the backup, so "
    "removing every passkey never locks anyone out.\n"
    "Access is limited to @cleverwolfdigital.com users (plus admin-invited guests).\n"
)


class ChatIn(BaseModel):
    message: str
    history: List[dict] = Field(default_factory=list)


def _redact_hidden(text, viewer):
    """Master_Dashboard.md lists the whole portfolio, so feeding it to Brain Chat
    verbatim would route around a locked business. Drop any LINE naming one the
    viewer may not see. This is line-level redaction of a prose file, not a
    guarantee — the authoritative lock is the DB filtering everywhere else."""
    hidden = _hidden_businesses(viewer)
    if not hidden or not text:
        return text
    low = [h.lower() for h in hidden if h]
    return "\n".join(ln for ln in text.splitlines()
                     if not any(h in ln.lower() for h in low))


def _state_context(viewer=None):
    """Build Grok's context from the SOURCE OF TRUTH (Master_Dashboard.md + project files)
    plus the live task list — not the stale DB seed. Tasks are scoped to the viewer."""
    parts = []
    for label, rel in [
        ("Master_Dashboard.md (SINGLE SOURCE OF TRUTH — portfolio by tier, staff+emails, projects, rotation, backlog)",
         "Personal/Notes/Master_Dashboard.md"),
    ]:
        try:
            text = (config.BRAIN_REPO / rel).read_text(encoding="utf-8")
            text = _redact_hidden(text, viewer)
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
        "Tier 1–4), staff + emails, projects, daily rotation, and automation backlog. "
        "Base every answer on this — never claim a business is missing if it appears below. "
        "When asked to add or change a business, staff member, project, or task, state exactly what "
        "you'd change and ask the user to confirm (live write-actions are rolling out; for now you "
        "advise, summarize, and draft). You can also explain how to USE the dashboard itself — the "
        "capabilities are listed below; walk users through steps (e.g. attaching a file, connecting "
        "Drive, starting a timer) when they ask.\n\n"
        + DASHBOARD_FEATURES + "\n" + _state_context(auth.current_user(request))
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
    assignee: Optional[str] = None                       # legacy single assignee
    assignees: List[str] = Field(default_factory=list)   # full set of people
    team_id: Optional[int] = None                        # assign to a whole team


@app.post("/api/tasks/{task_id}/assign")
def assign_task(task_id: int, body: AssignIn, request: Request):
    actor = _require_can_assign(request)
    _get_task(task_id)
    before = set(_clean_emails(
        r["email"] for r in db.query("SELECT email FROM task_assignees WHERE task_id=?", (task_id,))))
    people = _clean_emails(([body.assignee] if body.assignee else []) + list(body.assignees or []))
    final = _set_task_assignees(task_id, people, body.team_id or None)
    # Whoever assigns it becomes the person notified when it's completed.
    db.execute("UPDATE tasks SET assigned_by=? WHERE id=?",
               ((actor or "").strip().lower() or None, task_id))
    # Only email people who are NEW on the task, and never the assigner.
    added = [e for e in final if e not in before]
    notified = _notify_task_assigned(_get_task(task_id), actor, added, before)
    return {"ok": True, "assignee": (final[0] if final else None),
            "assignees": final, "notified": notified}


# ---------- Super Admin: users + access ----------
class AppUserIn(BaseModel):
    email: str
    name: Optional[str] = None
    role: Optional[str] = "staff"
    can_assign: Optional[bool] = None   # let a non-admin delegate work


class AccessIn(BaseModel):
    businesses: List[str] = Field(default_factory=list)
    projects: List[str] = Field(default_factory=list)


def _user_summary(u):
    email = u["email"]
    biz = [r["business"] for r in db.query("SELECT business FROM user_business_access WHERE email=?", (email,))]
    proj = [r["project"] for r in db.query("SELECT project FROM user_project_access WHERE email=?", (email,))]
    cnt = db.query(
        "SELECT COUNT(*) c FROM tasks t WHERE t.status!='done' AND ("
        "lower(t.assignee)=? OR EXISTS("
        "SELECT 1 FROM task_assignees ta WHERE ta.task_id=t.id AND lower(ta.email)=?))",
        (email.lower(), email.lower()),
    )[0]["c"]
    return {
        "email": email,
        "name": u.get("name"),
        "role": u.get("role") or "staff",
        "is_configured_admin": email.lower() in SUPER_ADMINS,
        "can_assign": _can_assign(email),
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
    # Guests are outside parties — they never get to hand work to staff.
    can_assign = 0 if role == "guest" else (1 if body.can_assign else 0)
    if db.query("SELECT email FROM app_users WHERE email=?", (email,)):
        db.execute("UPDATE app_users SET name=?, role=?, can_assign=? WHERE email=?", (name, role, can_assign, email))
    else:
        db.execute("INSERT INTO app_users(email,name,role,can_assign) VALUES(?,?,?,?)", (email, name, role, can_assign))
    return {"ok": True, "email": email, "can_assign": bool(can_assign)}


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
    viewer = auth.current_user(request)
    # A locked business is filtered out HERE, server-side. Hiding it in the frontend
    # would leave the whole record one /api/catalog call away from anyone signed in.
    hidden = _hidden_businesses(viewer)
    biz = db.query("SELECT * FROM businesses ORDER BY COALESCE(tier, 9), name")
    names = {b["id"]: b["name"] for b in biz}
    for b in biz:
        b["parent_name"] = names.get(b.get("parent_id"))
    projects = db.query("SELECT * FROM projects ORDER BY id")
    clients = db.query("SELECT * FROM clients ORDER BY name")
    recurring = db.query("SELECT * FROM recurring_tasks ORDER BY id")
    contacts = db.query("SELECT * FROM client_contacts ORDER BY id")
    notes = db.query("SELECT * FROM client_notes ORDER BY id DESC")
    if hidden:
        hidden_ids = {b["id"] for b in biz if b["name"] in hidden}
        biz = [b for b in biz if b["name"] not in hidden
               and b.get("parent_id") not in hidden_ids]
        projects = [p for p in projects if (p.get("business") or "") not in hidden]
        gone = {c["id"] for c in clients if (c.get("business") or "") in hidden}
        clients = [c for c in clients if c["id"] not in gone]
        recurring = [r for r in recurring if r.get("client_id") not in gone
                     and (r.get("business") or "") not in hidden]
        contacts = [c for c in contacts if c.get("client_id") not in gone]
        notes = [n for n in notes if n.get("client_id") not in gone]
    return {
        "businesses": biz,
        "projects": projects,
        "staff": db.query("SELECT * FROM staff ORDER BY id"),
        "clients": clients,
        "recurring": recurring,
        "contacts": contacts,
        "client_notes": notes,
        "teams": _teams_with_members(),
        # Billing is money — admins only. Everyone else gets an empty list and the
        # frontend hides the Billing view for them.
        "invoices": _invoices_for(viewer),
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
def google_start(request: Request, popup: int = 0):
    email = auth.current_user(request)
    if not email:
        return RedirectResponse("/login", status_code=302)
    if not google_int.configured():
        raise HTTPException(503, "Google integration is not configured yet.")
    state = auth._session.dumps({"email": email, "g": 1, "popup": 1 if popup else 0})
    return RedirectResponse(google_int.auth_url(state), status_code=302)


def _google_popup_close(status):
    """A tiny self-closing page for the popup OAuth flow: it tells the opener
    window the outcome via postMessage, then closes itself."""
    heading = "Google Drive connected ✓" if status == "connected" else "Connection " + status
    html = (
        '<!doctype html><html><head><meta charset="utf-8"><title>Google Drive</title></head>'
        '<body style="font-family:system-ui,sans-serif;background:#07121d;color:#edf4fa;'
        'display:grid;place-items:center;height:100vh;margin:0">'
        '<div style="text-align:center">'
        f'<p style="font-size:15px;margin:0 0 6px">{heading}</p>'
        '<p style="opacity:.55;font-size:12px;margin:0">You can close this window.</p></div>'
        '<script>'
        'try{if(window.opener)window.opener.postMessage({type:"google-' + status + '"},'
        'window.location.origin);}catch(e){}'
        'setTimeout(function(){window.close();},700);'
        '</script></body></html>'
    )
    return HTMLResponse(html)


@app.get("/auth/google/callback")
def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    popup = False
    email = None
    try:
        data = auth._session.loads(state, max_age=600)
        email = data.get("email")
        popup = bool(data.get("popup"))
    except Exception:
        pass
    if error:
        status = "denied" if error == "access_denied" else "error"
        return _google_popup_close(status) if popup else RedirectResponse(f"/?google={status}", status_code=302)
    if not email or not code:
        return _google_popup_close("error") if popup else RedirectResponse("/?google=error", status_code=302)
    try:
        tok = google_int.exchange_code(code)
        google_int.save_tokens(email, tok)
    except Exception:
        return _google_popup_close("error") if popup else RedirectResponse("/?google=error", status_code=302)
    return _google_popup_close("connected") if popup else RedirectResponse("/?google=connected", status_code=302)


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


MAX_UPLOAD_BYTES = int(os.environ.get("SB_MAX_UPLOAD_MB", "25") or "25") * 1024 * 1024


@app.post("/api/google/drive/upload")
async def google_drive_upload(request: Request, file: UploadFile = File(...)):
    email = auth.current_user(request)
    content = await file.read()
    if not content:
        raise HTTPException(422, "The file is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File is too large ({MAX_UPLOAD_BYTES // (1024 * 1024)} MB max).")
    try:
        res = google_int.upload_file(email, file.filename, content, file.content_type)
    except Exception as e:
        raise HTTPException(502, f"Google Drive upload error: {e}")
    if res is None:
        raise HTTPException(400, "Google is not connected.")
    return res


class ShareIn(BaseModel):
    type: str = "anyone"          # anyone | user
    email: Optional[str] = None
    role: str = "reader"          # reader | writer


@app.post("/api/google/drive/{file_id}/share")
def google_drive_share(file_id: str, body: ShareIn, request: Request):
    email = auth.current_user(request)
    try:
        res = google_int.share_file(email, file_id, body.type, body.email, body.role)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(502, f"Google Drive share error: {e}")
    if res is None:
        raise HTTPException(400, "Google is not connected.")
    return res


@app.post("/api/google/disconnect")
def google_disconnect(request: Request):
    google_int.disconnect(auth.current_user(request))
    return {"ok": True}


# ================= Passkeys (biometric sign-in) =================
# These live under /api (NOT /auth) on purpose: /auth/* is public so the login
# ceremony can run, whereas ENROLLING a passkey must require an existing session —
# otherwise anyone could bolt their own key onto someone else's account.
@app.get("/api/passkeys")
def list_passkeys(request: Request):
    email = auth.current_user(request)
    return {"passkeys": passkeys.list_for(email), "rp_id": passkeys.RP_ID}


@app.post("/api/passkeys/register/options")
def passkey_register_options(request: Request):
    email = auth.current_user(request)
    options_json, challenge = passkeys.registration_options(email, display_name=email)
    resp = JSONResponse(json.loads(options_json))
    auth._set_cookie(resp, auth.COOKIE_CHALLENGE, auth._challenge.dumps({"c": challenge}), passkeys.CHALLENGE_MAX_AGE)
    return resp


class PasskeyVerifyIn(BaseModel):
    credential: dict
    label: Optional[str] = None


@app.post("/api/passkeys/register/verify")
def passkey_register_verify(body: PasskeyVerifyIn, request: Request):
    email = auth.current_user(request)
    tok = request.cookies.get(auth.COOKIE_CHALLENGE)
    if not tok:
        raise HTTPException(400, "That took too long — try adding the passkey again.")
    try:
        challenge = auth._challenge.loads(tok, max_age=passkeys.CHALLENGE_MAX_AGE)["c"]
    except Exception:
        raise HTTPException(400, "That took too long — try adding the passkey again.")
    try:
        passkeys.registration_verify(email, body.credential, challenge, body.label)
    except Exception as e:
        raise HTTPException(400, f"Couldn't register that passkey: {e}")
    resp = JSONResponse({"ok": True, "passkeys": passkeys.list_for(email)})
    resp.delete_cookie(auth.COOKIE_CHALLENGE, path="/")
    return resp


@app.delete("/api/passkeys/{pid}")
def delete_passkey(pid: int, request: Request):
    """Remove one of my passkeys. Scoped to the caller, so this can only ever
    delete your own. TOTP remains, so removing every passkey never locks you out."""
    email = auth.current_user(request)
    passkeys.delete_for(email, pid)
    return {"ok": True, "passkeys": passkeys.list_for(email)}


# ================= Team calendar + scheduling =================
@app.get("/api/calendar/team")
def team_calendar(request: Request, email: str, date: Optional[str] = None, days: int = 1):
    """A teammate's calendar for the given range, read with the viewer's token.
    Returns accessible=False (with guidance) when the teammate hasn't shared it."""
    from datetime import date as _date
    viewer = auth.current_user(request)
    member = (email or "").strip().lower()
    if not member:
        raise HTTPException(422, "Pick a team member.")
    if date:
        try:
            anchor = _date.fromisoformat(date)
        except ValueError:
            raise HTTPException(422, "date must be YYYY-MM-DD.")
    else:
        now = datetime.now(HST)
        anchor = _date(now.year, now.month, now.day)
    days = max(1, min(31, days))
    start = datetime(anchor.year, anchor.month, anchor.day, tzinfo=HST)
    end = start + timedelta(days=days)
    try:
        events = google_int.list_calendar_events(viewer, member, start.isoformat(), end.isoformat())
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return {
                "accessible": False, "email": member, "events": [],
                "reason": (f"You don't have access to {member}'s calendar yet. Ask them to share it "
                           f"with you in Google Calendar (Settings → Share with specific people → add "
                           f"{viewer} with 'See all event details')."),
            }
        raise HTTPException(502, f"Google Calendar error: {e}")
    except Exception as e:
        raise HTTPException(502, f"Google Calendar error: {e}")
    if events is None:
        raise HTTPException(400, "Google Calendar isn't connected.")
    return {"accessible": True, "email": member, "events": events}


class EventIn(BaseModel):
    summary: str
    start: str                 # local datetime "YYYY-MM-DDTHH:MM:SS"
    end: str
    tz: Optional[str] = "Pacific/Honolulu"
    attendees: List[str] = Field(default_factory=list)
    description: Optional[str] = None
    location: Optional[str] = None


@app.post("/api/calendar/events")
def create_calendar_event(body: EventIn, request: Request):
    email = auth.current_user(request)
    if not (body.summary or "").strip():
        raise HTTPException(422, "Give the meeting a title.")
    if not body.start or not body.end:
        raise HTTPException(422, "Start and end times are required.")
    if body.end <= body.start:
        raise HTTPException(422, "The end time must be after the start time.")
    attendees = [a.strip().lower() for a in body.attendees if a and a.strip()]
    try:
        res = google_int.create_event(
            email, body.summary.strip(), body.start, body.end, body.tz,
            attendees, (body.description or "").strip() or None, (body.location or "").strip() or None,
        )
    except Exception as e:
        raise HTTPException(502, f"Couldn't create the event: {e}")
    if res is None:
        raise HTTPException(400, "Google Calendar isn't connected.")
    return res


# ================= Notifications (real-time, over the magic-link SMTP transport) ==============
APP_URL = os.environ.get("SB_APP_URL", "https://brain.cleverwolfdigital.com").rstrip("/")


def _admin_emails():
    rows = db.query("SELECT email FROM app_users WHERE role='super_admin'")
    return {(r["email"] or "").strip().lower() for r in rows if r.get("email")} | set(SUPER_ADMINS)


def _notify(to_emails, subject, text):
    """Best-effort fan-out. A mail failure must never break the action that caused it —
    completing a task shouldn't 500 because SMTP hiccuped."""
    clean = {(e or "").strip().lower() for e in to_emails}
    clean = {e for e in clean if e and "@" in e}
    sent = 0
    for addr in sorted(clean):
        try:
            if auth.send_email(addr, subject, text):
                sent += 1
        except Exception:
            pass
    return sent


def _hm(sec):
    sec = int(sec or 0)
    if sec <= 0:
        return "—"
    h, m = divmod(sec // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def _notify_task_done(t, actor):
    """Tell whoever assigned it (plus admins) that it's finished. Never emails the
    person who just clicked complete — that's the main source of self-spam."""
    actor = (actor or "").strip().lower()
    audience = set(_admin_emails())
    if t.get("assigned_by"):
        audience.add(t["assigned_by"])
    audience.discard(actor)
    if not audience:
        return 0
    return _notify(
        audience,
        f"[Single Brain] Completed: {t.get('name')}",
        f"{actor or 'Someone'} marked a task complete.\n\n"
        f"Task:     {t.get('name')}\n"
        f"Business: {t.get('business') or '—'}\n"
        f"Priority: {t.get('priority') or '—'}\n"
        f"Tracked:  {_hm(t.get('actual_sec'))}"
        + (f"\nEstimate: {t['estimate_min']}m" if t.get("estimate_min") else "")
        + f"\n\nOpen the dashboard: {APP_URL}\n",
    )


def _task_audience(task_id):
    """Everyone currently on a task — the full multi-assignee set (falls back to the
    legacy single assignee for rows created before teams existed)."""
    people = [(r["email"] or "").strip().lower()
              for r in db.query("SELECT email FROM task_assignees WHERE task_id=?", (task_id,))]
    return {e for e in people if e}


def _notify_task_assigned(t, actor, added, before):
    """Someone was put on a task — email the people who are NEW on it (never the person
    doing the assigning). `before` lets us stay quiet about people who were already on it,
    so re-saving an assignment doesn't re-spam the whole group."""
    actor = (actor or "").strip().lower()
    audience = {(e or "").strip().lower() for e in (added or [])}
    audience.discard(actor)
    audience = {e for e in audience if e and "@" in e}
    if not audience:
        return 0
    others = [e for e in _task_audience(t["id"]) if e not in audience]
    team = f"\nTeam:     {t.get('team')}" if t.get("team") else ""
    also = (f"\nAlso on it: {', '.join(sorted(others))}" if others else "")
    return _notify(
        audience,
        f"[Single Brain] Assigned to you: {t.get('name')}",
        f"{actor or 'Someone'} assigned you a task.\n\n"
        f"Task:     {t.get('name')}\n"
        f"Business: {t.get('business') or '—'}\n"
        f"Priority: {t.get('priority') or '—'}\n"
        f"Due:      {t.get('due') or '—'}"
        + team
        + (f"\nEstimate: {t['estimate_min']}m" if t.get("estimate_min") else "")
        + also
        + (f"\n\nNotes:\n{t['notes']}\n" if t.get("notes") else "")
        + f"\n\nOpen the dashboard: {APP_URL}\n",
    )


EDIT_WATCHED_FIELDS = [
    ("name", "Task"), ("business", "Business"), ("client", "Client"), ("category", "Category"),
    ("priority", "Priority"), ("due", "Due"), ("estimate_min", "Estimate (min)"), ("notes", "Notes"),
]


def _task_changes(before, after):
    """Human-readable 'old → new' lines for the fields worth telling people about.
    Timer/status columns are excluded — those have their own emails."""
    out = []
    for key, label in EDIT_WATCHED_FIELDS:
        old, new = before.get(key), after.get(key)
        if (old or "") == (new or ""):
            continue
        fmt = lambda v: (str(v).strip() or "—") if v not in (None, "") else "—"
        out.append(f"{label}: {fmt(old)} → {fmt(new)}")
    return out


def _notify_task_updated(t, actor, changes, skip):
    """A task's details changed — tell the people living with it (everyone on it plus
    whoever assigned it), never the editor, and never anyone who's just been added
    (they're getting the fuller 'assigned to you' email instead)."""
    if not changes:
        return 0
    actor = (actor or "").strip().lower()
    audience = _task_audience(t["id"])
    audience.add((t.get("assigned_by") or "").strip().lower())
    audience -= {(e or "").strip().lower() for e in (skip or set())}
    audience.discard(actor)
    audience = {e for e in audience if e and "@" in e}
    if not audience:
        return 0
    return _notify(
        audience,
        f"[Single Brain] Updated: {t.get('name')}",
        f"{actor or 'Someone'} edited a task you're on.\n\n"
        + "\n".join(f"  • {c}" for c in changes)
        + f"\n\nTask:     {t.get('name')}\n"
        f"Business: {t.get('business') or '—'}\n"
        f"Priority: {t.get('priority') or '—'}\n"
        f"Due:      {t.get('due') or '—'}\n"
        f"\nOpen the dashboard: {APP_URL}\n",
    )


def _notify_task_file(t, actor, name, link):
    """A file landed on a task — tell everyone on the task and the assigner (not the uploader)."""
    actor = (actor or "").strip().lower()
    audience = _task_audience(t["id"])
    audience.add((t.get("assigned_by") or "").strip().lower())
    audience.discard(actor)
    if not audience:
        return 0
    return _notify(
        audience,
        f"[Single Brain] File added to: {t.get('name')}",
        f"{actor or 'Someone'} added a file to a task you're on.\n\n"
        f"File:     {name}\n"
        f"Task:     {t.get('name')}\n"
        f"Business: {t.get('business') or '—'}\n"
        + (f"\nOpen the file: {link}\n" if link else "")
        + f"\nOpen the dashboard: {APP_URL}\n",
    )


# ================= Attachments (files on business / campaign / project / task) =================
ATTACH_TYPES = ("business", "campaign", "project", "task")


def _can_access_entity(email, entity_type, entity_id):
    """Attachments inherit the SAME visibility as the thing they're attached to.
    Without this any signed-in user — including an external guest — could list, add
    to, or delete files on any business/project/task."""
    biz, proj = _access_lists(email)
    # _access_lists already subtracts restricted businesses, so an admin only keeps
    # the blanket "yes" when nothing is being withheld from them.
    if _is_admin(email) and biz is None:
        return True
    allowed_biz, allowed_proj = set(biz or []), set(proj or [])
    if entity_type == "task":
        rows = db.query("SELECT * FROM tasks WHERE id=?", (entity_id,))
        return bool(rows) and bool(_visible_tasks(email, rows))
    if entity_type == "business":
        rows = db.query("SELECT name FROM businesses WHERE id=?", (entity_id,))
        return bool(rows) and rows[0]["name"] in allowed_biz
    if entity_type in ("project", "campaign"):
        rows = db.query("SELECT name, business FROM projects WHERE id=?", (entity_id,))
        if not rows:
            return False
        return rows[0]["name"] in allowed_proj or (rows[0].get("business") or "") in allowed_biz
    return False


def _require_entity_access(email, entity_type, entity_id):
    if not _can_access_entity(email, entity_type, entity_id):
        raise HTTPException(403, "You don't have access to that item.")


def _attach_row(r):
    return {
        "id": r["id"], "entity_type": r["entity_type"], "entity_id": r["entity_id"],
        "file_id": r.get("file_id"), "name": r.get("name"), "link": r.get("link"),
        "mime": r.get("mime"), "source": r.get("source") or "drive",
        "added_by": r.get("added_by"), "created_at": r.get("created_at"),
    }


@app.get("/api/attachments/counts")
def attachment_counts(request: Request):
    """Attachment counts for every entity, so cards/rows can show a badge at a
    glance without a query per item: {entity_type: {entity_id: count}}."""
    auth.current_user(request)
    rows = db.query("SELECT entity_type, entity_id, COUNT(*) c FROM attachments GROUP BY entity_type, entity_id")
    out = {}
    for r in rows:
        out.setdefault(r["entity_type"], {})[str(r["entity_id"])] = r["c"]
    return out


@app.get("/api/attachments")
def list_attachments(entity_type: str, entity_id: int, request: Request):
    email = auth.current_user(request)
    if entity_type not in ATTACH_TYPES:
        raise HTTPException(422, "entity_type must be business, campaign, project, or task.")
    _require_entity_access(email, entity_type, entity_id)
    rows = db.query(
        "SELECT * FROM attachments WHERE entity_type=? AND entity_id=? ORDER BY id DESC",
        (entity_type, entity_id),
    )
    return [_attach_row(r) for r in rows]


@app.post("/api/attachments/upload")
async def upload_attachment(
    request: Request,
    entity_type: str = Form(...),
    entity_id: int = Form(...),
    file: UploadFile = File(...),
):
    email = auth.current_user(request)
    if entity_type not in ATTACH_TYPES:
        raise HTTPException(422, "entity_type must be business, campaign, project, or task.")
    _require_entity_access(email, entity_type, entity_id)
    content = await file.read()
    if not content:
        raise HTTPException(422, "The file is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File is too large ({MAX_UPLOAD_BYTES // (1024 * 1024)} MB max).")
    try:
        res = google_int.upload_file(email, file.filename, content, file.content_type)
    except Exception as e:
        raise HTTPException(502, f"Google Drive upload error: {e}")
    if res is None:
        raise HTTPException(400, "Google Drive is not connected.")
    aid = db.execute(
        "INSERT INTO attachments(entity_type,entity_id,file_id,name,link,mime,source,added_by) "
        "VALUES(?,?,?,?,?,?, 'drive', ?)",
        (entity_type, entity_id, res.get("id"), res.get("name"), res.get("link"), res.get("mime"), email),
    )
    notified = _after_attachment_added(email, entity_type, entity_id, res.get("name"), res.get("link"), res.get("id"))
    return {"ok": True, "notified": notified,
            "attachment": _attach_row(db.query("SELECT * FROM attachments WHERE id=?", (aid,))[0])}


def _after_attachment_added(email, entity_type, entity_id, name, link, file_id):
    """Task files: tell the assignee + assigner, and — because the file lives in the
    UPLOADER's Drive — actually share it with them, or the link would just show them
    Google's 'Request access' page. Files on a business/project/campaign notify nobody
    (there's no assignee/assigner to notify)."""
    if entity_type != "task":
        return 0
    rows = db.query("SELECT * FROM tasks WHERE id=?", (entity_id,))
    if not rows:
        return 0
    t = rows[0]
    actor = (email or "").strip().lower()
    audience = {(t.get("assignee") or "").strip().lower(), (t.get("assigned_by") or "").strip().lower()}
    audience = {a for a in audience if a and a != actor}
    if not audience:
        return 0
    # Grant real access before announcing it. Best-effort: a share failure must not
    # fail the upload — the file is already saved and recorded.
    if file_id:
        for addr in audience:
            try:
                google_int.share_file(email, file_id, "user", addr, "writer")
            except Exception:
                pass
    return _notify_task_file(t, actor, name, link)


class AttachLinkIn(BaseModel):
    entity_type: str
    entity_id: int
    name: Optional[str] = None
    link: str


@app.post("/api/attachments/link")
def link_attachment(body: AttachLinkIn, request: Request):
    email = auth.current_user(request)
    if body.entity_type not in ATTACH_TYPES:
        raise HTTPException(422, "entity_type must be business, campaign, project, or task.")
    _require_entity_access(email, body.entity_type, body.entity_id)
    link = (body.link or "").strip()
    if not (link.startswith("http://") or link.startswith("https://")):
        raise HTTPException(422, "Enter a valid file link (http/https).")
    name = (body.name or "").strip() or link.rsplit("/", 1)[-1] or "Linked file"
    aid = db.execute(
        "INSERT INTO attachments(entity_type,entity_id,file_id,name,link,mime,source,added_by) "
        "VALUES(?,?,NULL,?,?,NULL,'link',?)",
        (body.entity_type, body.entity_id, name, link, email),
    )
    # No file_id — an external link isn't ours to share, so this only notifies.
    notified = _after_attachment_added(email, body.entity_type, body.entity_id, name, link, None)
    return {"ok": True, "notified": notified,
            "attachment": _attach_row(db.query("SELECT * FROM attachments WHERE id=?", (aid,))[0])}


@app.delete("/api/attachments/{aid}")
def delete_attachment(aid: int, request: Request, drive: bool = False):
    """Remove a file from the entity. By default this only detaches it (the file
    stays in Google Drive). With drive=1, the underlying Drive file is also moved to
    the user's Drive trash first; if that fails the row is kept so nothing is lost."""
    email = auth.current_user(request)
    rows = db.query("SELECT * FROM attachments WHERE id=?", (aid,))
    if not rows:
        raise HTTPException(404, "Attachment not found.")
    att = rows[0]
    _require_entity_access(email, att["entity_type"], att["entity_id"])
    if drive and att.get("source") == "drive" and att.get("file_id"):
        try:
            res = google_int.delete_drive_file(email, att["file_id"])
        except Exception as e:
            raise HTTPException(502, f"Couldn't delete from Drive: {e}")
        if res is None:
            raise HTTPException(400, "Google Drive isn't connected — use Remove to just detach it, "
                                     "or connect Drive and try again.")
    db.execute("DELETE FROM attachments WHERE id=?", (aid,))
    return {"ok": True, "drive_deleted": bool(drive and att.get("file_id"))}


class GoogleConfigIn(BaseModel):
    client_id: Optional[str] = None
    client_secret: Optional[str] = None


@app.post("/api/google/config")
def google_config(body: GoogleConfigIn, request: Request):
    _require_admin(request)
    google_int.set_config(body.client_id, body.client_secret)
    return {"ok": True, "configured": google_int.configured()}


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
    restricted: Optional[int] = 0


def _guard_business(request, bid):
    """Reading a locked business is already blocked; this stops an admin who can't
    see one from renaming, unlocking, or deleting it through the API."""
    email = auth.current_user(request)
    rows = db.query("SELECT name FROM businesses WHERE id=?", (bid,))
    if rows and not _can_see_business(email, rows[0]["name"]):
        raise HTTPException(403, "You don't have access to that business.")
    return email


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
        "INSERT INTO businesses(name,initials,tier,owner,state,status,kind,parent_id,notes,restricted) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (name, initials, _clamp_tier(b.tier), b.owner, b.state, b.status or "active", b.kind or "business",
         b.parent_id, b.notes, 1 if b.restricted else 0),
    )
    return {"ok": True, "id": bid}


@app.put("/api/businesses/{bid}")
def update_business(bid: int, b: BusinessIn, request: Request):
    _require_admin(request)
    _guard_business(request, bid)
    if not db.query("SELECT 1 FROM businesses WHERE id=?", (bid,)):
        raise HTTPException(404, "Business not found.")
    name = (b.name or "").strip()
    initials = (b.initials or "").strip() or catalog._initials(name)
    parent = b.parent_id if b.parent_id != bid else None  # no self-parenting
    db.execute(
        "UPDATE businesses SET name=?,initials=?,tier=?,owner=?,state=?,status=?,kind=?,parent_id=?,notes=?,restricted=? WHERE id=?",
        (name, initials, _clamp_tier(b.tier), b.owner, b.state, b.status, b.kind, parent, b.notes,
         1 if b.restricted else 0, bid),
    )
    return {"ok": True}


@app.delete("/api/businesses/{bid}")
def delete_business(bid: int, request: Request):
    _require_admin(request)
    _guard_business(request, bid)
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
    email = _require_admin(request)
    if not (p.name or "").strip():
        raise HTTPException(422, "Name is required.")
    _require_business_visible(email, p.business)
    pid = db.execute(
        "INSERT INTO projects(name,business,owner,status,state,badge,kind,priority,next_action,due) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (p.name.strip(), p.business, p.owner, p.status or "active", p.state, p.badge,
         p.kind or "project", p.priority, p.next_action, p.due),
    )
    return {"ok": True, "id": pid}


@app.put("/api/projects/{pid}")
def update_project(pid: int, p: ProjectIn, request: Request):
    email = _require_admin(request)
    rows = db.query("SELECT business FROM projects WHERE id=?", (pid,))
    if not rows:
        raise HTTPException(404, "Project not found.")
    _require_business_visible(email, rows[0].get("business"))
    _require_business_visible(email, p.business)
    db.execute(
        "UPDATE projects SET name=?,business=?,owner=?,status=?,state=?,badge=?,kind=?,priority=?,next_action=?,due=? WHERE id=?",
        (p.name.strip(), p.business, p.owner, p.status, p.state, p.badge, p.kind, p.priority, p.next_action, p.due, pid),
    )
    return {"ok": True}


@app.delete("/api/projects/{pid}")
def delete_project(pid: int, request: Request):
    email = _require_admin(request)
    rows = db.query("SELECT business FROM projects WHERE id=?", (pid,))
    if rows:
        _require_business_visible(email, rows[0].get("business"))
    db.execute("DELETE FROM projects WHERE id=?", (pid,))
    return {"ok": True}


# ================= Campaigns (advertising work run for a client) =================
# A campaign is a projects row with kind='campaign', run FOR a client over a flight
# against a budget. It carries line items (separate flights/creatives/markets) and
# periodic delivery metrics. Everything derived — spend to date, pacing, CTR, CPM —
# is computed on read so it can never contradict the numbers that were entered.
CAMPAIGN_STATUSES = ("planning", "live", "paused", "ended")


def _can_manage_campaigns(email, business):
    """Admins, plus any staffer explicitly granted that business. This is what lets
    the person actually running the ads manage them without handing over Super Admin."""
    if not _can_see_business(email, business):
        return False
    return _is_admin(email) or (business or "") in _granted_business_names(email)


def _require_campaign_access(request, business):
    email = auth.current_user(request)
    if not _can_manage_campaigns(email, business):
        raise HTTPException(403, "You're not allowed to manage campaigns for that business.")
    return email


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _rates(impressions, clicks, spend):
    """CTR / CPM / CPC, guarding every divide. Returns None where undefined rather
    than 0 — 'no clicks yet' and 'a 0% click rate' are not the same statement."""
    imp, clk, sp = _num(impressions), _num(clicks), _num(spend)
    return {
        "ctr": (clk / imp * 100) if imp else None,
        "cpm": (sp / imp * 1000) if imp else None,
        "cpc": (sp / clk) if clk else None,
    }


def _campaign_rollup(c):
    """Attach line items, metrics and every derived total to one campaign row."""
    c = dict(c)
    cid = c["id"]
    c["items"] = db.query("SELECT * FROM campaign_items WHERE campaign_id=? ORDER BY sort, id", (cid,))
    metrics = db.query("SELECT * FROM campaign_metrics WHERE campaign_id=? ORDER BY COALESCE(period_start,''), id", (cid,))
    for m in metrics:
        m.update(_rates(m.get("impressions"), m.get("clicks"), m.get("spend")))
    c["metrics"] = metrics
    imp = sum(int(m.get("impressions") or 0) for m in metrics)
    clk = sum(int(m.get("clicks") or 0) for m in metrics)
    spend = sum(_num(m.get("spend")) for m in metrics)
    budget = _num(c.get("budget"))
    c["totals"] = {
        "impressions": imp, "clicks": clk, "spend": spend,
        # Reach doesn't sum across periods (the same person is reached twice), so the
        # honest roll-up is the best single period, not a total that overstates it.
        "reach_best_period": max([int(m.get("reach") or 0) for m in metrics], default=0),
        "budget": budget,
        "remaining": (budget - spend) if budget else None,
        "spend_pct": (spend / budget * 100) if budget else None,
        "items_budget": sum(_num(i.get("budget")) for i in c["items"]),
        "periods": len(metrics),
        **_rates(imp, clk, spend),
    }
    c["flight"] = _flight_state(c.get("start_on"), c.get("end_on"))
    # Pacing: are we spending in step with the flight elapsing? >100 = ahead of plan.
    el = c["flight"].get("elapsed_pct")
    c["totals"]["pace_pct"] = (c["totals"]["spend_pct"] / el * 100) if (el and c["totals"]["spend_pct"] is not None) else None
    return c


def _flight_state(start, end):
    """How far through the booked dates we are. All-None when dates aren't set yet."""
    today = _today()
    out = {"start_on": start, "end_on": end, "days_total": None, "days_elapsed": None,
           "days_left": None, "elapsed_pct": None, "state": "unscheduled"}
    if not (start and end):
        return out
    total = _days_between(start, end)
    if total <= 0:
        total = 1
    elapsed = min(max(_days_between(start, today), 0), total) if today >= start else 0
    out.update({
        "days_total": total,
        "days_elapsed": elapsed,
        "days_left": max(_days_between(today, end), 0) if today <= end else 0,
        "elapsed_pct": elapsed / total * 100,
        "state": "upcoming" if today < start else ("ended" if today > end else "running"),
    })
    return out


def _visible_campaigns(email):
    hidden = _hidden_businesses(email)
    biz, proj = _access_lists(email)
    allowed_biz, allowed_proj = set(biz or []), set(proj or [])
    everything = _is_admin(email) and biz is None
    rows = db.query("SELECT * FROM projects WHERE kind='campaign' ORDER BY COALESCE(start_on,'') DESC, id DESC")
    out = []
    for r in rows:
        if (r.get("business") or "") in hidden:
            continue
        if not everything and (r.get("business") or "") not in allowed_biz and r["name"] not in allowed_proj:
            continue
        out.append(r)
    return out


class CampaignIn(BaseModel):
    name: str
    business: Optional[str] = None
    client_id: Optional[int] = None
    status: Optional[str] = "planning"
    owner: Optional[str] = None
    start_on: Optional[str] = None
    end_on: Optional[str] = None
    budget: Optional[float] = None
    goal: Optional[str] = None
    channel: Optional[str] = None
    next_action: Optional[str] = None


class CampaignItemIn(BaseModel):
    name: str
    market: Optional[str] = None
    creative: Optional[str] = None
    start_on: Optional[str] = None
    end_on: Optional[str] = None
    budget: Optional[float] = None
    notes: Optional[str] = None
    sort: Optional[int] = 0


class CampaignMetricIn(BaseModel):
    item_id: Optional[int] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    impressions: Optional[int] = None
    clicks: Optional[int] = None
    reach: Optional[int] = None
    spend: Optional[float] = None
    notes: Optional[str] = None


def _campaign_or_404(cid, request, manage=False):
    rows = db.query("SELECT * FROM projects WHERE id=? AND kind='campaign'", (cid,))
    if not rows:
        raise HTTPException(404, "Campaign not found.")
    c = rows[0]
    email = auth.current_user(request)
    if manage:
        _require_campaign_access(request, c.get("business"))
    elif not _can_see_business(email, c.get("business")):
        raise HTTPException(403, "You don't have access to that campaign.")
    return c


@app.get("/api/campaigns")
def list_campaigns(request: Request):
    email = auth.current_user(request)
    visible = _visible_campaigns(email)
    # Which businesses this viewer may create/edit campaigns under — the frontend
    # uses it to decide whether to offer the buttons at all.
    manageable = sorted({b["name"] for b in db.query("SELECT name FROM businesses")
                         if _can_manage_campaigns(email, b["name"])})
    return {"campaigns": [_campaign_rollup(c) for c in visible], "can_manage": manageable}


@app.get("/api/campaigns/{cid}")
def get_campaign(cid: int, request: Request):
    return _campaign_rollup(_campaign_or_404(cid, request))


@app.post("/api/campaigns")
def create_campaign(c: CampaignIn, request: Request):
    email = _require_campaign_access(request, c.business)
    if not (c.name or "").strip():
        raise HTTPException(422, "Name is required.")
    status = (c.status or "planning").lower()
    if status not in CAMPAIGN_STATUSES:
        raise HTTPException(422, "Status must be planning, live, paused, or ended.")
    client_name = None
    if c.client_id:
        rows = db.query("SELECT name, business FROM clients WHERE id=?", (c.client_id,))
        if not rows:
            raise HTTPException(422, "A valid client is required.")
        client_name = rows[0]["name"]
    cid = db.execute(
        "INSERT INTO projects(name,business,owner,status,kind,next_action,client_id,client_name,"
        "start_on,end_on,budget,goal,channel) VALUES(?,?,?,?,'campaign',?,?,?,?,?,?,?,?)",
        (c.name.strip(), c.business, (c.owner or email), status, c.next_action, c.client_id,
         client_name, c.start_on, c.end_on, c.budget, c.goal, c.channel),
    )
    return {"ok": True, "id": cid}


@app.put("/api/campaigns/{cid}")
def update_campaign(cid: int, c: CampaignIn, request: Request):
    cur = _campaign_or_404(cid, request, manage=True)
    _require_campaign_access(request, c.business or cur.get("business"))
    status = (c.status or "planning").lower()
    if status not in CAMPAIGN_STATUSES:
        raise HTTPException(422, "Status must be planning, live, paused, or ended.")
    client_name = None
    if c.client_id:
        rows = db.query("SELECT name FROM clients WHERE id=?", (c.client_id,))
        if not rows:
            raise HTTPException(422, "A valid client is required.")
        client_name = rows[0]["name"]
    db.execute(
        "UPDATE projects SET name=?,business=?,owner=?,status=?,next_action=?,client_id=?,client_name=?,"
        "start_on=?,end_on=?,budget=?,goal=?,channel=? WHERE id=?",
        (c.name.strip(), c.business, c.owner, status, c.next_action, c.client_id, client_name,
         c.start_on, c.end_on, c.budget, c.goal, c.channel, cid),
    )
    return {"ok": True}


@app.delete("/api/campaigns/{cid}")
def delete_campaign(cid: int, request: Request):
    _campaign_or_404(cid, request, manage=True)
    db.execute("DELETE FROM campaign_items WHERE campaign_id=?", (cid,))
    db.execute("DELETE FROM campaign_metrics WHERE campaign_id=?", (cid,))
    db.execute("DELETE FROM projects WHERE id=?", (cid,))
    return {"ok": True}


@app.post("/api/campaigns/{cid}/items")
def add_campaign_item(cid: int, item: CampaignItemIn, request: Request):
    _campaign_or_404(cid, request, manage=True)
    if not (item.name or "").strip():
        raise HTTPException(422, "Line item name is required.")
    iid = db.execute(
        "INSERT INTO campaign_items(campaign_id,name,market,creative,start_on,end_on,budget,notes,sort) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (cid, item.name.strip(), item.market, item.creative, item.start_on, item.end_on,
         item.budget, item.notes, item.sort or 0),
    )
    return {"ok": True, "id": iid}


def _item_campaign(iid):
    rows = db.query("SELECT campaign_id FROM campaign_items WHERE id=?", (iid,))
    if not rows:
        raise HTTPException(404, "Line item not found.")
    return rows[0]["campaign_id"]


@app.put("/api/campaign-items/{iid}")
def update_campaign_item(iid: int, item: CampaignItemIn, request: Request):
    _campaign_or_404(_item_campaign(iid), request, manage=True)
    db.execute(
        "UPDATE campaign_items SET name=?,market=?,creative=?,start_on=?,end_on=?,budget=?,notes=?,sort=? WHERE id=?",
        (item.name.strip(), item.market, item.creative, item.start_on, item.end_on,
         item.budget, item.notes, item.sort or 0, iid),
    )
    return {"ok": True}


@app.delete("/api/campaign-items/{iid}")
def delete_campaign_item(iid: int, request: Request):
    _campaign_or_404(_item_campaign(iid), request, manage=True)
    # Metrics logged against this line item become campaign-level rather than being
    # destroyed — deleting a line item must never silently delete delivery history.
    db.execute("UPDATE campaign_metrics SET item_id=NULL WHERE item_id=?", (iid,))
    db.execute("DELETE FROM campaign_items WHERE id=?", (iid,))
    return {"ok": True}


@app.post("/api/campaigns/{cid}/metrics")
def add_campaign_metric(cid: int, m: CampaignMetricIn, request: Request):
    _campaign_or_404(cid, request, manage=True)
    email = auth.current_user(request)
    if m.item_id and _item_campaign(m.item_id) != cid:
        raise HTTPException(422, "That line item belongs to a different campaign.")
    mid = db.execute(
        "INSERT INTO campaign_metrics(campaign_id,item_id,period_start,period_end,impressions,clicks,reach,spend,notes,recorded_by) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (cid, m.item_id, m.period_start, m.period_end, m.impressions, m.clicks, m.reach,
         m.spend, m.notes, email),
    )
    return {"ok": True, "id": mid}


def _metric_campaign(mid):
    rows = db.query("SELECT campaign_id FROM campaign_metrics WHERE id=?", (mid,))
    if not rows:
        raise HTTPException(404, "Metrics entry not found.")
    return rows[0]["campaign_id"]


@app.put("/api/campaign-metrics/{mid}")
def update_campaign_metric(mid: int, m: CampaignMetricIn, request: Request):
    cid = _metric_campaign(mid)
    _campaign_or_404(cid, request, manage=True)
    if m.item_id and _item_campaign(m.item_id) != cid:
        raise HTTPException(422, "That line item belongs to a different campaign.")
    db.execute(
        "UPDATE campaign_metrics SET item_id=?,period_start=?,period_end=?,impressions=?,clicks=?,reach=?,spend=?,notes=? WHERE id=?",
        (m.item_id, m.period_start, m.period_end, m.impressions, m.clicks, m.reach, m.spend, m.notes, mid),
    )
    return {"ok": True}


@app.delete("/api/campaign-metrics/{mid}")
def delete_campaign_metric(mid: int, request: Request):
    _campaign_or_404(_metric_campaign(mid), request, manage=True)
    db.execute("DELETE FROM campaign_metrics WHERE id=?", (mid,))
    return {"ok": True}


@app.get("/api/campaigns/{cid}/report")
def campaign_report(cid: int, request: Request):
    """Everything a client-ready report needs, assembled server-side so the document
    and the dashboard can never tell two different stories."""
    c = _campaign_rollup(_campaign_or_404(cid, request))
    start, end = c.get("start_on"), c.get("end_on")
    # Work completed for this client during the flight — proof of what was delivered
    # beyond the media numbers. Falls back to all of the client's completed work when
    # the campaign has no dates set yet.
    tasks = db.query(
        "SELECT name, business, client, completed_at, actual_sec FROM tasks "
        "WHERE status='done' AND client IS NOT NULL AND client=? ORDER BY completed_at DESC",
        (c.get("client_name"),),
    ) if c.get("client_name") else []
    done = []
    for t in tasks:
        when = datetime.fromtimestamp(t["completed_at"], HST).strftime("%Y-%m-%d") if t.get("completed_at") else None
        if start and end and when and not (start <= when <= end):
            continue
        done.append({"name": t["name"], "completed_on": when, "tracked_sec": t.get("actual_sec") or 0})
    by_item = {}
    for m in c["metrics"]:
        key = m.get("item_id") or 0
        agg = by_item.setdefault(key, {"impressions": 0, "clicks": 0, "spend": 0.0, "reach_best_period": 0})
        agg["impressions"] += int(m.get("impressions") or 0)
        agg["clicks"] += int(m.get("clicks") or 0)
        agg["spend"] += _num(m.get("spend"))
        agg["reach_best_period"] = max(agg["reach_best_period"], int(m.get("reach") or 0))
    for item in c["items"]:
        agg = by_item.get(item["id"], {"impressions": 0, "clicks": 0, "spend": 0.0, "reach_best_period": 0})
        item["delivered"] = {**agg, **_rates(agg["impressions"], agg["clicks"], agg["spend"])}
    return {
        "campaign": c,
        "client": (db.query("SELECT * FROM clients WHERE id=?", (c["client_id"],)) or [None])[0] if c.get("client_id") else None,
        "work_completed": done,
        "unassigned_delivery": by_item.get(0),   # metrics not tied to a line item
        "generated_on": _today(),
        "generated_by": auth.current_user(request),
    }


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


# ---------- Teams (reusable groups you can assign a whole task to) ----------
def _teams_with_members():
    teams = db.query("SELECT * FROM teams ORDER BY name")
    members = {}
    for r in db.query("SELECT team_id, email FROM team_members"):
        members.setdefault(r["team_id"], []).append(r["email"])
    for t in teams:
        t["members"] = sorted(members.get(t["id"], []))
    return teams


class TeamIn(BaseModel):
    name: str
    members: List[str] = Field(default_factory=list)


@app.get("/api/teams")
def list_teams(request: Request):
    auth.current_user(request)
    return _teams_with_members()


def _save_team_members(team_id, members):
    db.execute("DELETE FROM team_members WHERE team_id=?", (team_id,))
    for e in _clean_emails(members):
        db.execute("INSERT OR IGNORE INTO team_members(team_id,email) VALUES(?,?)", (team_id, e))


@app.post("/api/teams")
def create_team(body: TeamIn, request: Request):
    _require_admin(request)
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(422, "Team name is required.")
    if db.query("SELECT 1 FROM teams WHERE lower(name)=?", (name.lower(),)):
        raise HTTPException(422, "A team with that name already exists.")
    tid = db.execute("INSERT INTO teams(name) VALUES(?)", (name,))
    _save_team_members(tid, body.members)
    return {"ok": True, "id": tid}


@app.put("/api/teams/{team_id}")
def update_team(team_id: int, body: TeamIn, request: Request):
    _require_admin(request)
    if not db.query("SELECT 1 FROM teams WHERE id=?", (team_id,)):
        raise HTTPException(404, "Team not found.")
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(422, "Team name is required.")
    dup = db.query("SELECT 1 FROM teams WHERE lower(name)=? AND id!=?", (name.lower(), team_id))
    if dup:
        raise HTTPException(422, "A team with that name already exists.")
    db.execute("UPDATE teams SET name=? WHERE id=?", (name, team_id))
    _save_team_members(team_id, body.members)
    return {"ok": True}


@app.delete("/api/teams/{team_id}")
def delete_team(team_id: int, request: Request):
    _require_admin(request)
    db.execute("DELETE FROM team_members WHERE team_id=?", (team_id,))
    # Tasks keep their expanded assignees; they just lose the team label.
    db.execute("UPDATE tasks SET team_id=NULL WHERE team_id=?", (team_id,))
    db.execute("DELETE FROM teams WHERE id=?", (team_id,))
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
    email = _require_admin(request)
    if not (c.name or "").strip():
        raise HTTPException(422, "Name is required.")
    _require_business_visible(email, c.business)
    cid = db.execute(
        "INSERT INTO clients(name,business,retainer_amount,cadence,status,contact_name,contact_email,assignee,notes) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (c.name.strip(), c.business, c.retainer_amount, c.cadence or "monthly", c.status or "active",
         c.contact_name, c.contact_email, (c.assignee or "").strip().lower() or None, c.notes),
    )
    return {"ok": True, "id": cid}


@app.put("/api/clients/{cid}")
def update_client(cid: int, c: ClientIn, request: Request):
    email = _require_admin(request)
    rows = db.query("SELECT business FROM clients WHERE id=?", (cid,))
    if not rows:
        raise HTTPException(404, "Client not found.")
    _require_business_visible(email, rows[0].get("business"))
    _require_business_visible(email, c.business)
    db.execute(
        "UPDATE clients SET name=?,business=?,retainer_amount=?,cadence=?,status=?,contact_name=?,contact_email=?,assignee=?,notes=? WHERE id=?",
        (c.name.strip(), c.business, c.retainer_amount, c.cadence, c.status, c.contact_name, c.contact_email,
         (c.assignee or "").strip().lower() or None, c.notes, cid),
    )
    return {"ok": True}


@app.delete("/api/clients/{cid}")
def delete_client(cid: int, request: Request):
    email = _require_admin(request)
    rows = db.query("SELECT business FROM clients WHERE id=?", (cid,))
    if rows:
        _require_business_visible(email, rows[0].get("business"))
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
    # Generate this month's instance NOW. Without this, a new recurring task stayed
    # invisible until the next 08:00/18:00 sync (or a restart), which reads as "it
    # didn't work" — the task simply never appeared in My Tasks.
    created = catalog.generate_recurring()
    if created:
        _sync_tasks_to_repo()
    return {"ok": True, "id": rid, "generated": created}


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
    # A template that was just switched on (or edited before this month generated)
    # should land straight away rather than waiting for the next sync.
    created = catalog.generate_recurring()
    if created:
        _sync_tasks_to_repo()
    return {"ok": True, "generated": created}


@app.delete("/api/recurring/{rid}")
def delete_recurring(rid: int, request: Request):
    _require_admin(request)
    # Drop the template, and tidy up instances nobody has touched (still open, no
    # tracked time, never started) — deleting a mistake shouldn't leave orphans.
    # Anything with time logged against it is real work and is deliberately kept.
    db.execute(
        "DELETE FROM tasks WHERE recurring_id=? AND status='open' "
        "AND COALESCE(actual_sec,0)=0 AND started_at IS NULL",
        (rid,),
    )
    db.execute("DELETE FROM recurring_tasks WHERE id=?", (rid,))
    _sync_tasks_to_repo()
    return {"ok": True}


@app.post("/api/recurring/generate")
def recurring_generate(request: Request):
    _require_admin(request)
    created = catalog.generate_recurring()
    if created:
        _sync_tasks_to_repo()
    return {"ok": True, "created": created}


# ================= Client billing (invoices) =================
# Single Brain tracks whether a retainer was billed and paid; it does not issue the
# invoice document. Monthly rows are drafted from each client's retainer at boot
# (catalog.generate_invoices) and moved draft -> sent -> paid here.
INVOICE_STATUSES = ("draft", "sent", "paid", "void")


def _today():
    return datetime.now(HST).strftime("%Y-%m-%d")


def _decorate_invoice(inv):
    """Add the derived fields the UI needs. Overdue is computed, never stored, so a
    row can't sit marked 'overdue' after someone pays it."""
    inv = dict(inv)
    status = (inv.get("status") or "draft").lower()
    due = inv.get("due_on") or ""
    inv["overdue"] = bool(status == "sent" and due and due < _today())
    inv["days_overdue"] = _days_between(due, _today()) if inv["overdue"] else 0
    return inv


def _days_between(start, end):
    try:
        a = datetime.strptime(start, "%Y-%m-%d")
        b = datetime.strptime(end, "%Y-%m-%d")
        return max((b - a).days, 0)
    except (ValueError, TypeError):
        return 0


def _invoices_for(email):
    """Every invoice, newest period first — but only for admins. Billing is the one
    surface where a staff grant to a business is NOT enough. Invoices belonging to a
    RESTRICTED business drop out even for an admin who wasn't granted it."""
    if not _is_admin(email):
        return []
    rows = db.query("SELECT * FROM invoices ORDER BY COALESCE(period,'') DESC, id DESC")
    hidden = _hidden_businesses(email)
    if hidden:
        blocked = {c["id"] for c in db.query("SELECT id, business FROM clients")
                   if (c.get("business") or "") in hidden}
        rows = [r for r in rows if r.get("client_id") not in blocked
                and (r.get("business") or "") not in hidden]
    return [_decorate_invoice(r) for r in rows]


def _invoice_owners(inv):
    """Who hears about this invoice: the staffer who owns the client, plus admins."""
    who = set(_admin_emails())
    rows = db.query("SELECT assignee FROM clients WHERE id=?", (inv.get("client_id"),))
    if rows and (rows[0].get("assignee") or "").strip():
        who.add(rows[0]["assignee"].strip().lower())
    return who


def _money(v):
    try:
        return "$" + f"{float(v):,.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "—"


def _sweep_overdue_invoices():
    """Email the client's owner (and admins) about invoices that have gone past due.
    Internal only — nothing here ever mails the client. Capped at one nudge per
    invoice per day via reminded_on, so a restart loop can't turn into a mail storm."""
    today = _today()
    sent = 0
    for row in db.query("SELECT * FROM invoices WHERE lower(COALESCE(status,'draft'))='sent'"):
        inv = _decorate_invoice(row)
        if not inv["overdue"] or (inv.get("reminded_on") or "") == today:
            continue
        db.execute("UPDATE invoices SET reminded_on=? WHERE id=?", (today, inv["id"]))
        sent += _notify(
            _invoice_owners(inv),
            f"[Single Brain] Overdue: {inv.get('client_name') or 'client'} — {_money(inv.get('amount'))}",
            f"An invoice is past due and still marked sent.\n\n"
            f"Client:  {inv.get('client_name') or '—'}\n"
            f"Period:  {inv.get('period') or '—'}\n"
            f"Amount:  {_money(inv.get('amount'))}\n"
            f"Due:     {inv.get('due_on')} ({inv['days_overdue']} days ago)\n"
            + (f"Ref:     {inv['reference']}\n" if inv.get("reference") else "")
            + f"\nMark it paid in Billing once the money lands: {APP_URL}\n",
        )
    return sent


class InvoiceIn(BaseModel):
    client_id: int
    period: Optional[str] = None
    amount: Optional[float] = None
    status: Optional[str] = "draft"
    issued_on: Optional[str] = None
    due_on: Optional[str] = None
    paid_on: Optional[str] = None
    reference: Optional[str] = None
    notes: Optional[str] = None


class InvoiceStatusIn(BaseModel):
    status: str
    paid_on: Optional[str] = None


def _client_or_422(cid):
    rows = db.query("SELECT * FROM clients WHERE id=?", (cid,))
    if not rows:
        raise HTTPException(422, "A valid client is required.")
    return rows[0]


@app.get("/api/invoices")
def list_invoices(request: Request):
    email = _require_admin(request)
    return {"invoices": _invoices_for(email)}


@app.post("/api/invoices")
def create_invoice(inv: InvoiceIn, request: Request):
    _require_admin(request)
    c = _client_or_422(inv.client_id)
    status = (inv.status or "draft").lower()
    if status not in INVOICE_STATUSES:
        raise HTTPException(422, "Status must be draft, sent, paid, or void.")
    iid = db.execute(
        "INSERT INTO invoices(client_id,client_name,business,period,amount,status,issued_on,due_on,paid_on,reference,notes,auto) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,0)",
        (c["id"], c.get("name"), c.get("business"), (inv.period or "").strip() or None, inv.amount,
         status, inv.issued_on, inv.due_on, inv.paid_on, inv.reference, inv.notes),
    )
    return {"ok": True, "id": iid}


@app.put("/api/invoices/{iid}")
def update_invoice(iid: int, inv: InvoiceIn, request: Request):
    _require_admin(request)
    if not db.query("SELECT 1 FROM invoices WHERE id=?", (iid,)):
        raise HTTPException(404, "Invoice not found.")
    c = _client_or_422(inv.client_id)
    status = (inv.status or "draft").lower()
    if status not in INVOICE_STATUSES:
        raise HTTPException(422, "Status must be draft, sent, paid, or void.")
    db.execute(
        "UPDATE invoices SET client_id=?,client_name=?,business=?,period=?,amount=?,status=?,"
        "issued_on=?,due_on=?,paid_on=?,reference=?,notes=? WHERE id=?",
        (c["id"], c.get("name"), c.get("business"), (inv.period or "").strip() or None, inv.amount,
         status, inv.issued_on, inv.due_on, inv.paid_on, inv.reference, inv.notes, iid),
    )
    return {"ok": True}


@app.post("/api/invoices/{iid}/status")
def set_invoice_status(iid: int, body: InvoiceStatusIn, request: Request):
    """One-click draft -> sent -> paid from the Billing table. Stamps the matching
    date automatically so nobody has to type today's date to say 'this is paid'."""
    _require_admin(request)
    rows = db.query("SELECT * FROM invoices WHERE id=?", (iid,))
    if not rows:
        raise HTTPException(404, "Invoice not found.")
    status = (body.status or "").strip().lower()
    if status not in INVOICE_STATUSES:
        raise HTTPException(422, "Status must be draft, sent, paid, or void.")
    cur = rows[0]
    issued = cur.get("issued_on") or (_today() if status in ("sent", "paid") else None)
    paid = None
    if status == "paid":
        paid = (body.paid_on or "").strip() or cur.get("paid_on") or _today()
    db.execute(
        "UPDATE invoices SET status=?, issued_on=?, paid_on=? WHERE id=?",
        (status, issued, paid, iid),
    )
    return {"ok": True, "status": status, "issued_on": issued, "paid_on": paid}


@app.delete("/api/invoices/{iid}")
def delete_invoice(iid: int, request: Request):
    _require_admin(request)
    db.execute("DELETE FROM invoices WHERE id=?", (iid,))
    return {"ok": True}


@app.post("/api/invoices/generate")
def invoices_generate(request: Request):
    """Draft any missing retainer invoices for the current month, then nudge on
    anything overdue. Runs at boot too — this is the manual 'do it now' button."""
    _require_admin(request)
    created = catalog.generate_invoices()
    nudged = _sweep_overdue_invoices()
    return {"ok": True, "created": created, "nudged": nudged}


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


def _readable_context(raw):
    """Turn the captured JSON context into a couple of human lines for the email."""
    try:
        c = json.loads(raw) if raw else {}
    except Exception:
        return ""
    if not isinstance(c, dict):
        return ""
    bits = []
    if c.get("browser"):
        bits.append(f"Browser:  {c['browser']}")
    if c.get("os"):
        bits.append(f"OS:       {c['os']}")
    if c.get("screen"):
        bits.append(f"Screen:   {c['screen']}")
    if c.get("viewport"):
        bits.append(f"Viewport: {c['viewport']}")
    return "\n".join(bits)


@app.post("/api/feedback")
async def submit_feedback(
    request: Request,
    message: str = Form(...),
    kind: str = Form("bug"),
    page: Optional[str] = Form(None),
    context: Optional[str] = Form(None),
    screenshot: Optional[UploadFile] = File(None),
):
    email = auth.current_user(request) or "unknown"
    msg = (message or "").strip()
    if len(msg) < 3:
        raise HTTPException(422, "Please add a bit more detail.")
    kind = "suggestion" if (kind or "").lower().startswith("sugg") else "bug"

    shot, shot_mime = None, None
    if screenshot is not None:
        shot = await screenshot.read()
        if shot:
            if len(shot) > 6 * 1024 * 1024:
                raise HTTPException(413, "Screenshot is too large (6 MB max).")
            shot_mime = (screenshot.content_type or "image/png").split(";")[0]
        else:
            shot = None

    fid = db.execute(
        "INSERT INTO feedback(email,kind,message,page,status,context,screenshot,screenshot_mime) "
        "VALUES(?,?,?,?,'open',?,?,?)",
        (email, kind, msg, page, context, shot, shot_mime),
    )
    sent = False
    if FEEDBACK_TO:
        ctx = _readable_context(context)
        body = (
            f"{kind} report\nFrom: {email}\nPage: {page or '-'}\n"
            + (ctx + "\n" if ctx else "")
            + f"\n{msg}\n"
            + (f"\n(Screenshot attached — also viewable in the dashboard Feedback tab.)\n" if shot else "")
            + f"\nView in dashboard: {APP_URL}#feedback\n"
        )
        atts = [(f"feedback-{fid}.png", shot, shot_mime or "image/png")] if shot else None
        try:
            sent = auth.send_email(FEEDBACK_TO, f"[Single Brain] {kind} from {email} (#{fid})", body, attachments=atts)
        except Exception:
            pass
    return {"ok": True, "id": fid, "sent": bool(sent)}


@app.get("/api/feedback/{fid}/screenshot")
def feedback_screenshot(fid: int, request: Request):
    """The screenshot bytes — visible to an admin or to the person who submitted it."""
    email = (auth.current_user(request) or "").strip().lower()
    rows = db.query("SELECT email, screenshot, screenshot_mime FROM feedback WHERE id=?", (fid,))
    if not rows or not rows[0].get("screenshot"):
        raise HTTPException(404, "No screenshot.")
    r = rows[0]
    if not _is_admin(email) and (r.get("email") or "").strip().lower() != email:
        raise HTTPException(403, "Not allowed.")
    from fastapi.responses import Response
    return Response(content=r["screenshot"], media_type=r.get("screenshot_mime") or "image/png")


@app.get("/api/feedback")
def list_feedback(request: Request):
    email = (auth.current_user(request) or "").strip().lower()
    # Explicit columns — never ship the raw screenshot BLOB in the list (it isn't
    # JSON-serializable and would bloat the payload); a flag + the /screenshot route
    # cover it. `context` is parsed to an object for the client.
    cols = ("id, email, kind, message, page, status, admin_note, created_at, updated_at, context, "
            "(screenshot IS NOT NULL) AS has_screenshot")
    if _is_admin(email):
        rows = db.query(
            f"SELECT {cols} FROM feedback ORDER BY "
            "CASE status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END, id DESC"
        )
    else:
        rows = db.query(f"SELECT {cols} FROM feedback WHERE lower(email)=? ORDER BY id DESC", (email,))
    for r in rows:
        r["has_screenshot"] = bool(r.get("has_screenshot"))
        try:
            r["context"] = json.loads(r["context"]) if r.get("context") else None
        except Exception:
            r["context"] = None
    return rows


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
