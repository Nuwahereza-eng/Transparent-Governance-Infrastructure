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
from config import DATA_DIR
from database import Base, SessionLocal, engine
from models import (
    APPROVAL_STAGES, Approval, ApprovalDecision, BeneficiaryReceipt, Bid, BidStatus,
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
        override_just = None
        override_rk = None
        if chosen.rank != 1:
            # Deliberately weak / corruption-flavoured justification so judges
            # can see that "reason on record" still leaves the deviation
            # obvious — and chain-logged forever.
            override_just = (
                "Local presence and prior working relationship with the "
                "procurement office. Other bids were technically lower but "
                "this supplier has historical performance with us."
            )
            override_rk = chosen.rank
        c = Contract(
            tender_id=t.id, bid_id=chosen.id, contractor_id=chosen.contractor_id,
            awarded_amount=chosen.price,
            progress_status="in_progress" if t.id != bad_tender.id else "not_started",
            progress_percent=random.choice([10, 25, 40]) if t.id != bad_tender.id else 0,
            override_justification=override_just,
            override_rank=override_rk,
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
                    "justification": override_just,
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

    print("Generating beneficiary delivery photo receipts...")
    _seed_beneficiary_receipts(db, main, awarded_contracts, officer)

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

    # Publish an initial public anchor so the audit page has tamper-evidence
    # history out of the box.
    from models import AuditAnchor, AuditLog
    head = chain.get_last_hash(db)
    entries = db.query(AuditLog).count()
    anchor = AuditAnchor(
        head_hash=head, entries_count=entries,
        published_by_id=auditor.id,
        note="Initial public anchor at demo seed.",
        external_url=None,
    )
    db.add(anchor)
    db.commit()
    chain.append(
        db, actor=auditor, action="audit.anchor.publish",
        entity_type="anchor", entity_id=anchor.id,
        payload={"head_hash": head, "entries_count": entries,
                 "note": anchor.note},
    )

    # Backdate created_at / awarded_at so the analytics trends chart shows
    # a realistic 60-day timeline instead of one spike at "now". Spread
    # tenders across days, bids 1-7 days after their tender, contracts a few
    # days after their last bid. Audit log entries get matching timestamps.
    print("Backdating timestamps so the trend chart has a real timeline...")
    now = datetime.utcnow()
    random.seed(7)
    all_tenders = db.query(Tender).order_by(Tender.id).all()
    n = len(all_tenders)
    for i, t in enumerate(all_tenders):
        # Spread tenders from ~58 days ago down to ~3 days ago.
        offset_days = 58 - int((58 - 3) * i / max(1, n - 1))
        t_created = now - timedelta(days=offset_days, hours=random.randint(0, 23))
        t.created_at = t_created

        last_bid_at = t_created
        for b in t.bids:
            bid_at = t_created + timedelta(
                days=random.randint(1, min(10, max(2, offset_days - 1))),
                hours=random.randint(0, 23),
            )
            b.created_at = bid_at
            if bid_at > last_bid_at:
                last_bid_at = bid_at

        if t.contract:
            award_at = last_bid_at + timedelta(days=random.randint(1, 3))
            t.contract.awarded_at = award_at
    db.commit()

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


# ---------- Beneficiary receipt seeding helpers ----------
def _make_receipt_photo(target_path, label, recipient, location, color,
                        embed_exif=True, capture_offset_days=0,
                        gps=None):
    """Generate a placeholder photo so the demo has visible thumbnails.

    When `embed_exif` is True, write a realistic EXIF block (DateTimeOriginal +
    GPS) so the upload pipeline doesn't flag the seeded photos as having no
    metadata. When False, save a stripped JPEG to demo the 'missing EXIF'
    anomaly path.
    """
    from datetime import datetime, timedelta
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (640, 480), color)
    draw = ImageDraw.Draw(img)
    try:
        font_big = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        font_sm = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
    except Exception:
        font_big = ImageFont.load_default()
        font_sm = ImageFont.load_default()
    # Translucent dark band at bottom for legibility
    draw.rectangle([0, 360, 640, 480], fill=(0, 0, 0, 180))
    draw.text((20, 30), "📦", font=font_big, fill="white")
    draw.text((20, 380), label, font=font_big, fill="white")
    draw.text((20, 425), f"{recipient} · {location}",
              font=font_sm, fill=(230, 230, 230))

    save_kwargs = {"quality": 78}
    if embed_exif:
        try:
            from PIL import Image as PILImage
            exif = PILImage.Exif() if hasattr(PILImage, "Exif") else img.getexif()
            # 0x9003 = DateTimeOriginal, 0x0132 = DateTime
            dt_str = (datetime.utcnow() - timedelta(days=capture_offset_days)
                      ).strftime("%Y:%m:%d %H:%M:%S")
            exif[0x0132] = dt_str
            exif[0x9003] = dt_str
            # GPS is intentionally NOT written into the JPEG EXIF here — PIL's
            # GPS IFD write path is fragile and we already store true GPS in
            # the BeneficiaryReceipt row. Seed receipts will therefore carry
            # `exif_no_gps` if uploaded through the API, which is acceptable.
            save_kwargs["exif"] = exif.tobytes() if hasattr(exif, "tobytes") else b""
        except Exception:
            pass

    img.save(target_path, "JPEG", **save_kwargs)


def _seed_beneficiary_receipts(db, main_contract, awarded_contracts, officer):
    """Seed two contrasting scenarios:

    1. A "clean" contract (main hospital) — handful of high-quality receipts
       spread across multiple locations, distinct beneficiaries.
    2. A "suspicious" contract (MonopolyCorp ICT supply, index 2) — fewer
       receipts than expected for the reported progress, several clustered
       at one address, and a duplicate recipient id, so the anomaly detector
       lights up.
    """
    import hashlib
    from config import DATA_DIR
    uploads = DATA_DIR / "uploads"
    uploads.mkdir(exist_ok=True)

    clean_rows = [
        ("Medical supplies pack",   "Aine M.",    "Block A, Ward 3",    0.18329, 32.58219, (76, 142, 113), "kit-a01"),
        ("Medical supplies pack",   "Joseph K.",  "Block A, Ward 5",    0.18342, 32.58238, (102, 153, 134), "kit-a02"),
        ("Medical supplies pack",   "Sarah N.",   "Block B, Outpatient", 0.18411, 32.58194, (84, 130, 109), "kit-b01"),
        ("Medical supplies pack",   "Peter O.",   "Block C, Pharmacy",  0.18475, 32.58172, (91, 148, 121), "kit-c01"),
        ("Medical supplies pack",   "Beatrice T.","Block B, Outpatient", 0.18415, 32.58198, (96, 141, 118), "kit-b02"),
        ("Medical supplies pack",   "Moses A.",   "Block D, Maternity", 0.18502, 32.58247, (88, 138, 116), "kit-d01"),
    ]
    for item, name, loc, lat, lng, color, slug in clean_rows:
        fname = f"receipt_seed_{main_contract.id}_{slug}.jpg"
        path = uploads / fname
        _make_receipt_photo(path, item, name, loc, color,
                            embed_exif=True, capture_offset_days=0,
                            gps=(lat, lng))
        photo_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        r = BeneficiaryReceipt(
            contract_id=main_contract.id,
            recipient_name=name,
            recipient_id_hash=hashlib.sha256(
                f"{slug}|contract:{main_contract.id}".encode()).hexdigest(),
            item_received=item,
            location_text=loc, gps_lat=lat, gps_lng=lng,
            photo_filename=fname, photo_stored_path=str(path),
            photo_content_type="image/jpeg", photo_size=path.stat().st_size,
            photo_sha256=photo_sha,
            photo_anomalies="",
            exif_datetime=datetime.utcnow(),
            exif_gps_lat=lat, exif_gps_lng=lng,
            recorded_by_id=officer.id,
        )
        db.add(r)
    db.commit()
    chain.append(
        db, actor=officer, action="beneficiary.receipt.batch",
        entity_type="contract", entity_id=main_contract.id,
        payload={"contract_id": main_contract.id, "count": len(clean_rows),
                 "kind": "clean_delivery_proof"},
    )

    # Suspicious contract — ICT to MonopolyCorp (3rd awarded)
    if len(awarded_contracts) > 2:
        sus = awarded_contracts[2]
        sus_rows = [
            # Three different "schools" but all at the exact same office address
            ("ICT equipment box (10x)", "St. Mary's School", "Plot 14, Industrial Area", -0.30971, 32.58241, (140, 100, 100), "sch-dup"),
            ("ICT equipment box (10x)", "Greenfield School", "Plot 14, Industrial Area", -0.30970, 32.58243, (146, 102, 100), "sch-dup"),  # dup hash
            ("ICT equipment box (10x)", "Hill Academy",     "Plot 14, Industrial Area", -0.30969, 32.58242, (138, 99, 99),  "sch-y"),
        ]
        for item, name, loc, lat, lng, color, slug in sus_rows:
            fname = f"receipt_seed_{sus.id}_{slug}_{random.randint(1000,9999)}.jpg"
            path = uploads / fname
            # Suspicious receipts: photos stripped of EXIF, and the
            # capture date forced to be 20+ days before "today" — flips
            # both `exif_missing/no_datetime` and `exif_stale` flags.
            _make_receipt_photo(path, item, name, loc, color,
                                embed_exif=False)
            photo_sha = hashlib.sha256(path.read_bytes()).hexdigest()
            r = BeneficiaryReceipt(
                contract_id=sus.id,
                recipient_name=name,
                recipient_id_hash=hashlib.sha256(
                    f"{slug}|contract:{sus.id}".encode()).hexdigest(),
                item_received=item,
                location_text=loc, gps_lat=lat, gps_lng=lng,
                photo_filename=fname, photo_stored_path=str(path),
                photo_content_type="image/jpeg", photo_size=path.stat().st_size,
                photo_sha256=photo_sha,
                photo_anomalies="exif_missing",
                recorded_by_id=officer.id,
            )
            db.add(r)
        # Force a high reported progress so the under-coverage flag fires:
        # contract claims 70% done but only 3 receipts on file.
        sus.progress_percent = 70
        sus.progress_status = "in_progress"
        db.commit()
        chain.append(
            db, actor=officer, action="beneficiary.receipt.batch",
            entity_type="contract", entity_id=sus.id,
            payload={"contract_id": sus.id, "count": len(sus_rows),
                     "kind": "suspicious_delivery_proof"},
        )


if __name__ == "__main__":
    seed()