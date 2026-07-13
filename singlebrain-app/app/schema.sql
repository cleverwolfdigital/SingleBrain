-- Single Brain data model (MVP). SQLite; real persistence.

CREATE TABLE IF NOT EXISTS businesses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  status TEXT DEFAULT 'active',
  notes TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS staff (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  role TEXT, focus TEXT, status TEXT DEFAULT 'active',
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, business TEXT, owner TEXT,
  status TEXT DEFAULT 'not_started', next_action TEXT, due TEXT,
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

CREATE TABLE IF NOT EXISTS daily_journal (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL, type TEXT NOT NULL,
  energy_level INTEGER, top_priorities TEXT, new_context TEXT, awareness TEXT,
  what_got_done TEXT, what_didnt TEXT, new_decisions TEXT, new_blockers TEXT,
  wins TEXT, tomorrow_focus TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
