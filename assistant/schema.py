"""OpenAPI -> Tool catalog, plus keyword retrieval of candidate endpoints."""
import re
from dataclasses import dataclass, field

HTTP = {"get", "post", "put", "patch", "delete"}


@dataclass
class Tool:
    operation_id: str
    method: str
    path: str
    summary: str
    risk: str                      # read | low_write | high_write
    roles: list[str]
    input_schema: dict             # flat JSON Schema over path+query+body fields
    locs: dict[str, str] = field(default_factory=dict)  # arg -> path|query|body

    @property
    def is_write(self) -> bool:
        return self.risk != "read"


def _resolve(node, comps):
    if isinstance(node, dict):
        if "$ref" in node:
            return _resolve(comps[node["$ref"].split("/")[-1]], comps)
        return {k: _resolve(v, comps) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_resolve(x, comps) for x in node]
    return node


def _simplify(s: dict) -> dict:
    if "anyOf" in s:
        non_null = [x for x in s["anyOf"] if x.get("type") != "null"]
        rest = {k: v for k, v in s.items() if k != "anyOf"}
        if len(non_null) == 1:
            return {**rest, **non_null[0]}
    return s


def load_tools(spec: dict) -> dict[str, Tool]:
    comps = spec.get("components", {}).get("schemas", {})
    tools: dict[str, Tool] = {}
    for path, item in spec["paths"].items():
        for method, op in item.items():
            if method not in HTTP:
                continue
            props, required, locs = {}, [], {}
            for p in op.get("parameters", []):
                if p["in"] not in ("path", "query"):   # headers (role, dry-run) are the engine's job
                    continue
                sch = _simplify(_resolve(p.get("schema", {}), comps))
                if p.get("description"):
                    sch["description"] = p["description"]
                props[p["name"]], locs[p["name"]] = sch, p["in"]
                if p.get("required"):
                    required.append(p["name"])
            body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
            if body:
                body = _resolve(body, comps)
                for k, v in body.get("properties", {}).items():
                    props[k], locs[k] = _simplify(v), "body"
                required += body.get("required", [])
            tools[op["operationId"]] = Tool(
                operation_id=op["operationId"], method=method.upper(), path=path,
                summary=op.get("summary", ""),
                risk=op.get("x-risk", "high_write" if method != "get" else "read"),  # fail closed
                roles=op.get("x-roles", []),
                input_schema={"type": "object", "properties": props, "required": required,
                              "additionalProperties": False},
                locs=locs)
    return tools


SYN = {"refund": ["refund", "reimburse"], "upgrade": ["plan", "change"], "downgrade": ["plan", "change"],
       "billing": ["invoice"], "bill": ["invoice"], "charge": ["invoice", "refund"], "unpaid": ["invoice"],
       "issue": ["ticket"], "problem": ["ticket"], "find": ["search", "customer"], "lookup": ["customer"]}


def _tok(text: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 2}


def search(tools: dict[str, Tool], query: str, k: int = 8) -> list[Tool]:
    q = _tok(query)
    for w in list(q):
        q |= {x.rstrip("s") for x in SYN.get(w, [])}
    scored = []
    for t in tools.values():
        doc = _tok(f"{t.summary} {t.operation_id.replace('_', ' ')} {t.path}")
        scored.append((len(q & doc), t))
    scored.sort(key=lambda x: -x[0])
    hits = [t for s, t in scored if s > 0][:k]
    return hits or [t for _, t in scored][:k]
