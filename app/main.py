from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import os
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from app.rate_limit import limiter
from app.routers import auth, roles, candidates, dashboard, interviews, assignments, reference_checks, assignment_repository, candidate_lifecycle, role_requirements, compensation_benchmarks, templates_and_postings, recruiter_tools, duplicates_and_sources, candidate_views, interview_scheduling, internal, compensation_research

# Rate limiting — in-memory, per-process. That's genuinely sufficient here:
# this runs as a single Render free-tier instance, not multiple replicas
# behind a load balancer, so there's no need for a shared store (Redis)
# that a multi-instance deployment would require. If this ever moves to
# multiple instances, in-memory limits would need to become
# instance-specific (each replica tracks its own counts) — a real
# limitation worth knowing about before scaling up, not a bug today.

app = FastAPI(
    title="Solinas Hiring Management System",
    description="AI-assisted hiring operations API. AI agents assist and score; "
                "humans decide and advance stages (Section 2 of the operating design doc).",
    version="0.9.0",
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Error monitoring — degrades to doing nothing if SENTRY_DSN isn't set,
# same graceful-if-unconfigured pattern as the Resend email integration.
# Sign up at sentry.io (free, no card, 5,000 errors/month) to get a DSN.
sentry_dsn = os.environ.get("SENTRY_DSN")
if sentry_dsn:
    import sentry_sdk
    sentry_sdk.init(dsn=sentry_dsn, traces_sample_rate=0.1, send_default_pii=False)
    # send_default_pii=False deliberately — this app handles candidate PII
    # (names, resumes, compensation) and reference/HR notes; Sentry should
    # capture stack traces and error context, not accidentally forward
    # sensitive request bodies to a third party by default.

app.include_router(auth.router)
app.include_router(roles.router)
app.include_router(candidates.router)
app.include_router(dashboard.router)
app.include_router(interviews.router)
app.include_router(assignments.router)
app.include_router(reference_checks.router)
app.include_router(assignment_repository.router)
app.include_router(candidate_lifecycle.router)
app.include_router(role_requirements.router)
app.include_router(compensation_benchmarks.router)
app.include_router(templates_and_postings.router)
app.include_router(recruiter_tools.router)
app.include_router(duplicates_and_sources.router)
app.include_router(candidate_views.router)
app.include_router(interview_scheduling.router)
app.include_router(internal.router)
app.include_router(compensation_research.router)

# Frontend served at /app — kept off the root path so /docs, /health, and every
# existing API route are completely unaffected. html=True serves index.html
# automatically at /app/ with no separate build step or static-site host needed.
app.mount("/app", StaticFiles(directory="frontend", html=True), name="frontend")

# NOTE: schema creation/changes are now handled by Alembic (`alembic upgrade
# head`), not by calling Base.metadata.create_all() on startup. Running both
# would fight each other — create_all() doesn't know about migration history,
# and would silently create tables/columns Alembic doesn't know it needs to
# track. See alembic/README_DEPLOY.md for the one-time production stamp step.


@app.get("/health")
def health():
    return {"status": "ok"}
