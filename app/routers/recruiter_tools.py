from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.models import (
    Candidate, RecruiterScreeningNote, RecruiterTag, Role,
    ResumeScreeningResult, get_db, User,
)
from app.auth import get_current_user, require_roles
from app.sla import complete_open_clock_for
from app.permissions import filter_recruiter_note_dict
from app.sla import ROLE_AGING_DAYS

router = APIRouter(prefix="/candidates", tags=["recruiter-screening"])

VALID_TAGS = {
    "Fast Track", "Strong Referral", "Compensation Risk", "Communication Concern",
    "Leadership Potential", "Hold for Future", "Founder Review Recommended", "High Joining Risk",
}
VALID_RECRUITER_STATUSES = {
    "New", "Under Recruiter Review", "Awaiting Hiring Manager Review",
    "HM Shortlisted", "Hold", "Rejected",
}
VALID_PRIORITY_OVERRIDES = {"Normal", "High", "Critical"}


class ScreeningNoteCreate(BaseModel):
    recruiter_summary: str
    key_positives: Optional[str] = None
    key_concerns: Optional[str] = None
    compensation_alignment: Optional[str] = None
    notice_period_summary: Optional[str] = None
    communication_assessment: Optional[str] = None
    motivation_level: Optional[str] = None
    recruiter_recommendation: str
    status: Optional[str] = "New"


@router.post("/{candidate_id}/recruiter-notes", status_code=201)
def create_screening_note(
    candidate_id: int,
    payload: ScreeningNoteCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("recruitment")),
):
    if payload.status not in VALID_RECRUITER_STATUSES:
        raise HTTPException(422, f"status must be one of: {sorted(VALID_RECRUITER_STATUSES)}")
    if payload.recruiter_recommendation not in {"Proceed", "Hold", "Reject"}:
        raise HTTPException(422, "recruiter_recommendation must be Proceed, Hold, or Reject")

    candidate = db.query(Candidate).get(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")

    note = RecruiterScreeningNote(candidate_id=candidate_id, recruiter_name=user.full_name, **payload.dict())
    db.add(note)
    db.commit()
    db.refresh(note)
    complete_open_clock_for(db, "candidate", candidate_id, "High-fit review")
    return {"id": note.id, "candidate_id": candidate_id, "status": note.status}


@router.get("/{candidate_id}/recruiter-notes")
def get_screening_notes(candidate_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    notes = db.query(RecruiterScreeningNote).filter(RecruiterScreeningNote.candidate_id == candidate_id).all()
    result = []
    for n in notes:
        d = {
            "id": n.id, "recruiter_name": n.recruiter_name, "recruiter_summary": n.recruiter_summary,
            "key_positives": n.key_positives, "key_concerns": n.key_concerns,
            "compensation_alignment": n.compensation_alignment, "notice_period_summary": n.notice_period_summary,
            "communication_assessment": n.communication_assessment, "motivation_level": n.motivation_level,
            "recruiter_recommendation": n.recruiter_recommendation, "status": n.status, "created_at": n.created_at,
        }
        result.append(filter_recruiter_note_dict(d, user.role))
    return result


class TagCreate(BaseModel):
    tag: str


@router.post("/{candidate_id}/tags", status_code=201)
def add_tag(
    candidate_id: int,
    payload: TagCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("recruitment", "leadership")),
):
    if payload.tag not in VALID_TAGS:
        raise HTTPException(422, f"tag must be one of: {sorted(VALID_TAGS)}")
    candidate = db.query(Candidate).get(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")

    existing = db.query(RecruiterTag).filter(
        RecruiterTag.candidate_id == candidate_id, RecruiterTag.tag == payload.tag
    ).first()
    if existing:
        raise HTTPException(409, f"Candidate already has the '{payload.tag}' tag.")

    tag = RecruiterTag(candidate_id=candidate_id, tag=payload.tag, applied_by=user.email)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return {"id": tag.id, "tag": tag.tag}


@router.get("/{candidate_id}/tags")
def list_tags(candidate_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    tags = db.query(RecruiterTag).filter(RecruiterTag.candidate_id == candidate_id).all()
    return [{"id": t.id, "tag": t.tag, "applied_by": t.applied_by, "applied_at": t.applied_at} for t in tags]


@router.delete("/{candidate_id}/tags/{tag_id}")
def remove_tag(
    candidate_id: int, tag_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("recruitment", "leadership")),
):
    tag = db.query(RecruiterTag).filter(RecruiterTag.id == tag_id, RecruiterTag.candidate_id == candidate_id).first()
    if not tag:
        raise HTTPException(404, "Tag not found on this candidate")
    db.delete(tag)
    db.commit()
    return {"deleted": True}


class PriorityOverrideUpdate(BaseModel):
    priority_override: str


@router.patch("/{candidate_id}/priority-override")
def set_priority_override(
    candidate_id: int,
    payload: PriorityOverrideUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("recruitment", "hiring_manager", "leadership")),
):
    if payload.priority_override not in VALID_PRIORITY_OVERRIDES:
        raise HTTPException(422, f"priority_override must be one of: {sorted(VALID_PRIORITY_OVERRIDES)}")
    candidate = db.query(Candidate).get(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")
    candidate.priority_override = payload.priority_override
    db.commit()
    return {"candidate_id": candidate_id, "priority_override": candidate.priority_override}


class FounderFlagUpdate(BaseModel):
    needs_founder_review: bool


@router.patch("/{candidate_id}/founder-review-flag")
def set_founder_flag(
    candidate_id: int,
    payload: FounderFlagUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("recruitment", "hiring_manager", "leadership")),
):
    candidate = db.query(Candidate).get(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")
    candidate.needs_founder_review = payload.needs_founder_review
    db.commit()
    return {"candidate_id": candidate_id, "needs_founder_review": candidate.needs_founder_review}


@router.get("/queue/priority")
def priority_queue(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Section 19: 'Recruiter review queues should dynamically prioritize
    candidates... The system should NOT behave FIFO-style.'

    Composite score built from the signals the doc names that are actually
    modeled in this system today: priority_override, founder review flag,
    fit score, role priority, and role aging. Two named signals from the
    doc — notice period and referral strength — aren't modeled as
    structured fields anywhere in the system yet, so they're not part of
    this score; adding them later means extending this function, not
    redesigning it.
    """
    candidates = db.query(Candidate).filter(Candidate.status == "Active").all()
    now = datetime.now(timezone.utc)

    scored = []
    for c in candidates:
        score = 0.0
        priority_weight = {"Critical": 30, "High": 15, "Normal": 0}
        score += priority_weight.get(c.priority_override, 0)

        if c.needs_founder_review:
            score += 25

        latest_screening = (
            db.query(ResumeScreeningResult)
            .filter(ResumeScreeningResult.candidate_id == c.id)
            .order_by(ResumeScreeningResult.created_at.desc())
            .first()
        )
        fit_score = latest_screening.fit_score if latest_screening else None
        if fit_score is not None:
            score += fit_score * 0.3

        role = db.query(Role).get(c.role_id) if c.role_id else None
        if role:
            role_priority_weight = {"Critical": 20, "High": 10, "Medium": 0}
            score += role_priority_weight.get(role.hiring_priority, 0)

            if role.stage in ("Live Hiring", "Approved"):
                created = role.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                age_days = (now - created).days
                threshold = ROLE_AGING_DAYS.get(role.hiring_priority, 45)
                if age_days > threshold:
                    score += 15

        scored.append({
            "candidate_id": c.id, "full_name": c.full_name, "stage": c.stage,
            "priority_override": c.priority_override, "needs_founder_review": c.needs_founder_review,
            "fit_score": fit_score, "role_id": c.role_id,
            "priority_score": round(score, 1),
        })

    scored.sort(key=lambda x: x["priority_score"], reverse=True)
    return scored
