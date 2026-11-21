# Natural Language → API Assistant

Safe tool use over a real API contract (OpenAPI), not a loose list of tools.

```
request ─► retrieve candidate ops (schema search) ─► planner (LLM or rules) ─► structured Plan
        ─► validate: op exists · role allowed · JSON-Schema args · refs point backwards
        ─► per step: resolve refs ─► re-validate ─► [write? dry-run via X-Dry-Run]
        ─► high_write? pause → approval card → resume   ─► execute ─► typed RunState + audit log
```

**Design rules (interview talking points)**
- Risk, required roles and confirmation come from `x-risk` / `x-roles` in the OpenAPI doc, never from the model. Missing metadata fails closed (`high_write`).
- The model can't set headers (role, dry-run) – only schema'd path/query/body args.
- Lookups use `expect_single`: 0 or >1 matches → clarifying question, never a guess.
- Dry-run is server-side, so the preview reflects real business rules (e.g. refund > balance fails at dry-run).
- Every request, validation result, dry-run, approval and API response is logged to SQLite (`events`).

## Run
```
pip install -r requirements.txt
pytest -q                                   # ~50 tests, no network / no API key needed
streamlit run app.py                        # in-process mock API; set ANTHROPIC_API_KEY for the LLM planner
docker compose up --build                   # api :8000 (Swagger at /docs) + ui :8501
```
Without `ANTHROPIC_API_KEY` the deterministic `RuleBasedPlanner` is used; with it, `LLMPlanner` (default `claude-haiku-5-5`, override with `PLANNER_MODEL`) plans and gets one repair attempt on validation errors.

## Demo script
1. `show open invoices for alan@example.com` (viewer) – 2-step read chain
2. `upgrade alan@example.com to pro` (agent) – dry-run then auto-execute
3. `refund $40 on INV-1002 because duplicate charge` (admin) – approval card → approve
4. `find Sam Lee` – ambiguity → clarification; `refund ...` as agent – blocked; `delete all customers` – refused

## Next steps
Embedding-based endpoint retrieval, VCR-style recorded LLM fixtures, a golden-set accuracy report per planner, idempotency keys on writes.
# nl2api
# python-development-
