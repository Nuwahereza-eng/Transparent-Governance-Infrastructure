"""Audit log + analytics + transparency endpoints."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from audit import chain
from auth import require_roles
from database import get_db
from models import (
    AuditAnchor, AuditLog, Bid, Contract, Role, Tender, TenderStatus, User,
)
from schemas import (
    AnchorPublish, AuditAnchorOut, AuditAnchorState, AuditOut,
)

router = APIRouter(prefix="/api", tags=["transparency"])


@router.get("/audit", response_model=list[AuditOut])
def list_audit(limit: int = 200, db: Session = Depends(get_db)):
    rows = (
        db.query(AuditLog)
        .order_by(AuditLog.id.desc())
        .limit(limit)
        .all()
    )
    return rows


@router.get("/audit/verify")
def verify_audit(db: Session = Depends(get_db)):
    return chain.verify(db)


# ---------- Public anchors (tamper-evidence) ----------
def _anchor_out(a: AuditAnchor) -> AuditAnchorOut:
    return AuditAnchorOut(
        id=a.id, created_at=a.created_at,
        head_hash=a.head_hash, entries_count=a.entries_count,
        published_by_name=a.published_by.full_name if a.published_by else None,
        note=a.note or "", external_url=a.external_url,
    )


@router.get("/audit/anchors", response_model=list[AuditAnchorOut])
def list_anchors(limit: int = 50, db: Session = Depends(get_db)):
    """Public list of anchor commitments — each row is a point-in-time
    cryptographic fingerprint of the entire audit chain. Compare a freshly
    recomputed head against the latest anchor: if they differ, the local DB
    has been tampered with."""
    rows = (
        db.query(AuditAnchor)
        .order_by(AuditAnchor.id.desc())
        .limit(limit)
        .all()
    )
    return [_anchor_out(a) for a in rows]


@router.get("/audit/anchor", response_model=AuditAnchorState)
def anchor_state(db: Session = Depends(get_db)):
    """Current chain state + last published anchor + drift between them."""
    head = chain.get_last_hash(db)
    entries = db.query(func.count(AuditLog.id)).scalar() or 0
    last = (
        db.query(AuditAnchor)
        .order_by(AuditAnchor.id.desc())
        .first()
    )
    drift = entries - (last.entries_count if last else 0)
    in_sync = bool(last and last.head_hash == head)
    return AuditAnchorState(
        current_head=head,
        current_entries=entries,
        last_anchor=_anchor_out(last) if last else None,
        drift_since_last=max(drift, 0),
        in_sync=in_sync,
    )


@router.post("/audit/anchor", response_model=AuditAnchorOut)
def publish_anchor(
    payload: AnchorPublish | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.admin, Role.auditor)),
):
    """Publish the current chain head as a new anchor.

    Real-world deployment: a CI job (e.g. GitHub Actions) calls this
    endpoint daily, then commits the returned hash to a public Git repo so
    later tampering becomes externally verifiable."""
    head = chain.get_last_hash(db)
    entries = db.query(func.count(AuditLog.id)).scalar() or 0
    last = (
        db.query(AuditAnchor)
        .order_by(AuditAnchor.id.desc())
        .first()
    )
    if last and last.head_hash == head:
        raise HTTPException(
            400,
            "Chain head is unchanged since the last anchor — nothing new to publish.",
        )
    note = (payload.note or "").strip() if payload else ""
    external_url = (payload.external_url or "").strip() if payload else ""
    anchor = AuditAnchor(
        head_hash=head, entries_count=entries,
        published_by_id=user.id, note=note,
        external_url=external_url or None,
    )
    db.add(anchor)
    db.commit()
    db.refresh(anchor)
    # The anchor itself is also chain-logged — meta-evidence.
    chain.append(
        db, actor=user, action="audit.anchor.publish",
        entity_type="anchor", entity_id=anchor.id,
        payload={
            "head_hash": head, "entries_count": entries,
            "note": note, "external_url": external_url or None,
        },
    )
    return _anchor_out(anchor)


@router.get("/analytics/summary")
def analytics_summary(db: Session = Depends(get_db)):
    total_tenders = db.query(func.count(Tender.id)).scalar() or 0
    total_bids = db.query(func.count(Bid.id)).scalar() or 0
    total_contracts = db.query(func.count(Contract.id)).scalar() or 0
    total_awarded = db.query(func.coalesce(func.sum(Contract.awarded_amount), 0.0)).scalar() or 0.0
    avg_risk = db.query(func.avg(Bid.risk_score)).scalar()
    flagged = (
        db.query(func.count(Bid.id))
        .filter(Bid.risk_level == "High")
        .scalar() or 0
    )

    by_status = dict(
        db.query(Tender.status, func.count(Tender.id))
        .group_by(Tender.status)
        .all()
    )
    by_status = {k.value if hasattr(k, "value") else str(k): v for k, v in by_status.items()}

    # Average risk per region.
    region_rows = (
        db.query(Tender.region, func.avg(Bid.risk_score))
        .join(Bid, Bid.tender_id == Tender.id)
        .group_by(Tender.region)
        .all()
    )
    avg_risk_by_region = {r: round(s or 0, 1) for r, s in region_rows}

    category_rows = (
        db.query(Tender.category, func.avg(Bid.risk_score))
        .join(Bid, Bid.tender_id == Tender.id)
        .group_by(Tender.category)
        .all()
    )
    avg_risk_by_category = {r: round(s or 0, 1) for r, s in category_rows}

    # Top winning contractors.
    winner_rows = (
        db.query(User.full_name, func.count(Contract.id), func.sum(Contract.awarded_amount))
        .join(Contract, Contract.contractor_id == User.id)
        .group_by(User.id)
        .order_by(func.count(Contract.id).desc())
        .limit(5)
        .all()
    )
    top_winners = [
        {"name": n, "wins": int(c), "total_awarded": float(s or 0)}
        for n, c, s in winner_rows
    ]

    return {
        "total_tenders": total_tenders,
        "total_bids": total_bids,
        "total_contracts": total_contracts,
        "total_awarded": float(total_awarded),
        "average_risk_score": round(avg_risk, 1) if avg_risk is not None else None,
        "high_risk_bids": flagged,
        "tenders_by_status": by_status,
        "avg_risk_by_region": avg_risk_by_region,
        "avg_risk_by_category": avg_risk_by_category,
        "top_winners": top_winners,
    }


@router.get("/transparency/projects")
def transparency_projects(db: Session = Depends(get_db)):
    """Public, read-only project feed for citizens."""
    tenders = db.query(Tender).order_by(Tender.created_at.desc()).all()
    out = []
    for t in tenders:
        contract = t.contract
        out.append({
            "id": t.id,
            "title": t.title,
            "category": t.category,
            "region": t.region,
            "budget": t.budget,
            "status": t.status.value,
            "deadline": t.deadline.isoformat(),
            "bid_count": len(t.bids),
            "avg_risk": round(
                sum(b.risk_score or 0 for b in t.bids) / max(1, len(t.bids)), 1
            ) if t.bids else None,
            "contractor": contract.contractor.full_name if contract else None,
            "awarded_amount": contract.awarded_amount if contract else None,
            "progress_status": contract.progress_status if contract else None,
            "progress_percent": contract.progress_percent if contract else None,
            "contract_id": contract.id if contract else None,
        })
    return out


@router.get("/analytics/trends")
def analytics_trends(days: int = 90, db: Session = Depends(get_db)):
    """Time-series counts and avg risk grouped by day."""
    since = datetime.utcnow() - timedelta(days=days)

    bids_by_day = defaultdict(list)
    for b in db.query(Bid).filter(Bid.created_at >= since).all():
        key = b.created_at.date().isoformat()
        bids_by_day[key].append(b.risk_score or 0)

    contracts_by_day = defaultdict(int)
    for c in db.query(Contract).filter(Contract.awarded_at >= since).all():
        contracts_by_day[c.awarded_at.date().isoformat()] += 1

    tenders_by_day = defaultdict(int)
    for t in db.query(Tender).filter(Tender.created_at >= since).all():
        tenders_by_day[t.created_at.date().isoformat()] += 1

    days_list = sorted(set(list(bids_by_day) + list(contracts_by_day) + list(tenders_by_day)))
    return {
        "labels": days_list,
        "tenders_created": [tenders_by_day.get(d, 0) for d in days_list],
        "bids_submitted": [len(bids_by_day.get(d, [])) for d in days_list],
        "contracts_awarded": [contracts_by_day.get(d, 0) for d in days_list],
        "avg_risk": [
            round(sum(bids_by_day[d]) / len(bids_by_day[d]), 1) if bids_by_day.get(d) else 0
            for d in days_list
        ],
    }


@router.get("/analytics/regions")
def analytics_regions(db: Session = Depends(get_db)):
    """Per-region drill-down."""
    regions = (
        db.query(Tender.region)
        .group_by(Tender.region)
        .all()
    )
    out = []
    for (region,) in regions:
        tenders = db.query(Tender).filter(Tender.region == region).all()
        bids = [b for t in tenders for b in t.bids]
        contracts = [t.contract for t in tenders if t.contract]
        risks = [b.risk_score for b in bids if b.risk_score is not None]
        out.append({
            "region": region,
            "tender_count": len(tenders),
            "bid_count": len(bids),
            "contract_count": len(contracts),
            "total_awarded": sum(c.awarded_amount for c in contracts),
            "avg_risk": round(sum(risks) / len(risks), 1) if risks else None,
            "high_risk_bids": sum(1 for b in bids if (b.risk_level or "") == "High"),
        })
    out.sort(key=lambda r: (r["avg_risk"] or 0), reverse=True)
    return out
