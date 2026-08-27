from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models import Candidate, ActivityTimeline, ResumeScreeningResult, get_db, User
from app.auth import get_current_user, require_roles
from app.sla import start_sla_clock, complete_open_clock_for
from app.ai_contract import wrap_ai_output
from agents.resume_screening_agent import screen_candidate, parse_resume, structured_to_text

router = APIRouter(prefix="/candidates", tags=["candidates"])

ALLOWED_RESUME_EXTENSIONS = {".pdf", ".doc", ".docx"}
MAX_RESUME_FILE_BYTES = 5 * 1024 * 1024  # 5MB — resumes are small; this is generous, not a real limit on legitimate use


class CandidateCreate(BaseModel):
    full_name: str
    role_id: int
    resume_text: Optional[str] = ""  # optional — a candidate can be created first, then get
                                       # resume_text populated via POST .../resume-file afterward
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
    start_sla_clock(db, "candidate", candidate.id, "Resume review")
    db.commit()
    return {"id": candidate.id, "full_name": candidate.full_name, "stage": candidate.stage}


@router.post("/{candidate_id}/resume-file")
async def upload_resume_file(
    candidate_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("leadership", "recruitment")),
):
    """
    Section 18's resume-parsing decision (RChilli), made reachable. Parses via
    RChilli and REPLACES candidate.resume_text with the structured result —
    but only on success. If RChilli fails for any reason (missing key, bad
    file, API outage), the candidate's existing resume_text is left
    untouched and the failure is returned as an error, not silently
    swallowed into an empty overwrite. A failed upload should never destroy
    a working fallback.
    """
    candidate = db.query(Candidate).get(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")

    ext = "." + file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_RESUME_EXTENSIONS:
        raise HTTPException(422, f"Unsupported file type '{ext}'. Allowed: {sorted(ALLOWED_RESUME_EXTENSIONS)}")

    contents = await file.read()
    if len(contents) > MAX_RESUME_FILE_BYTES:
        raise HTTPException(422, f"File too large ({len(contents)} bytes). Max {MAX_RESUME_FILE_BYTES} bytes.")
    if len(contents) == 0:
        raise HTTPException(422, "Uploaded file is empty.")

    parsed = parse_resume(resume_text=candidate.resume_text, file_bytes=contents, filename=file.filename)

    if parsed["parsing_status"] != "rchilli_structured":
        # Existing resume_text is untouched — nothing destructive happened.
        raise HTTPException(502, f"Resume parsing failed, existing resume text unchanged: {parsed['parsing_status']}")

    new_text = structured_to_text(parsed)
    candidate.resume_text = new_text
    db.add(ActivityTimeline(
        candidate_id=candidate_id, activity=f"Resume file uploaded and parsed via RChilli ({file.filename})",
        actor=user.email,
    ))
    db.commit()

    return {
        "candidate_id": candidate_id,
        "filename": file.filename,
        "parsing_status": parsed["parsing_status"],
        "resume_text_preview": new_text[:300],
    }


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

    record = ResumeScreeningResult(candidate_id=candidate_id, role_id=role.id, triggered_by=user.email, **result)
    db.add(record)
    candidate.stage = "Resume Review"
    db.add(ActivityTimeline(
        candidate_id=candidate_id, activity="AI resume screening completed",
        stage_from="Applied", stage_to="Resume Review", actor="resume_screening_agent",
    ))
    complete_open_clock_for(db, "candidate", candidate_id, "Resume review")

    # High-fit review — 70 is a judgment call, not a value from the
    # document (it doesn't specify a threshold for "high fit"). Chosen as
    # a reasonable floor for "worth a recruiter's focused look," not
    # derived from any stated requirement.
    if result.get("fit_score") is not None and result["fit_score"] >= 70:
        start_sla_clock(db, "candidate", candidate_id, "High-fit review")

    db.commit()
    return wrap_ai_output(result, user.email)


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
