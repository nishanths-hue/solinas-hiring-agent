from collections import Counter
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.models import Role, Candidate, SlaClock, get_db, User
from app.auth import get_current_user
from app.sla import evaluate_open_clocks

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/overview")
def overview(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Section 31.A — Overall Hiring Dashboard. Deliberately role-level, no
    candidate PII (Section 30: 'candidate-level detail should NOT clutter dashboards')."""
    roles = db.query(Role).filter(Role.stage.in_(["Live Hiring", "Approved"])).all()
    candidates = db.query(Candidate).filter(Candidate.status == "Active").all()

    stage_counts = Counter(c.stage for c in candidates)

    return {
        "open_roles": len(roles),
        "roles_by_priority": Counter(r.hiring_priority for r in roles),
        "active_candidates": len(candidates),
        "candidates_by_stage": dict(stage_counts),
    }


@router.get("/sla-status")
def sla_status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Section 33/34 — operational velocity + ownership queue, condensed.
    Runs evaluate_open_clocks() live rather than relying on a stale cron
    result, since this is a low-volume table; move to a scheduled job once
    clock volume makes per-request evaluation slow."""
    changed = evaluate_open_clocks(db)
    open_clocks = db.query(SlaClock).filter(SlaClock.completed_at.is_(None)).all()
    breached = [c for c in open_clocks if c.escalation_level != "On Track"]

    return {
        "open_clocks": len(open_clocks),
        "breached": len(breached),
        "newly_escalated_this_check": len(changed),
        "breach_detail": [
            {"entity_type": c.entity_type, "entity_id": c.entity_id,
             "stage_name": c.stage_name, "escalation_level": c.escalation_level}
            for c in breached
        ],
    }
