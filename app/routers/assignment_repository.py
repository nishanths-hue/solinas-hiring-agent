from typing import List, Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models import AssignmentRepository, get_db, User
from app.auth import require_roles

router = APIRouter(prefix="/assignment-repository", tags=["assignment-repository"])


class RepositoryItemCreate(BaseModel):
    assignment_name: str
    role_category: Optional[str] = None
    experience_level: Optional[str] = None
    skills_covered: List[str] = []
    difficulty_level: Optional[str] = None
    assignment_content: str
    evaluation_criteria: dict = {}


@router.post("", status_code=201)
def create_repository_item(
    payload: RepositoryItemCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("leadership", "recruitment")),
):
    item = AssignmentRepository(**payload.dict())
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"id": item.id, "assignment_name": item.assignment_name}


@router.get("")
def list_repository_items(db: Session = Depends(get_db), user: User = Depends(require_roles("leadership", "recruitment", "hiring_manager"))):
    items = db.query(AssignmentRepository).all()
    return [{"id": i.id, "assignment_name": i.assignment_name, "role_category": i.role_category,
             "difficulty_level": i.difficulty_level, "historical_usage_count": i.historical_usage_count}
            for i in items]
