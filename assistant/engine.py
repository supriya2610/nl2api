"""Plan -> validate -> dry-run -> (approve) -> execute, with a full audit log in SQLite.
Risk level, required roles and confirmation come from the OpenAPI schema, never from the LLM."""
import json
import sqlite3
import time
import uuid
from typing import Any
from urllib.parse import quote

from .models import Plan, RunState
from .planner import Planner
from .schema import Tool, load_tools, search
from .validate import REF_RE, is_ref, validate_args


class Engine:
    def __init__(self, client, planner: Planner, db_path: str = ":memory:", confirm_low_risk: bool = False):
        """`client` is anything httpx-like (httpx.Client or fastapi.testclient.TestClient)."""
        self.client, self.planner, self.confirm_low_risk = client, planner, confirm_low_risk
        self.tools: dict[str, Tool] = load_tools(client.get("/openapi.json").json())
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, state TEXT, status TEXT, updated REAL);
            CREATE TABLE IF NOT EXISTS events(n INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT,
                                              ts REAL, kind TEXT, payload TEXT);""")

    # ---------- persistence / audit ----------
    def _save(self, st: RunState) -> RunState:
        self.db.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?,?)",
                        (st.run_id, st.model_dump_json(), st.status, time.time()))
        self.db.commit()
        return st

    def load(self, run_id: str) -> RunState:
        row = self.db.execute("SELECT state FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        return RunState.model_validate_json(row[0])

    def _log(self, run_id: str, kind: str, payload: Any) -> None:
        self.db.execute("INSERT INTO events(run_id,ts,kind,payload) VALUES(?,?,?,?)",
                        (run_id, time.time(), kind, json.dumps(payload, default=str)))
        self.db.commit()

    def events(self, run_id: str) -> list[dict]:
        rows = self.db.execute("SELECT kind,payload FROM events WHERE run_id=? ORDER BY n", (run_id,))
        return [{"kind": k, **json.loads(p)} if isinstance(json.loads(p), dict) else {"kind": k, "data": json.loads(p)}
                for k, p in rows]

    # ---------- planning ----------
    def _check_plan(self, plan: Plan, role: str) -> list[str]:
        errs, seen = [], set()
        for s in plan.steps:
            tool = self.tools.get(s.operation_id)
            if not tool:
                errs.append(f"{s.id}: unknown operation '{s.operation_id}'")
                continue
            if role not in tool.roles:
                errs.append(f"PERMISSION: role '{role}' may not call {tool.operation_id} (needs {tool.roles})")
            for k, v in s.args.items():
                if is_ref(v) and REF_RE.match(v).group(1) not in seen:
                    errs.append(f"{s.id}.{k}: reference {v} points to a step that has not run yet")
            errs += [f"{s.id}: {e}" for e in validate_args(tool, s.args, static=True)]
            seen.add(s.id)
        return errs

    def propose(self, request: str, role: str) -> RunState:
        run_id = uuid.uuid4().hex[:8]
        self._log(run_id, "request", {"request": request, "role": role})
        cands = search(self.tools, request, k=8)
        plan, errs, feedback = None, [], None
        for attempt in range(2):                      # one repair attempt
            try:
                plan = self.planner.plan(request, cands, role, feedback)
            except ValueError as e:
                plan, errs, feedback = None, [f"invalid plan format: {e}"], f"invalid plan format: {e}"
                continue
            errs = self._check_plan(plan, role)
            self._log(run_id, "validation", {"attempt": attempt, "errors": errs})
            if not errs or plan.refusal or plan.clarification or any(e.startswith("PERMISSION") for e in errs):
                break
            feedback = "; ".join(errs)
        st = RunState(run_id=run_id, request=request, role=role, plan=plan or Plan())
        if plan is None:
            st.status, st.message = "rejected", "Could not produce a valid plan: " + "; ".join(errs)
        elif plan.refusal:
            st.status, st.message = "rejected", plan.refusal
        elif plan.clarification:
            st.status, st.message = "needs_clarification", plan.clarification
        elif errs:
            st.status, st.message = "rejected", "; ".join(errs)
        self._log(run_id, "plan", {"plan": st.plan.model_dump(), "status": st.status})
        return self._save(st)

    # ---------- execution ----------
    @staticmethod
    def _resolve(val, results):
        m = REF_RE.match(val) if isinstance(val, str) else None
        if not m:
            return val
        cur = results[m.group(1)]
        for part in m.group(2).split("."):
            cur = cur[int(part)] if isinstance(cur, list) else cur[part]
        return cur

    def _call(self, tool: Tool, args: dict, role: str, dry: bool = False):
        path = tool.path.format(**{k: quote(str(v), safe="") for k, v in args.items() if tool.locs[k] == "path"})
        query = {k: v for k, v in args.items() if tool.locs[k] == "query"}
        body = {k: v for k, v in args.items() if tool.locs[k] == "body"}
        headers = {"X-Role": role, **({"X-Dry-Run": "true"} if dry else {})}
        r = self.client.request(tool.method, path, params=query or None,
                                json=body if body else None, headers=headers)
        try:
            data = r.json()
        except ValueError:
            data = r.text
        return r.status_code, data

    def _fail(self, st: RunState, msg: str) -> RunState:
        st.status, st.message = "failed", msg
        self._log(st.run_id, "failure", {"message": msg})
        return self._save(st)

    def execute(self, run_id: str) -> RunState:
        st = self.load(run_id)
        if st.status not in ("planned", "running"):
            return st
        st.status, st.pending_preview = "running", None
        while st.cursor < len(st.plan.steps):
            step = st.plan.steps[st.cursor]
            tool = self.tools[step.operation_id]
            try:
                args = {k: self._resolve(v, st.results) for k, v in step.args.items()}
            except (KeyError, IndexError, ValueError, TypeError) as e:
                return self._fail(st, f"{step.id}: could not resolve inputs from earlier steps ({e!r})")
            errs = validate_args(tool, args)
            self._log(run_id, "validation", {"step": step.id, "args": args, "errors": errs})
            if errs:
                return self._fail(st, f"{step.id}: invalid parameters: {'; '.join(errs)}")

            if tool.is_write and step.id not in st.approved_steps:
                code, preview = self._call(tool, args, st.role, dry=True)
                self._log(run_id, "dry_run", {"step": step.id, "status": code, "preview": preview})
                if code >= 400:
                    return self._fail(st, f"{step.id}: dry-run rejected by API ({code}): {preview.get('detail', preview)}")
                if tool.risk == "high_write" or self.confirm_low_risk:
                    st.status = "awaiting_approval"
                    st.pending_preview = {"step": step.id, "operation": tool.operation_id, "risk": tool.risk,
                                          "args": args, "summary": preview.get("summary")}
                    self._log(run_id, "approval_requested", st.pending_preview)
                    return self._save(st)

            code, data = self._call(tool, args, st.role)
            self._log(run_id, "api_call", {"step": step.id, "op": tool.operation_id, "args": args,
                                           "status": code, "response": data})
            if code >= 400:
                return self._fail(st, f"{step.id}: {tool.operation_id} failed ({code}): "
                                      f"{data.get('detail', data) if isinstance(data, dict) else data}")
            st.results[step.id] = data
            if step.expect_single:
                items = data.get("items", []) if isinstance(data, dict) else []
                if len(items) != 1:
                    st.status = "needs_clarification"
                    st.candidates = items
                    st.message = ("No match found. Please check the details." if not items else
                                  f"{len(items)} matches found: " + ", ".join(f"{c['id']} ({c['name']}, {c['email']})" for c in items)
                                  + ". Which one do you mean? Reply with the CUS id.")
                    return self._save(st)
            st.cursor += 1
            self._save(st)

        st.status = "completed"
        st.answer = "\n".join(f"- {s.reason or s.operation_id}: {json.dumps(st.results.get(s.id))[:220]}"
                              for s in st.plan.steps)
        self._log(run_id, "answer", {"answer": st.answer})
        return self._save(st)

    def approve(self, run_id: str, approved: bool) -> RunState:
        st = self.load(run_id)
        if st.status != "awaiting_approval" or not st.pending_preview:
            raise ValueError(f"run {run_id} is not awaiting approval (status={st.status})")
        self._log(run_id, "approval", {"step": st.pending_preview["step"], "approved": approved, "role": st.role})
        if not approved:
            st.status, st.message, st.pending_preview = "cancelled", "Action rejected by user; nothing was changed.", None
            return self._save(st)
        st.approved_steps.append(st.pending_preview["step"])
        st.status = "running"
        self._save(st)
        return self.execute(run_id)

    def ask(self, request: str, role: str) -> RunState:
        st = self.propose(request, role)
        return self.execute(st.run_id) if st.status == "planned" else st
