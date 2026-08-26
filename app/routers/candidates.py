from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models import Candidate, ActivityTimeline, ResumeScreeningResult, get_db, User
from app.auth import get_current_user, require_roles
from agents.resume_screening_agent import screen_candidate

router = APIRouter(prefix="/candidates", tags=["candidates"])


class CandidateCreate(BaseModel):
    full_name: str
    role_id: int
    resume_text: str
    email: Optional[str] = None
    candidate_source: Optional[str] = "Direct Application"


@router.post("", status_code=201)
def create_candidate(
    payload: CandidateCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("leadership", "recruitment")),
):
    candidate = Candidate(**payload.dict(), stage="Applied")
    db.add(candidate)
    db.commit()
    db.refresh(candidate)

    db.add(ActivityTimeline(
        candidate_id=candidate.id, activity="Applied",
        stage_to="Applied", actor=user.email,
    ))
    db.commit()
    return {"id": candidate.id, "full_name": candidate.full_name, "stage": candidate.stage}


@router.post("/{candidate_id}/screen")
def run_screening(
    candidate_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("leadership", "recruitment")),
):
    """Section 18. Writes the AI's output; a human (this endpoint's caller)
    is the one who triggers it and who owns advancing the candidate's stage
    afterward — the agent does not do that itself (Section 2)."""
    candidate = db.query(Candidate).get(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")
    role = candidate.role
    if not role:
        raise HTTPException(400, "Candidate has no associated role to screen against")

    result = screen_candidate(candidate.resume_text, {
        "role_title": role.role_title,
        "mandatory_skills": role.mandatory_skills or [],
        "nice_to_have_skills": role.nice_to_have_skills or [],
        "experience_range": role.experience_range,
    })

    record = ResumeScreeningResult(candidate_id=candidate_id, role_id=role.id, **result)
    db.add(record)
    candidate.stage = "Resume Review"
    db.add(ActivityTimeline(
        candidate_id=candidate_id, activity="AI resume screening completed",
        stage_from="Applied", stage_to="Resume Review", actor="resume_screening_agent",
    ))
    db.commit()
    return result


@router.get("/{candidate_id}")
def get_candidate(candidate_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Interviewers see a restricted slice per Section 38: candidate summary,
    prior evaluation summary, unresolved competency areas — not full contact/
    compensation-adjacent fields."""
    candidate = db.query(Candidate).get(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")

    base = {
        "id": candidate.id, "full_name": candidate.full_name,
        "stage": candidate.stage, "status": candidate.status,
        "role_id": candidate.role_id,
    }
    if user.role in ("leadership", "recruitment"):
        base.update({
            "email": candidate.email, "phone": candidate.phone,
            "candidate_source": candidate.candidate_source,
            "priority_override": candidate.priority_override,
        })
    if user.role in ("leadership", "recruitment", "hiring_manager"):
        base["screening_results"] = [
            {"fit_score": r.fit_score, "suggested_priority": r.suggested_priority,
             "score_explanation": r.score_explanation, "missing_skills": r.missing_skills}
            for r in candidate.screening_results
        ]
    return base
