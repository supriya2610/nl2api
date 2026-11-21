from assistant.schema import load_tools, search
from assistant.validate import validate_args


def test_risk_and_roles_come_from_openapi(client):
    tools = load_tools(client.get("/openapi.json").json())
    assert tools["get_customer"].risk == "read"
    assert tools["create_refund"].risk == "high_write" and tools["create_refund"].roles == ["admin"]
    assert tools["update_plan"].locs == {"subscription_id": "path", "plan": "body"}
    assert "X-Role" not in str(tools["create_refund"].input_schema)  # headers are not model-controlled


def test_search_finds_refund(client):
    tools = load_tools(client.get("/openapi.json").json())
    assert search(tools, "give them their money back refund", k=2)[0].operation_id == "create_refund"


def test_validation_rejects_bad_args(client):
    t = load_tools(client.get("/openapi.json").json())["create_refund"]
    assert validate_args(t, {"invoice_id": "INV-1", "amount": -5, "reason": "dup charge"})      # amount > 0
    assert validate_args(t, {"invoice_id": "INV-1", "amount": 5})                              # missing reason
    assert validate_args(t, {"invoice_id": "INV-1", "amount": 5, "reason": "dup", "x": 1})     # extra field
    assert not validate_args(t, {"invoice_id": "INV-1", "amount": 5, "reason": "dup charge"})
