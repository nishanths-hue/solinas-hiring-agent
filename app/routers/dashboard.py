from collections import Counter
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.models import Role, Candidate, SlaClock, Interview, ResumeScreeningResult, get_db, User
from app.auth import get_current_user
from app.sla import evaluate_open_clocks, ROLE_AGING_DAYS

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


@router.get("/funnel")
def funnel(role_id: int = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Section 31.B-style funnel view — candidate counts at each stage, either
    system-wide or for one role. This is the thing "how's hiring going for X
    role" actually needs, which /overview intentionally doesn't provide
    (overview is deliberately role-agnostic per its own docstring).
    """
    query = db.query(Candidate).filter(Candidate.status == "Active")
    if role_id is not None:
        role = db.query(Role).get(role_id)
        if not role:
            raise HTTPException(404, "Role not found")
        query = query.filter(Candidate.role_id == role_id)

    candidates = query.all()
    stage_order = [
        "Applied", "Resume Review", "Shortlisted", "Interview Process",
        "Assignment Sent", "Assignment Submitted", "Final Evaluation",
        "Reference Check", "Offer Discussion", "Offer Released",
        "Offer Accepted", "Joined",
    ]
    counts = Counter(c.stage for c in candidates)
    funnel_ordered = {stage: counts[stage] for stage in stage_order if stage in counts}

    rejected_query = db.query(Candidate).filter(Candidate.status == "Rejected")
    if role_id is not None:
        rejected_query = rejected_query.filter(Candidate.role_id == role_id)
    rejected_count = rejected_query.count()

    return {
        "role_id": role_id,
        "total_active": len(candidates),
        "funnel": funnel_ordered,
        "rejected": rejected_count,
    }


@router.get("/roles/{role_id}/pipeline")
def role_pipeline(role_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Per-candidate pipeline detail for ONE role — the drill-down /overview and
    /funnel both deliberately avoid (Section 30: dashboards shouldn't be
    cluttered with candidate-level detail by default). This exists as the
    explicit "click into a role" view, not the default landing view.
    Field-filtered same as GET /candidates/{id}: compensation-adjacent data
    isn't in here at all, so no role-based stripping needed at this endpoint.
    """
    role = db.query(Role).get(role_id)
    if not role:
        raise HTTPException(404, "Role not found")

    candidates = db.query(Candidate).filter(Candidate.role_id == role_id, Candidate.status == "Active").all()

    result = []
    for c in candidates:
        latest_screening = (
            db.query(ResumeScreeningResult)
            .filter(ResumeScreeningResult.candidate_id == c.id)
            .order_by(ResumeScreeningResult.created_at.desc())
            .first()
        )
        interviews = db.query(Interview).filter(Interview.candidate_id == c.id).all()
        well_covered = len([iv for iv in interviews if iv.coverage_level == "Well Covered"])

        result.append({
            "candidate_id": c.id,
            "full_name": c.full_name,
            "stage": c.stage,
            "fit_score": latest_screening.fit_score if latest_screening else None,
            "suggested_priority": latest_screening.suggested_priority if latest_screening else None,
            "interviews_completed": len(interviews),
            "areas_well_covered": well_covered,
        })

    return {
        "role_id": role_id,
        "role_title": role.role_title,
        "candidate_count": len(result),
        "candidates": result,
    }


@router.get("/aging-roles")
def aging_roles(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Section 29 — role-priority-relative aging (a Critical role open 20 days
    is a problem; a Medium role open 20 days isn't). Surfaces roles past
    their own priority's threshold, sorted worst-first — this is the
    Leadership-facing "what's stuck" view that /overview's raw counts don't
    surface on their own.
    """
    roles = db.query(Role).filter(Role.stage.in_(["Live Hiring", "Approved"])).all()
    now = datetime.now(timezone.utc)

    flagged = []
    for r in roles:
        created = r.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_days = (now - created).days
        threshold = ROLE_AGING_DAYS.get(r.hiring_priority, 45)
        if age_days > threshold:
            flagged.append({
                "role_id": r.id, "role_title": r.role_title,
                "hiring_priority": r.hiring_priority,
                "age_days": age_days, "threshold_days": threshold,
                "days_over": age_days - threshold,
            })

    flagged.sort(key=lambda x: x["days_over"], reverse=True)
    return {"aging_role_count": len(flagged), "roles": flagged}
