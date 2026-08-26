from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.routers import auth, roles, candidates, dashboard, interviews, assignments, reference_checks, assignment_repository, candidate_lifecycle

app = FastAPI(
    title="Solinas Hiring Management System",
    description="AI-assisted hiring operations API. AI agents assist and score; "
                "humans decide and advance stages (Section 2 of the operating design doc).",
    version="0.6.0",
)

app.include_router(auth.router)
app.include_router(roles.router)
app.include_router(candidates.router)
app.include_router(dashboard.router)
app.include_router(interviews.router)
app.include_router(assignments.router)
app.include_router(reference_checks.router)
app.include_router(assignment_repository.router)
app.include_router(candidate_lifecycle.router)

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
