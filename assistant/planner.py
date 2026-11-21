"""Planners turn text into a Plan. They never decide risk or permissions; the engine does,
from the OpenAPI schema."""
import json
import re
from typing import Callable, Protocol

from .models import Plan, PlanStep
from .schema import Tool


class Planner(Protocol):
    def plan(self, request: str, tools: list[Tool], role: str, feedback: str | None = None) -> Plan: ...


SYSTEM = """You plan API calls for a SaaS admin assistant. Output ONLY JSON:
{"steps":[{"id":"s1","operation_id":"...","args":{...},"reason":"...","expected":"...","expect_single":false}],
 "clarification":null,"refusal":null}
Rules:
- Use only the operations listed. Args must match each operation's input_schema exactly.
- To use an earlier step's output, write a string ref like "$s1.items.0.id" (step ids are s1, s2, ...).
- Customer lookups (by email/name) must set expect_single=true.
- Never invent ids, emails, amounts or reasons. If something required is missing or ambiguous, return
  steps=[] and a short clarification question.
- If the request is destructive or unsupported by the listed operations, return steps=[] and a refusal."""


class LLMPlanner:
    """`complete(system, user) -> str` is injected so tests can fake it and any provider can be used."""

    def __init__(self, complete: Callable[[str, str], str]):
        self.complete = complete

    def plan(self, request, tools, role, feedback=None) -> Plan:
        catalog = [{"operation_id": t.operation_id, "summary": t.summary, "risk": t.risk,
                    "input_schema": t.input_schema} for t in tools]
        user = f"Operations:\n{json.dumps(catalog)}\n\nUser role: {role}\nRequest: {request}"
        if feedback:
            user += f"\n\nYour previous plan was rejected: {feedback}\nFix it."
        raw = self.complete(SYSTEM, user).strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
        return Plan.model_validate_json(raw)


EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
INV, CUS = re.compile(r"INV-\d+", re.I), re.compile(r"CUS-\d+", re.I)
NAME = re.compile(r"\b(?:for|find|look up|lookup)\s+([A-Z][a-z]+ [A-Z][a-z]+)")
MONEY = re.compile(r"\$\s?(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:dollars|usd)", re.I)
UNSAFE = re.compile(r"\b(delete|drop|wipe|truncate|purge|destroy|remove all)\b", re.I)


class RuleBasedPlanner:
    """Offline baseline: deterministic, used for tests and when no API key is set."""

    def plan(self, request, tools, role, feedback=None) -> Plan:
        t, low = request, request.lower()
        if UNSAFE.search(t):
            return Plan(refusal="Destructive operations are not supported by this assistant.")
        steps: list[PlanStep] = []

        def add(op, args=None, reason="", expected="", single=False) -> str:
            sid = f"s{len(steps) + 1}"
            steps.append(PlanStep(id=sid, operation_id=op, args=args or {}, reason=reason,
                                  expected=expected, expect_single=single))
            return sid

        def customer() -> str | None:
            if m := CUS.search(t):
                return m.group(0).upper()
            if m := EMAIL.search(t):
                lookup = {"email": m.group(0)}
            elif m := NAME.search(t):
                lookup = {"name": m.group(1)}
            else:
                return None
            sid = add("list_customers", lookup, "Find the customer", "exactly one customer", True)
            return f"${sid}.items.0.id"

        if inv := INV.search(t):
            inv_id = inv.group(0).upper()
            if "refund" in low:
                m = MONEY.search(t)
                if not m:
                    return Plan(clarification="How much should be refunded?")
                reason = (re.search(r"because (.+)$", t, re.I) or [None, "customer request"])[1]
                add("get_invoice", {"invoice_id": inv_id}, "Check the invoice", "paid invoice")
                add("create_refund", {"invoice_id": inv_id, "amount": float(m.group(1) or m.group(2)),
                                      "reason": reason}, "Issue the refund", "refund record")
                return Plan(steps=steps)
            add("get_invoice", {"invoice_id": inv_id}, "Fetch the invoice", "invoice")
            return Plan(steps=steps)
        if "refund" in low:
            return Plan(clarification="Which invoice should be refunded (e.g. INV-1002)?")

        cid = customer()
        if cid is None:
            return Plan(clarification="Which customer do you mean? Please give an email, full name, or CUS id.")
        if "ticket" in low and re.search(r"\b(create|open|file|raise|log)\b|urgent", low):
            if "invoice" in low:
                add("list_invoices", {"customer_id": cid}, "Check invoice status", "invoice list")
            subj = (re.search(r"about (.+)$", t, re.I) or [None, None])[1]
            if not subj:
                return Plan(clarification="What should the ticket be about?")
            prio = "high" if re.search(r"urgent|high", low) else "normal"
            add("create_ticket", {"customer_id": cid, "subject": subj, "priority": prio},
                "Open the support ticket", "ticket record")
        elif re.search(r"upgrade|downgrade|switch|change .*plan|move .* to", low):
            plan = next((p for p in ("starter", "pro", "enterprise") if p in low), None)
            if not plan:
                return Plan(clarification="Which plan: starter, pro or enterprise?")
            s = add("get_subscription", {"customer_id": cid}, "Find current subscription", "subscription")
            add("update_plan", {"subscription_id": f"${s}.id", "plan": plan}, f"Change plan to {plan}", "updated subscription")
        elif "invoice" in low or "unpaid" in low or "billing" in low:
            status = "open" if re.search(r"\b(open|unpaid)\b", low) else "paid" if "paid" in low else None
            add("list_invoices", {"customer_id": cid, **({"status": status} if status else {})},
                "List invoices", "invoice list")
        elif re.search(r"subscription|plan", low):
            add("get_subscription", {"customer_id": cid}, "Get subscription", "subscription")
        elif re.search(r"customer|account|look ?up|find|who is", low):
            add("get_customer", {"customer_id": cid}, "Get customer", "customer record")
        else:
            return Plan(clarification="I couldn't map that to a supported action. Try asking about customers, invoices, subscriptions, tickets or refunds.")
        return Plan(steps=steps)
