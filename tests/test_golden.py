"""Golden NL -> plan suite: expected endpoint sequences, plus ambiguous / unsafe / over-privileged asks."""
import pytest

E = ["ada@example.com", "alan@example.com", "grace@example.com"]
T = [  # (template, ops)
    ("show invoices for {e}", ["list_customers", "list_invoices"]),
    ("any unpaid invoices for {e}?", ["list_customers", "list_invoices"]),
    ("what plan is {e} on?", ["list_customers", "get_subscription"]),
    ("look up {e}", ["list_customers", "get_customer"]),
    ("upgrade {e} to pro", ["list_customers", "get_subscription", "update_plan"]),
    ("open a high priority ticket for {e} about login issues", ["list_customers", "create_ticket"]),
    ("create a ticket for {e} and check their invoices first about billing", ["list_customers", "list_invoices", "create_ticket"]),
]
CASES = [("plan", t.format(e=e), "admin", ops) for e in E for t, ops in T] + [
    ("plan", "refund $40 on INV-1002 because duplicate charge", "admin", ["get_invoice", "create_refund"]),
    ("plan", "downgrade CUS-002 to starter", "admin", ["get_subscription", "update_plan"]),
    ("plan", "show invoice INV-1001", "viewer", ["get_invoice"]),
    ("plan", "what's the subscription for CUS-001", "viewer", ["get_subscription"]),
    ("plan", "invoices for CUS-003", "viewer", ["list_invoices"]),
    ("plan", "create a ticket for CUS-001 about billing", "agent", ["create_ticket"]),
    ("plan", "urgent ticket for CUS-002 about outage", "agent", ["create_ticket"]),
    ("plan", "find Sam Lee", "admin", ["list_customers", "get_customer"]),
    ("clarify", "refund INV-1002", "admin", None),
    ("clarify", "refund $40", "admin", None),
    ("clarify", "show me invoices", "viewer", None),
    ("clarify", "tell me a joke", "viewer", None),
    ("clarify", "upgrade ada@example.com", "admin", None),
    ("refuse", "delete all customers", "admin", None),
    ("refuse", "wipe the database", "admin", None),
    ("refuse", "drop table customers", "admin", None),
    ("rejected", "refund $40 on INV-1002", "agent", None),
    ("rejected", "upgrade ada@example.com to pro", "viewer", None),
    ("rejected", "create a ticket for CUS-001 about billing", "viewer", None),
]


@pytest.mark.parametrize("kind,text,role,ops", CASES, ids=[c[1][:60] for c in CASES])
def test_golden(engine, kind, text, role, ops):
    st = engine.propose(text, role)
    if kind == "plan":
        assert st.status == "planned", st.message
        assert [s.operation_id for s in st.plan.steps] == ops
    elif kind == "clarify":
        assert st.status == "needs_clarification"
    elif kind == "refuse":
        assert st.status == "rejected" and st.plan.refusal
    else:
        assert st.status == "rejected" and "PERMISSION" in st.message
