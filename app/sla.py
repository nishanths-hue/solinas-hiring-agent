"""
SQLAlchemy-backed SLA evaluation for the FastAPI app. Same rules as
agents/sla_agent.py (kept there for the standalone orchestrator script);
duplicated here rather than shared because the two run against different
DB access layers (raw sqlite3 vs SQLAlchemy) and forcing one shared
implementation would mean the script depends on the web app or vice versa.
If this drifts in practice, collapse both onto SQLAlchemy and delete db.py.
"""

from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from app.models import SlaClock

SLA_HOURS = {
    "Hiring request review": 24, "JD refinement": 48, "Job posting activation": 24,
    "Resume review": 48, "High-fit review": 24, "Feedback submission": 24,
    "Final evaluation decision": 48, "Assignment sent": 12, "Assignment review": 48,
    "Reference initiation": 24, "Reference completion": 48,
    "Offer approval": 24, "Offer release": 24,
}

ESCALATION_LADDER = [
    (0, "Friendly Reminder"), (24, "Strong Reminder"),
    (48, "Escalation"), (72, "Hiring Blocked"),
]

ROLE_AGING_DAYS = {"Critical": 15, "High": 30, "Medium": 45}


def start_sla_clock(db: Session, entity_type: str, entity_id: int, stage_name: str) -> SlaClock:
    hours = SLA_HOURS.get(stage_name)
    if hours is None:
        raise ValueError(f"No SLA defined for stage '{stage_name}'")
    clock = SlaClock(entity_type=entity_type, entity_id=entity_id,
                      stage_name=stage_name, sla_hours=hours)
    db.add(clock)
    db.commit()
    db.refresh(clock)
    return clock


def complete_sla_clock(db: Session, clock_id: int):
    clock = db.query(SlaClock).get(clock_id)
    if clock:
        clock.completed_at = datetime.now(timezone.utc)
        db.commit()


def complete_open_clock_for(db: Session, entity_type: str, entity_id: int, stage_name: str):
    """
    Finds and completes the open clock matching this entity+stage, if one
    exists. Silently does nothing if no matching open clock is found —
    that's the correct behavior for an edge case like a candidate reaching
    'Offer Released' without ever passing through 'Offer Discussion' (e.g.
    a skip-logged jump), not an error worth surfacing to the caller.
    """
    clock = (
        db.query(SlaClock)
        .filter(
            SlaClock.entity_type == entity_type,
            SlaClock.entity_id == entity_id,
            SlaClock.stage_name == stage_name,
            SlaClock.completed_at.is_(None),
        )
        .first()
    )
    if clock:
        clock.completed_at = datetime.now(timezone.utc)
        db.commit()


def evaluate_open_clocks(db: Session) -> list[dict]:
    open_clocks = db.query(SlaClock).filter(SlaClock.completed_at.is_(None)).all()
    changed = []
    now = datetime.now(timezone.utc)

    for clock in open_clocks:
        started = clock.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        due = started + timedelta(hours=clock.sla_hours)
        hours_overdue = (now - due).total_seconds() / 3600

        new_level = "On Track"
        for threshold, level in ESCALATION_LADDER:
            if hours_overdue >= threshold:
                new_level = level

        if new_level != clock.escalation_level:
            clock.escalation_level = new_level
            changed.append({
                "id": clock.id, "entity_type": clock.entity_type,
                "entity_id": clock.entity_id, "stage_name": clock.stage_name,
                "escalation_level": new_level, "hours_overdue": round(hours_overdue, 1),
            })
    db.commit()
    return changed
