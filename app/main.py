from fastapi import FastAPI
from app.routers import auth, roles, candidates, dashboard, interviews

app = FastAPI(
    title="Solinas Hiring Management System",
    description="AI-assisted hiring operations API. AI agents assist and score; "
                "humans decide and advance stages (Section 2 of the operating design doc).",
    version="0.3.0",
)

app.include_router(auth.router)
app.include_router(roles.router)
app.include_router(candidates.router)
app.include_router(dashboard.router)
app.include_router(interviews.router)

# NOTE: schema creation/changes are now handled by Alembic (`alembic upgrade
# head`), not by calling Base.metadata.create_all() on startup. Running both
# would fight each other — create_all() doesn't know about migration history,
# and would silently create tables/columns Alembic doesn't know it needs to
# track. See alembic/README_DEPLOY.md for the one-time production stamp step.


@app.get("/health")
def health():
    return {"status": "ok"}
