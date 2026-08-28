from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import datetime

from app.models import CompensationResearch, Role, get_db, User
from app.auth import require_roles
from app.ai_contract import wrap_ai_output
from agents.compensation_research_agent import research_compensation

router = APIRouter(prefix="/roles/{role_id}/compensation-research", tags=["compensation-research"])

RESTRICTED_TO = ("recruitment", "leadership")


@router.post("", status_code=201)
def run_compensation_research(role_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*RESTRICTED_TO))):
    role = db.query(Role).get(role_id)
    if not role:
        raise HTTPException(404, "Role not found")

    result = research_compensation(role.role_title, role.experience_range, role.location)

    record = CompensationResearch(
        role_id=role_id,
        low_range=result["low_range"], median_range=result["median_range"],
        high_range=result["high_range"], suggested_range=result["suggested_range"],
        confidence=result["confidence"], reasoning=result["reasoning"],
        sources=result["sources"], model_used=result["model_used"],
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    return wrap_ai_output({
        "id": record.id, "role_id": role_id,
        "low_range": record.low_range, "median_range": record.median_range,
        "high_range": record.high_range, "suggested_range": record.suggested_range,
        "confidence": record.confidence, "reasoning": record.reasoning,
        "sources": record.sources,
    }, user.email)


@router.get("")
def list_compensation_research(role_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*RESTRICTED_TO))):
    records = db.query(CompensationResearch).filter(CompensationResearch.role_id == role_id).order_by(CompensationResearch.researched_at.desc()).all()
    return [{
        "id": r.id, "low_range": r.low_range, "median_range": r.median_range, "high_range": r.high_range,
        "suggested_range": r.suggested_range, "confidence": r.confidence, "reasoning": r.reasoning,
        "sources": r.sources, "researched_at": r.researched_at,
        "hr_decision": r.hr_decision, "final_range": r.final_range,
        "decided_by": r.decided_by, "decided_at": r.decided_at,
    } for r in records]


class CompensationDecision(BaseModel):
    decision: str
    final_range: str


@router.post("/{research_id}/decide")
def decide_compensation(
    role_id: int, research_id: int, payload: CompensationDecision,
    db: Session = Depends(get_db), user: User = Depends(require_roles(*RESTRICTED_TO)),
):
    valid_decisions = {"Accepted", "Modified", "Custom"}
    if payload.decision not in valid_decisions:
        raise HTTPException(422, f"decision must be one of: {sorted(valid_decisions)}")

    record = db.query(CompensationResearch).filter(
        CompensationResearch.id == research_id, CompensationResearch.role_id == role_id
    ).first()
    if not record:
        raise HTTPException(404, "Compensation research record not found for this role")

    role = db.query(Role).get(role_id)
    record.hr_decision = payload.decision
    record.final_range = payload.final_range
    record.decided_by = user.email
    record.decided_at = datetime.utcnow()
    role.compensation_range = payload.final_range
    db.commit()

    return {"role_id": role_id, "research_id": research_id, "decision": payload.decision, "compensation_range": role.compensation_range}
