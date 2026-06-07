"""Seed the database with realistic demo data — including a deliberately
suspicious bid so judges can see the AI engine flag it, plus a fully
exercised multi-step approval workflow, milestones, budget transactions
and a citizen feedback example.

Reseed cleanly with:
    rm backend/data/app.db && python backend/seed.py
"""
from datetime import datetime, timedelta
import random

from ai import risk
from audit import chain
from auth import hash_password
from database import Base, SessionLocal, engine
from models import (
    APPROVAL_STAGES, Approval, ApprovalDecision, Bid, BidStatus,
    BudgetTransaction, BudgetTxKind, Contract, FeedbackReport,
    FeedbackStatus, Milestone, Role, Tender, TenderStatus, User,
)


def _user(db, email, name, role, org=None, password="password123"):
    u = db.query(User).filter(User.email == email).first()
    if u:
        return u
    u = User(
        email=email, full_name=name, organization=org,
        password_hash=hash_password(password), role=role,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _approve_through(db, t: Tender, approvers: dict):
    """Walk every approval stage and approve — produces a complete audit trail."""
    for stage in APPROVAL_STAGES:
        approver = approvers[stage]
        approval = Approval(
            tender_id=t.id, stage=stage, approver_id=approver.id,
            decision=ApprovalDecision.approved,
            comments=f"{stage.title()} review passed.",
        )
        db.add(approval)
        db.commit()
        chain.append(
            db, actor=approver, action=f"tender.approve.{stage}",
            entity_type="tender", entity_id=t.id,
            payload={"stage": stage, "decision": "approved", "comments": approval.comments},
        )
    t.status = TenderStatus.open
    t.current_stage = None
    db.commit()


def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    if db.query(User).count() > 0:
        print("Database already seeded — skipping.")
        db.close()
        return

    print("Seeding users (admin, officer, compliance officer, auditor, contractors, citizens)...")
    admin = _user(db, "admin@gov.demo", "System Admin", Role.admin, password="admin123")
    officer = _user(db, "officer@gov.demo", "Jane Officer", Role.officer, "Ministry of Works", password="officer123")
    compliance = _user(db, "compliance@gov.demo", "Sam Compliance", Role.compliance_officer, "Compliance Bureau", password="compliance123")
    auditor = _user(db, "auditor@gov.demo", "Alex Auditor", Role.auditor, "Office of Audit", password="auditor123")
    citizen = _user(db, "citizen@gov.demo", "Curious Citizen", Role.citizen, password="citizen123")

    contractors = [
        _user(db, "buildco@firm.demo", "BuildCo Ltd", Role.contractor, "BuildCo Ltd", password="contractor123"),
        _user(db, "roads@firm.demo", "RoadWorks Inc", Role.contractor, "RoadWorks Inc", password="contractor123"),
        _user(db, "techsys@firm.demo", "TechSys Group", Role.contractor, "TechSys Group", password="contractor123"),
        _user(db, "greenbuild@firm.demo", "GreenBuild Co", Role.contractor, "GreenBuild Co", password="contractor123"),
        _user(db, "monopoly@firm.demo", "MonopolyCorp", Role.contractor, "MonopolyCorp", password="contractor123"),
    ]
    chain.append(db, actor=admin, action="system.seed", entity_type="system", payload={"users": 10})

    approvers = {
        "department": officer, "financial": officer,
        "compliance": compliance, "final": admin,
    }

    print("Creating tenders (drafted by officer, walked through approval workflow)...")
    tenders_data = [
        ("Construction of District Hospital", "construction", "north", 2_500_000),
        ("Highway Repair – Section 14", "construction", "east", 1_200_000),
        ("School ICT Equipment Supply", "technology", "central", 450_000),
        ("Municipal Waste Management Software", "technology", "south", 180_000),
        ("Street Lighting Upgrade Phase 2", "energy", "west", 320_000),
        ("Hospital Medical Supplies (Annual)", "health", "north", 600_000),
    ]
    tenders = []
    for i, (title, cat, region, budget) in enumerate(tenders_data):
        t = Tender(
            title=title,
            description=f"Public procurement for {title.lower()}. Bidders must comply with the national procurement act and submit a detailed proposal.",
            category=cat, region=region, budget=budget,
            deadline=datetime.utcnow() + timedelta(days=30),
            created_by_id=officer.id,
            status=TenderStatus.pending_department,
            current_stage="department",
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        chain.append(
            db, actor=officer, action="tender.create", entity_type="tender", entity_id=t.id,
            payload={"title": t.title, "budget": t.budget, "category": t.category, "region": t.region},
        )
        # First five tenders go fully through approval. The last one stays
        # half-approved to demonstrate the approval queue in the UI.
        if i < 5:
            _approve_through(db, t, approvers)
        else:
            approval = Approval(
                tender_id=t.id, stage="department", approver_id=officer.id,
                decision=ApprovalDecision.approved, comments="Looks reasonable.",
            )
            db.add(approval)
            t.current_stage = "financial"
            t.status = TenderStatus.pending_financial
            db.commit()
            chain.append(
                db, actor=officer, action="tender.approve.department",
                entity_type="tender", entity_id=t.id,
                payload={"stage": "department", "decision": "approved"},
            )
        tenders.append(t)

    open_tenders = [t for t in tenders if t.status == TenderStatus.open]

    print("Submitting bids (with one deliberately suspicious bid)...")
    random.seed(42)
    for t in open_tenders:
        bidders = random.sample(contractors, k=4)
        for c in bidders:
            base = t.budget * random.uniform(0.85, 1.05)
            price = round(base, -2)
            proposal = (
                f"{c.full_name} proposes to deliver '{t.title}' using certified materials, "
                f"local subcontractors, and a transparent reporting schedule."
            )
            b = Bid(
                tender_id=t.id, contractor_id=c.id,
                price=price, delivery_days=random.randint(60, 180),
                proposal=proposal,
            )
            db.add(b)
            db.commit()
            db.refresh(b)
            chain.append(
                db, actor=c, action="bid.submit", entity_type="bid", entity_id=b.id,
                payload={"tender_id": t.id, "price": price},
            )

    # Inject the suspicious bid on the third open tender (School ICT).
    bad_tender = open_tenders[2]
    suspicious = Bid(
        tender_id=bad_tender.id,
        contractor_id=contractors[4].id,  # MonopolyCorp
        price=round(bad_tender.budget * 1.45, -2),
        delivery_days=400,  # implausible
        proposal="TBD",
    )
    db.add(suspicious)
    db.commit()
    db.refresh(suspicious)
    chain.append(
        db, actor=contractors[4], action="bid.submit", entity_type="bid", entity_id=suspicious.id,
        payload={"tender_id": bad_tender.id, "price": suspicious.price},
    )

    print("Ranking + risk scoring...")
    for t in open_tenders:
        risk.rerank_tender(db, t)
        chain.append(
            db, actor=officer, action="tender.evaluate", entity_type="tender", entity_id=t.id,
            payload={"bid_count": len(t.bids)},
        )

    print("Awarding 3 contracts (one is a deliberate override of the AI ranking)...")
    awarded_contracts = []
    for t in open_tenders[:3]:
        ranked = sorted(t.bids, key=lambda b: b.rank or 999)
        if t.id == bad_tender.id:
            chosen = next(b for b in t.bids if b.id == suspicious.id)
        else:
            chosen = ranked[0]
        c = Contract(
            tender_id=t.id, bid_id=chosen.id, contractor_id=chosen.contractor_id,
            awarded_amount=chosen.price,
            progress_status="in_progress" if t.id != bad_tender.id else "not_started",
            progress_percent=random.choice([10, 25, 40]) if t.id != bad_tender.id else 0,
        )
        db.add(c)
        chosen.status = BidStatus.awarded
        for other in t.bids:
            if other.id != chosen.id:
                other.status = BidStatus.rejected
        t.status = TenderStatus.awarded
        db.commit()
        db.refresh(c)
        if chosen.rank != 1:
            chain.append(
                db, actor=officer, action="bid.award.override", entity_type="bid", entity_id=chosen.id,
                payload={
                    "tender_id": t.id, "awarded_rank": chosen.rank,
                    "risk_score": chosen.risk_score, "price": chosen.price,
                    "warning": "Top-ranked bid was not selected.",
                },
            )
        chain.append(
            db, actor=officer, action="contract.award", entity_type="contract", entity_id=c.id,
            payload={
                "tender_id": t.id, "bid_id": chosen.id,
                "amount": chosen.price, "risk_score": chosen.risk_score,
            },
        )
        # Initial allocation transaction.
        db.add(BudgetTransaction(
            contract_id=c.id, kind=BudgetTxKind.allocation,
            amount=c.awarded_amount, note="Initial allocation on award",
            created_by_id=officer.id,
        ))
        db.commit()
        chain.append(
            db, actor=officer, action="budget.allocation",
            entity_type="budget_tx", entity_id=c.id,
            payload={"contract_id": c.id, "amount": c.awarded_amount},
        )
        awarded_contracts.append(c)

    print("Adding milestones + budget transactions to first contract...")
    main = awarded_contracts[0]
    for i, (title, days, done) in enumerate([
        ("Site preparation", 14, True),
        ("Foundation complete", 45, True),
        ("Structural framing", 90, False),
        ("Mechanical & electrical", 150, False),
        ("Finishing + handover", 200, False),
    ]):
        m = Milestone(
            contract_id=main.id, title=title,
            due_date=main.awarded_at + timedelta(days=days),
            completed_at=main.awarded_at + timedelta(days=days - 2) if done else None,
            notes=f"Stage {i+1} of project plan.",
        )
        db.add(m)
    db.commit()
    # Sample release and expense.
    db.add(BudgetTransaction(
        contract_id=main.id, kind=BudgetTxKind.release,
        amount=round(main.awarded_amount * 0.25, 2),
        note="First instalment released", created_by_id=officer.id,
    ))
    db.add(BudgetTransaction(
        contract_id=main.id, kind=BudgetTxKind.expense,
        amount=round(main.awarded_amount * 0.18, 2),
        note="Site prep + foundation", created_by_id=officer.id,
    ))
    db.commit()
    chain.append(db, actor=officer, action="budget.release", entity_type="budget_tx",
                 entity_id=main.id, payload={"contract_id": main.id, "amount": main.awarded_amount * 0.25})
    chain.append(db, actor=officer, action="budget.expense", entity_type="budget_tx",
                 entity_id=main.id, payload={"contract_id": main.id, "amount": main.awarded_amount * 0.18})

    print("Adding sample citizen feedback (one anonymous, one signed)...")
    db.add(FeedbackReport(
        tender_id=bad_tender.id, contract_id=awarded_contracts[2].id,
        reporter_id=None, reporter_label="Concerned resident",
        category="suspicious_award",
        message=(
            "The ICT supply contract was awarded to MonopolyCorp at 45% above the "
            "published budget, despite a missing proposal. Please investigate."
        ),
        status=FeedbackStatus.under_review,
    ))
    db.add(FeedbackReport(
        contract_id=main.id, reporter_id=citizen.id,
        reporter_label=citizen.full_name,
        category="progress_check",
        message="Drove past the hospital site yesterday — visible progress, on schedule.",
        status=FeedbackStatus.resolved,
    ))
    db.commit()
    chain.append(db, actor=None, action="feedback.submit", entity_type="feedback",
                 payload={"anonymous": True, "category": "suspicious_award"})
    chain.append(db, actor=citizen, action="feedback.submit", entity_type="feedback",
                 payload={"anonymous": False, "category": "progress_check"})

    db.close()
    print("\nSeed complete.")
    print("\nDemo logins:")
    for line in [
        ("admin@gov.demo",      "admin123",      "System Admin"),
        ("officer@gov.demo",    "officer123",    "Procurement Officer"),
        ("compliance@gov.demo", "compliance123", "Compliance Officer"),
        ("auditor@gov.demo",    "auditor123",    "Auditor"),
        ("buildco@firm.demo",   "contractor123", "Contractor (BuildCo)"),
        ("monopoly@firm.demo",  "contractor123", "Contractor (MonopolyCorp)"),
        ("citizen@gov.demo",    "citizen123",    "Citizen"),
    ]:
        print(f"  {line[0]:<22} / {line[1]:<15} — {line[2]}")


if __name__ == "__main__":
    seed()
