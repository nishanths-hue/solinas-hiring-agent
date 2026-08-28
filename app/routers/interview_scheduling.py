from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models import ScheduledInterview, Candidate, User, get_db
from app.auth import get_current_user, require_roles
from app.sla import start_sla_clock
from app.candidate_comms import send_and_log
from agents.email_agent import build_interview_scheduled_email

router = APIRouter(tags=["interview-scheduling"])

VALID_STATUSES = {"Scheduled", "Completed", "Cancelled", "No-Show"}


class ScheduleInterviewCreate(BaseModel):
    interviewer_user_id: int
    scheduled_at: datetime


@router.get("/scheduled-interviews/interviewer-options")
def list_interviewer_options(db: Session = Depends(get_db), user: User = Depends(require_roles("recruitment", "hiring_manager", "leadership"))):
    """
    Narrow, purpose-built list for populating a scheduling picker — id and
    name only, scoped to interviewer/hiring_manager accounts. Deliberately
    NOT reusing GET /auth/users here: that endpoint is leadership/
    recruitment-only and returns every account's email, which is more
    than a hiring_manager scheduling an interview needs or should see.
    Widening /auth/users to hiring_manager would leak the full user
    directory just to unblock this one picker — this endpoint gives
    exactly what scheduling needs and nothing more.
    """
    people = db.query(User).filter(User.role.in_(["interviewer", "hiring_manager"]), User.is_active == True).all()  # noqa: E712
    return [{"id": p.id, "full_name": p.full_name, "role": p.role} for p in people]


@router.post("/candidates/{candidate_id}/scheduled-interviews", status_code=201)
def schedule_interview(
    candidate_id: int,
    payload: ScheduleInterviewCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("recruitment", "hiring_manager", "leadership")),
):
    candidate = db.query(Candidate).get(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")
    interviewer = db.query(User).get(payload.interviewer_user_id)
    if not interviewer or interviewer.role not in ("interviewer", "hiring_manager"):
        raise HTTPException(422, "interviewer_user_id must belong to an interviewer or hiring_manager account")

    scheduled = ScheduledInterview(
        candidate_id=candidate_id, interviewer_user_id=payload.interviewer_user_id,
        scheduled_at=payload.scheduled_at, status="Scheduled", created_by=user.email,
    )
    db.add(scheduled)
    db.commit()
    db.refresh(scheduled)

    # Known simplification (see models.py docstring on ScheduledInterview):
    # the clock starts NOW, at scheduling time, not at the interview's
    # actual scheduled_at — this app has no scheduler to trigger a clock
    # start automatically at a future timestamp.
    start_sla_clock(db, "scheduled_interview", scheduled.id, "Feedback submission")

    role = candidate.role
    scheduled_display = payload.scheduled_at.strftime("%B %d, %Y at %I:%M %p")
    subject, html = build_interview_scheduled_email(
        candidate.full_name, role.role_title if role else "the role", scheduled_display,
    )
    send_and_log(db, candidate_id, candidate.email, "Interview", subject, html)

    return {
        "id": scheduled.id, "candidate_id": candidate_id,
        "interviewer_user_id": payload.interviewer_user_id, "scheduled_at": scheduled.scheduled_at,
        "status": scheduled.status,
    }


@router.get("/candidates/{candidate_id}/scheduled-interviews")
def list_scheduled_interviews(candidate_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(ScheduledInterview).filter(ScheduledInterview.candidate_id == candidate_id).order_by(ScheduledInterview.scheduled_at).all()
    result = []
    for r in rows:
        interviewer = db.query(User).get(r.interviewer_user_id)
        result.append({
            "id": r.id, "interviewer_name": interviewer.full_name if interviewer else "Unknown",
            "scheduled_at": r.scheduled_at, "status": r.status,
            "completed_interview_id": r.completed_interview_id,
        })
    return result


class ScheduledInterviewStatusUpdate(BaseModel):
    status: str


@router.patch("/scheduled-interviews/{scheduled_id}")
def update_scheduled_interview_status(
    scheduled_id: int,
    payload: ScheduledInterviewStatusUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("recruitment", "hiring_manager", "leadership")),
):
    """Manual status changes — Cancelled or No-Show. 'Completed' is set
    automatically when matching feedback is submitted (see
    interviews.py's submit_feedback), not through this endpoint, so a
    completion can't be claimed without the feedback that actually backs it."""
    if payload.status not in ("Cancelled", "No-Show"):
        raise HTTPException(422, "Only 'Cancelled' or 'No-Show' can be set manually here.")
    scheduled = db.query(ScheduledInterview).get(scheduled_id)
    if not scheduled:
        raise HTTPException(404, "Scheduled interview not found")
    scheduled.status = payload.status
    db.commit()
    return {"id": scheduled_id, "status": scheduled.status}
