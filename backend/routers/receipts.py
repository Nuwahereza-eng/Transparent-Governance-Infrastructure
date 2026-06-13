"""Beneficiary receipt routes — citizens and field officers upload photos
proving goods/services were actually received. Every receipt is publicly
listable and hash-chained.

Anti-corruption properties exposed:
- ghost beneficiary detection: receipt count vs. claimed delivery count
- duplicate detection: same id_hash submitted twice → flagged
- geo clustering: if 200 'deliveries' all happened at the same address → flagged
- photo integrity: same photo bytes reused → rejected;
  missing/stale EXIF → flagged so investigators can audit
"""
import hashlib
import io
import math
import uuid
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from audit import chain
from auth import get_current_user_optional
from config import DATA_DIR
from database import get_db
from models import BeneficiaryReceipt, Contract, Milestone, Role, User
from schemas import BeneficiaryReceiptOut, BeneficiaryReceiptStats

router = APIRouter(prefix="/api/contracts", tags=["receipts"])

UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
MAX_PHOTO_BYTES = 8 * 1024 * 1024  # 8 MB
PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp"}
STALE_PHOTO_DAYS = 14  # EXIF capture date older than this triggers a flag
GPS_MISMATCH_METERS = 500


def _anomalies_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [s for s in raw.split(",") if s]


def _out(r: BeneficiaryReceipt) -> BeneficiaryReceiptOut:
    return BeneficiaryReceiptOut(
        id=r.id, contract_id=r.contract_id, milestone_id=r.milestone_id,
        recipient_name=r.recipient_name, item_received=r.item_received,
        location_text=r.location_text,
        gps_lat=r.gps_lat, gps_lng=r.gps_lng,
        photo_url=f"/api/uploads/{Path(r.photo_stored_path).name}",
        photo_sha256=r.photo_sha256,
        photo_anomalies=_anomalies_list(r.photo_anomalies),
        exif_datetime=r.exif_datetime,
        recorded_by_name=r.recorded_by.full_name if r.recorded_by else None,
        is_anonymous_submission=r.recorded_by_id is None,
        created_at=r.created_at,
    )


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0  # earth radius m
    p = math.pi / 180
    a = (
        0.5 - math.cos((lat2 - lat1) * p) / 2
        + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2
    )
    return 2 * r * math.asin(math.sqrt(a))


def _exif_inspect(contents: bytes, declared_lat: float | None,
                  declared_lng: float | None) -> tuple[datetime | None, float | None, float | None, list[str]]:
    """Return (exif_dt, exif_lat, exif_lng, anomaly_codes).

    Anomaly codes:
      - exif_missing      : image has no EXIF metadata at all
      - exif_no_datetime  : EXIF present but no capture timestamp
      - exif_stale        : capture timestamp is >STALE_PHOTO_DAYS old
      - exif_no_gps       : EXIF present but no GPS coordinates
      - exif_gps_mismatch : EXIF GPS differs from submitted GPS by > 500m
    """
    anomalies: list[str] = []
    exif_dt: datetime | None = None
    exif_lat: float | None = None
    exif_lng: float | None = None
    try:
        from PIL import Image, ExifTags  # local import keeps cold-start cheap
    except Exception:
        return None, None, None, ["exif_lib_missing"]

    try:
        img = Image.open(io.BytesIO(contents))
        exif = img.getexif() if hasattr(img, "getexif") else None
    except Exception:
        return None, None, None, ["exif_unreadable"]

    if not exif:
        return None, None, None, ["exif_missing"]

    # Capture datetime
    tag_name = {v: k for k, v in ExifTags.TAGS.items()}
    dt_tag = tag_name.get("DateTimeOriginal") or tag_name.get("DateTime")
    raw_dt = exif.get(dt_tag) if dt_tag else None
    if raw_dt:
        try:
            exif_dt = datetime.strptime(str(raw_dt), "%Y:%m:%d %H:%M:%S")
        except ValueError:
            exif_dt = None
    if exif_dt is None:
        anomalies.append("exif_no_datetime")
    elif datetime.utcnow() - exif_dt > timedelta(days=STALE_PHOTO_DAYS):
        anomalies.append("exif_stale")

    # GPS
    gps_tag = tag_name.get("GPSInfo")
    gps_block = exif.get_ifd(gps_tag) if gps_tag and hasattr(exif, "get_ifd") else None
    if not gps_block:
        anomalies.append("exif_no_gps")
    else:
        def _dms_to_deg(dms, ref):
            try:
                d, m, s = [float(x) for x in dms]
                v = d + m / 60 + s / 3600
                if ref in ("S", "W"):
                    v = -v
                return v
            except Exception:
                return None
        lat_dms = gps_block.get(2)
        lat_ref = gps_block.get(1)
        lng_dms = gps_block.get(4)
        lng_ref = gps_block.get(3)
        if lat_dms and lng_dms and lat_ref and lng_ref:
            exif_lat = _dms_to_deg(lat_dms, lat_ref)
            exif_lng = _dms_to_deg(lng_dms, lng_ref)
            if (exif_lat is not None and exif_lng is not None
                    and declared_lat is not None and declared_lng is not None):
                try:
                    if _haversine_m(exif_lat, exif_lng, declared_lat, declared_lng) > GPS_MISMATCH_METERS:
                        anomalies.append("exif_gps_mismatch")
                except Exception:
                    pass
        else:
            anomalies.append("exif_no_gps")

    return exif_dt, exif_lat, exif_lng, anomalies


@router.get("/{contract_id}/receipts", response_model=list[BeneficiaryReceiptOut])
def list_receipts(contract_id: int, db: Session = Depends(get_db)):
    """Public: anyone can see the photo proof of deliveries."""
    c = db.query(Contract).get(contract_id)
    if not c:
        raise HTTPException(404, "Contract not found")
    rows = (
        db.query(BeneficiaryReceipt)
        .filter(BeneficiaryReceipt.contract_id == contract_id)
        .order_by(BeneficiaryReceipt.created_at.desc())
        .all()
    )
    return [_out(r) for r in rows]


@router.get("/{contract_id}/receipts/stats", response_model=BeneficiaryReceiptStats)
def receipt_stats(contract_id: int, db: Session = Depends(get_db)):
    """Aggregated transparency stats + automatic anomaly flags."""
    c = db.query(Contract).get(contract_id)
    if not c:
        raise HTTPException(404, "Contract not found")
    rows = (
        db.query(BeneficiaryReceipt)
        .filter(BeneficiaryReceipt.contract_id == contract_id)
        .all()
    )
    total = len(rows)
    locations = [r.location_text for r in rows if r.location_text]
    loc_counter = Counter(locations)
    location_clusters = [
        {"label": loc, "count": n}
        for loc, n in loc_counter.most_common(8)
    ]
    id_hashes = [r.recipient_id_hash for r in rows if r.recipient_id_hash]
    hash_counter = Counter(id_hashes)
    duplicates = [(h, n) for h, n in hash_counter.items() if n > 1]
    duplicate_id_count = sum(n - 1 for _h, n in duplicates)

    # Photo integrity stats
    missing_exif = 0
    stale_exif = 0
    gps_mismatch = 0
    reused_across = 0
    for r in rows:
        codes = set(_anomalies_list(r.photo_anomalies))
        if {"exif_missing", "exif_no_datetime", "exif_unreadable", "exif_lib_missing"} & codes:
            missing_exif += 1
        if "exif_stale" in codes:
            stale_exif += 1
        if "exif_gps_mismatch" in codes:
            gps_mismatch += 1
        if "photo_reused_across_contract" in codes:
            reused_across += 1
    # Same-contract dup photos: should be 0 because we reject on submit, but
    # surface defensively if seed/back-fill data ever produces one.
    photo_hashes = [r.photo_sha256 for r in rows if r.photo_sha256]
    photo_counter = Counter(photo_hashes)
    duplicate_photo_count = sum(n - 1 for _h, n in photo_counter.items() if n > 1)

    flags: list[dict] = []
    # 1. Duplicate beneficiary IDs
    if duplicate_id_count > 0:
        flags.append({
            "tone": "err",
            "title": f"{duplicate_id_count} duplicate recipient ID(s) detected",
            "detail": "The same beneficiary appears multiple times. "
                      "This may indicate double-dipping or fake distribution lists.",
        })
    # 2. Single-location concentration (>70% of receipts at one place when
    #    there are more than 10 receipts).
    if total >= 10 and location_clusters:
        top = location_clusters[0]
        share = top["count"] / total
        if share >= 0.7:
            flags.append({
                "tone": "warn",
                "title": f"{round(share * 100)}% of receipts cluster at a single location",
                "detail": f"\"{top['label']}\" accounts for {top['count']} of {total} receipts. "
                          "Genuine distribution programmes usually spread across sites.",
            })
    # 3. Coverage vs. progress: if contract reports >50% progress but <5
    #    receipts exist for an awarded contract, that's a red flag.
    if c.progress_percent and c.progress_percent >= 50 and total < 5:
        flags.append({
            "tone": "err",
            "title": "Reported progress not backed by delivery proof",
            "detail": f"Contract reports {c.progress_percent}% complete but only "
                      f"{total} beneficiary receipt(s) have been recorded.",
        })
    # 4. Photo integrity flags
    if duplicate_photo_count > 0:
        flags.append({
            "tone": "err",
            "title": f"{duplicate_photo_count} duplicate photo(s) on file",
            "detail": "The same image bytes were used for multiple receipts. "
                      "Strong indicator of fabricated delivery records.",
        })
    if reused_across > 0:
        flags.append({
            "tone": "err",
            "title": f"{reused_across} photo(s) reused from another contract",
            "detail": "These exact image bytes also appear on a different "
                      "contract's receipt — the photo cannot prove this delivery.",
        })
    if total >= 3 and missing_exif / max(total, 1) >= 0.5:
        flags.append({
            "tone": "warn",
            "title": f"{missing_exif} of {total} photos have no capture metadata",
            "detail": "Most receipts lack EXIF data — receipts may have been "
                      "edited, screenshotted, or re-uploaded.",
        })
    if gps_mismatch > 0:
        flags.append({
            "tone": "warn",
            "title": f"{gps_mismatch} photo(s) with GPS mismatch (>500m)",
            "detail": "The photo's embedded GPS does not match the submitted "
                      "location. Worth a closer look by auditors.",
        })

    most_recent = max((r.created_at for r in rows), default=None)
    return BeneficiaryReceiptStats(
        total_receipts=total,
        distinct_locations=len(loc_counter),
        duplicate_id_count=duplicate_id_count,
        duplicate_photo_count=duplicate_photo_count + reused_across,
        photos_missing_exif=missing_exif,
        most_recent_at=most_recent,
        location_clusters=location_clusters,
        flags=flags,
    )


@router.post("/{contract_id}/receipts", response_model=BeneficiaryReceiptOut)
async def submit_receipt(
    contract_id: int,
    request: Request,
    photo: UploadFile = File(...),
    item_received: str = Form(""),
    recipient_name: str = Form(""),
    recipient_id: str = Form(""),  # raw — we hash it server-side
    location_text: str = Form(""),
    gps_lat: float | None = Form(None),
    gps_lng: float | None = Form(None),
    milestone_id: int | None = Form(None),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    """Submit a delivery receipt with photo proof.

    Accepts both authenticated submissions (field officer with a token) and
    unauthenticated ones (citizen recording their own delivery). All entries
    are appended to the audit chain regardless."""
    c = db.query(Contract).get(contract_id)
    if not c:
        raise HTTPException(404, "Contract not found")

    if milestone_id is not None:
        m = db.query(Milestone).get(milestone_id)
        if not m or m.contract_id != c.id:
            raise HTTPException(400, "Milestone does not belong to this contract")

    if photo.content_type not in PHOTO_TYPES:
        raise HTTPException(400, f"Photo must be JPEG / PNG / WebP (got {photo.content_type})")

    contents = await photo.read()
    if len(contents) > MAX_PHOTO_BYTES:
        raise HTTPException(400, f"Photo too large (>{MAX_PHOTO_BYTES // 1024 // 1024} MB)")
    if len(contents) < 200:
        raise HTTPException(400, "Photo is suspiciously small (<200 bytes)")

    # Integrity: SHA-256 of the bytes. Reject if the exact same photo is
    # already on file for this contract — that's almost certainly a fake
    # delivery re-using a stock image or earlier capture.
    photo_sha = hashlib.sha256(contents).hexdigest()
    existing = (
        db.query(BeneficiaryReceipt)
        .filter(BeneficiaryReceipt.contract_id == c.id,
                BeneficiaryReceipt.photo_sha256 == photo_sha)
        .first()
    )
    if existing:
        raise HTTPException(
            400,
            "This exact photo has already been submitted for this contract "
            f"(receipt #{existing.id}). Each beneficiary must provide a unique "
            "photo as proof of delivery.",
        )
    cross_contract = (
        db.query(BeneficiaryReceipt)
        .filter(BeneficiaryReceipt.photo_sha256 == photo_sha)
        .first()
    )
    anomalies: list[str] = []
    if cross_contract:
        anomalies.append("photo_reused_across_contract")

    # EXIF inspection (best-effort, never blocks an upload).
    exif_dt, exif_lat, exif_lng, exif_codes = _exif_inspect(contents, gps_lat, gps_lng)
    anomalies.extend(exif_codes)

    ext = Path(photo.filename or "").suffix or ".jpg"
    safe_name = f"receipt_{uuid.uuid4().hex}{ext}"
    target = UPLOAD_DIR / safe_name
    with open(target, "wb") as f:
        f.write(contents)

    id_hash = None
    if recipient_id.strip():
        # one-way hash so we can detect duplicates without storing PII
        id_hash = hashlib.sha256(
            (recipient_id.strip().lower() + f"|contract:{c.id}").encode()
        ).hexdigest()

    r = BeneficiaryReceipt(
        contract_id=c.id,
        milestone_id=milestone_id,
        recipient_name=(recipient_name.strip() or None),
        recipient_id_hash=id_hash,
        item_received=item_received.strip(),
        location_text=(location_text.strip() or None),
        gps_lat=gps_lat,
        gps_lng=gps_lng,
        photo_filename=photo.filename or safe_name,
        photo_stored_path=str(target),
        photo_content_type=photo.content_type,
        photo_size=len(contents),
        photo_sha256=photo_sha,
        photo_anomalies=",".join(anomalies),
        exif_datetime=exif_dt,
        exif_gps_lat=exif_lat,
        exif_gps_lng=exif_lng,
        recorded_by_id=user.id if user else None,
    )
    db.add(r)
    db.commit()
    db.refresh(r)

    chain.append(
        db,
        actor=user,
        action="beneficiary.receipt",
        entity_type="receipt",
        entity_id=r.id,
        payload={
            "contract_id": c.id,
            "milestone_id": milestone_id,
            "item_received": r.item_received,
            "location_text": r.location_text,
            "has_gps": r.gps_lat is not None and r.gps_lng is not None,
            "has_recipient_id": id_hash is not None,
            "photo_size": r.photo_size,
            "photo_sha256": photo_sha,
            "photo_anomalies": anomalies,
            "anonymous": user is None,
        },
    )
    return _out(r)
