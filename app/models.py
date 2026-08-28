"""
SQLAlchemy models. Same tables as schema.sql, expressed as an ORM so the
same codebase runs on SQLite locally and Postgres in the cloud with zero
code changes — only DATABASE_URL differs.
"""

import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, Boolean, Float,
    DateTime, ForeignKey, JSON
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./hiring.db")

# Render/Heroku give postgres:// but SQLAlchemy 2.x wants postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class User(Base):
    """Section 38 — the 4 access roles. Real org identity (SSO) can map into
    this later; for now it's the minimum needed to enforce field-level access."""
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, nullable=False)  # leadership | recruitment | hiring_manager | interviewer
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Role(Base):
    __tablename__ = "roles"
    id = Column(Integer, primary_key=True)
    role_title = Column(String, nullable=False)
    department = Column(String, nullable=False)
    location = Column(String)
    work_mode = Column(String)  # Remote | Hybrid | On-site
    employment_type = Column(String)  # Full-time | Part-time | Contract, etc.
    budget = Column(String)  # HR's stated budget ceiling — distinct from compensation_range,
                              # which is the market-facing offered range set later in the process
    request_display_id = Column(String, unique=True)  # e.g. "HR-REQ-2026-001" — cosmetic/display
                                                          # only, set once at creation; the real
                                                          # primary key (id) is what every foreign
                                                          # key and internal join actually uses
    hiring_manager = Column(String, nullable=False)
    hiring_priority = Column(String, nullable=False)  # Critical | High | Medium
    target_joining_date = Column(DateTime)
    number_of_openings = Column(Integer, default=1)
    replacement_or_new = Column(String)
    experience_range = Column(String)
    mandatory_skills = Column(JSON, default=list)
    nice_to_have_skills = Column(JSON, default=list)
    business_need = Column(Text)
    kpi_expectations = Column(Text)
    assignment_required = Column(Boolean, default=False)
    suggested_interviewers = Column(JSON, default=list)
    # --- restricted fields, Section 10 — must never appear in a hiring_manager/interviewer response
    suggested_compensation_range = Column(String)
    compensation_range = Column(String)
    offer_strategy_notes = Column(Text)
    internal_risk_notes = Column(Text)
    # ---
    jd = Column(Text)
    hiring_notes = Column(Text)
    stage = Column(String, default="Draft Request")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    candidates = relationship("Candidate", back_populates="role")


class Candidate(Base):
    __tablename__ = "candidates"
    id = Column(Integer, primary_key=True)
    full_name = Column(String, nullable=False)
    email = Column(String)
    phone = Column(String)
    linkedin_url = Column(String)
    role_id = Column(Integer, ForeignKey("roles.id"))
    resume_text = Column(Text)
    candidate_source = Column(String)
    sub_source = Column(String)          # Section 16 — e.g. which LinkedIn post, which agency contact
    referral_employee = Column(String)   # Section 16 — populated when candidate_source == "Employee Referral"
    agency_name = Column(String)         # Section 16 — populated when candidate_source == "Agency"
    is_duplicate_of = Column(Integer, ForeignKey("candidates.id"))  # Section 17 — linked, not merged;
                                                                      # history is preserved by construction
                                                                      # since nothing is ever deleted
    stage = Column(String, default="Applied")
    status = Column(String, default="Active")
    priority_override = Column(String, default="Normal")
    needs_founder_review = Column(Boolean, default=False)
    rejection_reason = Column(String)   # Section 23 — one of the enumerated Rejection Reasons categories
    withdrawal_reason = Column(String)  # Section 23 — one of the enumerated Withdrawal Reasons categories
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    role = relationship("Role", back_populates="candidates")
    screening_results = relationship("ResumeScreeningResult", back_populates="candidate")


class ResumeScreeningResult(Base):
    __tablename__ = "resume_screening_results"
    id = Column(Integer, primary_key=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"))
    role_id = Column(Integer, ForeignKey("roles.id"))
    fit_score = Column(Integer)
    matched_skills = Column(JSON, default=list)
    missing_skills = Column(JSON, default=list)
    risk_flags = Column(JSON, default=list)
    suggested_probe_areas = Column(JSON, default=list)
    suggested_priority = Column(String)
    score_explanation = Column(Text)
    model_used = Column(String)
    triggered_by = Column(String)  # Phase (AI contract persistence) — who actually ran this screening,
                                     # so viewing this record later (not just the live response at
                                     # generation time) still shows who/when/which-model.
    parsing_status = Column(String)  # "rchilli_structured" | "rchilli_failed: ..." | "raw_text_only" —
                                       # lets a recruiter know whether this score used structured RChilli
                                       # data or degraded to raw text, since that's a real quality difference
    created_at = Column(DateTime, default=datetime.utcnow)

    candidate = relationship("Candidate", back_populates="screening_results")


class RecruiterScreeningNote(Base):
    """Section 19 — recruiter-only note. Compensation alignment field is
    restricted from hiring_manager/interviewer views (Section 10)."""
    __tablename__ = "recruiter_screening_notes"
    id = Column(Integer, primary_key=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"))
    recruiter_name = Column(String)
    recruiter_summary = Column(Text)
    key_positives = Column(Text)
    key_concerns = Column(Text)
    compensation_alignment = Column(Text)   # restricted, Section 10
    notice_period_summary = Column(Text)
    communication_assessment = Column(Text)
    motivation_level = Column(String)
    recruiter_recommendation = Column(String)
    status = Column(String, default="New")
    created_at = Column(DateTime, default=datetime.utcnow)


class RecruiterTag(Base):
    """Section 19 — 'operational tags' from the document's own fixed list.
    A candidate can carry multiple tags simultaneously, hence a separate
    table rather than a single column."""
    __tablename__ = "recruiter_tags"
    id = Column(Integer, primary_key=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"))
    tag = Column(String, nullable=False)
    applied_by = Column(String)
    applied_at = Column(DateTime, default=datetime.utcnow)


class ScheduledInterview(Base):
    """
    Phase G — Section 21's `schedule_interview` tool. Tracks an interview
    as a real entity BEFORE feedback exists, not just after — the previous
    build only ever captured feedback retroactively, with no record that
    an interview was ever scheduled to begin with.

    Known simplification: the "Feedback submission" SLA clock (24h, from
    app/sla.py's SLA_HOURS) starts at SCHEDULING time here, not at the
    interview's actual scheduled_at time. The document's intent is closer
    to the latter, but there's no background scheduler in this app to
    detect "the interview time has now passed" and start a clock
    automatically — that would need a cron/worker process this system
    doesn't have. Starting at scheduling time is the practical MVP; a true
    fix is a real, separate follow-up (adding a scheduler), not something
    to fake here.
    """
    __tablename__ = "scheduled_interviews"
    id = Column(Integer, primary_key=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"))
    interviewer_user_id = Column(Integer, ForeignKey("users.id"))
    scheduled_at = Column(DateTime, nullable=False)
    status = Column(String, default="Scheduled")  # Scheduled | Completed | Cancelled | No-Show
    created_by = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_interview_id = Column(Integer, ForeignKey("interviews.id"))  # linked once feedback is submitted


class Interview(Base):
    __tablename__ = "interviews"
    id = Column(Integer, primary_key=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"))
    interviewer_user_id = Column(Integer, ForeignKey("users.id"))  # who actually submitted this — enforces
                                                                     # Section 38 "interviewer can edit only their
                                                                     # own feedback" at creation time, since this is
                                                                     # always set from the authenticated user, never
                                                                     # accepted as client input
    interviewer_name = Column(String)
    evaluation_area = Column(String)
    coverage_level = Column(String)
    confidence_level = Column(String)
    assessment = Column(String)
    strengths = Column(Text)
    concerns = Column(Text)
    suggested_future_probes = Column(Text)
    recommendation = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class Communication(Base):
    """
    Priority 5 — Section 12's candidate-facing communication log. Genuinely
    distinct from ActivityTimeline (which records general events like
    stage changes) — this specifically tracks what was SENT to a
    candidate, its delivery status, and the actual message content, so
    "HR should be able to view the communication history" (the doc's own
    words) means something real and queryable, not just inferred from
    scattered activity log lines.
    """
    __tablename__ = "communications"
    id = Column(Integer, primary_key=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"))
    comm_type = Column(String, nullable=False)  # Application Received | Shortlisted | Assignment | Interview | Rejection
    channel = Column(String, default="Email")
    subject = Column(String)
    message = Column(Text)
    status = Column(String)  # Sent | Failed
    sent_at = Column(DateTime, default=datetime.utcnow)


class ActivityTimeline(Base):
    __tablename__ = "activity_timeline"
    id = Column(Integer, primary_key=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"))
    activity = Column(String, nullable=False)
    stage_from = Column(String)
    stage_to = Column(String)
    actor = Column(String)
    is_stage_skip = Column(Boolean, default=False)
    skip_reason = Column(Text)
    occurred_at = Column(DateTime, default=datetime.utcnow)


class AssignmentRepository(Base):
    """Section 21 — reusable assignment templates, not one-off per candidate."""
    __tablename__ = "assignment_repository"
    id = Column(Integer, primary_key=True)
    assignment_name = Column(String, nullable=False)
    role_category = Column(String)
    experience_level = Column(String)
    skills_covered = Column(JSON, default=list)
    difficulty_level = Column(String)
    assignment_content = Column(Text)
    evaluation_criteria = Column(JSON, default=dict)  # {"area": weight, ...}
    historical_usage_count = Column(Integer, default=0)


class Assignment(Base):
    """
    Section 21 — scoring is human-entered per criterion; weighted_total is
    computed deterministically (see agents/assignment_scoring.py), not by an
    LLM. Weights match the doc's own breakdown: technical 40%, problem
    solving 25%, clarity/structure 15%, practical thinking 10%, completeness 10%.
    """
    __tablename__ = "assignments"
    id = Column(Integer, primary_key=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"))
    assignment_repository_id = Column(Integer, ForeignKey("assignment_repository.id"))
    sent_at = Column(DateTime, default=datetime.utcnow)
    submission_deadline = Column(DateTime)
    submitted_at = Column(DateTime)
    status = Column(String, default="Sent")  # Sent | Submitted | Overdue | Scored
    technical_accuracy_score = Column(Float)
    problem_solving_score = Column(Float)
    clarity_structure_score = Column(Float)
    practical_thinking_score = Column(Float)
    completeness_score = Column(Float)
    weighted_total = Column(Float)
    scored_by = Column(String)


class ReferenceCheck(Base):
    """
    Section 22 — restricted to leadership + recruitment (Section 10/38), same
    as compensation. ai_summary is generated from raw call notes; risk_level
    and rehire_eligibility are AI-SUGGESTED, always human-confirmable, never
    auto-applied to the candidate's stage or status.
    """
    __tablename__ = "reference_checks"
    id = Column(Integer, primary_key=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"))
    reference_name = Column(String)
    reference_relationship = Column(String)
    raw_notes = Column(Text)
    overall_outcome = Column(String)
    positive_signals = Column(Text)
    concerns = Column(Text)
    rehire_eligibility = Column(String)
    risk_level = Column(String)  # Low | Medium | High
    ai_summary = Column(Text)
    ai_model_used = Column(String)  # Phase (AI contract persistence) — the actual model that
                                      # generated ai_summary/risk_level/rehire_eligibility, so
                                      # this is knowable later, not just in the live response.
    completed_at = Column(DateTime, default=datetime.utcnow)
    logged_by = Column(String)  # the human who logged this reference check — distinct from
                                  # ai_model_used, which records what generated the AI summary


class JoiningRiskTracker(Base):
    """
    Section 26 — created automatically once a candidate reaches Offer
    Accepted (doc: "After: Offer Accepted, the system should create joining
    tracker, assign recruiter owner"). Fields updated manually by
    recruitment — this is observation logging, not an AI agent; the doc
    doesn't call for AI involvement here at all.
    """
    __tablename__ = "joining_risk_trackers"
    id = Column(Integer, primary_key=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"), unique=True)
    recruiter_owner = Column(String)
    joining_confidence = Column(String)
    pending_documents = Column(Text)
    joining_confirmed = Column(Boolean, default=False)
    risk_level = Column(String, default="Low Risk")  # Low Risk | Moderate Risk | High Risk
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class RoleRequirementHistory(Base):
    """Section 9 — 'lightweight tracking for important edits.' One row per
    field per edit, not a full-document snapshot — matches the doc's own
    Updated By / Updated On / Previous Value / New Value shape exactly."""
    __tablename__ = "role_requirement_history"
    id = Column(Integer, primary_key=True)
    role_id = Column(Integer, ForeignKey("roles.id"))
    field_name = Column(String, nullable=False)
    previous_value = Column(Text)
    new_value = Column(Text)
    updated_by = Column(String, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow)


class CompensationBenchmark(Base):
    """Section 11 — 'should initially remain lightweight and editable.'
    Restricted to recruitment/leadership per Section 10's explicit listing
    of 'Internal Compensation Benchmarking' as a restricted field."""
    __tablename__ = "compensation_benchmarks"
    id = Column(Integer, primary_key=True)
    role_category = Column(String, nullable=False)
    experience_range = Column(String)
    typical_market_band_min = Column(Float)
    typical_market_band_max = Column(Float)
    currency = Column(String, default="INR")
    last_updated_by = Column(String)
    last_updated_at = Column(DateTime, default=datetime.utcnow)


class CompensationResearch(Base):
    """
    Priority 2 of the Recruitment Agent workflow doc — a single AI-run
    compensation research event for one specific role, distinct from
    CompensationBenchmark (a general, manually-maintained category
    repository, not tied to any one role or research event).

    hr_decision/final_range/decided_by are null until a human acts on
    this — the research alone is never authoritative. Once decided,
    final_range also gets written to role.compensation_range (the field
    the rest of the system already restricts and uses everywhere else,
    like offer release), rather than this table becoming a second,
    parallel "true" compensation field.
    """
    __tablename__ = "compensation_research"
    id = Column(Integer, primary_key=True)
    role_id = Column(Integer, ForeignKey("roles.id"))
    low_range = Column(String)
    median_range = Column(String)
    high_range = Column(String)
    suggested_range = Column(String)
    confidence = Column(String)  # Low | Medium | High
    reasoning = Column(Text)
    sources = Column(JSON, default=list)  # [{"url": ..., "title": ...}] — real search results only
    researched_at = Column(DateTime, default=datetime.utcnow)
    model_used = Column(String)
    hr_decision = Column(String)  # Accepted | Modified | Custom — null until HR acts
    final_range = Column(String)
    decided_by = Column(String)
    decided_at = Column(DateTime)


class HiringTemplate(Base):
    """Section 13 — fixed set of 4 template types per the doc's own table;
    'only role-specific variables should change automatically' means this
    is boilerplate content with placeholders, not a full generated JD."""
    __tablename__ = "hiring_templates"
    id = Column(Integer, primary_key=True)
    template_type = Column(String, nullable=False)  # Technical Hiring | Site Engineering | Sales Hiring | Urgent Hiring
    template_name = Column(String, nullable=False)
    template_content = Column(Text, nullable=False)
    created_by = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class RolePosting(Base):
    """Section 14 — 'Generated hiring assets should not auto-publish.' No
    agent in this system ever creates or advances a row in this table —
    every status change here is a human action through the API, by design."""
    __tablename__ = "role_postings"
    id = Column(Integer, primary_key=True)
    role_id = Column(Integer, ForeignKey("roles.id"))
    channel = Column(String, nullable=False)  # e.g. "LinkedIn", "Naukri", "Employee Referral"
    status = Column(String, default="Generated")  # Generated|Under Review|Approved|Posted|Paused|Closed
    posted_at = Column(DateTime)
    updated_by = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class SlaClock(Base):
    __tablename__ = "sla_clocks"
    id = Column(Integer, primary_key=True)
    entity_type = Column(String, nullable=False)
    entity_id = Column(Integer, nullable=False)
    stage_name = Column(String, nullable=False)
    sla_hours = Column(Integer, nullable=False)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    escalation_level = Column(String, default="On Track")


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
