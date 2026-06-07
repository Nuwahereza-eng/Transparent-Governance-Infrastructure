"""Contractor intelligence — performance history + reputation score."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Bid, Contract, FeedbackReport, FeedbackStatus, Role, User
from schemas import ContractorOut

router = APIRouter(prefix="/api/contractors", tags=["contractors"])


def _level(score: float) -> str:
    if score >= 80:
        return "Excellent"
    if score >= 60:
        return "Good"
    if score >= 40:
        return "Fair"
    if score >= 20:
        return "Poor"
    return "Critical"


def _build(db: Session, u: User) -> ContractorOut:
    bids = db.query(Bid).filter(Bid.contractor_id == u.id).all()
    contracts = db.query(Contract).filter(Contract.contractor_id == u.id).all()

    bids_submitted = len(bids)
    wins = len(contracts)
    completed = sum(1 for c in contracts if (c.progress_percent or 0) >= 100)
    total_awarded = sum(c.awarded_amount for c in contracts)

    awarded_bid_ids = [c.bid_id for c in contracts]
    winning_bids = db.query(Bid).filter(Bid.id.in_(awarded_bid_ids)).all() if awarded_bid_ids else []
    risks = [b.risk_score for b in winning_bids if b.risk_score is not None]
    avg_risk = round(sum(risks) / len(risks), 1) if risks else None
    progresses = [c.progress_percent or 0 for c in contracts]
    avg_progress = round(sum(progresses) / len(progresses), 1) if progresses else None

    # Pending feedback against this contractor (either tender they won or contract).
    contract_ids = [c.id for c in contracts]
    tender_ids = [c.tender_id for c in contracts]
    pending_feedback = 0
    if contract_ids or tender_ids:
        q = db.query(FeedbackReport).filter(
            FeedbackReport.status.in_([FeedbackStatus.pending, FeedbackStatus.under_review])
        )
        from sqlalchemy import or_
        conditions = []
        if contract_ids:
            conditions.append(FeedbackReport.contract_id.in_(contract_ids))
        if tender_ids:
            conditions.append(FeedbackReport.tender_id.in_(tender_ids))
        pending_feedback = q.filter(or_(*conditions)).count()

    # Reputation score: starts at 70, modulated by signals.
    score = 70.0
    score += min(20, completed * 5)
    if avg_progress is not None:
        if avg_progress >= 80:
            score += 10
        elif avg_progress < 30 and wins > 0:
            score -= 10
    if avg_risk is not None:
        if avg_risk >= 60:
            score -= 25
        elif avg_risk >= 30:
            score -= 10
        elif avg_risk < 10:
            score += 5
    score -= 5 * pending_feedback
    score = max(0.0, min(100.0, score))

    return ContractorOut(
        id=u.id, full_name=u.full_name, organization=u.organization,
        bids_submitted=bids_submitted, contracts_won=wins,
        contracts_completed=completed, total_awarded=total_awarded,
        average_risk=avg_risk, average_progress=avg_progress,
        pending_feedback=pending_feedback,
        reputation_score=round(score, 1), reputation_level=_level(score),
    )


@router.get("", response_model=list[ContractorOut])
def list_contractors(db: Session = Depends(get_db)):
    users = db.query(User).filter(User.role == Role.contractor).all()
    out = [_build(db, u) for u in users]
    out.sort(key=lambda c: c.reputation_score, reverse=True)
    return out


@router.get("/{contractor_id}", response_model=ContractorOut)
def get_contractor(contractor_id: int, db: Session = Depends(get_db)):
    u = db.query(User).get(contractor_id)
    if not u or u.role != Role.contractor:
        raise HTTPException(404, "Contractor not found")
    return _build(db, u)
