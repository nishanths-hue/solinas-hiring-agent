from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models import HiringTemplate, RolePosting, Role, Candidate, get_db, User
from app.auth import get_current_user, require_roles

router = APIRouter(tags=["templates-and-postings"])

# Section 13's own table — a fixed set, not free text. A template outside
# this list doesn't match what the document defines as reusable categories.
VALID_TEMPLATE_TYPES = {"Technical Hiring", "Site Engineering", "Sales Hiring", "Urgent Hiring"}

# Section 14's own status list, in order — no automated agent in this system
# is permitted to write "Posted"; every transition here is a human PATCH call.
VALID_POSTING_STATUSES = {"Generated", "Under Review", "Approved", "Posted", "Paused", "Closed"}


# ---------------- Section 13: Hiring Templates ----------------

class TemplateCreate(BaseModel):
    template_type: str
    template_name: str
    template_content: str


@router.post("/hiring-templates", status_code=201)
def create_template(
    payload: TemplateCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("leadership", "recruitment")),
):
    if payload.template_type not in VALID_TEMPLATE_TYPES:
        raise HTTPException(422, f"template_type must be one of: {sorted(VALID_TEMPLATE_TYPES)}")
    template = HiringTemplate(
        template_type=payload.template_type, template_name=payload.template_name,
        template_content=payload.template_content, created_by=user.email,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return {"id": template.id, "template_type": template.template_type, "template_name": template.template_name}


@router.get("/hiring-templates")
def list_templates(template_type: Optional[str] = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Visible to everyone who can create a role — Section 13 doesn't
    restrict viewing, only the recruitment/leadership-curated creation."""
    query = db.query(HiringTemplate)
    if template_type:
        query = query.filter(HiringTemplate.template_type == template_type)
    templates = query.all()
    return [
        {"id": t.id, "template_type": t.template_type, "template_name": t.template_name,
         "template_content": t.template_content, "created_by": t.created_by, "created_at": t.created_at}
        for t in templates
    ]


# ---------------- Section 14: Posting Workflow ----------------

class PostingCreate(BaseModel):
    channel: str


class PostingStatusUpdate(BaseModel):
    status: str


@router.post("/roles/{role_id}/postings", status_code=201)
def create_posting(
    role_id: int,
    payload: PostingCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("leadership", "recruitment")),
):
    """Always starts at 'Generated' — Section 14: 'Generated hiring assets
    should not auto-publish.' There is no path in this API that creates a
    posting already at 'Posted'."""
    role = db.query(Role).get(role_id)
    if not role:
        raise HTTPException(404, "Role not found")
    posting = RolePosting(role_id=role_id, channel=payload.channel, status="Generated", updated_by=user.email)
    db.add(posting)
    db.commit()
    db.refresh(posting)
    return {"id": posting.id, "role_id": role_id, "channel": posting.channel, "status": posting.status}


@router.patch("/postings/{posting_id}")
def update_posting_status(
    posting_id: int,
    payload: PostingStatusUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("leadership", "recruitment")),
):
    if payload.status not in VALID_POSTING_STATUSES:
        raise HTTPException(422, f"status must be one of: {sorted(VALID_POSTING_STATUSES)}")
    posting = db.query(RolePosting).get(posting_id)
    if not posting:
        raise HTTPException(404, "Posting not found")

    posting.status = payload.status
    posting.updated_by = user.email
    from datetime import datetime
    posting.updated_at = datetime.utcnow()
    if payload.status == "Posted" and not posting.posted_at:
        posting.posted_at = datetime.utcnow()

    db.commit()
    return {"id": posting.id, "status": posting.status}


@router.get("/roles/{role_id}/postings")
def list_postings_for_role(role_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Section 14: 'The system should track posting source, candidate inflow,
    source effectiveness.' inflow_count is computed live from
    candidates.candidate_source matching the channel name, rather than a
    separately maintained counter that could drift out of sync with the
    actual candidate records.
    """
    postings = db.query(RolePosting).filter(RolePosting.role_id == role_id).all()
    result = []
    for p in postings:
        inflow = db.query(Candidate).filter(
            Candidate.role_id == role_id, Candidate.candidate_source == p.channel
        ).count()
        result.append({
            "id": p.id, "channel": p.channel, "status": p.status,
            "posted_at": p.posted_at, "candidate_inflow": inflow,
        })
    return result
