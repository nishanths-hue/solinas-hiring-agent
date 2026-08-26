from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models import Candidate, ActivityTimeline, JoiningRiskTracker, get_db, User
from app.auth import get_current_user, require_roles

router = APIRouter(prefix="/candidates", tags=["candidate-lifecycle"])

# Section 15 — "Final Candidate Stages", in the document's own order. Used to
# detect skips: moving from index 2 to index 5 skipped stages 3-4, which
# Section 15 says MUST be marked and logged, not silently allowed.
STAGE_ORDER = [
    "Applied", "Resume Review", "Shortlisted", "Interview Process",
    "Assignment Sent", "Assignment Submitted", "Final Evaluation",
    "Reference Check", "Offer Discussion", "Offer Released",
    "Offer Accepted", "Joined",
]
# Terminal/side statuses that sit outside the main pipeline order — reachable
# from anywhere, not part of the skip-detection sequence.
SIDE_STAGES = {"Rejected", "Hold for Future", "On Hold"}
ALL_VALID_STAGES = set(STAGE_ORDER) | SIDE_STAGES

# Section 15 — "Stage Movement Ownership" table, exactly as specified. Only
# the transitions the document explicitly names are restricted to a specific
# owner; anything else defaults to recruitment/leadership/hiring_manager
# (see DEFAULT_ALLOWED_ROLES) since the doc doesn't name an owner for those.
NAMED_TRANSITION_OWNERS = {
    ("Applied", "Resume Review"): {"recruitment"},
    ("Resume Review", "Shortlisted"): {"recruitment", "hiring_manager"},
    ("Shortlisted", "Interview Process"): {"recruitment"},
    ("Interview Process", "Assignment Sent"): {"recruitment"},
    ("Assignment Submitted", "Final Evaluation"): {"hiring_manager"},
    ("Final Evaluation", "Reference Check"): {"hiring_manager"},
    ("Reference Check", "Offer Discussion"): {"hiring_manager", "leadership"},
    ("Offer Released", "Joined"): {"recruitment"},
}
DEFAULT_ALLOWED_ROLES = {"recruitment", "leadership", "hiring_manager"}

# Section 23 — enumerated reason categories, exactly as specified
REJECTION_REASONS = {
    "Technical Gap", "Communication Gap", "Compensation Mismatch",
    "Stability Concern", "Cultural Fit", "Leadership Gap",
    "Assignment Weak", "Candidate Withdrew",
}
WITHDRAWAL_REASONS = {
    "Accepted Other Offer", "Compensation", "Delayed Process",
    "Role Mismatch", "Relocation", "No Longer Interested",
}


class StageTransition(BaseModel):
    to_stage: str
    skip_reason: Optional[str] = None
    rejection_reason: Optional[str] = None
    withdrawal_reason: Optional[str] = None


@router.post("/{candidate_id}/transition")
def transition_candidate(
    candidate_id: int,
    payload: StageTransition,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Section 15's general stage-movement endpoint. Enforces three things the
    document is explicit about:
      1. Named transitions are restricted to their documented owner role(s).
      2. A skip (jumping past intermediate stages in STAGE_ORDER) requires
         skip_reason — "skipped stage must be marked, skip reason must be
         logged" is not optional in the source text.
      3. Moving to Rejected/Hold for Future requires the matching enumerated
         reason from Section 23 (rejection) — Hold for Future doesn't require
         a reason since Section 24 doesn't ask for one, only rejection does.
    """
    if payload.to_stage not in ALL_VALID_STAGES:
        raise HTTPException(422, f"'{payload.to_stage}' is not a valid stage. Valid: {sorted(ALL_VALID_STAGES)}")

    candidate = db.query(Candidate).get(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")

    from_stage = candidate.stage

    # Ownership check — only for named transitions; anything else falls
    # through to DEFAULT_ALLOWED_ROLES
    owners = NAMED_TRANSITION_OWNERS.get((from_stage, payload.to_stage), DEFAULT_ALLOWED_ROLES)
    if user.role not in owners:
        raise HTTPException(
            403,
            f"Role '{user.role}' cannot move a candidate from '{from_stage}' to '{payload.to_stage}'. "
            f"Requires one of: {sorted(owners)}",
        )

    # Skip detection — only meaningful within the main pipeline order
    is_skip = False
    if from_stage in STAGE_ORDER and payload.to_stage in STAGE_ORDER:
        from_idx = STAGE_ORDER.index(from_stage)
        to_idx = STAGE_ORDER.index(payload.to_stage)
        if to_idx > from_idx + 1:
            is_skip = True
            if not payload.skip_reason:
                raise HTTPException(
                    422,
                    f"Moving from '{from_stage}' to '{payload.to_stage}' skips intermediate stages "
                    f"({', '.join(STAGE_ORDER[from_idx+1:to_idx])}). skip_reason is required.",
                )

    # Section 23 — rejection requires an enumerated reason
    if payload.to_stage == "Rejected":
        if not payload.rejection_reason or payload.rejection_reason not in REJECTION_REASONS:
            raise HTTPException(422, f"rejection_reason is required and must be one of: {sorted(REJECTION_REASONS)}")
        candidate.rejection_reason = payload.rejection_reason
        candidate.status = "Rejected"
    elif payload.to_stage == "Hold for Future":
        candidate.status = "Hold for Future"
    elif payload.to_stage == "On Hold":
        candidate.status = "On Hold"
    elif payload.to_stage == "Joined":
        candidate.status = "Closed"

    if payload.withdrawal_reason:
        if payload.withdrawal_reason not in WITHDRAWAL_REASONS:
            raise HTTPException(422, f"withdrawal_reason must be one of: {sorted(WITHDRAWAL_REASONS)}")
        candidate.withdrawal_reason = payload.withdrawal_reason

    candidate.stage = payload.to_stage
    db.add(ActivityTimeline(
        candidate_id=candidate_id, activity=f"Stage changed: {from_stage} → {payload.to_stage}",
        stage_from=from_stage, stage_to=payload.to_stage, actor=user.email,
        is_stage_skip=is_skip, skip_reason=payload.skip_reason,
    ))

    # Section 26 — joining tracker created automatically on Offer Accepted
    if payload.to_stage == "Offer Accepted":
        existing = db.query(JoiningRiskTracker).filter(JoiningRiskTracker.candidate_id == candidate_id).first()
        if not existing:
            db.add(JoiningRiskTracker(candidate_id=candidate_id, recruiter_owner=user.full_name))

    db.commit()
    return {
        "candidate_id": candidate_id, "from_stage": from_stage, "to_stage": payload.to_stage,
        "status": candidate.status, "was_skip": is_skip,
    }


@router.get("/pool/hold-for-future")
def hold_for_future_pool(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Section 24 — candidates in this pool stay searchable, deliberately
    separate from the main active pipeline (Sections 30/32 filter to Active only)."""
    candidates = db.query(Candidate).filter(Candidate.status == "Hold for Future").all()
    return [{"id": c.id, "full_name": c.full_name, "role_id": c.role_id,
              "updated_at": c.updated_at} for c in candidates]


@router.get("/pool/archived")
def archived_candidates(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Section 25 — Rejected/Closed/Hold-for-Future/On-Hold candidates,
    deliberately excluded from the active operational views (dashboards,
    funnel) per Section 25's 'should not clutter active operational views',
    but still fully searchable here for historical reference."""
    candidates = db.query(Candidate).filter(Candidate.status != "Active").all()
    return [{"id": c.id, "full_name": c.full_name, "status": c.status, "stage": c.stage,
              "rejection_reason": c.rejection_reason, "withdrawal_reason": c.withdrawal_reason,
              "role_id": c.role_id, "updated_at": c.updated_at} for c in candidates]


class JoiningRiskUpdate(BaseModel):
    joining_confidence: Optional[str] = None
    pending_documents: Optional[str] = None
    joining_confirmed: Optional[bool] = None
    risk_level: Optional[str] = None
    notes: Optional[str] = None


@router.get("/{candidate_id}/joining-risk")
def get_joining_risk(candidate_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    tracker = db.query(JoiningRiskTracker).filter(JoiningRiskTracker.candidate_id == candidate_id).first()
    if not tracker:
        raise HTTPException(404, "No joining risk tracker exists for this candidate yet — "
                                  "it's created automatically when a candidate reaches Offer Accepted.")
    return {
        "candidate_id": candidate_id, "recruiter_owner": tracker.recruiter_owner,
        "joining_confidence": tracker.joining_confidence, "pending_documents": tracker.pending_documents,
        "joining_confirmed": tracker.joining_confirmed, "risk_level": tracker.risk_level,
        "notes": tracker.notes, "updated_at": tracker.updated_at,
    }


@router.patch("/{candidate_id}/joining-risk")
def update_joining_risk(
    candidate_id: int,
    payload: JoiningRiskUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("recruitment", "leadership")),
):
    """Section 26: 'recruitment team should manually update' — no AI agent
    involved here, this is pure human observation logging."""
    tracker = db.query(JoiningRiskTracker).filter(JoiningRiskTracker.candidate_id == candidate_id).first()
    if not tracker:
        raise HTTPException(404, "No joining risk tracker exists for this candidate yet.")

    valid_risk_levels = {"Low Risk", "Moderate Risk", "High Risk"}
    if payload.risk_level and payload.risk_level not in valid_risk_levels:
        raise HTTPException(422, f"risk_level must be one of: {sorted(valid_risk_levels)}")

    for field, value in payload.dict(exclude_unset=True).items():
        setattr(tracker, field, value)
    from datetime import datetime
    tracker.updated_at = datetime.utcnow()
    db.commit()
    return {"candidate_id": candidate_id, "updated": True, "risk_level": tracker.risk_level}
