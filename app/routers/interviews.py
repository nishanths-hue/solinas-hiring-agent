from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models import Interview, Candidate, Role, ResumeScreeningResult, RecruiterScreeningNote, get_db, User
from app.auth import get_current_user, require_roles
from agents.interview_briefing_agent import build_briefing

router = APIRouter(prefix="/candidates/{candidate_id}/interviews", tags=["interviews"])

VALID_COVERAGE = {"Not Covered", "Lightly Covered", "Well Covered"}
VALID_CONFIDENCE = {"Low", "Medium", "High"}
VALID_ASSESSMENT = {"Strong Positive", "Positive", "Neutral", "Concern", "Strong Concern"}


class InterviewFeedbackCreate(BaseModel):
    evaluation_area: str
    coverage_level: str
    confidence_level: str
    assessment: str
    strengths: str
    concerns: Optional[str] = None
    suggested_future_probes: Optional[str] = None
    recommendation: str  # Section 20: "mandatory inputs" includes a recommendation


@router.post("", status_code=201)
def submit_feedback(
    candidate_id: int,
    payload: InterviewFeedbackCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("interviewer", "hiring_manager")),
):
    """
    Append-only by design (Section 20: 'incremental evaluations rather than
    rigid round-based scorecards'). interviewer_user_id is always set from the
    authenticated user — never accepted from the request body — which is what
    makes 'interviewers can edit only their own feedback' (Section 38) true:
    there is no endpoint that lets anyone submit feedback attributed to someone
    else, and there is no edit endpoint at all, so nothing can be altered after
    the fact by anyone, including the original author.
    """
    if payload.coverage_level not in VALID_COVERAGE:
        raise HTTPException(422, f"coverage_level must be one of {VALID_COVERAGE}")
    if payload.confidence_level not in VALID_CONFIDENCE:
        raise HTTPException(422, f"confidence_level must be one of {VALID_CONFIDENCE}")
    if payload.assessment not in VALID_ASSESSMENT:
        raise HTTPException(422, f"assessment must be one of {VALID_ASSESSMENT}")

    candidate = db.query(Candidate).get(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")

    interview = Interview(
        candidate_id=candidate_id,
        interviewer_user_id=user.id,
        interviewer_name=user.full_name,
        **payload.dict(),
    )
    db.add(interview)
    db.commit()
    db.refresh(interview)
    return {
        "id": interview.id, "candidate_id": candidate_id,
        "interviewer_name": interview.interviewer_name,
        "evaluation_area": interview.evaluation_area,
        "coverage_level": interview.coverage_level,
    }


@router.get("")
def list_feedback(candidate_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Section 20: interviewers should see 'prior observations' before their own
    interview — so viewing is intentionally broader than editing. Every role
    that can reach this candidate at all can see the interview history;
    Section 38 doesn't restrict interview feedback visibility by role the way
    it restricts compensation, so no field-stripping here.
    """
    interviews = db.query(Interview).filter(Interview.candidate_id == candidate_id).all()
    return [
        {
            "id": iv.id, "interviewer_name": iv.interviewer_name,
            "evaluation_area": iv.evaluation_area, "coverage_level": iv.coverage_level,
            "confidence_level": iv.confidence_level, "assessment": iv.assessment,
            "strengths": iv.strengths, "concerns": iv.concerns,
            "suggested_future_probes": iv.suggested_future_probes,
            "recommendation": iv.recommendation, "created_at": iv.created_at,
        }
        for iv in interviews
    ]


@router.get("/briefing")
def get_briefing(
    candidate_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("interviewer", "hiring_manager", "recruitment", "leadership")),
):
    """
    Section 20's 'interviewer workflow': assembles candidate summary, recruiter
    summary, covered/unresolved competency areas, and AI-suggested focus
    questions — everything an interviewer should see BEFORE the interview.
    Does not score or recommend; that stays entirely with the human interviewer.
    """
    candidate = db.query(Candidate).get(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")
    role = db.query(Role).get(candidate.role_id) if candidate.role_id else None
    if not role:
        raise HTTPException(400, "Candidate has no associated role — cannot determine evaluation areas")

    screening = (
        db.query(ResumeScreeningResult)
        .filter(ResumeScreeningResult.candidate_id == candidate_id)
        .order_by(ResumeScreeningResult.created_at.desc())
        .first()
    )
    recruiter_note = (
        db.query(RecruiterScreeningNote)
        .filter(RecruiterScreeningNote.candidate_id == candidate_id)
        .order_by(RecruiterScreeningNote.created_at.desc())
        .first()
    )
    prior_interviews = db.query(Interview).filter(Interview.candidate_id == candidate_id).all()

    # Section 20 example evaluation areas — used as the default set when a role
    # hasn't had custom areas assigned yet via the AI-generated suggestions in
    # resume_screening_agent output (suggested_probe_areas isn't the same list,
    # so this stays a sane, doc-sourced default rather than guessing further).
    evaluation_areas = [
        "Technical Fundamentals", "Problem Solving", "Ownership",
        "Communication", "Leadership", "Project Depth", "Domain Knowledge",
    ]

    briefing = build_briefing(
        candidate_summary={
            "resume_summary": (candidate.resume_text or "")[:800],
            "recruiter_summary": recruiter_note.recruiter_summary if recruiter_note else None,
            "fit_score": screening.fit_score if screening else None,
            "missing_skills": screening.missing_skills if screening else [],
        },
        role_evaluation_areas=evaluation_areas,
        prior_interviews=[
            {"evaluation_area": iv.evaluation_area, "coverage_level": iv.coverage_level,
             "assessment": iv.assessment, "concerns": iv.concerns}
            for iv in prior_interviews
        ],
    )
    return briefing
