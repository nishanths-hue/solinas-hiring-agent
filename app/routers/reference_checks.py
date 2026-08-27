from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models import ReferenceCheck, Candidate, Interview, Role, get_db, User
from app.auth import get_current_user, require_roles
from app.sla import complete_open_clock_for
from agents.reference_check_agent import generate_reference_questions, summarize_reference_response

router = APIRouter(prefix="/candidates/{candidate_id}/reference-checks", tags=["reference-checks"])

# Section 10/38: reference data is restricted to leadership + recruitment,
# same tier as compensation. This is enforced at every endpoint in this
# router, not just the list view — there's no partial-access path here.
RESTRICTED_TO = ("leadership", "recruitment")


class ReferenceCheckCreate(BaseModel):
    reference_name: str
    reference_relationship: str
    raw_notes: str


@router.get("/questions")
def get_reference_questions(
    candidate_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*RESTRICTED_TO)),
):
    """Section 22 — generates questions BEFORE the call, tailored to concerns
    already on record for this candidate (pulled from interview feedback)."""
    candidate = db.query(Candidate).get(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")
    role = db.query(Role).get(candidate.role_id) if candidate.role_id else None

    prior_concerns = [
        iv.concerns for iv in
        db.query(Interview).filter(Interview.candidate_id == candidate_id).all()
        if iv.concerns
    ]

    return generate_reference_questions(
        role.role_title if role else "Unknown Role",
        prior_concerns,
    )


@router.post("", status_code=201)
def log_reference_check(
    candidate_id: int,
    payload: ReferenceCheckCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*RESTRICTED_TO)),
):
    """
    AI summarizes raw notes into structured fields. suggested_risk_level and
    suggested_rehire_eligibility are exactly that — suggestions, stored as-is
    but never used to auto-advance or auto-reject the candidate (Section 2).
    A human reads this and decides what happens next.
    """
    candidate = db.query(Candidate).get(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")

    ai_result = summarize_reference_response(payload.raw_notes)

    ref_check = ReferenceCheck(
        candidate_id=candidate_id,
        reference_name=payload.reference_name,
        reference_relationship=payload.reference_relationship,
        raw_notes=payload.raw_notes,
        ai_summary=ai_result.get("ai_summary"),
        positive_signals=ai_result.get("positive_signals"),
        concerns=ai_result.get("concerns"),
        overall_outcome=ai_result.get("overall_outcome"),
        rehire_eligibility=ai_result.get("suggested_rehire_eligibility"),
        risk_level=ai_result.get("suggested_risk_level"),
        logged_by=user.full_name,
    )
    db.add(ref_check)
    db.commit()
    db.refresh(ref_check)
    complete_open_clock_for(db, "candidate", candidate_id, "Reference completion")

    return {
        "id": ref_check.id, "candidate_id": candidate_id,
        "ai_summary": ref_check.ai_summary,
        "overall_outcome": ref_check.overall_outcome,
        "suggested_risk_level": ref_check.risk_level,
        "suggested_rehire_eligibility": ref_check.rehire_eligibility,
        "note": "risk_level and rehire_eligibility are AI-suggested, not final — human review required",
    }


@router.get("")
def list_reference_checks(
    candidate_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*RESTRICTED_TO)),
):
    checks = db.query(ReferenceCheck).filter(ReferenceCheck.candidate_id == candidate_id).all()
    return [
        {"id": c.id, "reference_name": c.reference_name, "overall_outcome": c.overall_outcome,
         "risk_level": c.risk_level, "rehire_eligibility": c.rehire_eligibility,
         "ai_summary": c.ai_summary, "completed_at": c.completed_at}
        for c in checks
    ]
