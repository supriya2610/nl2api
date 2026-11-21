import json

from assistant.models import Plan, PlanStep
from assistant.engine import Engine
from assistant.planner import LLMPlanner


def test_read_workflow_chains_outputs(engine):
    st = engine.ask("show open invoices for alan@example.com", "viewer")
    assert st.status == "completed"
    assert st.results["s2"]["items"][0]["id"] == "INV-1003"


def test_high_risk_requires_approval_and_dry_run_does_not_mutate(engine, client):
    st = engine.ask("refund $40 on INV-1002 because duplicate charge", "admin")
    assert st.status == "awaiting_approval"
    assert "Would refund $40.00" in st.pending_preview["summary"]
    assert client.get("/invoices/INV-1002").json()["refunded"] == 0.0     # nothing changed yet
    st = engine.approve(st.run_id, True)
    assert st.status == "completed"
    assert client.get("/invoices/INV-1002").json()["refunded"] == 40.0


def test_rejecting_approval_changes_nothing(engine, client):
    st = engine.ask("refund $40 on INV-1002 because duplicate charge", "admin")
    st = engine.approve(st.run_id, False)
    assert st.status == "cancelled"
    assert client.get("/invoices/INV-1002").json()["refunded"] == 0.0


def test_low_risk_write_runs_after_dry_run(engine, client):
    st = engine.ask("upgrade alan@example.com to pro", "agent")
    assert st.status == "completed"
    assert client.get("/customers/CUS-002/subscription").json()["plan"] == "pro"
    kinds = [e["kind"] for e in engine.events(st.run_id)]
    assert kinds.index("dry_run") < kinds.index("api_call", kinds.index("dry_run"))


def test_role_denied_before_any_call(engine):
    st = engine.ask("refund $40 on INV-1002", "agent")
    assert st.status == "rejected" and "PERMISSION" in st.message
    assert not [e for e in engine.events(st.run_id) if e["kind"] == "api_call"]


def test_ambiguous_match_asks_instead_of_guessing(engine):
    st = engine.ask("find Sam Lee", "admin")
    assert st.status == "needs_clarification" and len(st.candidates) == 2


def test_api_side_dry_run_catches_overrefund(engine, client):
    st = engine.ask("refund $5000 on INV-1002 because test", "admin")
    assert st.status == "failed" and "exceeds" in st.message


class BadThenGood:
    """Fake LLM: first plan hallucinates a field; repair attempt fixes it."""
    def __init__(self): self.n = 0
    def __call__(self, system, user):
        self.n += 1
        args = {"customer_id": "CUS-001", "subject": "hi there", "bogus": 1} if self.n == 1 else \
               {"customer_id": "CUS-001", "subject": "hi there"}
        return json.dumps({"steps": [{"id": "s1", "operation_id": "create_ticket", "args": args}]})


def test_llm_plan_repair_loop(client, tmp_path):
    fake = BadThenGood()
    eng = Engine(client, LLMPlanner(fake), db_path=str(tmp_path / "r.db"))
    st = eng.ask("open a ticket for CUS-001", "agent")
    assert fake.n == 2 and st.status == "completed"


def test_llm_cannot_invent_operations(client, tmp_path):
    fake = lambda s, u: json.dumps({"steps": [{"id": "s1", "operation_id": "delete_customer", "args": {}}]})
    st = Engine(client, LLMPlanner(fake), db_path=str(tmp_path / "r.db")).ask("x", "admin")
    assert st.status == "rejected"
