"""Seed the dashboard from Master_Dashboard.md so the command center is alive on
first launch. Idempotent (only seeds a table if empty)."""
from . import db

BUSINESSES = [
    ("Clever Wolf Digital", "active", "Sales sprint paused 2026-07-12 to build Single Brain MVP"),
    ("Tea Monkey", "parked", ""),
    ("Lifeline Hawaii", "parked", ""),
    ("Red Dot Productions", "parked", ""),
]

STAFF = [
    ("Quincy", "Owner", "Sales + overall direction", "active"),
    ("Jeremy Lum", "Sr. Digital Marketing Manager", "Marketing & content", "active"),
    ("Jordan", "Email Systems", "Email infrastructure", "active"),
    ("Nicole", "", "", "unassigned"), ("Kari", "", "", "unassigned"),
    ("Delmore", "", "", "unassigned"), ("Jay", "", "", "unassigned"),
    ("Madison", "", "", "unassigned"), ("Gio", "", "", "unassigned"),
    ("Tyler", "", "", "unassigned"),
]

BLOCKERS = [
    ("Composio tools often return 'Tool not found' when called directly", "Tooling", "open"),
    ("Many emails still need review", "Ops", "open"),
    ("Second Hermes gateway not yet connected to Discord", "Infra", "open"),
]

PROJECTS = [
    ("Single Brain MVP", "CWD", "Quincy", "in_progress", "Phase 1: backend + real DB", ""),
    ("Email Cleanup (Inbox)", "Personal", "Quincy", "in_progress", "Continue daily review", ""),
    ("Composio Integration", "CWD", "Quincy", "partial", "Improve Gmail/Calendar tools", ""),
    ("Hermes Bot (2nd Gateway)", "CWD", "Quincy", "in_progress", "Finish Discord connection", ""),
]

RECOMMENDATIONS = [
    ("Follow up every warm call from last sprint and ask for the close", "Sales"),
    ("Lock six-module pricing + lighthouse cap before any offer build", "Offers"),
    ("Fix VPS -> GitHub push so journal entries reach the source of truth", "Infra"),
]


def seed():
    if not db.query("SELECT 1 FROM businesses LIMIT 1"):
        for n, s, notes in BUSINESSES:
            db.execute("INSERT INTO businesses(name,status,notes) VALUES(?,?,?)", (n, s, notes))
    if not db.query("SELECT 1 FROM staff LIMIT 1"):
        for n, r, f, st in STAFF:
            db.execute("INSERT INTO staff(name,role,focus,status) VALUES(?,?,?,?)", (n, r, f, st))
    if not db.query("SELECT 1 FROM blockers LIMIT 1"):
        for s, a, st in BLOCKERS:
            db.execute("INSERT INTO blockers(summary,area,status) VALUES(?,?,?)", (s, a, st))
    if not db.query("SELECT 1 FROM projects LIMIT 1"):
        for n, b, o, s, na, due in PROJECTS:
            db.execute("INSERT INTO projects(name,business,owner,status,next_action,due) VALUES(?,?,?,?,?,?)",
                       (n, b, o, s, na, due))
    if not db.query("SELECT 1 FROM recommendations LIMIT 1"):
        for text, cat in RECOMMENDATIONS:
            db.execute("INSERT INTO recommendations(text,category) VALUES(?,?)", (text, cat))
