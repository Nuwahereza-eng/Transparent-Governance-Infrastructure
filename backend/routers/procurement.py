"""Tender + bid + contract routes."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ai import risk
from audit import chain
from auth import get_current_user, require_roles
from database import get_db
from models import Bid, BidStatus, Contract, Role, Tender, TenderStatus, User
from schemas import (
    AwardRequest, BidCreate, BidOut, ContractOut, ProgressUpdate,
    TenderCreate, TenderOut,
)

router = APIRouter(prefix="/api", tags=["procurement"])


def _tender_out(t: Tender) -> TenderOut:
    avg = None
    if t.bids:
        scored = [b.risk_score for b in t.bids if b.risk_score is not None]
        if scored:
            avg = round(sum(scored) / len(scored), 1)
    awarded_amount = t.contract.awarded_amount if t.contract else None
    awarded_to = t.contract.contractor.full_name if t.contract else None
    contract_id = t.contract.id if t.contract else None
    return TenderOut(
        id=t.id, title=t.title, description=t.description,
        category=t.category, region=t.region, budget=t.budget,
        deadline=t.deadline, status=t.status, current_stage=t.current_stage,
        created_at=t.created_at,
        bid_count=len(t.bids), avg_risk=avg,
        awarded_amount=awarded_amount, awarded_to=awarded_to,
        contract_id=contract_id,
    )


def _bid_out(b: Bid) -> BidOut:
    return BidOut(
        id=b.id, tender_id=b.tender_id, contractor_id=b.contractor_id,
        contractor_name=b.contractor.full_name if b.contractor else None,
        price=b.price, delivery_days=b.delivery_days, proposal=b.proposal,
        status=b.status, risk_score=b.risk_score, risk_level=b.risk_level,
        risk_explanation=b.risk_explanation, rank=b.rank,
        composite_score=b.composite_score, created_at=b.created_at,
    )


def _contract_out(c: Contract, bid: Bid | None = None) -> ContractOut:
    contractor_name = None
    if c.contractor:
        contractor_name = c.contractor.full_name
    elif bid and bid.contractor:
        contractor_name = bid.contractor.full_name
    return ContractOut(
        id=c.id, tender_id=c.tender_id, bid_id=c.bid_id,
        contractor_id=c.contractor_id,
        contractor_name=contractor_name,
        awarded_amount=c.awarded_amount, awarded_at=c.awarded_at,
        progress_status=c.progress_status, progress_percent=c.progress_percent,
        override_justification=c.override_justification,
        override_rank=c.override_rank,
    )


# ---------- Tenders ----------
@router.get("/tenders", response_model=list[TenderOut])
def list_tenders(
    status: Optional[TenderStatus] = None,
    category: Optional[str] = None,
    region: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(Tender)
    if status:
        q = q.filter(Tender.status == status)
    if category:
        q = q.filter(Tender.category == category)
    if region:
        q = q.filter(Tender.region == region)
    return [_tender_out(t) for t in q.order_by(Tender.created_at.desc()).all()]


@router.get("/tenders/{tender_id}", response_model=TenderOut)
def get_tender(tender_id: int, db: Session = Depends(get_db)):
    t = db.query(Tender).get(tender_id)
    if not t:
        raise HTTPException(404, "Tender not found")
    return _tender_out(t)


@router.post("/tenders", response_model=TenderOut)
def create_tender(
    payload: TenderCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.officer, Role.admin)),
):
    if payload.deadline <= datetime.utcnow():
        raise HTTPException(400, "Deadline must be in the future")
    t = Tender(
        title=payload.title, description=payload.description,
        category=payload.category, region=payload.region,
        budget=payload.budget, deadline=payload.deadline,
        created_by_id=user.id,
        status=TenderStatus.pending_department,
        current_stage="department",
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    chain.append(
        db, actor=user, action="tender.create", entity_type="tender", entity_id=t.id,
        payload={"title": t.title, "budget": t.budget, "category": t.category, "region": t.region},
    )
    return _tender_out(t)


@router.post("/tenders/{tender_id}/cancel", response_model=TenderOut)
def cancel_tender(
    tender_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.officer, Role.admin)),
):
    t = db.query(Tender).get(tender_id)
    if not t:
        raise HTTPException(404, "Tender not found")
    if t.status == TenderStatus.awarded:
        raise HTTPException(400, "Cannot cancel an awarded tender")
    t.status = TenderStatus.cancelled
    db.commit()
    chain.append(
        db, actor=user, action="tender.cancel", entity_type="tender", entity_id=t.id,
        payload={"title": t.title},
    )
    return _tender_out(t)


# ---------- Bids ----------
@router.get("/tenders/{tender_id}/bids", response_model=list[BidOut])
def list_bids(tender_id: int, db: Session = Depends(get_db)):
    t = db.query(Tender).get(tender_id)
    if not t:
        raise HTTPException(404, "Tender not found")
    bids = sorted(t.bids, key=lambda b: (b.rank or 1_000_000, b.price))
    return [_bid_out(b) for b in bids]


@router.post("/tenders/{tender_id}/bids", response_model=BidOut)
def submit_bid(
    tender_id: int,
    payload: BidCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.contractor, Role.admin)),
):
    t = db.query(Tender).get(tender_id)
    if not t:
        raise HTTPException(404, "Tender not found")
    if t.status != TenderStatus.open:
        raise HTTPException(400, f"Tender is {t.status.value}, not open")
    if t.deadline <= datetime.utcnow():
        raise HTTPException(400, "Tender deadline has passed")
    existing = (
        db.query(Bid)
        .filter(Bid.tender_id == tender_id, Bid.contractor_id == user.id)
        .first()
    )
    if existing:
        raise HTTPException(400, "You have already submitted a bid for this tender")

    bid = Bid(
        tender_id=tender_id, contractor_id=user.id,
        price=payload.price, delivery_days=payload.delivery_days,
        proposal=payload.proposal,
    )
    db.add(bid)
    db.commit()
    db.refresh(bid)
    chain.append(
        db, actor=user, action="bid.submit", entity_type="bid", entity_id=bid.id,
        payload={"tender_id": tender_id, "price": bid.price, "delivery_days": bid.delivery_days},
    )
    # Re-rank everyone now that a new bid exists.
    risk.rerank_tender(db, t)
    db.refresh(bid)
    return _bid_out(bid)


@router.post("/tenders/{tender_id}/evaluate", response_model=list[BidOut])
def evaluate_tender(
    tender_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.officer, Role.admin)),
):
    t = db.query(Tender).get(tender_id)
    if not t:
        raise HTTPException(404, "Tender not found")
    if not t.bids:
        raise HTTPException(400, "No bids to evaluate")
    t.status = TenderStatus.evaluating
    bids = risk.rerank_tender(db, t)
    chain.append(
        db, actor=user, action="tender.evaluate", entity_type="tender", entity_id=t.id,
        payload={
            "bid_count": len(bids),
            "ranking": [
                {"bid_id": b.id, "rank": b.rank, "risk": b.risk_score, "price": b.price}
                for b in bids
            ],
        },
    )
    return [_bid_out(b) for b in sorted(bids, key=lambda b: b.rank or 0)]


@router.post("/tenders/{tender_id}/award/{bid_id}", response_model=ContractOut)
def award_contract(
    tender_id: int,
    bid_id: int,
    payload: AwardRequest | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.officer, Role.admin)),
):
    t = db.query(Tender).get(tender_id)
    if not t:
        raise HTTPException(404, "Tender not found")
    if t.status == TenderStatus.awarded:
        raise HTTPException(400, "Tender is already awarded")
    bid = db.query(Bid).get(bid_id)
    if not bid or bid.tender_id != tender_id:
        raise HTTPException(404, "Bid not found for this tender")

    if bid.rank is None:
        risk.rerank_tender(db, t)
        db.refresh(bid)

    justification = (payload.justification or "").strip() if payload else ""
    override_rank: int | None = None
    if bid.rank != 1:
        # Awarding a non-top bid is allowed but requires a written reason
        # that is permanently chain-logged.
        if len(justification) < 30:
            raise HTTPException(
                400,
                "A written justification of at least 30 characters is required "
                "when awarding a non-top-ranked bid. The reason will be "
                "permanently recorded on the public audit chain.",
            )
        override_rank = bid.rank
        chain.append(
            db, actor=user, action="bid.award.override", entity_type="bid", entity_id=bid.id,
            payload={
                "tender_id": tender_id, "awarded_rank": bid.rank,
                "risk_score": bid.risk_score, "price": bid.price,
                "justification": justification,
                "warning": "Top-ranked bid was not selected.",
            },
        )

    contract = Contract(
        tender_id=tender_id, bid_id=bid.id, contractor_id=bid.contractor_id,
        awarded_amount=bid.price,
        override_justification=justification if override_rank is not None else None,
        override_rank=override_rank,
    )
    db.add(contract)
    bid.status = BidStatus.awarded
    for other in t.bids:
        if other.id != bid.id:
            other.status = BidStatus.rejected
    t.status = TenderStatus.awarded
    db.commit()
    db.refresh(contract)
    # Auto-create initial allocation budget transaction so the budget module
    # has a starting point.
    from models import BudgetTransaction, BudgetTxKind
    alloc = BudgetTransaction(
        contract_id=contract.id, kind=BudgetTxKind.allocation,
        amount=contract.awarded_amount, note="Initial allocation on award",
        created_by_id=user.id,
    )
    db.add(alloc)
    db.commit()
    chain.append(
        db, actor=user, action="contract.award", entity_type="contract", entity_id=contract.id,
        payload={
            "tender_id": tender_id, "bid_id": bid.id,
            "contractor_id": bid.contractor_id, "amount": bid.price,
            "risk_score": bid.risk_score, "rank": bid.rank,
        },
    )
    return _contract_out(contract, bid=bid)


# ---------- Contracts ----------
@router.get("/contracts", response_model=list[ContractOut])
def list_contracts(db: Session = Depends(get_db)):
    contracts = db.query(Contract).order_by(Contract.awarded_at.desc()).all()
    return [_contract_out(c) for c in contracts]


@router.post("/contracts/{contract_id}/progress", response_model=ContractOut)
def update_progress(
    contract_id: int,
    payload: ProgressUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.officer, Role.contractor, Role.admin)),
):
    c = db.query(Contract).get(contract_id)
    if not c:
        raise HTTPException(404, "Contract not found")
    if user.role == Role.contractor and c.contractor_id != user.id:
        raise HTTPException(403, "You can only update your own contract")
    c.progress_status = payload.progress_status
    c.progress_percent = payload.progress_percent
    db.commit()
    chain.append(
        db, actor=user, action="contract.progress", entity_type="contract", entity_id=c.id,
        payload={"status": c.progress_status, "percent": c.progress_percent},
    )
    return _contract_out(c)
