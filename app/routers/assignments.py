from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models import Assignment, AssignmentRepository, Candidate, get_db, User
from app.auth import get_current_user, require_roles
from agents.assignment_scoring import compute_weighted_total

router = APIRouter(prefix="/candidates/{candidate_id}/assignments", tags=["assignments"])


class AssignmentSend(BaseModel):
    assignment_repository_id: int
    submission_deadline: Optional[datetime] = None


class AssignmentScore(BaseModel):
    technical_accuracy_score: float
    problem_solving_score: float
    clarity_structure_score: float
    practical_thinking_score: float
    completeness_score: float


@router.post("", status_code=201)
def send_assignment(
    candidate_id: int,
    payload: AssignmentSend,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("recruitment", "hiring_manager")),
):
    candidate = db.query(Candidate).get(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")
    repo_item = db.query(AssignmentRepository).get(payload.assignment_repository_id)
    if not repo_item:
        raise HTTPException(404, "Assignment repository item not found")

    assignment = Assignment(
        candidate_id=candidate_id,
        assignment_repository_id=payload.assignment_repository_id,
        submission_deadline=payload.submission_deadline,
        status="Sent",
    )
    db.add(assignment)
    repo_item.historical_usage_count = (repo_item.historical_usage_count or 0) + 1
    db.commit()
    db.refresh(assignment)
    return {"id": assignment.id, "candidate_id": candidate_id, "status": "Sent",
            "assignment_name": repo_item.assignment_name}


@router.post("/{assignment_id}/submit")
def mark_submitted(
    candidate_id: int, assignment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("recruitment", "hiring_manager")),
):
    """Marks receipt — separate from scoring, since a submission can sit
    unscored for a while and Section 21's SLA clocks track that gap."""
    assignment = db.query(Assignment).filter(
        Assignment.id == assignment_id, Assignment.candidate_id == candidate_id
    ).first()
    if not assignment:
        raise HTTPException(404, "Assignment not found")
    assignment.submitted_at = datetime.utcnow()
    assignment.status = "Submitted"
    db.commit()
    return {"id": assignment.id, "status": "Submitted"}


@router.post("/{assignment_id}/score")
def score_assignment(
    candidate_id: int, assignment_id: int,
    payload: AssignmentScore,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("hiring_manager", "interviewer")),
):
    """
    Weighted total is computed here deterministically (agents/assignment_scoring.py),
    never by an LLM — see that module's docstring for why. Any of the 5 scores
    outside 0-100 is rejected outright rather than silently clamped, so a typo'd
    score doesn't quietly produce a wrong total.
    """
    assignment = db.query(Assignment).filter(
        Assignment.id == assignment_id, Assignment.candidate_id == candidate_id
    ).first()
    if not assignment:
        raise HTTPException(404, "Assignment not found")

    try:
        weighted_total = compute_weighted_total(payload.dict())
    except ValueError as e:
        raise HTTPException(422, str(e))

    for field, value in payload.dict().items():
        setattr(assignment, field, value)
    assignment.weighted_total = weighted_total
    assignment.status = "Scored"
    assignment.scored_by = user.full_name
    db.commit()

    return {
        "id": assignment.id, "status": "Scored",
        "weighted_total": weighted_total, "scored_by": user.full_name,
    }


@router.get("")
def list_assignments(candidate_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assignments = db.query(Assignment).filter(Assignment.candidate_id == candidate_id).all()
    return [
        {"id": a.id, "status": a.status, "weighted_total": a.weighted_total,
         "sent_at": a.sent_at, "submitted_at": a.submitted_at}
        for a in assignments
    ]
