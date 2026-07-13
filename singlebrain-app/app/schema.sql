-- Single Brain data model (MVP). SQLite; real persistence.

CREATE TABLE IF NOT EXISTS businesses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  status TEXT DEFAULT 'active',
  notes TEXT,
  initials TEXT,
  tier INTEGER,
  owner TEXT,
  state TEXT,
  kind TEXT DEFAULT 'business',   -- 'business' | 'subbusiness'
  parent_id INTEGER,              -- self-reference for sub-businesses
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS staff (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  role TEXT, focus TEXT, status TEXT DEFAULT 'active',
  email TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, business TEXT, owner TEXT,
  status TEXT DEFAULT 'not_started', next_action TEXT, due TEXT,
  state TEXT,
  badge TEXT,
  kind TEXT DEFAULT 'project',    -- 'project' | 'campaign'
  priority TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS app_meta (
  key TEXT PRIMARY KEY,
  value TEXT
);

-- Per-user sidebar pins (max 5, enforced in the API). kind: business|project|client|campaign
CREATE TABLE IF NOT EXISTS user_pins (
  email TEXT NOT NULL,
  kind TEXT NOT NULL,
  ref TEXT NOT NULL,
  created_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (email, kind, ref)
);

CREATE TABLE IF NOT EXISTS clients (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  business TEXT,
  retainer_amount REAL,
  cadence TEXT DEFAULT 'monthly',
  status TEXT DEFAULT 'active',
  contact_name TEXT,
  contact_email TEXT,
  notes TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT,                     -- submitter
  kind TEXT,                      -- bug | suggestion
  message TEXT NOT NULL,
  page TEXT,
  status TEXT DEFAULT 'open',     -- open | in_progress | resolved | wont_fix
  admin_note TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS recurring_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  business TEXT,
  client_id INTEGER,
  client_name TEXT,
  category TEXT,
  priority TEXT DEFAULT 'Medium',
  estimate_min INTEGER,
  assignee TEXT,
  day_of_month INTEGER DEFAULT 1,
  active INTEGER DEFAULT 1,
  last_generated TEXT,            -- 'YYYY-MM' of the last month generated
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS blockers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  summary TEXT NOT NULL, area TEXT, status TEXT DEFAULT 'open',
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS recommendations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text TEXT NOT NULL, category TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);

-- Quick Add tasks (business, name, category, priority, due, notes, dependencies)
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  business TEXT,
  name TEXT NOT NULL,
  category TEXT,
  priority TEXT DEFAULT 'Medium',
  due TEXT,
  notes TEXT,
  status TEXT DEFAULT 'open',
  estimate_min INTEGER,          -- planned duration, minutes
  actual_sec INTEGER DEFAULT 0,  -- accumulated tracked time, seconds
  started_at INTEGER,            -- epoch seconds; set while a timer is running
  completed_at INTEGER,          -- epoch seconds; set when marked done
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS task_dependencies (
  task_id INTEGER NOT NULL,
  depends_on INTEGER NOT NULL,
  PRIMARY KEY (task_id, depends_on)
);

-- RBAC: app users (roles) + per-user business/project view grants.
CREATE TABLE IF NOT EXISTS app_users (
  email TEXT PRIMARY KEY,
  name TEXT,
  role TEXT NOT NULL DEFAULT 'staff',   -- 'super_admin' | 'staff'
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_business_access (
  email TEXT NOT NULL,
  business TEXT NOT NULL,
  PRIMARY KEY (email, business)
);

CREATE TABLE IF NOT EXISTS user_project_access (
  email TEXT NOT NULL,
  project TEXT NOT NULL,
  PRIMARY KEY (email, project)
);

CREATE TABLE IF NOT EXISTS daily_journal (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL, type TEXT NOT NULL,
  energy_level INTEGER, top_priorities TEXT, new_context TEXT, awareness TEXT,
  what_got_done TEXT, what_didnt TEXT, new_decisions TEXT, new_blockers TEXT,
  wins TEXT, tomorrow_focus TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
