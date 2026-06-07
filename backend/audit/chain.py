"""Hash-chained audit log (blockchain simulation).

Every entry is linked to the previous one via SHA-256 of:
    prev_hash || timestamp || actor || action || entity || payload
This makes any tampering detectable: a chain.verify() walks the table and
re-hashes each entry — if any byte is altered the chain breaks.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from config import GENESIS_HASH
from models import AuditLog, User


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _compute_hash(
    prev_hash: str,
    timestamp: datetime,
    actor_email: Optional[str],
    action: str,
    entity_type: str,
    entity_id: Optional[int],
    payload: str,
) -> str:
    h = hashlib.sha256()
    h.update(prev_hash.encode())
    h.update(timestamp.isoformat().encode())
    h.update((actor_email or "system").encode())
    h.update(action.encode())
    h.update(entity_type.encode())
    h.update(str(entity_id or "").encode())
    h.update(payload.encode())
    return h.hexdigest()


def get_last_hash(db: Session) -> str:
    last = db.query(AuditLog).order_by(AuditLog.id.desc()).first()
    return last.hash if last else GENESIS_HASH


def append(
    db: Session,
    *,
    actor: Optional[User],
    action: str,
    entity_type: str,
    entity_id: Optional[int] = None,
    payload: Optional[dict] = None,
) -> AuditLog:
    """Append an entry to the immutable chain."""
    prev = get_last_hash(db)
    ts = datetime.utcnow()
    payload_str = _canonical(payload or {})
    actor_email = actor.email if actor else None
    actor_id = actor.id if actor else None

    digest = _compute_hash(prev, ts, actor_email, action, entity_type, entity_id, payload_str)
    entry = AuditLog(
        timestamp=ts,
        actor_id=actor_id,
        actor_email=actor_email,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload_str,
        prev_hash=prev,
        hash=digest,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def verify(db: Session) -> dict:
    """Walk the chain and verify integrity."""
    entries = db.query(AuditLog).order_by(AuditLog.id.asc()).all()
    prev = GENESIS_HASH
    for e in entries:
        if e.prev_hash != prev:
            return {
                "valid": False,
                "broken_at": e.id,
                "reason": "prev_hash mismatch",
                "entries_checked": e.id,
            }
        recomputed = _compute_hash(
            e.prev_hash, e.timestamp, e.actor_email, e.action,
            e.entity_type, e.entity_id, e.payload,
        )
        if recomputed != e.hash:
            return {
                "valid": False,
                "broken_at": e.id,
                "reason": "hash mismatch (data tampered)",
                "entries_checked": e.id,
            }
        prev = e.hash
    return {"valid": True, "entries_checked": len(entries), "head": prev}
