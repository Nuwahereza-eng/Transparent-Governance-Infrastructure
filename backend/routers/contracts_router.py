"""Contract-level features: milestones, evidence uploads, budget transparency."""
import os
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from audit import chain
from auth import get_current_user, require_roles
from config import DATA_DIR
from database import get_db
from models import (
    BudgetTransaction, BudgetTxKind, Contract, Evidence, Milestone, Role, User,
)
from schemas import (
    BudgetSummary, BudgetTxCreate, BudgetTxOut, EvidenceOut,
    MilestoneComplete, MilestoneCreate, MilestoneOut,
)

router = APIRouter(prefix="/api/contracts", tags=["contracts"])

UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB
ALLOWED_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/gif",
    "application/pdf", "text/plain", "application/zip",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _require_contract_access(db: Session, contract_id: int, user: User) -> Contract:
    c = db.query(Contract).get(contract_id)
    if not c:
        raise HTTPException(404, "Contract not found")
    # Officers/auditors/admin can edit all. Contractor only their own. Citizens read-only above.
    return c


# ---------- Milestones ----------
def _milestone_out(m: Milestone) -> MilestoneOut:
    return MilestoneOut(
        id=m.id, contract_id=m.contract_id, title=m.title,
        due_date=m.due_date, completed_at=m.completed_at,
        notes=m.notes, created_at=m.created_at,
    )


@router.get("/{contract_id}/milestones", response_model=list[MilestoneOut])
def list_milestones(contract_id: int, db: Session = Depends(get_db)):
    c = db.query(Contract).get(contract_id)
    if not c:
        raise HTTPException(404, "Contract not found")
    return [_milestone_out(m) for m in c.milestones]


@router.post("/{contract_id}/milestones", response_model=MilestoneOut)
def add_milestone(
    contract_id: int,
    payload: MilestoneCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.officer, Role.compliance_officer, Role.admin)),
):
    c = db.query(Contract).get(contract_id)
    if not c:
        raise HTTPException(404, "Contract not found")
    m = Milestone(
        contract_id=c.id, title=payload.title,
        due_date=payload.due_date, notes=payload.notes,
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    chain.append(
        db, actor=user, action="milestone.create", entity_type="milestone", entity_id=m.id,
        payload={"contract_id": c.id, "title": m.title, "due_date": m.due_date.isoformat() if m.due_date else None},
    )
    return _milestone_out(m)


@router.post("/{contract_id}/milestones/{milestone_id}/complete", response_model=MilestoneOut)
def complete_milestone(
    contract_id: int, milestone_id: int,
    payload: MilestoneComplete,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    c = db.query(Contract).get(contract_id)
    if not c:
        raise HTTPException(404, "Contract not found")
    m = db.query(Milestone).get(milestone_id)
    if not m or m.contract_id != contract_id:
        raise HTTPException(404, "Milestone not found")
    if user.role == Role.contractor and c.contractor_id != user.id:
        raise HTTPException(403, "You can only update your own contract")
    if user.role not in {Role.officer, Role.compliance_officer, Role.admin, Role.contractor}:
        raise HTTPException(403, "Insufficient permissions")
    m.completed_at = datetime.utcnow()
    if payload.notes:
        m.notes = (m.notes + "\n" + payload.notes).strip() if m.notes else payload.notes
    # Auto-update contract progress based on completed milestones.
    siblings = c.milestones
    done = sum(1 for x in siblings if x.completed_at is not None)
    c.progress_percent = int(round(100 * done / max(1, len(siblings))))
    c.progress_status = "completed" if c.progress_percent >= 100 else "in_progress"
    db.commit()
    db.refresh(m)
    chain.append(
        db, actor=user, action="milestone.complete", entity_type="milestone", entity_id=m.id,
        payload={
            "contract_id": c.id, "title": m.title,
            "progress_percent": c.progress_percent,
        },
    )
    return _milestone_out(m)


# ---------- Evidence uploads ----------
def _evidence_out(e: Evidence) -> EvidenceOut:
    return EvidenceOut(
        id=e.id, contract_id=e.contract_id, uploader_id=e.uploader_id,
        uploader_name=e.uploader.full_name if e.uploader else None,
        filename=e.filename, content_type=e.content_type, size=e.size,
        url=f"/api/uploads/{Path(e.stored_path).name}",
        caption=e.caption, created_at=e.created_at,
    )


@router.get("/{contract_id}/evidence", response_model=list[EvidenceOut])
def list_evidence(contract_id: int, db: Session = Depends(get_db)):
    c = db.query(Contract).get(contract_id)
    if not c:
        raise HTTPException(404, "Contract not found")
    return [_evidence_out(e) for e in c.evidence]


@router.post("/{contract_id}/evidence", response_model=EvidenceOut)
async def upload_evidence(
    contract_id: int,
    caption: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    c = db.query(Contract).get(contract_id)
    if not c:
        raise HTTPException(404, "Contract not found")
    if user.role == Role.contractor and c.contractor_id != user.id:
        raise HTTPException(403, "You can only upload evidence to your own contract")
    if user.role not in {Role.officer, Role.compliance_officer, Role.admin, Role.contractor, Role.auditor}:
        raise HTTPException(403, "Insufficient permissions to upload evidence")
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, f"Unsupported file type: {file.content_type}")

    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"File too large (>{MAX_UPLOAD_BYTES // 1024 // 1024}MB)")

    ext = Path(file.filename or "").suffix
    safe_name = f"{uuid.uuid4().hex}{ext}"
    target = UPLOAD_DIR / safe_name
    with open(target, "wb") as f:
        f.write(contents)

    ev = Evidence(
        contract_id=c.id, uploader_id=user.id,
        filename=file.filename or safe_name,
        content_type=file.content_type, size=len(contents),
        stored_path=str(target), caption=caption,
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    chain.append(
        db, actor=user, action="evidence.upload", entity_type="evidence", entity_id=ev.id,
        payload={
            "contract_id": c.id, "filename": ev.filename,
            "content_type": ev.content_type, "size": ev.size,
        },
    )
    return _evidence_out(ev)


# ---------- Budget transactions ----------
def _tx_out(t: BudgetTransaction) -> BudgetTxOut:
    return BudgetTxOut(
        id=t.id, contract_id=t.contract_id, kind=t.kind,
        amount=t.amount, note=t.note,
        created_by_name=t.created_by.full_name if t.created_by else None,
        created_at=t.created_at,
    )


def _budget_summary(c: Contract) -> BudgetSummary:
    allocated = sum(t.amount for t in c.budget_tx if t.kind == BudgetTxKind.allocation)
    released = sum(t.amount for t in c.budget_tx if t.kind == BudgetTxKind.release)
    spent = sum(t.amount for t in c.budget_tx if t.kind == BudgetTxKind.expense)
    remaining = released - spent
    utilization = round(100 * spent / allocated, 1) if allocated else 0.0
    return BudgetSummary(
        allocated=allocated, released=released, spent=spent,
        remaining=remaining, utilization_percent=utilization,
    )


@router.get("/{contract_id}/budget", response_model=dict)
def get_budget(contract_id: int, db: Session = Depends(get_db)):
    c = db.query(Contract).get(contract_id)
    if not c:
        raise HTTPException(404, "Contract not found")
    summary = _budget_summary(c)
    return {
        "summary": summary.model_dump(),
        "transactions": [_tx_out(t).model_dump() for t in c.budget_tx],
    }


@router.post("/{contract_id}/budget", response_model=BudgetTxOut)
def add_budget_tx(
    contract_id: int,
    payload: BudgetTxCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.officer, Role.admin)),
):
    c = db.query(Contract).get(contract_id)
    if not c:
        raise HTTPException(404, "Contract not found")
    summary = _budget_summary(c)
    if payload.kind == BudgetTxKind.release and summary.released + payload.amount > summary.allocated:
        raise HTTPException(400, "Cannot release more than allocated")
    if payload.kind == BudgetTxKind.expense and summary.spent + payload.amount > summary.released:
        raise HTTPException(400, "Cannot spend more than released")

    tx = BudgetTransaction(
        contract_id=c.id, kind=payload.kind, amount=payload.amount,
        note=payload.note, created_by_id=user.id,
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    chain.append(
        db, actor=user, action=f"budget.{payload.kind.value}", entity_type="budget_tx", entity_id=tx.id,
        payload={"contract_id": c.id, "amount": payload.amount, "note": payload.note},
    )
    return _tx_out(tx)
