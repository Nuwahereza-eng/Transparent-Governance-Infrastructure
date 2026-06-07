# API Reference

Base URL: `http://localhost:8000`
Interactive docs: `/docs` (Swagger), `/redoc` (ReDoc).

All authenticated endpoints expect:
```
Authorization: Bearer <jwt>
```

## Auth

### `POST /api/auth/register`
```json
{ "email": "x@y.z", "full_name": "Name", "password": "secret123",
  "role": "contractor", "organization": "Acme" }
```
→ `{ access_token, token_type, user }`

### `POST /api/auth/login`
`application/x-www-form-urlencoded` with `username`, `password`.

### `GET /api/auth/me`
Returns the authenticated user.

---

## Tenders

| Method | Path                              | Auth          |
| ------ | --------------------------------- | ------------- |
| GET    | `/api/tenders`                    | public        |
| GET    | `/api/tenders/{id}`               | public        |
| POST   | `/api/tenders`                    | officer/admin |
| POST   | `/api/tenders/{id}/cancel`        | officer/admin |
| POST   | `/api/tenders/{id}/evaluate`      | officer/admin |
| POST   | `/api/tenders/{id}/award/{bidId}` | officer/admin |

Create payload:
```json
{ "title": "School Roof Repair", "description": "...",
  "category": "construction", "region": "north",
  "budget": 50000, "deadline": "2026-12-31T23:59:00" }
```

## Bids

| Method | Path                              | Auth                |
| ------ | --------------------------------- | ------------------- |
| GET    | `/api/tenders/{id}/bids`          | public              |
| POST   | `/api/tenders/{id}/bids`          | contractor/admin    |

Create payload:
```json
{ "price": 48000, "delivery_days": 90, "proposal": "..." }
```
Response includes `risk_score`, `risk_level`, `risk_explanation`, `rank`, `composite_score`.

## Contracts

| Method | Path                                  | Auth                          |
| ------ | ------------------------------------- | ----------------------------- |
| GET    | `/api/contracts`                      | public                        |
| POST   | `/api/contracts/{id}/progress`        | officer / contract owner      |

Progress payload:
```json
{ "progress_status": "in_progress", "progress_percent": 35 }
```

## Transparency

| Method | Path                              | Auth   |
| ------ | --------------------------------- | ------ |
| GET    | `/api/transparency/projects`      | public |
| GET    | `/api/analytics/summary`          | public |

## Audit

| Method | Path                  | Auth   |
| ------ | --------------------- | ------ |
| GET    | `/api/audit?limit=N`  | public |
| GET    | `/api/audit/verify`   | public |

`/audit/verify` returns either:
```json
{ "valid": true, "entries_checked": 42, "head": "abc…" }
```
or
```json
{ "valid": false, "broken_at": 17, "reason": "hash mismatch (data tampered)",
  "entries_checked": 17 }
```

## Error format

FastAPI's standard:
```json
{ "detail": "human-readable message" }
```
Common codes: `400` validation, `401` unauth, `403` role, `404` not found.
