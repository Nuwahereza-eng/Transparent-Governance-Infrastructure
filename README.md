# 🧠 ProcureChain — Corruption-Resistant Procurement System

A web-based platform for transparent public procurement built around eight anti-corruption pillars:

1. **4-stage approval workflow** — department → financial → compliance → final. No single person can push a contract through.
2. **AI risk engine** — every bid is scored 0–100 with a human-readable explanation.
3. **Hash-chained audit log** — every critical action is appended to a SHA-256-linked chain. Verifiable live.
4. **Citizen feedback portal** — anyone (anonymously if needed) can file concerns; moderators must act and every action is chained.
5. **Contractor reputation** — composite scoring on wins, completion rate, avg risk and citizen complaints.
6. **Milestone + evidence tracking** — contract progress is broken into milestones; deliverables uploaded as evidence.
7. **Budget transparency** — allocation → release → spend tracked per contract; over-spending is rejected at the API.
8. **Open Data portal** — every dataset (tenders, bids, contracts, contractors, audit, feedback) downloadable as CSV or JSON.

Layered on top: **rate limiting** on auth/feedback to deter abuse, **trend + regional analytics** to spot patterns.

> *“Corruption is harder when every decision is transparent, traceable, multi-signed and algorithmically verified.”*

---

## Quick start (under 10 minutes)

Requirements: **Python 3.10+** only. SQLite ships with Python; no other database needed.

```bash
./run.sh
```

That script will:

1. Create a `.venv` virtual environment in `backend/`.
2. Install dependencies from [backend/requirements.txt](backend/requirements.txt).
3. Seed a demo database (only on first run).
4. Launch the API + frontend at <http://localhost:8000>.

Open <http://localhost:8000> in your browser.

### Manual setup (if you don't want the script)

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python seed.py        # one-time demo data
uvicorn app:app --reload
```

---

## Demo accounts

Pre-seeded by [backend/seed.py](backend/seed.py):

| Role                | Email                  | Password        |
| ------------------- | ---------------------- | --------------- |
| Admin               | admin@gov.demo         | admin123        |
| Procurement Officer | officer@gov.demo       | officer123      |
| Compliance Officer  | compliance@gov.demo    | compliance123   |
| Auditor             | auditor@gov.demo       | auditor123      |
| Contractor          | buildco@firm.demo      | contractor123   |
| Contractor          | monopoly@firm.demo     | contractor123   |
| Citizen             | citizen@gov.demo       | citizen123      |

The seed deliberately includes a **suspicious bid** (MonopolyCorp, ICT tender, +45% over budget, missing proposal, implausible timeline) and **awards it on purpose** so judges can see:

- the **AI flags it as High risk** with explanations,
- the **rank-override** is permanently logged on the audit chain,
- the citizen transparency dashboard shows the high risk score,
- MonopolyCorp's **reputation score drops** to *Fair* on the contractor page,
- a citizen has already filed a **feedback report** about the award.

---

## Walkthrough for judges (≈ 5 minutes)

1. Open <http://localhost:8000> — landing page shows live system snapshot.
2. Visit **Projects** (public, no login) — click an awarded amount to drill into milestones, evidence, budget and feedback for that contract.
3. Visit **Analytics** — KPIs, charts, trend graph (last 90 days) and regional drill-down table.
4. Visit **Contractors** — reputation scores; note MonopolyCorp drops to *Fair* because of risk + citizen feedback.
5. Visit **Feedback** — submit a report anonymously (no login). Then sign in as compliance and moderate it.
6. Visit **Audit Chain** — click **Verify chain** to recompute every SHA-256 link (every approval, feedback action and budget transaction is on it).
7. Visit **Open Data** — download any dataset as CSV or JSON.
8. Sign in as `officer@gov.demo / officer123`, open the **Dashboard** — create a new tender and watch it enter the approval queue. Then sign in as `compliance@gov.demo` to walk it through **Approvals**.
9. (Optional) Sign in as `buildco@firm.demo / contractor123` to submit a bid; watch it auto-ranked and risk-scored.

---

## Architecture

```
┌────────────────────────────────────────────────────────┐
│  Frontend (vanilla JS + Tailwind CDN + Chart.js CDN)   │
│  index | login | dashboard | transparency | analytics  │
│                | audit                                  │
└────────────────────────┬───────────────────────────────┘
                         │  fetch  /api/*
┌────────────────────────▼───────────────────────────────┐
│             FastAPI (backend/app.py)                   │
│  ┌──────────┐  ┌────────────┐  ┌──────────────────┐    │
│  │ auth     │  │ procurement│  │ transparency +   │    │
│  │ JWT/RBAC │  │ tender/bid │  │ analytics +audit │    │
│  └────┬─────┘  └─────┬──────┘  └────────┬─────────┘    │
│       │              │                   │             │
│       ▼              ▼                   ▼             │
│  ┌────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │ SQLAlchemy │  │ ai/risk.py   │  │ audit/chain.py │  │
│  │ (SQLite)   │  │ rule engine  │  │ SHA-256 chain  │  │
│  └────────────┘  └──────────────┘  └────────────────┘  │
└────────────────────────────────────────────────────────┘
```

See [docs/architecture.md](docs/architecture.md) for the full breakdown.

---

## API at a glance

Interactive Swagger UI: <http://localhost:8000/docs>

Highlights:

| Method | Path                                            | Description                                |
| ------ | ----------------------------------------------- | ------------------------------------------ |
| POST   | `/api/auth/register`                            | Create an account (rate-limited)           |
| POST   | `/api/auth/login`                               | OAuth2 password login → JWT (rate-limited) |
| GET    | `/api/tenders`                                  | Public list of tenders                     |
| POST   | `/api/tenders`                                  | Officer drafts tender → approval queue     |
| POST   | `/api/tenders/{id}/bids`                        | Contractor submits bid (auto-ranked)       |
| POST   | `/api/tenders/{id}/evaluate`                    | Officer re-runs AI ranking                 |
| POST   | `/api/tenders/{id}/award/{bid_id}`              | Officer awards contract                    |
| GET    | `/api/approvals/queue`                          | Approvals queue for current user's role    |
| POST   | `/api/tenders/{id}/approve`                     | Approve or reject the current stage        |
| GET    | `/api/transparency/projects`                    | Public, no-auth project feed               |
| GET    | `/api/analytics/summary`                        | KPIs + charts data                         |
| GET    | `/api/analytics/trends?days=N`                  | Daily activity series                      |
| GET    | `/api/analytics/regions`                        | Regional drill-down                        |
| GET    | `/api/contractors`                              | Reputation table                           |
| POST   | `/api/feedback`                                 | Submit citizen feedback (rate-limited)     |
| POST   | `/api/feedback/{id}/moderate`                   | Compliance moderates feedback              |
| GET    | `/api/contracts/{id}/milestones`                | List milestones                            |
| POST   | `/api/contracts/{id}/milestones`                | Add a milestone                            |
| POST   | `/api/contracts/{id}/milestones/{mid}/complete` | Mark milestone complete                    |
| GET    | `/api/contracts/{id}/budget`                    | Allocation / release / spend summary       |
| POST   | `/api/contracts/{id}/budget`                    | Record a budget transaction                |
| GET    | `/api/contracts/{id}/evidence`                  | List uploaded evidence                     |
| POST   | `/api/contracts/{id}/evidence`                  | Upload evidence file (≤ 5 MB)              |
| GET    | `/api/opendata`                                 | Open data index                            |
| GET    | `/api/opendata/{ds}.csv` / `.json`              | Download a dataset                         |
| GET    | `/api/audit`                                    | Latest audit-log entries                   |
| GET    | `/api/audit/verify`                             | Re-verify the entire SHA-256 chain         |

Full reference: [docs/api.md](docs/api.md).

---

## AI risk scoring (rule-based, fully explainable)

Implemented in [backend/ai/risk.py](backend/ai/risk.py):

| Signal                                                        | Points |
| ------------------------------------------------------------- | ------ |
| Bid price > 25% above peer average                            | +30    |
| Bid price < -40% below peer average (suspicious underbidding) | +20    |
| Bid > 110% of tender budget                                   | +15    |
| Contractor already won 3+ contracts                           | +20    |
| Contractor already won 2 contracts                            | +10    |
| Contractor bidding repeatedly in same category                | +15    |
| Per anomaly (missing proposal, bad timeline, zero price)      | +10    |

Levels: **0–29 Low**, **30–59 Medium**, **60–100 High**.

Final ranking uses a composite: `0.6 × normalized_price + 0.4 × risk/100` — so the cheapest *and* lowest-risk bid wins.

---

## Audit chain (blockchain simulation)

Implemented in [backend/audit/chain.py](backend/audit/chain.py).

Each entry stores:
- `prev_hash` — the previous entry's hash (genesis = `"0"*64`)
- `hash = SHA-256(prev_hash || timestamp || actor || action || entity || canonical-json-payload)`

The `/api/audit/verify` endpoint walks the table and re-hashes every row.
**Tamper test:** open `backend/data/app.db` with any SQLite tool, change a payload, then click *Verify chain* — the verifier will identify the broken entry.

---

## Project structure

```
.
├── run.sh                       # one-shot setup + start
├── README.md
├── backend/
│   ├── app.py                   # FastAPI entrypoint + rate limiter + static mount
│   ├── config.py
│   ├── database.py
│   ├── models.py                # SQLAlchemy models (incl. approvals, milestones, budget, feedback)
│   ├── schemas.py               # Pydantic schemas
│   ├── auth.py                  # JWT + role helpers
│   ├── seed.py                  # demo data (10 users, walked through approvals)
│   ├── requirements.txt
│   ├── ai/
│   │   └── risk.py              # corruption-risk engine
│   ├── audit/
│   │   └── chain.py             # hash-chained audit log
│   └── routers/
│       ├── auth_router.py
│       ├── procurement.py
│       ├── transparency.py      # + trend/region analytics
│       ├── approvals.py         # 4-stage approval workflow
│       ├── feedback.py          # citizen feedback + moderation
│       ├── contractors.py       # reputation scoring
│       ├── contracts_router.py  # milestones / evidence / budget
│       └── opendata.py          # CSV + JSON exports
├── frontend/
│   ├── index.html               # landing
│   ├── login.html
│   ├── dashboard.html           # officer/contractor workflow
│   ├── transparency.html        # public projects (drill into contracts)
│   ├── analytics.html           # KPIs + trends + regions
│   ├── audit.html               # audit-chain viewer + verifier
│   ├── approvals.html           # approval queue
│   ├── feedback.html            # citizen feedback portal
│   ├── contractors.html         # reputation table
│   ├── contract.html            # milestones / evidence / budget per contract
│   ├── opendata.html            # open-data downloads
│   ├── app.js
│   └── styles.css
└── docs/
    ├── architecture.md
    └── api.md
```

---

## Resetting the demo

```bash
rm backend/data/app.db
python backend/seed.py
```

---

## Notes & non-goals

- This is an MVP, not a production government system.
- SQLite is used for zero-setup. For production, set `DATABASE_URL` to PostgreSQL (Supabase/Neon).
- The audit chain is a *simulation* — the same approach can be anchored to a public blockchain (Sepolia, etc.) by periodically committing the head hash.
- The risk engine is intentionally rule-based + explainable. ML can be layered on top later without breaking the API contract.
