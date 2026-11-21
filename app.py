"""Streamlit UI:  streamlit run app.py   (set API_URL to hit a running mock API, else in-process)"""
import json
import os

import httpx
import streamlit as st

from assistant.engine import Engine
from assistant.llm import anthropic_complete
from assistant.planner import LLMPlanner, RuleBasedPlanner


@st.cache_resource
def get_engine() -> Engine:
    if url := os.getenv("API_URL"):
        client = httpx.Client(base_url=url, timeout=10)
    else:
        from fastapi.testclient import TestClient
        from mock_api.main import app
        client = TestClient(app)
    planner = LLMPlanner(anthropic_complete) if os.getenv("ANTHROPIC_API_KEY") else RuleBasedPlanner()
    return Engine(client, planner, db_path=os.getenv("DB_PATH", "runs.db"))


eng = get_engine()
st.title("Natural Language → API Assistant")
st.caption(f"Planner: {type(eng.planner).__name__}")
role = st.sidebar.selectbox("Acting as role", ["viewer", "agent", "admin"], index=2)
st.sidebar.markdown("Try:\n- show open invoices for ada@example.com\n- upgrade alan@example.com to pro\n"
                    "- refund $40 on INV-1002 because duplicate charge\n- find Sam Lee\n- delete all customers")

req = st.text_input("Request")
if st.button("Run") and req:
    state = eng.propose(req, role)
    if state.status == "planned":
        state = eng.execute(state.run_id)
    st.session_state.run_id = state.run_id

if rid := st.session_state.get("run_id"):
    state = eng.load(rid)
    st.subheader(f"Run {rid}: {state.status}")
    if state.plan.steps:
        st.markdown("**Planned calls**")
        for s in state.plan.steps:
            t = eng.tools[s.operation_id]
            st.code(f"{s.id}  {t.method} {t.path}   [{t.risk}]\nargs: {json.dumps(s.args)}\nwhy: {s.reason}")
    if state.message:
        st.warning(state.message)
    if state.status == "awaiting_approval":
        p = state.pending_preview
        st.error(f"High-risk action needs approval:\n\n**{p['summary']}**")
        c1, c2 = st.columns(2)
        if c1.button("Approve"):
            eng.approve(rid, True); st.rerun()
        if c2.button("Reject"):
            eng.approve(rid, False); st.rerun()
    if state.answer:
        st.success(state.answer)
    with st.expander("Audit log"):
        st.json(eng.events(rid))
