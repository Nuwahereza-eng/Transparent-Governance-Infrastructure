"""Multi-step tender approval workflow.

Tender lifecycle (new):
    pending_department → pending_financial → pending_compliance → pending_final → open
At any stage, a rejection ends the workflow with status=rejected.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from audit import chain
from auth import get_current_user
from database import get_db
from models import (
    APPROVAL_STAGES, Approval, ApprovalDecision, Role, STAGE_ROLES,
    STAGE_TENDER_STATUS, Tender, TenderStatus, User,
)
from schemas import ApprovalCreate, ApprovalOut

router = APIRouter(prefix="/api", tags=["approvals"])


def _approval_out(a: Approval) -> ApprovalOut:
    return ApprovalOut(
        id=a.id, tender_id=a.tender_id, stage=a.stage,
        approver_id=a.approver_id,
        approver_name=a.approver.full_name if a.approver else None,
        approver_role=a.approver.role.value if a.approver else None,
        decision=a.decision, comments=a.comments, created_at=a.created_at,
    )


@router.get("/tenders/{tender_id}/approvals", response_model=list[ApprovalOut])
def list_approvals(tender_id: int, db: Session = Depends(get_db)):
    t = db.query(Tender).get(tender_id)
    if not t:
        raise HTTPException(404, "Tender not found")
    return [_approval_out(a) for a in t.approvals]


@router.get("/approvals/queue", response_model=list[dict])
def approval_queue(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Tenders waiting for the current user to act on (based on role)."""
    queue = []
    pending = (
        db.query(Tender)
        .filter(Tender.status.in_([
            TenderStatus.pending_department, TenderStatus.pending_financial,
            TenderStatus.pending_compliance, TenderStatus.pending_final,
        ]))
        .order_by(Tender.created_at.desc())
        .all()
    )
    for t in pending:
        stage = t.current_stage
        if not stage:
            continue
        allowed = STAGE_ROLES.get(stage, set())
        if user.role in allowed or user.role == Role.admin:
            queue.append({
                "tender_id": t.id, "title": t.title, "category": t.category,
                "region": t.region, "budget": t.budget,
                "stage": stage, "status": t.status.value,
                "created_by": t.created_by.full_name if t.created_by else None,
                "created_at": t.created_at.isoformat(),
            })
    return queue


@router.post("/tenders/{tender_id}/approve", response_model=ApprovalOut)
def act_on_approval(
    tender_id: int,
    payload: ApprovalCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = db.query(Tender).get(tender_id)
    if not t:
        raise HTTPException(404, "Tender not found")
    stage = t.current_stage
    if not stage or t.status not in (
        TenderStatus.pending_department, TenderStatus.pending_financial,
        TenderStatus.pending_compliance, TenderStatus.pending_final,
    ):
        raise HTTPException(400, f"Tender is not awaiting approval (status={t.status.value})")

    allowed = STAGE_ROLES.get(stage, set())
    if user.role not in allowed and user.role != Role.admin:
        raise HTTPException(
            403,
            f"Stage '{stage}' requires one of: {sorted(r.value for r in allowed)}",
        )

    approval = Approval(
        tender_id=t.id, stage=stage, approver_id=user.id,
        decision=payload.decision, comments=payload.comments,
    )
    db.add(approval)

    if payload.decision == ApprovalDecision.rejected:
        t.status = TenderStatus.rejected
        t.current_stage = None
    else:
        # Advance to next stage, or open the tender if final stage passed.
        idx = APPROVAL_STAGES.index(stage)
        if idx + 1 < len(APPROVAL_STAGES):
            t.current_stage = APPROVAL_STAGES[idx + 1]
            t.status = STAGE_TENDER_STATUS[t.current_stage]
        else:
            t.current_stage = None
            t.status = TenderStatus.open

    db.commit()
    db.refresh(approval)
    chain.append(
        db, actor=user,
        action=f"tender.approve.{stage}" if payload.decision == ApprovalDecision.approved
               else f"tender.reject.{stage}",
        entity_type="tender", entity_id=t.id,
        payload={
            "stage": stage, "decision": payload.decision.value,
            "comments": payload.comments, "new_status": t.status.value,
        },
    )
    return _approval_out(approval)
