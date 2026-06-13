"""Pydantic request/response schemas."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field

from models import (
    ApprovalDecision, BidStatus, BudgetTxKind, FeedbackStatus, Role, TenderStatus,
)


# ---------- Auth ----------
class UserRegister(BaseModel):
    email: EmailStr
    full_name: str
    password: str = Field(min_length=6)
    role: Role = Role.citizen
    organization: Optional[str] = None


class UserOut(BaseModel):
    id: int
    email: EmailStr
    full_name: str
    organization: Optional[str] = None
    role: Role

    class Config:
        from_attributes = True


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---------- Tenders ----------
class TenderCreate(BaseModel):
    title: str
    description: str = ""
    category: str = "general"
    region: str = "national"
    budget: float = Field(gt=0)
    deadline: datetime


class TenderOut(BaseModel):
    id: int
    title: str
    description: str
    category: str
    region: str
    budget: float
    deadline: datetime
    status: TenderStatus
    current_stage: Optional[str] = None
    created_at: datetime
    bid_count: int = 0
    avg_risk: Optional[float] = None
    awarded_amount: Optional[float] = None
    awarded_to: Optional[str] = None
    contract_id: Optional[int] = None

    class Config:
        from_attributes = True


# ---------- Bids ----------
class BidCreate(BaseModel):
    price: float = Field(gt=0)
    delivery_days: int = Field(gt=0, le=365 * 3)
    proposal: str = ""


class BidOut(BaseModel):
    id: int
    tender_id: int
    contractor_id: int
    contractor_name: Optional[str] = None
    price: float
    delivery_days: int
    proposal: str
    status: BidStatus
    risk_score: Optional[float] = None
    risk_level: Optional[str] = None
    risk_explanation: Optional[str] = None
    rank: Optional[int] = None
    composite_score: Optional[float] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ---------- Contracts ----------
class ContractOut(BaseModel):
    id: int
    tender_id: int
    bid_id: int
    contractor_id: int
    contractor_name: Optional[str] = None
    awarded_amount: float
    awarded_at: datetime
    progress_status: str
    progress_percent: int
    override_justification: Optional[str] = None
    override_rank: Optional[int] = None

    class Config:
        from_attributes = True


class AwardRequest(BaseModel):
    # Required only when the awarded bid is not AI-ranked #1.
    justification: Optional[str] = Field(default=None, max_length=2000)


class ProgressUpdate(BaseModel):
    progress_status: str
    progress_percent: int = Field(ge=0, le=100)


# ---------- Audit ----------
class AuditOut(BaseModel):
    id: int
    timestamp: datetime
    actor_email: Optional[str]
    action: str
    entity_type: str
    entity_id: Optional[int]
    payload: str
    prev_hash: str
    hash: str

    class Config:
        from_attributes = True


# ---------- Approvals ----------
class ApprovalCreate(BaseModel):
    decision: ApprovalDecision
    comments: str = ""


class ApprovalOut(BaseModel):
    id: int
    tender_id: int
    stage: str
    approver_id: int
    approver_name: Optional[str] = None
    approver_role: Optional[str] = None
    decision: ApprovalDecision
    comments: str
    created_at: datetime

    class Config:
        from_attributes = True


# ---------- Feedback ----------
class FeedbackCreate(BaseModel):
    tender_id: Optional[int] = None
    contract_id: Optional[int] = None
    category: str = "general"
    message: str = Field(min_length=5, max_length=4000)
    evidence_url: Optional[str] = None
    reporter_label: Optional[str] = Field(default=None, max_length=255)


class FeedbackModerate(BaseModel):
    status: FeedbackStatus
    moderator_note: str = ""


class FeedbackOut(BaseModel):
    id: int
    tender_id: Optional[int] = None
    contract_id: Optional[int] = None
    reporter_id: Optional[int] = None
    reporter_label: Optional[str] = None
    is_anonymous: bool = True
    category: str
    message: str
    evidence_url: Optional[str] = None
    status: FeedbackStatus
    moderator_note: str = ""
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ---------- Milestones / evidence / budget ----------
class MilestoneCreate(BaseModel):
    title: str
    due_date: Optional[datetime] = None
    notes: str = ""


class MilestoneComplete(BaseModel):
    notes: str = ""


class MilestoneOut(BaseModel):
    id: int
    contract_id: int
    title: str
    due_date: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    notes: str
    created_at: datetime

    class Config:
        from_attributes = True


class EvidenceOut(BaseModel):
    id: int
    contract_id: int
    uploader_id: int
    uploader_name: Optional[str] = None
    filename: str
    content_type: str
    size: int
    url: str
    caption: str
    created_at: datetime


class BudgetTxCreate(BaseModel):
    kind: BudgetTxKind
    amount: float = Field(gt=0)
    note: str = ""


class BudgetTxOut(BaseModel):
    id: int
    contract_id: int
    kind: BudgetTxKind
    amount: float
    note: str
    created_by_name: Optional[str] = None
    created_at: datetime


class BudgetSummary(BaseModel):
    allocated: float
    released: float
    spent: float
    remaining: float
    utilization_percent: float


# ---------- Beneficiary receipts (delivery proof) ----------
class BeneficiaryReceiptOut(BaseModel):
    id: int
    contract_id: int
    milestone_id: Optional[int] = None
    recipient_name: Optional[str] = None
    item_received: str
    location_text: Optional[str] = None
    gps_lat: Optional[float] = None
    gps_lng: Optional[float] = None
    photo_url: str
    photo_sha256: Optional[str] = None
    photo_anomalies: list[str] = []
    exif_datetime: Optional[datetime] = None
    recorded_by_name: Optional[str] = None
    is_anonymous_submission: bool
    created_at: datetime


class BeneficiaryReceiptStats(BaseModel):
    total_receipts: int
    distinct_locations: int
    duplicate_id_count: int
    duplicate_photo_count: int = 0
    photos_missing_exif: int = 0
    most_recent_at: Optional[datetime] = None
    location_clusters: list[dict] = []  # [{ "label": "Block 4", "count": 23 }]
    flags: list[dict] = []  # [{ "tone": "warn"|"err", "title": str, "detail": str }]


# ---------- Audit anchors (tamper-evidence) ----------
class AnchorPublish(BaseModel):
    note: Optional[str] = Field(default=None, max_length=500)
    external_url: Optional[str] = Field(default=None, max_length=500)


class AuditAnchorOut(BaseModel):
    id: int
    created_at: datetime
    head_hash: str
    entries_count: int
    published_by_name: Optional[str] = None
    note: str = ""
    external_url: Optional[str] = None


class AuditAnchorState(BaseModel):
    current_head: Optional[str] = None
    current_entries: int
    last_anchor: Optional[AuditAnchorOut] = None
    drift_since_last: int = 0
    in_sync: bool = True


# ---------- Contractor reputation ----------
class ContractorOut(BaseModel):
    id: int
    full_name: str
    organization: Optional[str] = None
    bids_submitted: int
    contracts_won: int
    contracts_completed: int
    total_awarded: float
    average_risk: Optional[float] = None
    average_progress: Optional[float] = None
    pending_feedback: int
    reputation_score: float
    reputation_level: str

