from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.models import (
    Candidate, Role, ResumeScreeningResult, RecruiterScreeningNote, RecruiterTag,
    Interview, Assignment, ReferenceCheck, ActivityTimeline, get_db, User,
)
from app.auth import get_current_user
from app.permissions import filter_recruiter_note_dict, can_view_references
from app.ai_contract import wrap_ai_output
from agents.search_interpreter import interpret_search_query

router = APIRouter(tags=["candidate-views"])


def _latest_screening(db: Session, candidate_id: int):
    return (
        db.query(ResumeScreeningResult)
        .filter(ResumeScreeningResult.candidate_id == candidate_id)
        .order_by(ResumeScreeningResult.created_at.desc())
        .first()
    )


@router.get("/dashboard/candidates-table")
def candidates_table(role_id: int = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = db.query(Candidate).filter(Candidate.status == "Active")
    if role_id is not None:
        query = query.filter(Candidate.role_id == role_id)
    candidates = query.all()

    stage_owner = {
        "Applied": "recruitment", "Resume Review": "recruitment", "Shortlisted": "recruitment",
        "Interview Process": "hiring_manager", "Assignment Sent": "recruitment",
        "Assignment Submitted": "hiring_manager", "Final Evaluation": "hiring_manager",
        "Reference Check": "hiring_manager", "Offer Discussion": "leadership",
        "Offer Released": "recruitment",
    }
    next_action_by_stage = {
        "Applied": "Run resume screening", "Resume Review": "Recruiter to shortlist",
        "Shortlisted": "Move to interview process", "Interview Process": "Schedule/complete interviews",
        "Assignment Sent": "Await submission", "Assignment Submitted": "Score assignment",
        "Final Evaluation": "Reference check", "Reference Check": "Offer discussion",
        "Offer Discussion": "Release offer", "Offer Released": "Await candidate response",
    }

    rows = []
    for c in candidates:
        role = db.query(Role).get(c.role_id) if c.role_id else None
        screening = _latest_screening(db, c.id)
        assignment = db.query(Assignment).filter(Assignment.candidate_id == c.id).order_by(Assignment.sent_at.desc()).first()
        last_activity = (
            db.query(ActivityTimeline).filter(ActivityTimeline.candidate_id == c.id)
            .order_by(ActivityTimeline.occurred_at.desc()).first()
        )
        rows.append({
            "candidate_id": c.id, "full_name": c.full_name,
            "applied_role": role.role_title if role else None,
            "current_stage": c.stage,
            "fit_score": screening.fit_score if screening else None,
            "priority_bucket": screening.suggested_priority if screening else None,
            "assignment_status": assignment.status if assignment else "Not sent",
            "last_activity": last_activity.activity if last_activity else None,
            "last_activity_at": last_activity.occurred_at if last_activity else None,
            "next_action": next_action_by_stage.get(c.stage, "Review"),
            "owner": stage_owner.get(c.stage, "recruitment"),
        })
    return rows


@router.get("/candidates/{candidate_id}/detail-view")
def candidate_detail_view(candidate_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    candidate = db.query(Candidate).get(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")

    role = db.query(Role).get(candidate.role_id) if candidate.role_id else None
    screenings = db.query(ResumeScreeningResult).filter(ResumeScreeningResult.candidate_id == candidate_id).all()
    interviews = db.query(Interview).filter(Interview.candidate_id == candidate_id).all()
    assignments = db.query(Assignment).filter(Assignment.candidate_id == candidate_id).all()
    tags = db.query(RecruiterTag).filter(RecruiterTag.candidate_id == candidate_id).all()
    timeline = (
        db.query(ActivityTimeline).filter(ActivityTimeline.candidate_id == candidate_id)
        .order_by(ActivityTimeline.occurred_at.asc()).all()
    )

    recruiter_notes_raw = db.query(RecruiterScreeningNote).filter(RecruiterScreeningNote.candidate_id == candidate_id).all()
    recruiter_notes = [
        filter_recruiter_note_dict({
            "recruiter_summary": n.recruiter_summary, "key_positives": n.key_positives,
            "key_concerns": n.key_concerns, "compensation_alignment": n.compensation_alignment,
            "recruiter_recommendation": n.recruiter_recommendation, "status": n.status,
        }, user.role)
        for n in recruiter_notes_raw
    ]

    references = []
    if can_view_references(user.role):
        refs = db.query(ReferenceCheck).filter(ReferenceCheck.candidate_id == candidate_id).all()
        references = [
            {"reference_name": r.reference_name, "overall_outcome": r.overall_outcome,
             "risk_level": r.risk_level, "ai_summary": r.ai_summary}
            for r in refs
        ]

    well_covered_areas = [i.evaluation_area for i in interviews if i.coverage_level == "Well Covered"]
    open_concerns = [i.concerns for i in interviews if i.concerns]

    return {
        "candidate_id": candidate.id, "full_name": candidate.full_name,
        "applied_role": role.role_title if role else None, "stage": candidate.stage, "status": candidate.status,
        "resume_text": candidate.resume_text,
        "recruiter_summaries": recruiter_notes,
        "interview_history": [
            {"evaluation_area": i.evaluation_area, "coverage_level": i.coverage_level,
             "assessment": i.assessment, "interviewer_name": i.interviewer_name,
             "recommendation": i.recommendation, "created_at": i.created_at}
            for i in interviews
        ],
        "competency_coverage_well_covered": well_covered_areas,
        "pending_concerns": open_concerns,
        "assignment_submissions": [
            {"status": a.status, "weighted_total": a.weighted_total, "sent_at": a.sent_at}
            for a in assignments
        ],
        "reference_summaries": references,
        "ai_summaries": [
            {"fit_score": s.fit_score, "suggested_priority": s.suggested_priority,
             "score_explanation": s.score_explanation}
            for s in screenings
        ],
        "tags": [t.tag for t in tags],
        "rejection_reason": candidate.rejection_reason,
        "withdrawal_reason": candidate.withdrawal_reason,
        "candidate_timeline": [
            {"activity": t.activity, "stage_from": t.stage_from, "stage_to": t.stage_to,
             "actor": t.actor, "occurred_at": t.occurred_at}
            for t in timeline
        ],
    }


@router.get("/candidates/search/natural")
def natural_language_search(q: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Section 20's "Natural-language search" — distinct from the existing
    keyword search at /candidates/search/query. The LLM only translates
    intent into a structured filter (see agents/search_interpreter.py);
    every actual filtering decision below is deterministic, not the LLM
    guessing which candidates match.

    Known, stated limitation: min_experience_years is interpreted but NOT
    enforced as a filter — there's no structured "years of experience"
    field anywhere in this schema, only free-text resume content, and
    filtering on an unreliable text-pattern guess would be worse than
    being honest that this criterion isn't actually applied yet.
    """
    if not q or not q.strip():
        raise HTTPException(422, "Query must not be empty.")

    filter_spec = interpret_search_query(q)

    candidates = db.query(Candidate).all()
    now = datetime.now(timezone.utc)
    results = []

    for c in candidates:
        if filter_spec["stages"] and c.stage not in filter_spec["stages"]:
            continue

        latest_screening = (
            db.query(ResumeScreeningResult)
            .filter(ResumeScreeningResult.candidate_id == c.id)
            .order_by(ResumeScreeningResult.created_at.desc())
            .first()
        )

        if filter_spec["min_fit_score"] is not None:
            if not latest_screening or (latest_screening.fit_score or 0) < filter_spec["min_fit_score"]:
                continue

        matched_skills_for_candidate = set(latest_screening.matched_skills or []) if latest_screening else set()
        if filter_spec["skills_required"]:
            required_lower = {s.lower() for s in filter_spec["skills_required"]}
            candidate_skills_lower = {s.lower() for s in matched_skills_for_candidate}
            # Fall back to resume_text substring match for unscreened
            # candidates — same approach the existing keyword search uses,
            # so a candidate isn't invisible to natural-language search
            # just because they haven't been AI-screened yet.
            resume_lower = (c.resume_text or "").lower()
            if not required_lower.issubset(candidate_skills_lower):
                if not all(skill in resume_lower for skill in required_lower):
                    continue

        if filter_spec["days_since_last_activity_min"] is not None:
            last_activity = (
                db.query(ActivityTimeline)
                .filter(ActivityTimeline.candidate_id == c.id)
                .order_by(ActivityTimeline.occurred_at.desc())
                .first()
            )
            reference_time = last_activity.occurred_at if last_activity else c.created_at
            if reference_time.tzinfo is None:
                reference_time = reference_time.replace(tzinfo=timezone.utc)
            days_idle = (now - reference_time).total_seconds() / 86400
            if days_idle < filter_spec["days_since_last_activity_min"]:
                continue

        results.append({
            "candidate_id": c.id, "full_name": c.full_name, "stage": c.stage,
            "fit_score": latest_screening.fit_score if latest_screening else None,
            "matched_skills": list(matched_skills_for_candidate),
        })

    return wrap_ai_output({
        "query": q,
        "interpreted_filter": {k: v for k, v in filter_spec.items() if k != "model_used"},
        "experience_filter_note": (
            "min_experience_years was interpreted but is NOT enforced — no structured "
            "experience field exists to filter on reliably."
        ) if filter_spec["min_experience_years"] is not None else None,
        "results": results,
    }, user.email)


@router.get("/candidates/search/query")
# Mounted at /candidates/search/query, not /candidates/search — the latter
# is a single path segment and collides with the existing
# GET /candidates/{candidate_id} route (app/routers/candidates.py, built
# Day 1): FastAPI tried to parse "search" as an integer candidate_id and
# threw a routing error before ever reaching this function. Confirmed via
# a real failing test, not caught by inspection — /candidates/queue/priority
# and /candidates/pool/hold-for-future (earlier phases) avoided this only
# because they're two path segments, not one.
def search_candidates(q: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    terms = [t.strip().lower() for t in q.split() if t.strip()]
    if not terms:
        raise HTTPException(422, "Query must contain at least one search term.")

    candidates = db.query(Candidate).all()
    results = []
    for c in candidates:
        screening = _latest_screening(db, c.id)
        tags = db.query(RecruiterTag).filter(RecruiterTag.candidate_id == c.id).all()

        searchable_text = " ".join(filter(None, [
            c.resume_text or "",
            c.candidate_source or "",
            " ".join(t.tag for t in tags),
            " ".join(screening.matched_skills) if screening and screening.matched_skills else "",
        ])).lower()

        hits = sum(1 for term in terms if term in searchable_text)
        if hits > 0:
            results.append({
                "candidate_id": c.id, "full_name": c.full_name, "stage": c.stage, "status": c.status,
                "candidate_source": c.candidate_source, "match_score": hits,
            })

    results.sort(key=lambda x: x["match_score"], reverse=True)
    return results
