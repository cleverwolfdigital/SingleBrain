"""Single Brain - Streamlit command center (Phase 1).

Run: streamlit run dashboard.py
Sections: Command Center, Daily Journal, Businesses, Projects, Staff, Blockers.
Interim password gate via SB_APP_PASSWORD until Magic Link + 2FA (Phase 2).
"""
from datetime import date
import streamlit as st

from app import db, journal, seed, config

st.set_page_config(page_title="Single Brain", page_icon="\U0001F9E0", layout="wide")


def gate():
    if not config.APP_PASSWORD:
        return True
    if st.session_state.get("authed"):
        return True
    st.title("\U0001F9E0 Single Brain")
    st.caption("Enter password (interim gate — Magic Link + 2FA coming in Phase 2)")
    pw = st.text_input("Password", type="password")
    if st.button("Enter"):
        if pw == config.APP_PASSWORD:
            st.session_state.authed = True
            st.rerun()
        else:
            st.error("Wrong password")
    return False


db.init_db()
seed.seed()

if not gate():
    st.stop()

st.sidebar.title("\U0001F9E0 Single Brain")
st.sidebar.caption("Personal AI operating system")
page = st.sidebar.radio(
    "Navigate",
    ["Command Center", "Daily Journal", "Businesses", "Projects", "Staff", "Blockers"],
)

if page == "Command Center":
    st.header("Command Center")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Businesses", len(db.query("SELECT 1 FROM businesses")))
    c2.metric("Open blockers", len(db.query("SELECT 1 FROM blockers WHERE status='open'")))
    c3.metric("Active staff", len(db.query("SELECT 1 FROM staff WHERE status='active'")))
    c4.metric("Journal entries", len(db.query("SELECT 1 FROM daily_journal")))

    st.subheader("Recent journal")
    rows = db.query("SELECT date, type, energy_level, tomorrow_focus FROM daily_journal ORDER BY id DESC LIMIT 8")
    st.dataframe(rows or [{"date": "—", "type": "no entries yet"}], use_container_width=True, hide_index=True)

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("Open blockers")
        st.dataframe(db.query("SELECT summary, area FROM blockers WHERE status='open'"),
                     use_container_width=True, hide_index=True)
    with col_b:
        st.subheader("Active projects")
        st.dataframe(db.query("SELECT name, business, status, next_action FROM projects"),
                     use_container_width=True, hide_index=True)

elif page == "Daily Journal":
    st.header("Daily Journal")
    tab_m, tab_e = st.tabs(["\U0001F305 Morning", "\U0001F319 End of Day"])

    with tab_m:
        with st.form("morning", clear_on_submit=True):
            d = st.date_input("Date", date.today(), key="m_date")
            energy = st.slider("Energy Level", 1, 10, 6)
            prio = st.text_area("Top 3 Priorities (one per line)")
            ctx = st.text_area("New Context / Updates")
            aware = st.text_area("Anything I Should Be Aware Of?")
            if st.form_submit_button("Save morning entry", type="primary"):
                res = journal.save_entry("morning", {
                    "date": str(d), "energy_level": energy, "top_priorities": prio,
                    "new_context": ctx, "awareness": aware,
                })
                st.success(f"Saved to DB. Committed: {res['committed']} | Pushed to GitHub: {res['pushed']}")
                if not res["pushed"]:
                    st.info("Saved locally + committed on the VPS. GitHub push pending (credentials).")

    with tab_e:
        with st.form("eod", clear_on_submit=True):
            d = st.date_input("Date", date.today(), key="e_date")
            done = st.text_area("What Got Done")
            notdone = st.text_area("What Didn't Get Done + Why")
            dec = st.text_area("New Decisions")
            blk = st.text_area("New Blockers")
            wins = st.text_area("Wins")
            tom = st.text_area("Tomorrow's Top Focus")
            if st.form_submit_button("Save end-of-day entry", type="primary"):
                res = journal.save_entry("eod", {
                    "date": str(d), "what_got_done": done, "what_didnt": notdone,
                    "new_decisions": dec, "new_blockers": blk, "wins": wins, "tomorrow_focus": tom,
                })
                st.success(f"Saved to DB. Committed: {res['committed']} | Pushed to GitHub: {res['pushed']}")

elif page == "Businesses":
    st.header("Businesses")
    st.dataframe(db.query("SELECT name, status, notes FROM businesses"),
                 use_container_width=True, hide_index=True)

elif page == "Projects":
    st.header("Projects")
    st.dataframe(db.query("SELECT name, business, owner, status, next_action, due FROM projects"),
                 use_container_width=True, hide_index=True)

elif page == "Staff":
    st.header("Staff")
    st.dataframe(db.query("SELECT name, role, focus, status FROM staff"),
                 use_container_width=True, hide_index=True)

elif page == "Blockers":
    st.header("Blockers")
    st.dataframe(db.query("SELECT summary, area, status FROM blockers"),
                 use_container_width=True, hide_index=True)
