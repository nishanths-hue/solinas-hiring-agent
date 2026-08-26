"""
SLA Tracking Agent — Sections 27, 28, 29 of the operating design doc.

Deliberately NOT an LLM call. SLA breach detection is deterministic arithmetic
on timestamps — running it through an LLM would add cost, latency, and a
hallucination surface for zero benefit. This is the one "agent" in the system
that should just be a scheduled job.
"""

from datetime import datetime, timedelta, timezone
from db import get_conn, fetch_all

# Section 27 — SLA definitions (hours)
SLA_HOURS = {
    "Hiring request review": 24,
    "JD refinement": 48,
    "Job posting activation": 24,
    "Resume review": 48,
    "High-fit review": 24,
    "Feedback submission": 24,
    "Final evaluation decision": 48,
    "Assignment sent": 12,
    "Assignment review": 48,
    "Reference initiation": 24,
    "Reference completion": 48,
    "Offer approval": 24,
    "Offer release": 24,
}

# Section 28 — escalation ladder
ESCALATION_LADDER = [
    (0, "Friendly Reminder"),
    (24, "Strong Reminder"),
    (48, "Escalation"),
    (72, "Hiring Blocked"),
]

# Section 29 — role priority aging thresholds (days), separate from stage SLAs
ROLE_AGING_DAYS = {
    "Critical": 15,
    "High": 30,
    "Medium": 45,
}


def start_sla_clock(entity_type: str, entity_id: int, stage_name: str):
    hours = SLA_HOURS.get(stage_name)
    if hours is None:
        raise ValueError(f"No SLA defined for stage '{stage_name}'")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sla_clocks (entity_type, entity_id, stage_name, sla_hours) VALUES (?, ?, ?, ?)",
            (entity_type, entity_id, stage_name, hours),
        )


def complete_sla_clock(clock_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE sla_clocks SET completed_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), clock_id),
        )


def evaluate_open_clocks() -> list[dict]:
    """Run periodically (e.g. hourly cron). Updates escalation_level on every
    open clock and returns the ones that changed level, for the activity feed
    (Section 35) and escalation notifications."""
    open_clocks = fetch_all("SELECT * FROM sla_clocks WHERE completed_at IS NULL")
    changed = []
    now = datetime.now(timezone.utc)

    with get_conn() as conn:
        for clock in open_clocks:
            started = datetime.fromisoformat(clock["started_at"])
            due = started + timedelta(hours=clock["sla_hours"])
            hours_overdue = (now - due).total_seconds() / 3600

            new_level = "On Track"
            for threshold, level in ESCALATION_LADDER:
                if hours_overdue >= threshold:
                    new_level = level

            if new_level != clock["escalation_level"]:
                conn.execute(
                    "UPDATE sla_clocks SET escalation_level = ? WHERE id = ?",
                    (new_level, clock["id"]),
                )
                changed.append({**clock, "escalation_level": new_level, "hours_overdue": round(hours_overdue, 1)})
    return changed


def role_aging_status(role_priority: str, role_created_at: str) -> dict:
    """Section 29 — role-priority-relative aging, not a universal threshold."""
    threshold_days = ROLE_AGING_DAYS.get(role_priority, 45)
    created = datetime.fromisoformat(role_created_at)
    age_days = (datetime.now(timezone.utc) - created).days
    return {
        "age_days": age_days,
        "threshold_days": threshold_days,
        "is_aging": age_days > threshold_days,
    }
