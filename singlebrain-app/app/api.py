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

from . import db, seed, auth, repo, config

app = FastAPI(title="Single Brain API")
app.include_router(auth.router)

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "index.html"


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
    auth.init_auth_db()


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
    return {"email": auth.current_user(request)}


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


def _tasks_with_deps():
    tasks = db.query("SELECT * FROM tasks ORDER BY id DESC")
    deps = db.query("SELECT task_id, depends_on FROM task_dependencies")
    by_task = {}
    for d in deps:
        by_task.setdefault(d["task_id"], []).append(d["depends_on"])
    for t in tasks:
        t["dependencies"] = by_task.get(t["id"], [])
        _apply_timing(t)
    return tasks


@app.get("/api/state")
def state():
    return {
        "businesses": db.query("SELECT * FROM businesses ORDER BY name"),
        "projects": db.query("SELECT * FROM projects ORDER BY id"),
        "staff": db.query("SELECT * FROM staff ORDER BY id"),
        "blockers": db.query("SELECT * FROM blockers ORDER BY id"),
        "recommendations": db.query("SELECT * FROM recommendations ORDER BY id"),
        "tasks": _tasks_with_deps(),
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
    dependencies: List[int] = Field(default_factory=list)


@app.get("/api/tasks")
def get_tasks():
    return _tasks_with_deps()


@app.post("/api/tasks")
def create_task(t: TaskIn):
    name = (t.name or "").strip()
    if len(name) < 5:
        raise HTTPException(422, "Task name must be at least 5 characters.")
    if t.priority not in ("High", "Medium", "Low"):
        raise HTTPException(422, "Priority must be High, Medium, or Low.")
    est = t.estimate_min if (t.estimate_min and t.estimate_min > 0) else None
    tid = db.execute(
        "INSERT INTO tasks(business,name,category,priority,due,notes,status,estimate_min,actual_sec) "
        "VALUES(?,?,?,?,?,?,'open',?,0)",
        (t.business, name, t.category, t.priority, t.due, t.notes, est),
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
def reports(period: str = "week", date: Optional[str] = None, offset: int = 0):
    from datetime import date as _date
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

    # Portfolio-wide snapshot (not period-bound) for context.
    open_rows = db.query("SELECT status, started_at FROM tasks WHERE status!='done'")
    active_count = sum(1 for r in open_rows if r.get("started_at"))

    return {
        "period": period,
        "label": label,
        "start": start,
        "end": end,
        "completed": len(done),
        "actual_sec": total_actual,
        "estimate_min": total_est,
        "tracked_count": len(tracked),
        "open_count": len(open_rows),
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


def _state_context():
    """Build Grok's context from the SOURCE OF TRUTH (Master_Dashboard.md + project files)
    plus the live task list — not the stale DB seed."""
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
        s = state()
        tasks = "; ".join(
            f'{t["name"]} [{t.get("priority") or "?"}, {t.get("business") or "-"}, {t.get("status") or "open"}, due {t.get("due") or "-"}]'
            for t in s["tasks"][:60]
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
        "advise, summarize, and draft).\n\n" + _state_context()
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
