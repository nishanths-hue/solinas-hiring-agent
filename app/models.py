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
    completed_at = Column(DateTime, default=datetime.utcnow)
    logged_by = Column(String)


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
