from fastapi import FastAPI
from app.models import init_db
from app.routers import auth, roles, candidates, dashboard

app = FastAPI(
    title="Solinas Hiring Management System",
    description="AI-assisted hiring operations API. AI agents assist and score; "
                "humans decide and advance stages (Section 2 of the operating design doc).",
    version="0.1.0",
)

app.include_router(auth.router)
app.include_router(roles.router)
app.include_router(candidates.router)
app.include_router(dashboard.router)


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/health")
def health():
    return {"status": "ok"}
