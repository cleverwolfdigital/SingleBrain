"""Daily journal capture. Writes to SQLite AND appends a markdown entry into the
brain repo, then commits + pushes (so the journal feeds the single source of truth).
"""
from . import db, repo, config


def _bullets(text):
    lines = [l.strip() for l in str(text or "").splitlines() if l.strip()]
    return "\n".join(f"  {i + 1}. {l}" for i, l in enumerate(lines)) or "  (none)"


def _md_morning(d):
    return (
        f"## Morning — {d['date']}\n\n"
        f"- Energy: {d.get('energy_level', '')}/10\n"
        f"- Top 3 Priorities:\n{_bullets(d.get('top_priorities'))}\n"
        f"- New Context / Updates: {d.get('new_context', '') or '(none)'}\n"
        f"- Aware of: {d.get('awareness', '') or '(none)'}\n"
    )


def _md_eod(d):
    return (
        f"## End of Day — {d['date']}\n\n"
        f"- What got done: {d.get('what_got_done', '') or '(none)'}\n"
        f"- What didn't + why: {d.get('what_didnt', '') or '(none)'}\n"
        f"- New decisions: {d.get('new_decisions', '') or '(none)'}\n"
        f"- New blockers: {d.get('new_blockers', '') or '(none)'}\n"
        f"- Wins: {d.get('wins', '') or '(none)'}\n"
        f"- Tomorrow's top focus: {d.get('tomorrow_focus', '') or '(none)'}\n"
    )


def save_entry(kind, d):
    assert kind in ("morning", "eod")
    repo.pull()  # Pull first

    db.execute(
        """INSERT INTO daily_journal
           (date, type, energy_level, top_priorities, new_context, awareness,
            what_got_done, what_didnt, new_decisions, new_blockers, wins, tomorrow_focus)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (d.get("date"), kind, d.get("energy_level"), d.get("top_priorities"),
         d.get("new_context"), d.get("awareness"), d.get("what_got_done"),
         d.get("what_didnt"), d.get("new_decisions"), d.get("new_blockers"),
         d.get("wins"), d.get("tomorrow_focus")),
    )

    config.JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    fp = config.JOURNAL_DIR / f"{d.get('date')}.md"
    header = "" if fp.exists() else f"# Journal — {d.get('date')}\n\n"
    block = _md_morning(d) if kind == "morning" else _md_eod(d)
    with open(fp, "a", encoding="utf-8") as f:
        f.write(header + block + "\n")

    return repo.commit_and_push(f"journal: {kind} entry {d.get('date')}", [fp])
