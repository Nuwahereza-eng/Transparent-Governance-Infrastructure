# Architecture

## Layered design

| Layer        | Files                                                            | Purpose |
|--------------|------------------------------------------------------------------|---------|
| HTTP / UI    | `frontend/*.html`, `frontend/app.js`                             | Static SPA-like pages served by FastAPI. Tailwind + Chart.js via CDN, no build step. |
| API gateway  | `backend/app.py`                                                 | FastAPI app, CORS, static mount, router wiring. |
| Auth         | `backend/auth.py`, `backend/routers/auth_router.py`              | bcrypt password hashing, JWT (HS256), `OAuth2PasswordBearer` deps, `require_roles()`. |
| Procurement  | `backend/routers/procurement.py`                                 | Tender / bid / contract endpoints. Calls AI engine + audit chain on every state change. |
| Transparency | `backend/routers/transparency.py`                                | Public read-only endpoints + analytics aggregations. |
| AI           | `backend/ai/risk.py`                                             | Rule-based risk score, composite (price+risk) score, ranking. |
| Audit        | `backend/audit/chain.py`                                         | Hash-chained, append-only log with verify. |
| Data         | `backend/models.py`, `backend/database.py`                       | SQLAlchemy 2.x, SQLite by default. |

## Request flow — submit a bid

```
Contractor → POST /api/tenders/{id}/bids
  ↓
auth.require_roles(contractor)         (JWT verified)
  ↓
Validate tender is open, deadline > now, no duplicate bid
  ↓
INSERT bid                             (DB)
  ↓
audit.chain.append("bid.submit", …)    (writes SHA-256-linked row)
  ↓
ai.risk.rerank_tender(tender)
   for each bid: score_bid + composite_score; bid.rank = …
  ↓
Return bid (with risk_score, level, explanation, rank)
```

## Audit chain integrity

Each `audit_logs` row stores:
- `timestamp`, `actor_email`, `action`, `entity_type`, `entity_id`, `payload` (canonical JSON)
- `prev_hash` — the previous row's hash (genesis = `"0"*64`)
- `hash = SHA-256(prev_hash || ts || actor || action || entity_type || entity_id || payload)`

`GET /api/audit/verify` walks the table in id-ascending order and re-derives each hash.
Mismatch ⇒ chain broken.

## Roles & access

| Role        | Can …                                                                 |
| ----------- | --------------------------------------------------------------------- |
| `admin`     | everything (including all officer/contractor actions)                 |
| `officer`   | create tenders, evaluate, award, cancel, update progress              |
| `contractor`| submit bids on open tenders, update progress on own contracts         |
| `citizen`   | read-only views (transparency, analytics, audit chain)                |

`backend/auth.py::require_roles(*roles)` enforces this; admin always passes.

## Storage

- SQLite file at `backend/data/app.db`.
- Switch to PostgreSQL by setting `DATABASE_URL=postgresql+psycopg2://…` — no code changes required.

## Deployment notes

- **Frontend:** already bundled — any static host can serve `frontend/` if you split it out.
- **Backend:** `uvicorn app:app` works on Render / Railway / Fly free tiers. Set `SECRET_KEY` env var.
- **Database:** point `DATABASE_URL` at Supabase/Neon for free Postgres.
