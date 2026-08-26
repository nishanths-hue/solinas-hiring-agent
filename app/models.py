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
