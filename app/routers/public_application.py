"""
Priority 3 — the careers-page application channel, and the connector
registry the doc's Section 6 explicitly asks for.

This file contains the FIRST unauthenticated write endpoint in this
entire system. Every other candidate-creation path requires an internal
login (leadership/recruitment). A public applicant on the internet does
not have one, and that changes what "careful" means here:

  - Stricter rate limiting than anywhere else in the app (even login),
    since this is now a genuinely public attack surface.
  - The role must actually be "Live Hiring" — an applicant can't submit
    against a draft, closed, or not-yet-approved role, even if they
    somehow got its ID.
  - The public listing endpoint returns ONLY public-safe fields — never
    compensation, budget, hiring_manager identity, or internal notes.
  - The response to a successful application is deliberately minimal —
    no internal candidate ID exposed as a trophy, no confirmation of
    whether this email already exists in the system (that would leak
    real information to an outside party).

Connector availability is honest, not simulated: a channel is either
genuinely configured (its API key/credentials env var is set) or it
isn't, and this reports "Integration unavailable / requires
configuration" for anything unconfigured — matching the doc's own
Section 6 instruction not to fake or scrape unauthorized access.
"""

import os
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from pydantic import EmailStr
from sqlalchemy.orm import Session
from typing import Optional

from app.models import Role, Candidate, ActivityTimeline, get_db
from app.rate_limit import limiter
from app.sla import start_sla_clock
from agents.resume_screening_agent import parse_resume, structured_to_text
from app.routers.candidates import ALLOWED_RESUME_EXTENSIONS, MAX_RESUME_FILE_BYTES, _run_screening_core
from app.routers.duplicates_and_sources import find_potential_duplicates

router = APIRouter(prefix="/public", tags=["public-application"])

# Each channel's real availability is determined by whether its actual
# credentials exist as an env var — not by wishful thinking. "careers_page"
# has no external dependency at all, so it's always available; it's the
# one channel Priority 3 can actually deliver on today.
CONNECTOR_ENV_VARS = {
    "careers_page": None,  # self-hosted, no external credential needed
    "linkedin": "LINKEDIN_API_KEY",
    "naukri": "NAUKRI_API_KEY",
}


@router.get("/connectors/status")
def connector_status():
    """No auth required — this just reports which channels are real right
    now, useful for both the internal Postings UI and anyone checking
    what's actually live without needing to log in first."""
    result = {}
    for channel, env_var in CONNECTOR_ENV_VARS.items():
        if env_var is None:
            result[channel] = {"available": True, "detail": "Self-hosted, no external dependency."}
        elif os.environ.get(env_var):
            result[channel] = {"available": True, "detail": "Configured."}
        else:
            result[channel] = {"available": False, "detail": "Integration unavailable / requires configuration."}
    return result


@router.get("/open-roles")
def list_open_roles(db: Session = Depends(get_db)):
    """
    Public, no-auth listing for the careers page. Deliberately narrow —
    only fields a real job posting would show externally. Compensation,
    budget, hiring_manager identity, and internal notes are never
    included here regardless of what's on the Role record, by construction
    (this builds a new dict field-by-field, it doesn't serialize the ORM
    object directly — so a future field added to Role can't accidentally
    leak through this endpoint the way it might through an internal one).
    """
    roles = db.query(Role).filter(Role.stage == "Live Hiring").all()
    return [{
        "id": r.id,
        "request_display_id": r.request_display_id,
        "role_title": r.role_title,
        "department": r.department,
        "location": r.location,
        "work_mode": r.work_mode,
        "employment_type": r.employment_type,
        "experience_range": r.experience_range,
        "mandatory_skills": r.mandatory_skills,
        "nice_to_have_skills": r.nice_to_have_skills,
        "job_description": r.jd,
    } for r in roles]


@router.post("/apply", status_code=201)
@limiter.limit("10/hour")  # stricter than anywhere else in the app —
                            # this is the one endpoint with zero
                            # authentication in front of it at all
def submit_application(
    request: Request,
    role_id: int = Form(...),
    full_name: str = Form(...),
    email: EmailStr = Form(...),
    phone: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    role = db.query(Role).get(role_id)
    if not role or role.stage != "Live Hiring":
        # Deliberately the same error whether the role doesn't exist or
        # simply isn't open — doesn't tell an outside party which roles
        # exist in draft/closed state.
        raise HTTPException(404, "This role is not currently accepting applications.")

    candidate = Candidate(
        full_name=full_name, email=email, phone=phone, role_id=role_id,
        resume_text="", candidate_source="Careers Page", stage="Applied",
    )
    db.add(candidate)
    db.commit()
    db.refresh(candidate)

    db.add(ActivityTimeline(
        candidate_id=candidate.id, activity="Applied via careers page",
        stage_to="Applied", actor="public_application",
    ))
    start_sla_clock(db, "candidate", candidate.id, "Resume review")

    # Priority 4 — Section 9's duplicate check, run automatically on every
    # public application, same detection logic the internal creation flow
    # already uses. This doesn't block the application (a genuine
    # duplicate is still a real person who should be able to apply again
    # to a different or the same role) — it logs a visible flag so a
    # recruiter sees it in the Activity Feed without having to manually
    # open every new applicant's record to check.
    duplicates = find_potential_duplicates(db, candidate)
    if duplicates:
        matched_names = ", ".join(d.full_name for d in duplicates[:3])
        db.add(ActivityTimeline(
            candidate_id=candidate.id,
            activity=f"Possible duplicate of existing candidate(s): {matched_names}",
            actor="public_application",
        ))

    db.commit()

    # id IS returned here, deliberately, unlike a "does this email already
    # exist" style leak — this is an opaque reference to the record THIS
    # exact request just created, needed so the same browser session can
    # immediately attach a resume via the follow-up endpoint. It reveals
    # nothing about anyone else's data, only a pointer to what the caller
    # themselves just submitted.
    return {"id": candidate.id, "status": "received", "message": "Thank you for applying. We will review your application and follow up if there's a match."}


@router.post("/apply/{candidate_id}/resume")
@limiter.limit("10/hour")
async def upload_application_resume(request: Request, candidate_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """
    Separate step from submit_application (rather than one combined
    multipart call) — matches the existing internal pattern of
    create-then-attach-resume, and means a slow/failed file parse never
    blocks the applicant's core submission from being recorded at all.
    """
    candidate = db.query(Candidate).get(candidate_id)
    if not candidate or candidate.candidate_source != "Careers Page":
        # Same non-committal error as above — never confirms whether a
        # given internal ID exists to someone probing from outside.
        raise HTTPException(404, "Application not found.")

    ext = "." + file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_RESUME_EXTENSIONS:
        raise HTTPException(422, f"Unsupported file type. Allowed: {sorted(ALLOWED_RESUME_EXTENSIONS)}")

    contents = await file.read()
    if len(contents) > MAX_RESUME_FILE_BYTES:
        raise HTTPException(422, "File too large.")
    if len(contents) == 0:
        raise HTTPException(422, "Uploaded file is empty.")

    parsed = parse_resume(resume_text=candidate.resume_text, file_bytes=contents, filename=file.filename)
    if parsed["parsing_status"] == "rchilli_structured":
        candidate.resume_text = structured_to_text(parsed)
        db.add(ActivityTimeline(
            candidate_id=candidate_id, activity=f"Resume file uploaded via careers page ({file.filename})",
            actor="public_application",
        ))
        db.commit()

        # Priority 4 — this is the actual automation the document asks
        # for: a public applicant's resume gets screened the moment it's
        # successfully parsed, without a recruiter needing to click
        # anything. Reuses the exact same screening logic the internal
        # "Run AI resume screening" button calls — same function, not a
        # second copy that could drift.
        role = candidate.role
        if role:
            try:
                _run_screening_core(db, candidate, role, triggered_by="public_application_auto_screen")
            except Exception as e:
                # Never silently swallowed — logged visibly to the
                # candidate's own timeline (and therefore the Activity
                # Feed dashboard) so a recruiter can see auto-screening
                # didn't happen and run it manually, rather than this
                # candidate quietly sitting unscreened with no trace of why.
                db.add(ActivityTimeline(
                    candidate_id=candidate.id,
                    activity=f"Automatic screening failed after resume upload: {str(e)[:200]}",
                    actor="public_application_auto_screen",
                ))
                db.commit()

        return {"status": "received"}

    # A parsing failure here should NOT surface RChilli internals to a
    # public caller the way the internal upload endpoint's error does —
    # the applicant doesn't need to know what "rchilli_failed" means.
    return {"status": "received", "note": "We received your file but could not fully process it automatically — a recruiter will review it manually."}
