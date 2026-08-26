from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models import Candidate, get_db, User
from app.auth import get_current_user, require_roles

router = APIRouter(tags=["duplicates-and-sources"])


def find_potential_duplicates(db: Session, candidate: Candidate) -> list:
    matches = []
    if candidate.email:
        matches += db.query(Candidate).filter(
            Candidate.email == candidate.email, Candidate.id != candidate.id
        ).all()
    if candidate.phone:
        matches += db.query(Candidate).filter(
            Candidate.phone == candidate.phone, Candidate.id != candidate.id
        ).all()
    if candidate.linkedin_url:
        matches += db.query(Candidate).filter(
            Candidate.linkedin_url == candidate.linkedin_url, Candidate.id != candidate.id
        ).all()
    seen = {}
    for m in matches:
        seen[m.id] = m
    return list(seen.values())


@router.get("/candidates/{candidate_id}/potential-duplicates")
def get_potential_duplicates(candidate_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    candidate = db.query(Candidate).get(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")
    matches = find_potential_duplicates(db, candidate)
    result = []
    for m in matches:
        matched_on = []
        if candidate.email and m.email == candidate.email:
            matched_on.append("email")
        if candidate.phone and m.phone == candidate.phone:
            matched_on.append("phone")
        if candidate.linkedin_url and m.linkedin_url == candidate.linkedin_url:
            matched_on.append("linkedin_url")
        result.append({
            "candidate_id": m.id, "full_name": m.full_name, "email": m.email,
            "phone": m.phone, "linkedin_url": m.linkedin_url, "role_id": m.role_id,
            "matched_on": matched_on,
        })
    return result


class MarkDuplicate(BaseModel):
    duplicate_of_candidate_id: int


@router.post("/candidates/{candidate_id}/mark-duplicate")
def mark_duplicate(
    candidate_id: int,
    payload: MarkDuplicate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("recruitment", "leadership")),
):
    candidate = db.query(Candidate).get(candidate_id)
    original = db.query(Candidate).get(payload.duplicate_of_candidate_id)
    if not candidate or not original:
        raise HTTPException(404, "Candidate not found")
    if candidate.id == original.id:
        raise HTTPException(422, "A candidate cannot be marked as a duplicate of itself")

    candidate.is_duplicate_of = original.id
    candidate.status = "Closed"
    db.commit()
    return {"candidate_id": candidate_id, "is_duplicate_of": original.id}


@router.get("/dashboard/source-breakdown")
def source_breakdown(role_id: int = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = db.query(Candidate)
    if role_id is not None:
        query = query.filter(Candidate.role_id == role_id)
    candidates = query.all()

    breakdown = {}
    for c in candidates:
        source = c.candidate_source or "Unknown"
        if source not in breakdown:
            breakdown[source] = {"total": 0, "converted": 0}
        breakdown[source]["total"] += 1
        if c.stage in ("Offer Accepted", "Joined"):
            breakdown[source]["converted"] += 1

    for source, data in breakdown.items():
        data["conversion_rate"] = round(data["converted"] / data["total"] * 100, 1) if data["total"] else 0.0

    return breakdown
