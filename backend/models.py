"""Database models."""
from datetime import datetime
import enum

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, ForeignKey, Text, Enum, Boolean
)
from sqlalchemy.orm import relationship

from database import Base


class Role(str, enum.Enum):
    admin = "admin"
    officer = "officer"                       # procurement officer
    compliance_officer = "compliance_officer"
    auditor = "auditor"
    contractor = "contractor"
    citizen = "citizen"


class TenderStatus(str, enum.Enum):
    draft = "draft"
    pending_department = "pending_department"
    pending_financial = "pending_financial"
    pending_compliance = "pending_compliance"
    pending_final = "pending_final"
    open = "open"
    evaluating = "evaluating"
    awarded = "awarded"
    cancelled = "cancelled"
    rejected = "rejected"


# Workflow definition — ordered list of approval stages.
APPROVAL_STAGES = [
    "department",
    "financial",
    "compliance",
    "final",
]
# Map: stage -> set of roles allowed to act on it.
STAGE_ROLES = {
    "department": {Role.officer, Role.admin},
    "financial":  {Role.officer, Role.admin},
    "compliance": {Role.compliance_officer, Role.admin},
    "final":      {Role.admin},
}
# Map: stage -> tender status to set while waiting on this stage.
STAGE_TENDER_STATUS = {
    "department": TenderStatus.pending_department,
    "financial":  TenderStatus.pending_financial,
    "compliance": TenderStatus.pending_compliance,
    "final":      TenderStatus.pending_final,
}


class BidStatus(str, enum.Enum):
    submitted = "submitted"
    ranked = "ranked"
    awarded = "awarded"
    rejected = "rejected"


class FeedbackStatus(str, enum.Enum):
    pending = "pending"
    under_review = "under_review"
    resolved = "resolved"
    dismissed = "dismissed"


class BudgetTxKind(str, enum.Enum):
    allocation = "allocation"
    release = "release"
    expense = "expense"


class ApprovalDecision(str, enum.Enum):
    approved = "approved"
    rejected = "rejected"


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    full_name = Column(String(255), nullable=False)
    organization = Column(String(255), nullable=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(Enum(Role), nullable=False, default=Role.citizen)
    created_at = Column(DateTime, default=datetime.utcnow)

    bids = relationship("Bid", back_populates="contractor")


class Tender(Base):
    __tablename__ = "tenders"
    id = Column(Integer, primary_key=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False, default="")
    category = Column(String(100), nullable=False, default="general")
    region = Column(String(100), nullable=False, default="national")
    budget = Column(Float, nullable=False)
    deadline = Column(DateTime, nullable=False)
    status = Column(Enum(TenderStatus), nullable=False, default=TenderStatus.pending_department)
    current_stage = Column(String(50), nullable=True, default="department")
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    created_by = relationship("User")
    bids = relationship("Bid", back_populates="tender", cascade="all, delete-orphan")
    contract = relationship("Contract", back_populates="tender", uselist=False)
    approvals = relationship("Approval", back_populates="tender",
                             cascade="all, delete-orphan",
                             order_by="Approval.id")


class Approval(Base):
    __tablename__ = "approvals"
    id = Column(Integer, primary_key=True)
    tender_id = Column(Integer, ForeignKey("tenders.id"), nullable=False)
    stage = Column(String(50), nullable=False)
    approver_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    decision = Column(Enum(ApprovalDecision), nullable=False)
    comments = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    tender = relationship("Tender", back_populates="approvals")
    approver = relationship("User")


class Bid(Base):
    __tablename__ = "bids"
    id = Column(Integer, primary_key=True)
    tender_id = Column(Integer, ForeignKey("tenders.id"), nullable=False)
    contractor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    price = Column(Float, nullable=False)
    delivery_days = Column(Integer, nullable=False, default=30)
    proposal = Column(Text, nullable=False, default="")
    status = Column(Enum(BidStatus), nullable=False, default=BidStatus.submitted)
    risk_score = Column(Float, nullable=True)
    risk_level = Column(String(20), nullable=True)
    risk_explanation = Column(Text, nullable=True)
    rank = Column(Integer, nullable=True)
    composite_score = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    tender = relationship("Tender", back_populates="bids")
    contractor = relationship("User", back_populates="bids")


class Contract(Base):
    __tablename__ = "contracts"
    id = Column(Integer, primary_key=True)
    tender_id = Column(Integer, ForeignKey("tenders.id"), unique=True, nullable=False)
    bid_id = Column(Integer, ForeignKey("bids.id"), nullable=False)
    contractor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    awarded_amount = Column(Float, nullable=False)
    awarded_at = Column(DateTime, default=datetime.utcnow)
    progress_status = Column(String(50), default="not_started")
    progress_percent = Column(Integer, default=0)

    tender = relationship("Tender", back_populates="contract")
    bid = relationship("Bid")
    contractor = relationship("User")
    milestones = relationship("Milestone", back_populates="contract",
                              cascade="all, delete-orphan",
                              order_by="Milestone.id")
    evidence = relationship("Evidence", back_populates="contract",
                            cascade="all, delete-orphan",
                            order_by="Evidence.id.desc()")
    budget_tx = relationship("BudgetTransaction", back_populates="contract",
                             cascade="all, delete-orphan",
                             order_by="BudgetTransaction.id")


class Milestone(Base):
    __tablename__ = "milestones"
    id = Column(Integer, primary_key=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=False)
    title = Column(String(255), nullable=False)
    due_date = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    contract = relationship("Contract", back_populates="milestones")


class Evidence(Base):
    __tablename__ = "evidence"
    id = Column(Integer, primary_key=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=False)
    uploader_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    filename = Column(String(255), nullable=False)
    content_type = Column(String(100), nullable=False, default="application/octet-stream")
    size = Column(Integer, nullable=False, default=0)
    stored_path = Column(String(500), nullable=False)
    caption = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    contract = relationship("Contract", back_populates="evidence")
    uploader = relationship("User")


class BudgetTransaction(Base):
    __tablename__ = "budget_tx"
    id = Column(Integer, primary_key=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=False)
    kind = Column(Enum(BudgetTxKind), nullable=False)
    amount = Column(Float, nullable=False)
    note = Column(Text, nullable=False, default="")
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    contract = relationship("Contract", back_populates="budget_tx")
    created_by = relationship("User")


class FeedbackReport(Base):
    __tablename__ = "feedback_reports"
    id = Column(Integer, primary_key=True)
    tender_id = Column(Integer, ForeignKey("tenders.id"), nullable=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=True)
    reporter_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # null = anonymous
    reporter_label = Column(String(255), nullable=True)
    category = Column(String(100), nullable=False, default="general")
    message = Column(Text, nullable=False)
    evidence_url = Column(String(500), nullable=True)
    status = Column(Enum(FeedbackStatus), nullable=False, default=FeedbackStatus.pending)
    moderator_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    moderator_note = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tender = relationship("Tender", foreign_keys=[tender_id])
    contract = relationship("Contract", foreign_keys=[contract_id])
    reporter = relationship("User", foreign_keys=[reporter_id])
    moderator = relationship("User", foreign_keys=[moderator_id])


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    actor_email = Column(String(255), nullable=True)
    action = Column(String(100), nullable=False)
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(Integer, nullable=True)
    payload = Column(Text, nullable=False, default="{}")
    prev_hash = Column(String(64), nullable=False)
    hash = Column(String(64), nullable=False, unique=True)
