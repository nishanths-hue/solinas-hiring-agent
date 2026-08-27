"""
SQLAlchemy-backed SLA evaluation for the FastAPI app. Same rules as
agents/sla_agent.py (kept there for the standalone orchestrator script);
duplicated here rather than shared because the two run against different
DB access layers (raw sqlite3 vs SQLAlchemy) and forcing one shared
implementation would mean the script depends on the web app or vice versa.
If this drifts in practice, collapse both onto SQLAlchemy and delete db.py.

Phase H — reminders. IMPORTANT LIMITATION, stated here because it matters:
evaluate_open_clocks() is only ever called from GET /dashboard/sla-status
(see app/routers/dashboard.py). There is no background scheduler anywhere
in this app. That means the escalation emails this module sends are
REACTIVE, not proactive — they fire only when a human happens to load
that dashboard, which recomputes every open clock's level and emails
whoever's newly escalated. A clock can sit badly overdue for days with
nobody notified if nobody opens the dashboard in that window. A real fix
needs an actual scheduler (e.g. a periodic job hitting this endpoint, or
a proper task queue) — that's a genuine, separate follow-up, not
something faked here.
"""

from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from app.models import SlaClock, User, ScheduledInterview
from agents.email_agent import send_sla_escalation_email

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

# Which role type "owns" each named SLA stage, for reminder purposes only.
# This is deliberately NOT the same as candidate_lifecycle.py's per-
# transition ownership table — that governs who's ALLOWED to move a
# candidate; this governs who gets pinged when a clock breaches. Only
# stages with clocks actually wired to real actions (see the 5 callers of
# start_sla_clock across the routers) will ever appear here in practice.
SLA_STAGE_OWNER_ROLES = {
    "Hiring request review": ["leadership", "recruitment"],
    "Resume review": ["recruitment"],
    "JD refinement": ["recruitment", "leadership"],
    "Job posting activation": ["recruitment"],
    "High-fit review": ["recruitment"],
    "Assignment sent": ["recruitment"],
    "Assignment review": ["hiring_manager"],
    "Final evaluation decision": ["hiring_manager"],
    "Reference initiation": ["recruitment"],
    "Reference completion": ["recruitment"],
    "Offer release": ["leadership", "recruitment"],
    "Feedback submission": [],  # handled specially below — scheduled_interview
                                  # clocks have a real named interviewer, not a role type
    # "Offer approval" (the 13th named SLA stage) is deliberately never
    # wired anywhere via start_sla_clock — its window (Offer Discussion
    # through the offer actually being released) is identical to what
    # "Offer release" already covers. Wiring both to the same real event
    # would double-count one action under two names and send two
    # redundant reminder emails for the same breach. Left unwired
    # honestly rather than inventing an artificial distinction the
    # document doesn't actually draw between "approval" and "release."
}


def _notify_escalation(db: Session, clock: SlaClock, escalation_level: str, hours_overdue: float):
    """
    Resolves who to email for a given breached clock and sends it.
    scheduled_interview clocks have a real, specific person (the assigned
    interviewer) — email them directly. Every other clock type only has a
    role type as its "owner" (see SLA_STAGE_OWNER_ROLES above), so every
    active account with that role gets notified — there's no per-person
    assignment modeled for candidates/roles beyond role type, and
    fabricating one would be worse than being honest about the real
    granularity available.
    """
    if clock.entity_type == "scheduled_interview":
        scheduled = db.query(ScheduledInterview).get(clock.entity_id)
        if scheduled:
            interviewer = db.query(User).get(scheduled.interviewer_user_id)
            if interviewer:
                send_sla_escalation_email(interviewer.email, clock.entity_type, clock.stage_name, escalation_level, hours_overdue)
        return

    owner_roles = SLA_STAGE_OWNER_ROLES.get(clock.stage_name, [])
    if not owner_roles:
        return
    recipients = db.query(User).filter(User.role.in_(owner_roles), User.is_active == True).all()  # noqa: E712
    for person in recipients:
        send_sla_escalation_email(person.email, clock.entity_type, clock.stage_name, escalation_level, hours_overdue)


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
            # Only email on a genuine escalation (not the initial "On Track"
            # state, and not every re-evaluation of an unchanged level) —
            # this fires once per actual level transition, e.g. the moment
            # a clock crosses from "On Track" into "Friendly Reminder", or
            # from "Friendly Reminder" into "Strong Reminder".
            if new_level != "On Track":
                _notify_escalation(db, clock, new_level, hours_overdue)
    db.commit()
    return changed
