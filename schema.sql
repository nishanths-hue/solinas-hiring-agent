-- Solinas Hiring Management System — Core Schema
-- Maps directly to Section 6 (Core Data Structure) of the operating design doc.
-- SQLite for portability; swap for Postgres in production (see README).

PRAGMA foreign_keys = ON;

-- ============================================================
-- 1. ROLES / HIRING REQUESTS  (Section 7, 8, 9)
-- ============================================================
CREATE TABLE roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_title TEXT NOT NULL,
    department TEXT NOT NULL,
    hiring_manager TEXT NOT NULL,
    hiring_priority TEXT CHECK(hiring_priority IN ('Critical','High','Medium')) NOT NULL,
    target_joining_date DATE,
    number_of_openings INTEGER DEFAULT 1,
    replacement_or_new TEXT CHECK(replacement_or_new IN ('Replacement','New Role')),
    experience_range TEXT,
    mandatory_skills TEXT,          -- JSON array
    nice_to_have_skills TEXT,       -- JSON array
    business_need TEXT,
    kpi_expectations TEXT,
    assignment_required INTEGER DEFAULT 0,
    suggested_interviewers TEXT,    -- JSON array
    suggested_compensation_range TEXT,   -- restricted field
    compensation_range TEXT,             -- restricted field, editable post-approval
    jd TEXT,
    hiring_notes TEXT,
    stage TEXT CHECK(stage IN ('Draft Request','Under Review','Approved','Live Hiring','On Hold','Closed'))
        NOT NULL DEFAULT 'Draft Request',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Lightweight version tracking for editable requirement fields (Section 9)
CREATE TABLE role_requirement_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id INTEGER NOT NULL REFERENCES roles(id),
    field_name TEXT NOT NULL,
    previous_value TEXT,
    new_value TEXT,
    updated_by TEXT NOT NULL,
    updated_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 2. CANDIDATES  (Section 15, 16, 17)
-- ============================================================
CREATE TABLE candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    linkedin_url TEXT,
    role_id INTEGER REFERENCES roles(id),
    resume_path TEXT,
    resume_text TEXT,               -- raw parsed text, feeds the AI agent
    candidate_source TEXT,          -- LinkedIn, Naukri, Employee Referral, Agency, Founder Referral, Direct
    sub_source TEXT,
    referral_employee TEXT,
    agency_name TEXT,
    stage TEXT CHECK(stage IN (
        'Applied','Resume Review','Shortlisted','Interview Process','Assignment Sent',
        'Assignment Submitted','Final Evaluation','Reference Check','Offer Discussion',
        'Offer Released','Offer Accepted','Joined','Rejected','Hold for Future','On Hold'
    )) NOT NULL DEFAULT 'Applied',
    status TEXT CHECK(status IN ('Active','Closed','Rejected','Hold for Future','On Hold'))
        NOT NULL DEFAULT 'Active',
    priority_override TEXT CHECK(priority_override IN ('Normal','High','Critical')) DEFAULT 'Normal',
    needs_founder_review INTEGER DEFAULT 0,
    is_duplicate_of INTEGER REFERENCES candidates(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 3. AI SCREENING OUTPUT  (Section 18 — this is the agent's primary write target)
-- ============================================================
CREATE TABLE resume_screening_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    role_id INTEGER NOT NULL REFERENCES roles(id),
    fit_score INTEGER,                     -- 0-100
    matched_skills TEXT,                   -- JSON array
    missing_skills TEXT,                   -- JSON array
    risk_flags TEXT,                       -- JSON array
    suggested_probe_areas TEXT,            -- JSON array
    suggested_priority TEXT CHECK(suggested_priority IN ('Priority','Review','Low Priority','Reject')),
    score_explanation TEXT,                -- must always be human-readable (Section 18 requirement)
    model_used TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Recruiter screening layer sits on top of / can override AI output (Section 19)
CREATE TABLE recruiter_screening_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    recruiter_name TEXT NOT NULL,
    recruiter_summary TEXT,
    key_positives TEXT,
    key_concerns TEXT,
    compensation_alignment TEXT,
    notice_period_summary TEXT,
    communication_assessment TEXT,
    motivation_level TEXT,
    suggested_priority TEXT,
    suggested_probe_areas TEXT,
    recruiter_recommendation TEXT CHECK(recruiter_recommendation IN ('Proceed','Hold','Reject')),
    status TEXT CHECK(status IN (
        'New','Under Recruiter Review','Awaiting Hiring Manager Review','HM Shortlisted','Hold','Rejected'
    )) DEFAULT 'New',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE recruiter_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    tag TEXT NOT NULL,
    applied_by TEXT,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 4. INTERVIEWS  (Section 20)
-- ============================================================
CREATE TABLE interviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    interviewer_name TEXT NOT NULL,
    scheduled_at TIMESTAMP,
    evaluation_area TEXT,
    coverage_level TEXT CHECK(coverage_level IN ('Not Covered','Lightly Covered','Well Covered')),
    confidence_level TEXT CHECK(confidence_level IN ('Low','Medium','High')),
    assessment TEXT CHECK(assessment IN ('Strong Positive','Positive','Neutral','Concern','Strong Concern')),
    strengths TEXT,
    concerns TEXT,
    suggested_future_probes TEXT,
    recommendation TEXT,
    feedback_submitted_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 5. ASSIGNMENTS  (Section 21)
-- ============================================================
CREATE TABLE assignment_repository (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_name TEXT NOT NULL,
    role_category TEXT,
    experience_level TEXT,
    skills_covered TEXT,          -- JSON array
    difficulty_level TEXT,
    assignment_content TEXT,
    evaluation_criteria TEXT,     -- JSON: {area: weight}
    historical_usage_count INTEGER DEFAULT 0
);

CREATE TABLE assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    assignment_repository_id INTEGER REFERENCES assignment_repository(id),
    sent_at TIMESTAMP,
    submission_deadline TIMESTAMP,
    submitted_at TIMESTAMP,
    status TEXT CHECK(status IN ('Sent','Submitted','Overdue')) DEFAULT 'Sent',
    technical_accuracy_score REAL,   -- weight 40%
    problem_solving_score REAL,      -- weight 25%
    clarity_structure_score REAL,    -- weight 15%
    practical_thinking_score REAL,   -- weight 10%
    completeness_score REAL,         -- weight 10%
    weighted_total REAL
);

-- ============================================================
-- 6. REFERENCE CHECKS  (Section 22)
-- ============================================================
CREATE TABLE reference_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    reference_name TEXT,
    reference_relationship TEXT,
    overall_outcome TEXT,
    positive_signals TEXT,
    concerns TEXT,
    rehire_eligibility TEXT,
    risk_level TEXT CHECK(risk_level IN ('Low','Medium','High')),
    ai_summary TEXT,
    completed_at TIMESTAMP
);

-- ============================================================
-- 7. ACTIVITY TIMELINE  (Section 15 — the operational source of truth)
-- ============================================================
CREATE TABLE activity_timeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    activity TEXT NOT NULL,
    stage_from TEXT,
    stage_to TEXT,
    actor TEXT,
    is_stage_skip INTEGER DEFAULT 0,
    skip_reason TEXT,
    occurred_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 8. EVALUATION QUESTION REPOSITORY  (Section 20)
-- ============================================================
CREATE TABLE evaluation_question_repository (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_area TEXT NOT NULL,
    role_category TEXT,
    experience_level TEXT,
    question_type TEXT,
    question_text TEXT NOT NULL,
    priority TEXT,
    mandatory INTEGER DEFAULT 0,
    is_ai_generated INTEGER DEFAULT 0,   -- AI may suggest/append, never overwrite curated rows
    curated_locked INTEGER DEFAULT 0     -- if 1, AI must not modify or delete this row
);

-- ============================================================
-- 9. COMPENSATION BENCHMARK REPOSITORY  (Section 11)
-- ============================================================
CREATE TABLE compensation_benchmark_repository (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_category TEXT NOT NULL,
    experience_range TEXT,
    typical_market_band_min REAL,
    typical_market_band_max REAL,
    currency TEXT DEFAULT 'INR',
    last_updated_by TEXT,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- SLA TRACKING  (Section 27, 28)
-- ============================================================
CREATE TABLE sla_clocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,        -- 'role' | 'candidate'
    entity_id INTEGER NOT NULL,
    stage_name TEXT NOT NULL,
    sla_hours INTEGER NOT NULL,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    escalation_level TEXT CHECK(escalation_level IN (
        'On Track','Friendly Reminder','Strong Reminder','Escalation','Hiring Blocked'
    )) DEFAULT 'On Track'
);

CREATE INDEX idx_candidates_role ON candidates(role_id);
CREATE INDEX idx_candidates_stage ON candidates(stage);
CREATE INDEX idx_screening_candidate ON resume_screening_results(candidate_id);
CREATE INDEX idx_sla_open ON sla_clocks(entity_type, entity_id) WHERE completed_at IS NULL;
