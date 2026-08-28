from typing import Optional, List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models import Role, get_db, User
from app.auth import get_current_user, require_roles
from app.permissions import filter_role_dict, can_edit
from app.sla import start_sla_clock, complete_open_clock_for
from app.ai_contract import wrap_ai_output
from agents.jd_agent import generate_hiring_assets

router = APIRouter(prefix="/roles", tags=["roles"])


class RoleCreate(BaseModel):
    role_title: str
    department: str
    hiring_manager: str
    hiring_priority: str  # Critical | High | Medium
    experience_range: Optional[str] = None
    mandatory_skills: List[str] = []
    nice_to_have_skills: List[str] = []
    business_need: Optional[str] = None
    kpi_expectations: Optional[str] = None
    replacement_or_new: Optional[str] = None
    suggested_compensation_range: Optional[str] = None
    location: Optional[str] = None
    work_mode: Optional[str] = None
    employment_type: Optional[str] = None
    budget: Optional[str] = None


def _role_to_dict(role: Role) -> dict:
    return {c.name: getattr(role, c.name) for c in role.__table__.columns}


@router.post("", status_code=201)
def create_role(
    payload: RoleCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("leadership", "recruitment", "hiring_manager")),
):
    role = Role(**payload.dict(), stage="Draft Request")
    db.add(role)
    db.commit()
    db.refresh(role)
    # Display ID needs the real auto-increment id first, so it's set in a
    # second pass right after — this is cosmetic only (shown to humans),
    # never used as a foreign key or lookup key anywhere in the system.
    role.request_display_id = f"HR-REQ-{datetime.utcnow().year}-{role.id:03d}"
    db.commit()
    start_sla_clock(db, "role", role.id, "Hiring request review")
    return filter_role_dict(_role_to_dict(role), user.role)


@router.get("")
def list_roles(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    roles = db.query(Role).all()
    return [filter_role_dict(_role_to_dict(r), user.role) for r in roles]


@router.get("/{role_id}")
def get_role(role_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    role = db.query(Role).get(role_id)
    if not role:
        raise HTTPException(404, "Role not found")
    return filter_role_dict(_role_to_dict(role), user.role)


@router.post("/{role_id}/generate-jd")
def generate_jd(
    role_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("leadership", "recruitment")),
):
    """Section 12 — triggers the AI JD agent. Writes a draft; does NOT
    publish anywhere (Section 14 posting workflow is a separate, human-gated step)."""
    role = db.query(Role).get(role_id)
    if not role:
        raise HTTPException(404, "Role not found")

    assets = generate_hiring_assets({
        "role_title": role.role_title,
        "department": role.department,
        "experience_range": role.experience_range,
        "mandatory_skills": role.mandatory_skills or [],
        "nice_to_have_skills": role.nice_to_have_skills or [],
        "business_need": role.business_need,
        "kpi_expectations": role.kpi_expectations,
        "hiring_priority": role.hiring_priority,
    })
    role.jd = assets["internal_assets"]["job_description"]
    db.commit()
    start_sla_clock(db, "role", role_id, "JD refinement")
    return wrap_ai_output(assets, user.email)


@router.patch("/{role_id}/compensation")
def update_compensation(
    role_id: int,
    compensation_range: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("leadership", "recruitment")),
):
    """Separate endpoint (not a generic PATCH /roles/{id}) specifically so
    compensation edits always go through the require_roles check — a generic
    update endpoint would need to re-implement this filtering per-field."""
    if not can_edit(user.role, "role.compensation_range"):
        raise HTTPException(403, "Not permitted to edit compensation")
    role = db.query(Role).get(role_id)
    if not role:
        raise HTTPException(404, "Role not found")
    role.compensation_range = compensation_range
    db.commit()
    return {"role_id": role_id, "compensation_range": compensation_range}


# Section 7 — "Role Stages" and "Role Ownership" tables, exactly as specified
ROLE_STAGE_ORDER = ["Draft Request", "Under Review", "Approved", "Live Hiring", "Closed"]
ROLE_STAGE_TRANSITION_OWNERS = {
    ("Draft Request", "Under Review"): {"hiring_manager"},
    ("Under Review", "Approved"): {"leadership", "recruitment"},
    ("Approved", "Live Hiring"): {"recruitment"},
    ("Live Hiring", "Closed"): {"recruitment", "hiring_manager"},
}
ROLE_DEFAULT_ALLOWED_ROLES = {"leadership", "recruitment", "hiring_manager"}


class RoleTransition(BaseModel):
    to_stage: str


@router.post("/{role_id}/transition")
def transition_role(
    role_id: int,
    payload: RoleTransition,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Section 7's role stage-movement endpoint, enforcing the document's
    exact transition-ownership table. 'On Hold' isn't in the doc's Role
    Stages table for transitions but is a valid stage value elsewhere in
    the schema — reachable by the default allowed roles, no named owner."""
    valid_stages = set(ROLE_STAGE_ORDER) | {"On Hold"}
    if payload.to_stage not in valid_stages:
        raise HTTPException(422, f"'{payload.to_stage}' is not a valid role stage. Valid: {sorted(valid_stages)}")

    role = db.query(Role).get(role_id)
    if not role:
        raise HTTPException(404, "Role not found")

    from_stage = role.stage
    owners = ROLE_STAGE_TRANSITION_OWNERS.get((from_stage, payload.to_stage), ROLE_DEFAULT_ALLOWED_ROLES)
    if user.role not in owners:
        raise HTTPException(
            403,
            f"Role '{user.role}' cannot move this role from '{from_stage}' to '{payload.to_stage}'. "
            f"Requires one of: {sorted(owners)}",
        )

    role.stage = payload.to_stage
    if payload.to_stage == "Approved":
        complete_open_clock_for(db, "role", role_id, "Hiring request review")
    if payload.to_stage == "Live Hiring":
        complete_open_clock_for(db, "role", role_id, "JD refinement")
    db.commit()
    return {"role_id": role_id, "from_stage": from_stage, "to_stage": payload.to_stage}
