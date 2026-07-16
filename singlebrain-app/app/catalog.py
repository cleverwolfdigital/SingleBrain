"""DB-backed catalog: businesses (with parent/sub-business hierarchy), projects,
campaigns, staff, clients (monthly retainers), and recurring monthly tasks.

seed_catalog() performs a ONE-TIME migration of the previously-hardcoded frontend
arrays into the DB, guarded by an app_meta flag so later edits in the UI persist.
It only rewrites catalog metadata (businesses/projects/staff) — never user tasks,
which reference businesses by name string, not by id.
"""
from datetime import datetime, timezone, timedelta
from . import db

HST = timezone(timedelta(hours=-10))

# (name, initials, tier, owner, state, status, kind, parent_name)
BUSINESSES = [
    ("Clever Wolf Digital", "CW", 1, "Quincy", "Active", "active", "business", None),
    ("CleverWolf.ai", "CA", 1, "Quincy", "Active", "active", "business", None),
    ("Titan Medical Transportation", "TM", 1, "Quincy + Peter (50/50)", "Active", "active", "business", None),
    ("Kapwa Coffee Bar", "KC", 1, "—", "Active", "active", "business", None),
    ("Vending / HVAC", "VH", 1, "Quincy", "Active", "active", "business", None),
    ("Kapuna Meals", "KM", 2, "—", "Starting", "warning", "business", None),
    ("Opala Hoa", "OH", 2, "—", "Starting", "warning", "business", None),
    ("Shaka's Food Truck", "SF", 2, "—", "Active", "active", "business", None),
    ("Ghost Kitchen Hawaii", "GK", 2, "—", "Starting", "warning", "business", None),
    ("Island Laundromat Co.", "IL", 2, "—", "Diligence", "warning", "business", None),
    ("Level Up Corporation", "LC", 3, "—", "Active", "active", "business", None),
    ("Level Up Self", "LS", 3, "—", "Active", "active", "subbusiness", "Level Up Corporation"),
    ("Level Up Smarter", "LM", 3, "—", "Active", "active", "subbusiness", "Level Up Corporation"),
    ("Level Up Library", "LL", 3, "—", "Active", "active", "subbusiness", "Level Up Corporation"),
    ("The Smarter Podcast", "SP", 3, "—", "Active", "active", "subbusiness", "Level Up Corporation"),
    ("The Ongoing Conversation", "OC", 3, "—", "Active", "active", "business", None),
    ("Let's Try Hawaii", "TH", 3, "—", "Active", "active", "business", None),
    ("Pop808Hawaii", "P8", 3, "—", "Active", "active", "business", None),
    ("Fort Madison Monthly", "FM", 3, "—", "Active", "active", "business", None),
    ("Feedback Seattle", "FS", 3, "—", "Active", "active", "business", None),
    ("Spooky Action", "SA", 3, "—", "Active", "active", "business", None),
    ("Aries", "AR", 3, "—", "Active", "active", "business", None),
    ("Guns A Blazin'", "GB", 3, "—", "Active", "active", "business", None),
    ("Hawaii SEO", "HS", 3, "—", "Active", "active", "business", None),
    ("Shopify Hawaii", "SH", 3, "—", "Active", "active", "business", None),
    ("Spotify Hawaii", "SY", 3, "—", "Active", "active", "business", None),
    ("Island Parking Services", "IP", 4, "—", "Idea", "parked", "business", None),
    ("Privatized Storage Units", "PS", 4, "—", "Idea", "parked", "business", None),
    ("Bing Bros", "BB", 4, "—", "Idea", "parked", "business", None),
    ("Salmon Distribution", "SD", 4, "—", "Idea", "parked", "business", None),
]

# (name, business, state, badge, status, next_action, due, kind)
PROJECTS = [
    ("Email Cleanup (Inbox)", "Personal / CWD", "In Progress", "warning", "active", "Continue daily review", "—", "project"),
    ("Composio Integration", "Clever Wolf Digital", "Partial", "warning", "active", "Improve Gmail/Calendar tools", "—", "project"),
    ("Hermes Bot (2nd Gateway)", "Clever Wolf Digital", "In Progress", "warning", "active", "Finish Discord connection", "—", "project"),
    ("ScrapeGraphAI Research", "Clever Wolf Digital", "Not Started", "parked", "parked", "Define first target", "—", "project"),
    ("July 15 Meetings (Koko Head + Rise)", "Let's Try Hawaii", "Scheduled", "", "active", "Confirm with Gio and Jay", "Jul 15", "project"),
    ("Titan Medical Transportation", "Titan Medical Transportation", "Active", "", "active", "Ongoing operations", "—", "project"),
    ("Vending / HVAC Operations", "Vending / HVAC", "Active", "", "active", "Financial tracking & vendor management", "—", "project"),
    ("Kapwa Coffee Bar Operations", "Kapwa Coffee Bar", "Active", "", "active", "Margin optimization", "—", "project"),
]

# (name, role, focus, status, email)
STAFF = [
    ("Quincy Solano", "Owner / Decision Maker", "Sales, fit calls, closes, invoicing, and overall direction.", "active", "quincy@cleverwolfdigital.com"),
    ("Jeremy Lum", "Sr. Digital Marketing Manager", "Marketing and content support for current initiatives.", "active", "jeremy@cleverwolfdigital.com"),
    ("Jordan", "Email Systems", "Email infrastructure after the manual motion is proven.", "active", "jordan@cleverwolfdigital.com"),
    ("Nicole", "Team Member", "Role and current focus awaiting dashboard update.", "unassigned", "nicole@cleverwolfdigital.com"),
    ("Kari", "Team Member", "Role and current focus awaiting dashboard update.", "unassigned", "kari@cleverwolfdigital.com"),
    ("Delmore", "Team Member", "Role and current focus awaiting dashboard update.", "unassigned", "delmore@cleverwolfdigital.com"),
    ("Jay", "Team Member", "Role and current focus awaiting dashboard update.", "unassigned", "jay@cleverwolfdigital.com"),
    ("Madison", "Team Member", "Role and current focus awaiting dashboard update.", "unassigned", "madison@cleverwolfdigital.com"),
    ("Gio", "Team Member", "Role and current focus awaiting dashboard update.", "unassigned", "gio@cleverwolfdigital.com"),
    ("Tyler", "Team Member", "Role and current focus awaiting dashboard update.", "unassigned", "tyler@cleverwolfdigital.com"),
]


def _meta_get(key):
    r = db.query("SELECT value FROM app_meta WHERE key=?", (key,))
    return r[0]["value"] if r else None


def _meta_set(key, value):
    db.execute(
        "INSERT INTO app_meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def _initials(name):
    parts = [p for p in (name or "").replace("/", " ").split() if p[0].isalnum()]
    if not parts:
        return (name or "?")[:2].upper()
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


def seed_catalog():
    """One-time migration of the hardcoded catalog into the DB (idempotent)."""
    if _meta_get("catalog_v1") == "done":
        return
    # Businesses (rewrite metadata cleanly; tasks are unaffected — they key on name).
    db.execute("DELETE FROM businesses")
    ids = {}
    for name, initials, tier, owner, state, status, kind, _parent in BUSINESSES:
        ids[name] = db.execute(
            "INSERT INTO businesses(name,initials,tier,owner,state,status,kind) VALUES(?,?,?,?,?,?,?)",
            (name, initials, tier, owner, state, status, kind),
        )
    for name, _i, _t, _o, _s, _st, _k, parent in BUSINESSES:
        if parent and parent in ids:
            db.execute("UPDATE businesses SET parent_id=? WHERE name=?", (ids[parent], name))
    # Projects
    db.execute("DELETE FROM projects")
    for name, business, state, badge, status, na, due, kind in PROJECTS:
        db.execute(
            "INSERT INTO projects(name,business,state,badge,status,next_action,due,kind) VALUES(?,?,?,?,?,?,?,?)",
            (name, business, state, badge, status, na, due, kind),
        )
    # Staff
    db.execute("DELETE FROM staff")
    for name, role, focus, status, email in STAFF:
        db.execute(
            "INSERT INTO staff(name,role,focus,status,email) VALUES(?,?,?,?,?)",
            (name, role, focus, status, email),
        )
    _meta_set("catalog_v1", "done")


def current_month(month=None):
    if month:
        return month
    now = datetime.now(HST)
    return f"{now.year:04d}-{now.month:02d}"


def generate_recurring(month=None):
    """Create this month's task instances from active recurring templates. Idempotent
    per month via each template's last_generated marker. Returns the count created."""
    month = current_month(month)
    y, m = int(month[:4]), int(month[5:7])
    created = 0
    for rt in db.query("SELECT * FROM recurring_tasks WHERE active=1"):
        if (rt.get("last_generated") or "") == month:
            continue
        day = min(max(int(rt.get("day_of_month") or 1), 1), 28)
        due = f"{y:04d}-{m:02d}-{day:02d}"
        note = f"Recurring monthly task" + (f" · {rt['client_name']}" if rt.get("client_name") else "")
        db.execute(
            "INSERT INTO tasks(business,name,category,priority,due,notes,status,estimate_min,actual_sec,assignee,client,recurring_id) "
            "VALUES(?,?,?,?,?,?,'open',?,0,?,?,?)",
            (rt.get("business"), rt.get("name"), rt.get("category"), rt.get("priority") or "Medium",
             due, note, rt.get("estimate_min"), rt.get("assignee"), rt.get("client_name"), rt["id"]),
        )
        db.execute("UPDATE recurring_tasks SET last_generated=? WHERE id=?", (month, rt["id"]))
        created += 1
    return created
