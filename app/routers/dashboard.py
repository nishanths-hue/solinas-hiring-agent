from collections import Counter
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.models import Role, Candidate, SlaClock, Interview, ResumeScreeningResult, ActivityTimeline, get_db, User
from app.auth import get_current_user
from app.sla import evaluate_open_clocks, ROLE_AGING_DAYS

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/overview")
def overview(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Section 31.A — Overall Hiring Dashboard. Deliberately role-level, no
    candidate PII (Section 30: 'candidate-level detail should NOT clutter dashboards')."""
    roles = db.query(Role).filter(Role.stage.in_(["Live Hiring", "Approved"])).all()
    candidates = db.query(Candidate).filter(Candidate.status == "Active").all()

    stage_counts = Counter(c.stage for c in candidates)

    return {
        "open_roles": len(roles),
        "roles_by_priority": Counter(r.hiring_priority for r in roles),
        "active_candidates": len(candidates),
        "candidates_by_stage": dict(stage_counts),
    }


@router.get("/sla-status")
def sla_status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Section 33/34 — operational velocity + ownership queue, condensed.
    Runs evaluate_open_clocks() live rather than relying on a stale cron
    result, since this is a low-volume table; move to a scheduled job once
    clock volume makes per-request evaluation slow."""
    changed = evaluate_open_clocks(db)
    open_clocks = db.query(SlaClock).filter(SlaClock.completed_at.is_(None)).all()
    breached = [c for c in open_clocks if c.escalation_level != "On Track"]

    return {
        "open_clocks": len(open_clocks),
        "breached": len(breached),
        "newly_escalated_this_check": len(changed),
        "breach_detail": [
            {"entity_type": c.entity_type, "entity_id": c.entity_id,
             "stage_name": c.stage_name, "escalation_level": c.escalation_level}
            for c in breached
        ],
    }


@router.get("/funnel")
def funnel(role_id: int = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Section 31.B-style funnel view — candidate counts at each stage, either
    system-wide or for one role. This is the thing "how's hiring going for X
    role" actually needs, which /overview intentionally doesn't provide
    (overview is deliberately role-agnostic per its own docstring).
    """
    query = db.query(Candidate).filter(Candidate.status == "Active")
    if role_id is not None:
        role = db.query(Role).get(role_id)
        if not role:
            raise HTTPException(404, "Role not found")
        query = query.filter(Candidate.role_id == role_id)

    candidates = query.all()
    stage_order = [
        "Applied", "Resume Review", "Shortlisted", "Interview Process",
        "Assignment Sent", "Assignment Submitted", "Final Evaluation",
        "Reference Check", "Offer Discussion", "Offer Released",
        "Offer Accepted", "Joined",
    ]
    counts = Counter(c.stage for c in candidates)
    # ordered so a bar chart / funnel viz on the frontend doesn't have to
    # re-sort — dict insertion order is preserved in the JSON response
    funnel_ordered = {stage: counts[stage] for stage in stage_order if stage in counts}

    rejected_query = db.query(Candidate).filter(Candidate.status == "Rejected")
    if role_id is not None:
        rejected_query = rejected_query.filter(Candidate.role_id == role_id)
    rejected_count = rejected_query.count()

    return {
        "role_id": role_id,
        "total_active": len(candidates),
        "funnel": funnel_ordered,
        "rejected": rejected_count,
    }


@router.get("/roles/{role_id}/pipeline")
def role_pipeline(role_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Per-candidate pipeline detail for ONE role — the drill-down /overview and
    /funnel both deliberately avoid (Section 30: dashboards shouldn't be
    cluttered with candidate-level detail by default). This exists as the
    explicit "click into a role" view, not the default landing view.
    Field-filtered same as GET /candidates/{id}: compensation-adjacent data
    isn't in here at all, so no role-based stripping needed at this endpoint.
    """
    role = db.query(Role).get(role_id)
    if not role:
        raise HTTPException(404, "Role not found")

    candidates = db.query(Candidate).filter(Candidate.role_id == role_id, Candidate.status == "Active").all()

    result = []
    for c in candidates:
        latest_screening = (
            db.query(ResumeScreeningResult)
            .filter(ResumeScreeningResult.candidate_id == c.id)
            .order_by(ResumeScreeningResult.created_at.desc())
            .first()
        )
        interviews = db.query(Interview).filter(Interview.candidate_id == c.id).all()
        well_covered = len([iv for iv in interviews if iv.coverage_level == "Well Covered"])

        result.append({
            "candidate_id": c.id,
            "full_name": c.full_name,
            "stage": c.stage,
            "fit_score": latest_screening.fit_score if latest_screening else None,
            "suggested_priority": latest_screening.suggested_priority if latest_screening else None,
            "interviews_completed": len(interviews),
            "areas_well_covered": well_covered,
        })

    return {
        "role_id": role_id,
        "role_title": role.role_title,
        "request_display_id": role.request_display_id,
        "candidate_count": len(result),
        "candidates": result,
    }


@router.get("/aging-roles")
def aging_roles(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Section 29 — role-priority-relative aging (a Critical role open 20 days
    is a problem; a Medium role open 20 days isn't). Surfaces roles past
    their own priority's threshold, sorted worst-first — this is the
    Leadership-facing "what's stuck" view that /overview's raw counts don't
    surface on their own.
    """
    roles = db.query(Role).filter(Role.stage.in_(["Live Hiring", "Approved"])).all()
    now = datetime.now(timezone.utc)

    flagged = []
    for r in roles:
        created = r.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_days = (now - created).days
        threshold = ROLE_AGING_DAYS.get(r.hiring_priority, 45)
        if age_days > threshold:
            flagged.append({
                "role_id": r.id, "role_title": r.role_title,
                "hiring_priority": r.hiring_priority,
                "age_days": age_days, "threshold_days": threshold,
                "days_over": age_days - threshold,
            })

    flagged.sort(key=lambda x: x["days_over"], reverse=True)
    return {"aging_role_count": len(flagged), "roles": flagged}


@router.get("/funnel-metrics")
def funnel_metrics(department: str = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Section 32 — the KPIs /funnel and /overview don't already cover:
    interview-to-offer ratio, offer acceptance rate, low-pipeline roles,
    high-drop-off roles. 'department' doubles as Section 31's function
    filter (Engineering/Sales/Operations/Product) — the doc's named filter
    values are just department values in this schema, not a separate field.
    """
    roles_q = db.query(Role).filter(Role.stage.in_(["Live Hiring", "Approved"]))
    if department:
        roles_q = roles_q.filter(Role.department == department)
    roles = roles_q.all()
    role_ids = [r.id for r in roles]

    candidates_q = db.query(Candidate).filter(Candidate.role_id.in_(role_ids)) if role_ids else db.query(Candidate).filter(False)
    all_candidates = candidates_q.all()

    interviewed = len([c for c in all_candidates if c.stage not in ("Applied", "Resume Review", "Shortlisted")])
    offers_released = len([c for c in all_candidates if c.stage in ("Offer Released", "Offer Accepted", "Joined")])
    offers_accepted = len([c for c in all_candidates if c.stage in ("Offer Accepted", "Joined")])
    rejected = len([c for c in all_candidates if c.status == "Rejected"])

    low_pipeline_roles = []
    high_dropoff_roles = []
    for r in roles:
        role_candidates = [c for c in all_candidates if c.role_id == r.id]
        active_count = len([c for c in role_candidates if c.status == "Active"])
        rejected_count = len([c for c in role_candidates if c.status == "Rejected"])
        total = len(role_candidates)
        if active_count < 3:  # Section 32: "Insufficient active candidates" — no fixed number given in the
                               # doc, so this is a judgment call; 3 is a reasonable floor for a live pipeline,
                               # not a value derived from the document itself
            low_pipeline_roles.append({"role_id": r.id, "role_title": r.role_title, "active_candidates": active_count})
        if total >= 3 and (rejected_count / total) > 0.6:  # same caveat — 60% is a chosen threshold, not a doc value
            high_dropoff_roles.append({"role_id": r.id, "role_title": r.role_title,
                                        "rejection_rate": round(rejected_count / total * 100, 1)})

    return {
        "open_roles": len(roles),
        "interview_to_offer_ratio": round(offers_released / interviewed, 2) if interviewed else None,
        "offer_acceptance_rate": round(offers_accepted / offers_released * 100, 1) if offers_released else None,
        "low_pipeline_roles": low_pipeline_roles,
        "high_dropoff_roles": high_dropoff_roles,
    }


@router.get("/velocity")
def operational_velocity(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Section 33 — computed from activity_timeline, NOT sla_clocks. sla_clocks
    exists in the schema and the escalation logic (Sections 27-28) is real
    and tested, but no endpoint in this API actually calls start_sla_clock —
    only the standalone demo script does. That means sla_clocks has no real
    production data to compute SLA Compliance % from. This endpoint computes
    what activity_timeline's real timestamps CAN support: turnaround time
    between logged stage transitions, and idle-candidate detection. SLA
    Compliance % is intentionally omitted rather than computed from empty
    data and presented as if it means something.
    """
    now = datetime.now(timezone.utc)

    # Resume Review TAT: time between "Applied" and screening-completed activity
    applied_events = db.query(ActivityTimeline).filter(ActivityTimeline.stage_to == "Applied").all()
    screening_events = db.query(ActivityTimeline).filter(
        ActivityTimeline.activity.like("AI resume screening completed%")
    ).all()
    screening_by_candidate = {e.candidate_id: e.occurred_at for e in screening_events}

    review_times = []
    for e in applied_events:
        if e.candidate_id in screening_by_candidate:
            applied_at = e.occurred_at
            screened_at = screening_by_candidate[e.candidate_id]
            if applied_at.tzinfo is None:
                applied_at = applied_at.replace(tzinfo=timezone.utc)
            if screened_at.tzinfo is None:
                screened_at = screened_at.replace(tzinfo=timezone.utc)
            review_times.append((screened_at - applied_at).total_seconds() / 3600)
    avg_resume_review_tat_hours = round(sum(review_times) / len(review_times), 1) if review_times else None

    # Resume Aging: candidates still at "Applied" for >48h with no screening event yet
    resume_aging = []
    active_applied = db.query(Candidate).filter(Candidate.stage == "Applied", Candidate.status == "Active").all()
    for c in active_applied:
        created = c.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        hours_waiting = (now - created).total_seconds() / 3600
        if hours_waiting > 48:
            resume_aging.append({"candidate_id": c.id, "full_name": c.full_name, "hours_waiting": round(hours_waiting, 1)})

    # Candidate Idle Cases: Active candidates with no activity_timeline entry in >3 days
    idle_cases = []
    active_candidates = db.query(Candidate).filter(Candidate.status == "Active").all()
    for c in active_candidates:
        last_activity = (
            db.query(ActivityTimeline)
            .filter(ActivityTimeline.candidate_id == c.id)
            .order_by(ActivityTimeline.occurred_at.desc())
            .first()
        )
        reference_time = last_activity.occurred_at if last_activity else c.created_at
        if reference_time.tzinfo is None:
            reference_time = reference_time.replace(tzinfo=timezone.utc)
        days_idle = (now - reference_time).total_seconds() / 86400
        if days_idle > 3:
            idle_cases.append({"candidate_id": c.id, "full_name": c.full_name, "days_idle": round(days_idle, 1)})

    # SLA compliance — now computable, since 5 of 13 named SLA stages are
    # wired to real actions (see app/sla.py callers). Compliance is measured
    # against COMPLETED clocks only — an open, still-running clock isn't yet
    # a pass or a fail, so including it would understate or overstate the
    # rate depending on how long it's been open.
    completed_clocks = db.query(SlaClock).filter(SlaClock.completed_at.isnot(None)).all()
    sla_compliance_percent = None
    compliance_by_stage = {}
    if completed_clocks:
        met = 0
        for clock in completed_clocks:
            started = clock.started_at
            completed = clock.completed_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if completed.tzinfo is None:
                completed = completed.replace(tzinfo=timezone.utc)
            hours_taken = (completed - started).total_seconds() / 3600
            within_sla = hours_taken <= clock.sla_hours
            if within_sla:
                met += 1
            bucket = compliance_by_stage.setdefault(clock.stage_name, {"met": 0, "total": 0})
            bucket["total"] += 1
            if within_sla:
                bucket["met"] += 1
        sla_compliance_percent = round(met / len(completed_clocks) * 100, 1)
        for stage, b in compliance_by_stage.items():
            b["compliance_percent"] = round(b["met"] / b["total"] * 100, 1)

    return {
        "avg_resume_review_tat_hours": avg_resume_review_tat_hours,
        "resume_aging_over_48h": resume_aging,
        "candidate_idle_cases_over_3d": idle_cases,
        "sla_compliance_percent": sla_compliance_percent,
        "sla_compliance_by_stage": compliance_by_stage,
        "note": "sla_compliance_percent covers only the 5 SLA stages wired to real actions "
                "(Hiring request review, Resume review, Assignment sent, Reference completion, "
                "Offer release) out of 13 named in the original document — the other 8 don't have "
                "a single unambiguous action boundary in the current API and remain unwired rather "
                "than guessed at. null means no clocks have completed yet.",
    }


@router.get("/ownership-queue")
def ownership_queue(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Section 34 — 'pending operational actions by owner.' Derived from the
    same Stage Movement Ownership table Section 15's transition endpoint
    already enforces (app/routers/candidate_lifecycle.py) — whichever role
    owns the NEXT transition from a candidate's current stage is who has a
    pending action on that candidate right now.
    """
    active_candidates = db.query(Candidate).filter(Candidate.status == "Active").all()

    # Mirrors candidate_lifecycle.py's NAMED_TRANSITION_OWNERS keys, read as
    # "whose turn is it FROM this stage" rather than duplicating the full
    # ownership table — if that table changes, this drifts, which is a real
    # coupling worth knowing about rather than silently accepting.
    stage_owner = {
        "Applied": "recruitment", "Resume Review": "recruitment", "Shortlisted": "recruitment",
        "Interview Process": "hiring_manager", "Assignment Sent": "recruitment",
        "Assignment Submitted": "hiring_manager", "Final Evaluation": "hiring_manager",
        "Reference Check": "hiring_manager", "Offer Discussion": "leadership",
        "Offer Released": "recruitment",
    }

    counts = {"recruitment": 0, "hiring_manager": 0, "interviewer": 0, "leadership": 0}
    for c in active_candidates:
        owner = stage_owner.get(c.stage)
        if owner:
            counts[owner] += 1
        if c.stage == "Interview Process":
            counts["interviewer"] += 1  # an interview pending is also an interviewer action, not exclusive

    return counts


@router.get("/activity-feed")
def activity_feed(limit: int = 30, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Section 35 — 'centralized operational activity feed.' Combines real
    activity_timeline entries with the exception conditions the doc names
    (SLA breaches — via the escalation logic that IS real and tested, aging
    roles, founder review requests) into one reverse-chronological feed.
    """
    recent_activity = (
        db.query(ActivityTimeline)
        .order_by(ActivityTimeline.occurred_at.desc())
        .limit(limit)
        .all()
    )
    feed = [
        {"type": "activity", "candidate_id": a.candidate_id, "description": a.activity,
         "actor": a.actor, "occurred_at": a.occurred_at}
        for a in recent_activity
    ]

    founder_review = db.query(Candidate).filter(Candidate.needs_founder_review == True, Candidate.status == "Active").all()  # noqa: E712
    for c in founder_review:
        feed.append({"type": "founder_review_pending", "candidate_id": c.id,
                     "description": f"{c.full_name} flagged for founder review", "occurred_at": c.updated_at})

    feed.sort(key=lambda x: x["occurred_at"] or datetime.min, reverse=True)
    return feed[:limit]
