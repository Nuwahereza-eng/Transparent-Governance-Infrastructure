"""Open data portal — CSV / JSON exports of every public dataset."""
import csv
import io
import json
from typing import Iterable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from database import get_db
from models import (
    AuditLog, Bid, Contract, FeedbackReport, Role, Tender, User,
)

router = APIRouter(prefix="/api/opendata", tags=["opendata"])


def _csv_response(rows: Iterable[dict], filename: str) -> StreamingResponse:
    rows = list(rows)
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    else:
        buf.write("")
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _json_response(rows, filename: str) -> Response:
    return Response(
        content=json.dumps(rows, default=str, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _serialize(dataset: str, db: Session) -> list[dict]:
    if dataset == "tenders":
        return [
            {
                "id": t.id, "title": t.title, "category": t.category, "region": t.region,
                "budget": t.budget, "status": t.status.value,
                "current_stage": t.current_stage,
                "deadline": t.deadline.isoformat(),
                "created_at": t.created_at.isoformat(),
                "bid_count": len(t.bids),
            }
            for t in db.query(Tender).all()
        ]
    if dataset == "bids":
        return [
            {
                "id": b.id, "tender_id": b.tender_id,
                "contractor_id": b.contractor_id,
                "contractor_name": b.contractor.full_name if b.contractor else None,
                "price": b.price, "delivery_days": b.delivery_days,
                "status": b.status.value, "rank": b.rank,
                "risk_score": b.risk_score, "risk_level": b.risk_level,
                "created_at": b.created_at.isoformat(),
            }
            for b in db.query(Bid).all()
        ]
    if dataset == "contracts":
        return [
            {
                "id": c.id, "tender_id": c.tender_id, "bid_id": c.bid_id,
                "contractor_id": c.contractor_id,
                "contractor_name": c.contractor.full_name if c.contractor else None,
                "awarded_amount": c.awarded_amount,
                "awarded_at": c.awarded_at.isoformat(),
                "progress_status": c.progress_status,
                "progress_percent": c.progress_percent,
            }
            for c in db.query(Contract).all()
        ]
    if dataset == "contractors":
        # Compute reputation inline.
        from routers.contractors import _build
        users = db.query(User).filter(User.role == Role.contractor).all()
        return [_build(db, u).model_dump() for u in users]
    if dataset == "audit":
        return [
            {
                "id": a.id, "timestamp": a.timestamp.isoformat(),
                "actor": a.actor_email, "action": a.action,
                "entity_type": a.entity_type, "entity_id": a.entity_id,
                "payload": a.payload, "prev_hash": a.prev_hash, "hash": a.hash,
            }
            for a in db.query(AuditLog).order_by(AuditLog.id.asc()).all()
        ]
    if dataset == "feedback":
        return [
            {
                "id": f.id, "tender_id": f.tender_id, "contract_id": f.contract_id,
                "category": f.category, "message": f.message,
                "status": f.status.value, "anonymous": f.reporter_id is None,
                "created_at": f.created_at.isoformat(),
            }
            for f in db.query(FeedbackReport).all()
        ]
    raise HTTPException(404, f"Unknown dataset: {dataset}")


DATASETS = ["tenders", "bids", "contracts", "contractors", "audit", "feedback"]


@router.get("")
def list_datasets():
    return {
        "datasets": DATASETS,
        "formats": ["json", "csv"],
        "example": "/api/opendata/tenders.csv",
    }


@router.get("/{dataset}.csv")
def export_csv(dataset: str, db: Session = Depends(get_db)):
    rows = _serialize(dataset, db)
    # Flatten nested dicts for CSV (everything is shallow already).
    return _csv_response(rows, f"{dataset}.csv")


@router.get("/{dataset}.json")
def export_json(dataset: str, db: Session = Depends(get_db)):
    rows = _serialize(dataset, db)
    return _json_response(rows, f"{dataset}.json")
