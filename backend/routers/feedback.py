"""Citizen feedback + anonymous reports + moderation."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from audit import chain
from auth import get_current_user, oauth2_scheme
from database import get_db
from models import (
    Contract, FeedbackReport, FeedbackStatus, Role, Tender, User,
)
from rate_limit import limiter
from schemas import FeedbackCreate, FeedbackModerate, FeedbackOut

router = APIRouter(prefix="/api/feedback", tags=["feedback"])

MODERATOR_ROLES = {Role.officer, Role.compliance_officer, Role.auditor, Role.admin}


def _optional_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Best-effort current user — None if not logged in (for anonymous posts)."""
    if not token:
        return None
    from jose import JWTError, jwt
    from config import ALGORITHM, SECRET_KEY
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        uid = int(payload.get("sub"))
        return db.query(User).get(uid)
    except (JWTError, ValueError, TypeError):
        return None


def _feedback_out(f: FeedbackReport) -> FeedbackOut:
    return FeedbackOut(
        id=f.id, tender_id=f.tender_id, contract_id=f.contract_id,
        reporter_id=f.reporter_id, reporter_label=f.reporter_label,
        is_anonymous=f.reporter_id is None,
        category=f.category, message=f.message,
        evidence_url=f.evidence_url, status=f.status,
        moderator_note=f.moderator_note,
        created_at=f.created_at, updated_at=f.updated_at,
    )


@router.post("", response_model=FeedbackOut)
@limiter.limit("10/minute")
def submit_feedback(
    request: Request,
    payload: FeedbackCreate,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(_optional_user),
):
    if payload.tender_id and not db.query(Tender).get(payload.tender_id):
        raise HTTPException(404, "Tender not found")
    if payload.contract_id and not db.query(Contract).get(payload.contract_id):
        raise HTTPException(404, "Contract not found")

    fb = FeedbackReport(
        tender_id=payload.tender_id, contract_id=payload.contract_id,
        reporter_id=user.id if user else None,
        reporter_label=payload.reporter_label if not user else user.full_name,
        category=payload.category, message=payload.message,
        evidence_url=payload.evidence_url,
    )
    db.add(fb)
    db.commit()
    db.refresh(fb)
    chain.append(
        db, actor=user, action="feedback.submit", entity_type="feedback", entity_id=fb.id,
        payload={
            "tender_id": fb.tender_id, "contract_id": fb.contract_id,
            "category": fb.category, "anonymous": user is None,
        },
    )
    return _feedback_out(fb)


@router.get("", response_model=list[FeedbackOut])
def list_feedback(
    status: Optional[FeedbackStatus] = None,
    tender_id: Optional[int] = None,
    contract_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Public list — reporters remain anonymous when reporter_id is null."""
    q = db.query(FeedbackReport)
    if status:
        q = q.filter(FeedbackReport.status == status)
    if tender_id:
        q = q.filter(FeedbackReport.tender_id == tender_id)
    if contract_id:
        q = q.filter(FeedbackReport.contract_id == contract_id)
    return [_feedback_out(f) for f in q.order_by(FeedbackReport.created_at.desc()).all()]


@router.post("/{fb_id}/moderate", response_model=FeedbackOut)
def moderate_feedback(
    fb_id: int,
    payload: FeedbackModerate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role not in MODERATOR_ROLES:
        raise HTTPException(403, "Insufficient permissions to moderate")
    fb = db.query(FeedbackReport).get(fb_id)
    if not fb:
        raise HTTPException(404, "Report not found")
    fb.status = payload.status
    fb.moderator_id = user.id
    fb.moderator_note = payload.moderator_note
    db.commit()
    db.refresh(fb)
    chain.append(
        db, actor=user, action="feedback.moderate", entity_type="feedback", entity_id=fb.id,
        payload={"status": fb.status.value, "note": payload.moderator_note},
    )
    return _feedback_out(fb)
