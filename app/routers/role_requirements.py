from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models import Role, RoleRequirementHistory, get_db, User
from app.auth import get_current_user

router = APIRouter(prefix="/roles", tags=["role-requirements"])

# Section 9's "Requirement Ownership Logic" table, translated to field-level
# permissions. Not every editable field maps to an explicit doc statement —
# where the doc doesn't name an owner for a specific field (e.g. individual
# skill lists), it's treated as shared collaborative territory between
# Hiring Manager and Recruitment (both "provide"/"refine" role content),
# with Leadership always included since Section 9 says Leadership "can
# override" broadly, not just the two fields it names explicitly.
FIELD_OWNERS = {
    "compensation_range": {"recruitment", "leadership"},           # Recruitment refines, Leadership overrides
    "suggested_compensation_range": {"hiring_manager", "recruitment", "leadership"},  # HM provides initially
    "hiring_priority": {"leadership", "recruitment"},               # Leadership explicitly overrides this
    "mandatory_skills": {"hiring_manager", "recruitment", "leadership"},
    "nice_to_have_skills": {"hiring_manager", "recruitment", "leadership"},
    "target_joining_date": {"hiring_manager", "recruitment", "leadership"},
    "suggested_interviewers": {"hiring_manager", "recruitment", "leadership"},
    "assignment_required": {"hiring_manager", "recruitment", "leadership"},
    "number_of_openings": {"hiring_manager", "recruitment", "leadership"},
    "jd": {"recruitment", "leadership"},                             # Recruitment owns "JD quality"
    "hiring_notes": {"hiring_manager", "recruitment", "leadership"},
    "location": {"hiring_manager", "recruitment", "leadership"},
    "work_mode": {"hiring_manager", "recruitment", "leadership"},
    "employment_type": {"hiring_manager", "recruitment", "leadership"},
    "budget": {"recruitment", "leadership"},  # same restricted tier as compensation_range
}


class RequirementUpdate(BaseModel):
    compensation_range: Optional[str] = None
    suggested_compensation_range: Optional[str] = None
    hiring_priority: Optional[str] = None
    mandatory_skills: Optional[List[str]] = None
    nice_to_have_skills: Optional[List[str]] = None
    target_joining_date: Optional[str] = None
    suggested_interviewers: Optional[List[str]] = None
    assignment_required: Optional[bool] = None
    number_of_openings: Optional[int] = None
    jd: Optional[str] = None
    hiring_notes: Optional[str] = None
    location: Optional[str] = None
    work_mode: Optional[str] = None
    employment_type: Optional[str] = None
    budget: Optional[str] = None


@router.patch("/{role_id}/requirements")
def update_requirements(
    role_id: int,
    payload: RequirementUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Section 9's general editable-requirements endpoint. Every field present
    in the request is checked against FIELD_OWNERS independently — a
    hiring_manager can update mandatory_skills and hiring_notes in the same
    call, but if they also include compensation_range, the WHOLE request is
    rejected with a specific error naming which field they can't touch,
    rather than silently applying the fields they're allowed to and
    dropping the rest (that would be confusing: did it save or not?).

    Every changed field writes one RoleRequirementHistory row — Section 9's
    'lightweight tracking,' not a full role snapshot per edit.
    """
    role = db.query(Role).get(role_id)
    if not role:
        raise HTTPException(404, "Role not found")

    updates = payload.dict(exclude_unset=True)
    if not updates:
        raise HTTPException(422, "No fields provided to update.")

    unauthorized = [f for f in updates if user.role not in FIELD_OWNERS.get(f, set())]
    if unauthorized:
        detail = {f: sorted(FIELD_OWNERS[f]) for f in unauthorized}
        raise HTTPException(
            403,
            f"Role '{user.role}' is not permitted to edit: {list(detail.keys())}. "
            f"Required roles per field: {detail}",
        )

    changed_fields = []
    for field, new_value in updates.items():
        old_value = getattr(role, field)
        if old_value == new_value:
            continue  # no actual change, don't log noise
        db.add(RoleRequirementHistory(
            role_id=role_id, field_name=field,
            previous_value=str(old_value) if old_value is not None else None,
            new_value=str(new_value) if new_value is not None else None,
            updated_by=user.email,
        ))
        setattr(role, field, new_value)
        changed_fields.append(field)

    db.commit()
    return {"role_id": role_id, "fields_changed": changed_fields}


@router.get("/{role_id}/requirements/history")
def get_requirement_history(role_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    history = (
        db.query(RoleRequirementHistory)
        .filter(RoleRequirementHistory.role_id == role_id)
        .order_by(RoleRequirementHistory.updated_at.desc())
        .all()
    )
    return [
        {"field_name": h.field_name, "previous_value": h.previous_value, "new_value": h.new_value,
         "updated_by": h.updated_by, "updated_at": h.updated_at}
        for h in history
    ]
