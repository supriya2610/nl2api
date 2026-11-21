"""Mock SaaS admin API. Every operation carries x-risk and x-roles metadata in its OpenAPI entry.
Write endpoints honour an `X-Dry-Run: true` header (server-side preview, no mutation)."""
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

app = FastAPI(title="Mock SaaS Admin API", version="1.0")
DB: dict = {}


def reset() -> None:
    DB.clear()
    DB.update(
        customers={
            "CUS-001": {"id": "CUS-001", "name": "Ada Lovelace", "email": "ada@example.com"},
            "CUS-002": {"id": "CUS-002", "name": "Alan Turing", "email": "alan@example.com"},
            "CUS-003": {"id": "CUS-003", "name": "Grace Hopper", "email": "grace@example.com"},
            "CUS-004": {"id": "CUS-004", "name": "Sam Lee", "email": "sam@acme.com"},
            "CUS-005": {"id": "CUS-005", "name": "Sam Lee", "email": "sam@globex.com"},
        },
        subscriptions={
            f"SUB-00{i}": {"id": f"SUB-00{i}", "customer_id": f"CUS-00{i}", "plan": p, "status": "active"}
            for i, p in zip(range(1, 6), ["pro", "starter", "pro", "starter", "enterprise"])
        },
        invoices={
            "INV-1001": {"id": "INV-1001", "customer_id": "CUS-001", "amount": 120.0, "status": "paid", "refunded": 0.0},
            "INV-1002": {"id": "INV-1002", "customer_id": "CUS-001", "amount": 49.0, "status": "paid", "refunded": 0.0},
            "INV-1003": {"id": "INV-1003", "customer_id": "CUS-002", "amount": 99.0, "status": "open", "refunded": 0.0},
            "INV-1004": {"id": "INV-1004", "customer_id": "CUS-003", "amount": 20.0, "status": "paid", "refunded": 0.0},
            "INV-1005": {"id": "INV-1005", "customer_id": "CUS-004", "amount": 99.0, "status": "open", "refunded": 0.0},
        },
        tickets={},
        refunds={},
    )


reset()

ALL, WRITERS, ADMIN = ["viewer", "agent", "admin"], ["agent", "admin"], ["admin"]


def ext(risk: str, roles: list[str]) -> dict:
    return {"x-risk": risk, "x-roles": roles}


def guard(*roles: str):
    def dep(x_role: str = Header("viewer")):
        if x_role not in roles:
            raise HTTPException(403, f"role '{x_role}' not allowed; need one of {list(roles)}")
        return x_role
    return dep


def need(table: str, key: str):
    obj = DB[table].get(key)
    if not obj:
        raise HTTPException(404, f"{table[:-1]} {key} not found")
    return obj


# ---------- reads ----------
@app.get("/customers", operation_id="list_customers", summary="Search customers by email or name",
         openapi_extra=ext("read", ALL), dependencies=[Depends(guard(*ALL))])
def list_customers(email: str | None = Query(None, description="Exact email"),
                   name: str | None = Query(None, description="Case-insensitive name substring")):
    items = list(DB["customers"].values())
    if email:
        items = [c for c in items if c["email"].lower() == email.lower()]
    if name:
        items = [c for c in items if name.lower() in c["name"].lower()]
    return {"items": items}


@app.get("/customers/{customer_id}", operation_id="get_customer", summary="Get a customer by id",
         openapi_extra=ext("read", ALL), dependencies=[Depends(guard(*ALL))])
def get_customer(customer_id: str):
    return need("customers", customer_id)


@app.get("/customers/{customer_id}/subscription", operation_id="get_subscription",
         summary="Get a customer's current subscription and plan",
         openapi_extra=ext("read", ALL), dependencies=[Depends(guard(*ALL))])
def get_subscription(customer_id: str):
    need("customers", customer_id)
    for s in DB["subscriptions"].values():
        if s["customer_id"] == customer_id:
            return s
    raise HTTPException(404, "no subscription")


@app.get("/customers/{customer_id}/invoices", operation_id="list_invoices",
         summary="List a customer's invoices, optionally filtered by status",
         openapi_extra=ext("read", ALL), dependencies=[Depends(guard(*ALL))])
def list_invoices(customer_id: str, status: Literal["paid", "open", "refunded"] | None = None):
    need("customers", customer_id)
    items = [i for i in DB["invoices"].values() if i["customer_id"] == customer_id]
    if status:
        items = [i for i in items if i["status"] == status]
    return {"items": items}


@app.get("/invoices/{invoice_id}", operation_id="get_invoice", summary="Get a single invoice by id",
         openapi_extra=ext("read", ALL), dependencies=[Depends(guard(*ALL))])
def get_invoice(invoice_id: str):
    return need("invoices", invoice_id)


# ---------- writes ----------
class TicketIn(BaseModel):
    customer_id: str = Field(min_length=1)
    subject: str = Field(min_length=3, max_length=200)
    priority: Literal["low", "normal", "high"] = "normal"


@app.post("/tickets", operation_id="create_ticket", status_code=201,
          summary="Create a support ticket for a customer",
          openapi_extra=ext("low_write", WRITERS), dependencies=[Depends(guard(*WRITERS))])
def create_ticket(body: TicketIn, x_dry_run: bool = Header(False)):
    need("customers", body.customer_id)
    if x_dry_run:
        return {"dry_run": True, "summary": f"Would open a {body.priority}-priority ticket for "
                f"{body.customer_id}: '{body.subject}'"}
    tid = f"TCK-{len(DB['tickets']) + 1:04d}"
    DB["tickets"][tid] = {"id": tid, **body.model_dump(), "status": "open"}
    return DB["tickets"][tid]


class PlanIn(BaseModel):
    plan: Literal["starter", "pro", "enterprise"]


@app.put("/subscriptions/{subscription_id}/plan", operation_id="update_plan",
         summary="Change a subscription's plan (upgrade or downgrade)",
         openapi_extra=ext("low_write", WRITERS), dependencies=[Depends(guard(*WRITERS))])
def update_plan(subscription_id: str, body: PlanIn, x_dry_run: bool = Header(False)):
    sub = need("subscriptions", subscription_id)
    if sub["plan"] == body.plan:
        raise HTTPException(409, f"already on {body.plan}")
    if x_dry_run:
        return {"dry_run": True, "summary": f"Would change {subscription_id} from {sub['plan']} to {body.plan}"}
    sub["plan"] = body.plan
    return sub


class RefundIn(BaseModel):
    invoice_id: str
    amount: float = Field(gt=0)
    reason: str = Field(min_length=3)


@app.post("/refunds", operation_id="create_refund", status_code=201,
          summary="Issue a refund against a paid invoice (money movement)",
          openapi_extra=ext("high_write", ADMIN), dependencies=[Depends(guard(*ADMIN))])
def create_refund(body: RefundIn, x_dry_run: bool = Header(False)):
    inv = need("invoices", body.invoice_id)
    if inv["status"] != "paid":
        raise HTTPException(409, f"invoice is '{inv['status']}', only paid invoices can be refunded")
    remaining = round(inv["amount"] - inv["refunded"], 2)
    if body.amount > remaining:
        raise HTTPException(422, f"amount {body.amount} exceeds refundable balance {remaining}")
    if x_dry_run:
        return {"dry_run": True, "summary": f"Would refund ${body.amount:.2f} on {inv['id']} "
                f"(reason: {body.reason}); refundable balance would go from ${remaining:.2f} "
                f"to ${remaining - body.amount:.2f}"}
    inv["refunded"] = round(inv["refunded"] + body.amount, 2)
    if inv["refunded"] >= inv["amount"]:
        inv["status"] = "refunded"
    rid = f"REF-{len(DB['refunds']) + 1:04d}"
    DB["refunds"][rid] = {"id": rid, **body.model_dump()}
    return DB["refunds"][rid]
