"""Rule-based corruption-risk scoring engine.

This is intentionally simple, transparent and explainable — judges (and
citizens) need to understand *why* a bid was flagged, not just see a number.

Score ranges 0–100. Higher = riskier.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean
from typing import Iterable

from sqlalchemy.orm import Session

from models import Bid, BidStatus, Contract, Tender, User


@dataclass
class RiskResult:
    score: float
    level: str
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"score": round(self.score, 1), "level": self.level, "reasons": self.reasons}


def _level(score: float) -> str:
    if score >= 60:
        return "High"
    if score >= 30:
        return "Medium"
    return "Low"


def score_bid(db: Session, bid: Bid, peer_bids: Iterable[Bid] | None = None) -> RiskResult:
    """Compute a risk score for a single bid."""
    reasons: list[str] = []
    score = 0.0

    # 1) Price deviation from peer average (excluding this bid).
    if peer_bids is None:
        peer_bids = (
            db.query(Bid)
            .filter(Bid.tender_id == bid.tender_id, Bid.id != bid.id)
            .all()
        )
    peer_prices = [b.price for b in peer_bids if b.price is not None]
    if peer_prices:
        avg = mean(peer_prices)
        if avg > 0:
            dev = (bid.price - avg) / avg
            if dev > 0.25:
                score += 30
                reasons.append(
                    f"Price is {dev * 100:.0f}% above peer average ({avg:,.0f})."
                )
            elif dev < -0.40:
                score += 20
                reasons.append(
                    f"Price is {abs(dev) * 100:.0f}% below peer average — possible underbidding."
                )

    # 2) Price vs tender budget.
    tender = bid.tender or db.query(Tender).get(bid.tender_id)
    if tender and tender.budget:
        if bid.price > tender.budget * 1.10:
            score += 15
            reasons.append("Bid exceeds the published tender budget by >10%.")

    # 3) Contractor history — frequent winners.
    wins = (
        db.query(Contract)
        .filter(Contract.contractor_id == bid.contractor_id)
        .count()
    )
    if wins >= 3:
        score += 20
        reasons.append(f"Contractor has already won {wins} contracts (concentration risk).")
    elif wins == 2:
        score += 10
        reasons.append("Contractor has won 2 previous contracts.")

    # 4) Repeated pattern — same contractor bidding on many tenders in same category.
    if tender:
        same_cat = (
            db.query(Bid)
            .join(Tender, Bid.tender_id == Tender.id)
            .filter(
                Bid.contractor_id == bid.contractor_id,
                Tender.category == tender.category,
                Bid.id != bid.id,
            )
            .count()
        )
        if same_cat >= 5:
            score += 15
            reasons.append(
                f"Contractor has bid on {same_cat} other tenders in category '{tender.category}'."
            )

    # 5) Missing/anomalous data.
    anomalies = 0
    if not bid.proposal or len(bid.proposal.strip()) < 20:
        anomalies += 1
        reasons.append("Proposal description is missing or extremely short.")
    if bid.delivery_days <= 0 or bid.delivery_days > 365:
        anomalies += 1
        reasons.append("Delivery timeline is implausible (>1 year or non-positive).")
    if bid.price <= 0:
        anomalies += 1
        reasons.append("Bid price is zero or negative.")
    score += anomalies * 10

    score = max(0.0, min(100.0, score))
    if not reasons:
        reasons.append("No risk indicators detected.")
    return RiskResult(score=score, level=_level(score), reasons=reasons)


def composite_score(bid: Bid, min_price: float, max_price: float) -> float:
    """Lower is better. Combines price competitiveness with risk.

    Uses a normalized price (0=cheapest, 1=most expensive) and risk/100,
    so the cheapest *and* lowest-risk bid wins.
    """
    price_norm = 0.0
    if max_price > min_price:
        price_norm = (bid.price - min_price) / (max_price - min_price)
    risk_norm = (bid.risk_score or 0) / 100.0
    # 60% weight on price, 40% on risk.
    return round(0.6 * price_norm + 0.4 * risk_norm, 4)


def rerank_tender(db: Session, tender: Tender) -> list[Bid]:
    """Recompute risk + composite score and rank for every bid in a tender."""
    bids: list[Bid] = list(tender.bids)
    if not bids:
        return []

    for b in bids:
        result = score_bid(db, b, peer_bids=[x for x in bids if x.id != b.id])
        b.risk_score = result.score
        b.risk_level = result.level
        b.risk_explanation = "\n".join(f"• {r}" for r in result.reasons)

    prices = [b.price for b in bids]
    lo, hi = min(prices), max(prices)
    for b in bids:
        b.composite_score = composite_score(b, lo, hi)

    bids.sort(key=lambda b: (b.composite_score, b.price))
    for idx, b in enumerate(bids, start=1):
        b.rank = idx
        if b.status == BidStatus.submitted:
            b.status = BidStatus.ranked

    db.commit()
    return bids
